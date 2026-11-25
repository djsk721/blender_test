"""Semantic Base Mesh manipulation, body-aware garment extraction, Bezier hair, and hand grip utilities.

Designed for approved CC0 anatomical Base Meshes (e.g., Blender Studio Human Base Meshes)
loaded natively via `bpy.data.libraries.load` after fail-closed SHA-256 provenance validation.
Strict zero asset-specific hardcoding: all landmark mapping, stylization controls, garment slicing,
curve-based hair, and hand grip helpers operate dynamically from mesh geometry in Blender.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from . import blender_toolkit as bt
from .mcp_adapter import verify_approved_base_mesh_manifest

try:
    import bpy  # type: ignore
    import bmesh  # type: ignore
    from mathutils import Euler, Matrix, Vector  # type: ignore
except ImportError:  # Allow importing outside Blender for signature/unit tests
    bpy = None
    bmesh = None
    Euler = Matrix = Vector = None


def _require_blender() -> None:
    if bpy is None or bmesh is None:
        raise RuntimeError("base_mesh_ops functions must execute inside Blender (bpy/bmesh required).")


# Allowed semantic stylization parameter ranges (validated & clamped)
SEMANTIC_CONTROL_LIMITS: dict[str, tuple[float, float]] = {
    "head_scale": (-0.5, 0.6),
    "eye_size": (-0.4, 0.6),
    "eye_separation": (-0.4, 0.5),
    "jaw_width": (-0.5, 0.5),
    "chin_length": (-0.4, 0.5),
    "nose_size": (-0.4, 0.5),
    "ear_pointedness": (0.0, 1.0),
    "shoulder_width": (-0.45, 0.5),
    "waist_taper": (-0.5, 0.5),
    "hip_width": (-0.45, 0.5),
    "torso_length": (-0.35, 0.35),
    "limb_slenderness": (-0.45, 0.45),
    "hand_scale": (-0.4, 0.5),
    "neck_length": (-0.35, 0.45),
}


def _eval_mesh_world_verts(obj: Any) -> tuple[list[Any], Any]:
    """Return world-space vertex positions from the evaluated depsgraph mesh."""
    _require_blender()
    deps = bpy.context.evaluated_depsgraph_get()
    eval_obj = obj.evaluated_get(deps)
    mesh = eval_obj.to_mesh()
    mat = eval_obj.matrix_world.copy()
    try:
        verts = [mat @ v.co for v in mesh.vertices]
        polys = [
            {
                "verts": list(p.vertices),
                "center": mat @ p.center,
                "normal": (mat.to_3x3() @ p.normal).normalized(),
            }
            for p in mesh.polygons
        ]
    finally:
        eval_obj.to_mesh_clear()
    return verts, polys


def _auto_detect_front_axis(obj: Any) -> float:
    """Estimate Z rotation (degrees) needed to orient a humanoid mesh to face -Y."""
    zs = [v.co.z for v in obj.data.vertices]
    if not zs:
        return 0.0
    z0, z1 = min(zs), max(zs)
    h = max(z1 - z0, 1e-4)

    # Determine lateral (shoulder-to-shoulder) axis from torso/arms band (0.50..0.85 height)
    upper_pts = [v.co for v in obj.data.vertices if 0.50 <= (v.co.z - z0) / h <= 0.85] or list(
        v.co for v in obj.data.vertices
    )
    upper_span_x = max(p.x for p in upper_pts) - min(p.x for p in upper_pts)
    upper_span_y = max(p.y for p in upper_pts) - min(p.y for p in upper_pts)

    foot_pts = [v.co for v in obj.data.vertices if (v.co.z - z0) / h <= 0.05]
    ankle_pts = [v.co for v in obj.data.vertices if 0.05 < (v.co.z - z0) / h <= 0.10]
    probe = foot_pts if len(foot_pts) >= 8 else [v.co for v in obj.data.vertices if 0.87 <= (v.co.z - z0) / h <= 0.95]
    if not probe:
        return 0.0

    if upper_span_x >= upper_span_y:
        # Left-right is already along X; forward is either -Y (0 deg) or +Y (180 deg)
        ref_y = (sum(p.y for p in ankle_pts) / len(ankle_pts)) if ankle_pts else (sum(p.y for p in probe) / len(probe))
        max_pos_y = max(p.y for p in probe) - ref_y
        max_neg_y = ref_y - min(p.y for p in probe)
        return 180.0 if max_pos_y > max_neg_y else 0.0

    # Left-right is along Y; forward is +X (-90 deg turns +X to -Y) or -X (+90 deg turns -X to -Y)
    ref_x = (sum(p.x for p in ankle_pts) / len(ankle_pts)) if ankle_pts else (sum(p.x for p in probe) / len(probe))
    max_pos_x = max(p.x for p in probe) - ref_x
    max_neg_x = ref_x - min(p.x for p in probe)
    return -90.0 if max_pos_x > max_neg_x else 90.0


def verify_and_load_base_mesh(
    scene: Any,
    manifest_path: Path | str,
    object_name: str,
    *,
    target_height_m: float = 1.65,
    collection: Any | None = None,
    new_name: str = "SM_BaseBody",
    rotate_z_deg: float | None = None,
) -> dict[str, Any]:
    """Verify SHA-256 provenance, append approved Base Mesh via bpy.data.libraries.load, and normalize."""
    _require_blender()
    prov = verify_approved_base_mesh_manifest(manifest_path)
    blend_path = prov["blend_path"]

    bt.remove_object_if_exists(new_name)
    col = collection or bt.get_or_create_collection(scene, "COL_Export")

    with bpy.data.libraries.load(blend_path, link=False) as (data_from, data_to):
        avail = list(data_from.objects)
        if object_name not in avail:
            matches = [n for n in avail if n.lower() == object_name.lower() or object_name.lower() in n.lower()]
            if not matches:
                raise ValueError(f"Object {object_name!r} not found in verified bundle {blend_path}. Available: {avail}")
            resolved_name = matches[0]
        else:
            resolved_name = object_name
        companion_eye_names = [
            cand for cand in (f"{resolved_name}.eye.L", f"{resolved_name}.eye.R") if cand in avail
        ]
        data_to.objects = [resolved_name] + companion_eye_names

    appended = [o for o in data_to.objects if o is not None]
    if not appended:
        raise RuntimeError(f"Failed to append object {resolved_name!r} from {blend_path}")
    obj = appended[0]
    if obj.type != "MESH" or obj.data is None:
        raise ValueError(f"Appended object {obj.name!r} is type {obj.type}, expected MESH.")

    companion_objs = [o for o in appended[1:] if o.type == "MESH" and o.data is not None]

    # Link first & update view layer so Blender evaluates parent + matrix_parent_inverse + location into matrix_world!
    all_loaded = [obj] + companion_objs
    for loaded_obj in all_loaded:
        bt.link_to_collection(loaded_obj, col)
    bpy.context.view_layer.update()

    # Bake initial world transforms (including parent offsets before clearing parent) into vertices
    for loaded_obj in all_loaded:
        mat_w = loaded_obj.matrix_world.copy()
        loaded_obj.parent = None
        for m in list(loaded_obj.modifiers):
            if m.type == "ARMATURE":
                loaded_obj.modifiers.remove(m)
        for v in loaded_obj.data.vertices:
            v.co = mat_w @ v.co
        loaded_obj.matrix_world = Matrix.Identity(4)

    obj.name = new_name
    obj.data.name = f"{new_name}_Mesh"

    mesh = obj.data
    rot_deg = _auto_detect_front_axis(obj) if rotate_z_deg is None else float(rotate_z_deg)
    rmat = Euler((0.0, 0.0, math.radians(rot_deg)), "XYZ").to_matrix().to_4x4() if abs(rot_deg) > 1e-4 else None
    if rmat is not None:
        for loaded_obj in all_loaded:
            for v in loaded_obj.data.vertices:
                v.co = rmat @ v.co

    # Center X/Y near 0 and ground min_z = 0.0, scaling uniformly to target_height_m
    xs = [v.co.x for v in mesh.vertices]
    ys = [v.co.y for v in mesh.vertices]
    zs = [v.co.z for v in mesh.vertices]
    min_z, max_z = min(zs), max(zs)
    cur_h = max(max_z - min_z, 1e-4)
    scale_f = float(target_height_m) / cur_h
    cx = 0.5 * (min(xs) + max(xs))
    cy = 0.5 * (min(ys) + max(ys))

    eye_names: list[str] = []
    for idx, loaded_obj in enumerate(all_loaded):
        for v in loaded_obj.data.vertices:
            v.co = Vector(((v.co.x - cx) * scale_f, (v.co.y - cy) * scale_f, (v.co.z - min_z) * scale_f))
        loaded_obj.data.update()
        bt.set_smooth(loaded_obj, True)
        if len(loaded_obj.data.uv_layers) == 0:
            bt.smart_uv_unwrap(loaded_obj)
        if idx > 0:
            side_tag = "L" if loaded_obj.name.endswith(".L") else "R"
            ename = f"{new_name}_Eye_{side_tag}"
            bt.remove_object_if_exists(ename)
            loaded_obj.name = ename
            loaded_obj.data.name = f"{ename}_Mesh"
            bt.link_to_collection(loaded_obj, col)
            eye_names.append(ename)

    landmarks = inspect_anatomical_regions(obj, assign_vertex_groups=True)
    return {
        "object": obj,
        "object_name": obj.name,
        "companion_eyes": eye_names,
        "original_asset_name": resolved_name,
        "provenance": prov,
        "rotate_z_deg": rot_deg,
        "target_height_m": target_height_m,
        "vertices": len(mesh.vertices),
        "polygons": len(mesh.polygons),
        "uv_layers": [u.name for u in mesh.uv_layers],
        "landmarks": landmarks,
    }


def _find_head_shoulder_splits(verts: list[Any], z_min: float, h: float) -> dict[str, float]:
    """Estimate X/Z anatomical transition heights from silhouette width profile."""
    # Detect narrowest neck slice around 0.80..0.88 height
    neck_z = z_min + 0.84 * h
    best_w = 1e9
    for step in range(20):
        z_probe = z_min + (0.79 + 0.005 * step) * h
        band = [v for v in verts if abs(v.z - z_probe) <= 0.012 * h and abs(v.x) <= 0.14 * h]
        if len(band) >= 6:
            w = max(v.x for v in band) - min(v.x for v in band)
            if w < best_w:
                best_w = w
                neck_z = z_probe
    # Shoulder height slightly below neck where torso+arms widen
    shoulder_z = neck_z - 0.045 * h
    waist_z = z_min + 0.60 * h
    hip_z = z_min + 0.50 * h
    knee_z = z_min + 0.27 * h
    ankle_z = z_min + 0.055 * h
    return {
        "neck_z": round(neck_z, 4),
        "shoulder_z": round(shoulder_z, 4),
        "waist_z": round(waist_z, 4),
        "hip_z": round(hip_z, 4),
        "knee_z": round(knee_z, 4),
        "ankle_z": round(ankle_z, 4),
    }


def inspect_anatomical_regions(obj: Any, *, assign_vertex_groups: bool = True) -> dict[str, Any]:
    """Inspect existing vertex groups/shape keys and map verified anatomical landmarks & ANAT_* groups."""
    _require_blender()
    if obj.type != "MESH" or obj.data is None:
        raise ValueError(f"Expected MESH object, got {obj.type}")

    mesh = obj.data
    verts, _ = _eval_mesh_world_verts(obj)
    if not verts:
        raise ValueError(f"Base mesh {obj.name} has zero vertices.")

    xs = [v.x for v in verts]
    ys = [v.y for v in verts]
    zs = [v.z for v in verts]
    z_min, z_max = min(zs), max(zs)
    h = max(z_max - z_min, 1e-4)
    splits = _find_head_shoulder_splits(verts, z_min, h)
    neck_z = splits["neck_z"]
    head_h = max(z_max - neck_z, 0.10 * h)

    # Measure head center and front surface from head vertices
    head_pts = [v for v in verts if v.z >= neck_z]
    head_x_span = (max(v.x for v in head_pts) - min(v.x for v in head_pts)) if head_pts else 0.16
    head_y_min = min((v.y for v in head_pts), default=-0.10)
    head_y_max = max((v.y for v in head_pts), default=0.10)
    head_cy = 0.5 * (head_y_min + head_y_max)

    # Torso shoulder/wrist detection by X extent in upper body
    torso_pts = [v for v in verts if splits["waist_z"] <= v.z <= neck_z]
    max_arm_x = max((abs(v.x) for v in torso_pts), default=0.35 * h)
    arm_threshold_x = min(0.14 * h, 0.42 * max_arm_x)
    # Check if arms are in A-pose (angling down) or T-pose
    outer_arm_pts = [v for v in verts if abs(v.x) > arm_threshold_x and splits["hip_z"] * 0.7 <= v.z <= neck_z]
    shoulder_half_w = min(0.115 * h, 0.35 * max_arm_x)

    def _mean_pt(pts: list[Any], fallback: tuple[float, float, float]) -> list[float]:
        if not pts:
            return [round(c, 4) for c in fallback]
        n = len(pts)
        return [
            round(sum(p.x for p in pts) / n, 4),
            round(sum(p.y for p in pts) / n, 4),
            round(sum(p.z for p in pts) / n, 4),
        ]

    # Facial landmark estimates on the -Y front of the head
    eye_z = neck_z + 0.56 * head_h
    brow_z = neck_z + 0.66 * head_h
    forehead_z = neck_z + 0.79 * head_h
    nose_z = neck_z + 0.42 * head_h
    mouth_z = neck_z + 0.25 * head_h
    chin_z = neck_z + 0.08 * head_h

    def _front_near(x_target: float, z_target: float, x_tol: float = 0.025, z_tol: float = 0.022) -> list[float]:
        cands = [v for v in head_pts if abs(v.x - x_target) <= x_tol and abs(v.z - z_target) <= z_tol and v.y <= head_cy]
        if not cands:
            cands = [v for v in head_pts if abs(v.z - z_target) <= z_tol * 1.6 and v.y <= head_cy]
        if cands:
            cands.sort(key=lambda p: p.y)
            front = cands[: max(1, len(cands) // 3)]
            return _mean_pt(front, (x_target, head_y_min, z_target))
        return [round(x_target, 4), round(head_y_min, 4), round(z_target, 4)]

    eye_x = 0.21 * head_x_span
    ear_pts_l = [v for v in head_pts if v.x >= 0.43 * head_x_span and neck_z + 0.35 * head_h <= v.z <= neck_z + 0.68 * head_h]
    ear_pts_r = [v for v in head_pts if v.x <= -0.43 * head_x_span and neck_z + 0.35 * head_h <= v.z <= neck_z + 0.68 * head_h]

    # Hand/finger landmarks (outermost lateral + low forearm points along +/-X arm)
    arm_l_pts = sorted([v for v in outer_arm_pts if v.x > 0], key=lambda p: p.x)
    arm_r_pts = sorted([v for v in outer_arm_pts if v.x < 0], key=lambda p: -p.x)
    hand_l_pts = arm_l_pts[-max(1, len(arm_l_pts) // 5):] if arm_l_pts else []
    hand_r_pts = arm_r_pts[-max(1, len(arm_r_pts) // 5):] if arm_r_pts else []

    sh_l_pts = [v for v in verts if 0.08 * h <= v.x <= 0.14 * h and abs(v.z - splits["shoulder_z"]) <= 0.04 * h]
    sh_r_pts = [v for v in verts if -0.14 * h <= v.x <= -0.08 * h and abs(v.z - splits["shoulder_z"]) <= 0.04 * h]
    feet_l_pts = [v for v in verts if v.x > 0.01 and v.z <= splits["ankle_z"]]
    feet_r_pts = [v for v in verts if v.x < -0.01 and v.z <= splits["ankle_z"]]

    landmarks: dict[str, Any] = {
        "ground_z": round(z_min, 4),
        "head_top_z": round(z_max, 4),
        "height_m": round(h, 4),
        "neck": _mean_pt([v for v in verts if abs(v.z - neck_z) <= 0.02 * h and abs(v.x) <= 0.06 * h], (0.0, 0.0, neck_z)),
        "head_center": [0.0, round(head_cy, 4), round(0.5 * (neck_z + z_max), 4)],
        "forehead": _front_near(0.0, forehead_z, 0.035, 0.025),
        "eye_L": _front_near(eye_x, eye_z, 0.02, 0.018),
        "eye_R": _front_near(-eye_x, eye_z, 0.02, 0.018),
        "eyebrow_L": _front_near(eye_x, brow_z, 0.022, 0.016),
        "eyebrow_R": _front_near(-eye_x, brow_z, 0.022, 0.016),
        "nose_bridge": _front_near(0.0, 0.5 * (eye_z + nose_z), 0.015, 0.016),
        "nose_tip": _front_near(0.0, nose_z, 0.015, 0.016),
        "cheek_L": _front_near(0.32 * head_x_span, 0.5 * (nose_z + mouth_z), 0.022, 0.022),
        "cheek_R": _front_near(-0.32 * head_x_span, 0.5 * (nose_z + mouth_z), 0.022, 0.022),
        "lips": _front_near(0.0, mouth_z, 0.022, 0.016),
        "chin": _front_near(0.0, chin_z, 0.022, 0.02),
        "ear_L": _mean_pt(ear_pts_l, (0.48 * head_x_span, head_cy, eye_z)),
        "ear_R": _mean_pt(ear_pts_r, (-0.48 * head_x_span, head_cy, eye_z)),
        "shoulder_L": _mean_pt(sh_l_pts, (shoulder_half_w, 0.0, splits["shoulder_z"])),
        "shoulder_R": _mean_pt(sh_r_pts, (-shoulder_half_w, 0.0, splits["shoulder_z"])),
        "waist": [0.0, 0.0, splits["waist_z"]],
        "hips": [0.0, 0.0, splits["hip_z"]],
        "hand_L": _mean_pt(hand_l_pts, (max_arm_x * 0.92, 0.0, splits["hip_z"])),
        "hand_R": _mean_pt(hand_r_pts, (-max_arm_x * 0.92, 0.0, splits["hip_z"])),
        "wrist_L": _mean_pt(arm_l_pts[int(len(arm_l_pts) * 0.72) : int(len(arm_l_pts) * 0.85)] if len(arm_l_pts) > 8 else hand_l_pts, (max_arm_x * 0.78, 0.0, splits["hip_z"] + 0.07)),
        "wrist_R": _mean_pt(arm_r_pts[int(len(arm_r_pts) * 0.72) : int(len(arm_r_pts) * 0.85)] if len(arm_r_pts) > 8 else hand_r_pts, (-max_arm_x * 0.78, 0.0, splits["hip_z"] + 0.07)),
        "elbow_L": _mean_pt(arm_l_pts[int(len(arm_l_pts) * 0.35) : int(len(arm_l_pts) * 0.52)] if len(arm_l_pts) > 8 else sh_l_pts, (shoulder_half_w + 0.14, 0.0, splits["waist_z"] + 0.05)),
        "elbow_R": _mean_pt(arm_r_pts[int(len(arm_r_pts) * 0.35) : int(len(arm_r_pts) * 0.52)] if len(arm_r_pts) > 8 else sh_r_pts, (-shoulder_half_w - 0.14, 0.0, splits["waist_z"] + 0.05)),
        "knee_L": _mean_pt([v for v in verts if v.x > 0.01 and abs(v.z - splits["knee_z"]) <= 0.02 * h], (0.065 * h, 0.0, splits["knee_z"])),
        "knee_R": _mean_pt([v for v in verts if v.x < -0.01 and abs(v.z - splits["knee_z"]) <= 0.02 * h], (-0.065 * h, 0.0, splits["knee_z"])),
        "ankle_L": _mean_pt([v for v in verts if v.x > 0.01 and abs(v.z - splits["ankle_z"]) <= 0.02 * h], (0.065 * h, 0.0, splits["ankle_z"])),
        "ankle_R": _mean_pt([v for v in verts if v.x < -0.01 and abs(v.z - splits["ankle_z"]) <= 0.02 * h], (-0.065 * h, 0.0, splits["ankle_z"])),
        "foot_L": _mean_pt(feet_l_pts, (0.065 * h, -0.03, 0.02)),
        "foot_R": _mean_pt(feet_r_pts, (-0.065 * h, -0.03, 0.02)),
        "bbox_min": [round(min(xs), 4), round(min(ys), 4), round(z_min, 4)],
        "bbox_max": [round(max(xs), 4), round(max(ys), 4), round(z_max, 4)],
    }

    anat_groups_created: list[str] = []
    if assign_vertex_groups:
        # Define verified anatomical regions cleanly around detected landmarks
        region_spheres: dict[str, tuple[list[float], float]] = {
            "ANAT_Head": (landmarks["head_center"], head_h * 0.65),
            "ANAT_Forehead": (landmarks["forehead"], head_h * 0.28),
            "ANAT_EyeSockets": ([0.0, landmarks["eye_L"][1], eye_z], head_x_span * 0.42),
            "ANAT_Eyebrows": ([0.0, landmarks["eyebrow_L"][1], brow_z], head_x_span * 0.40),
            "ANAT_Nose": (landmarks["nose_tip"], head_h * 0.16),
            "ANAT_Cheeks": ([0.0, landmarks["cheek_L"][1], 0.5 * (nose_z + mouth_z)], head_x_span * 0.48),
            "ANAT_Lips": (landmarks["lips"], head_h * 0.14),
            "ANAT_JawChin": (landmarks["chin"], head_h * 0.28),
            "ANAT_Ear_L": (landmarks["ear_L"], head_h * 0.18),
            "ANAT_Ear_R": (landmarks["ear_R"], head_h * 0.18),
            "ANAT_Neck": (landmarks["neck"], head_h * 0.28),
            "ANAT_Hand_L": (landmarks["hand_L"], 0.11),
            "ANAT_Hand_R": (landmarks["hand_R"], 0.11),
        }
        for gname, (center, rad) in region_spheres.items():
            vg = obj.vertex_groups.get(gname) or obj.vertex_groups.new(name=gname)
            cvec = Vector(center)
            rad = max(1e-4, rad)
            for idx, wco in enumerate(verts):
                dist = (wco - cvec).length
                if dist < rad:
                    w = 0.5 * (1.0 + math.cos(math.pi * dist / rad))
                    if w > 0.02:
                        vg.add([idx], w, "REPLACE")
            anat_groups_created.append(gname)

    return {
        "object_name": obj.name,
        "existing_vertex_groups": [g.name for g in obj.vertex_groups if not g.name.startswith("ANAT_")],
        "anat_groups_created": anat_groups_created,
        "existing_shape_keys": [k.name for k in mesh.shape_keys.key_blocks] if mesh.shape_keys else [],
        "landmarks": landmarks,
    }


def apply_semantic_stylization(obj: Any, controls: dict[str, float]) -> dict[str, Any]:
    """Apply clamped semantic artistic stylization via non-destructive Blender Shape Keys."""
    _require_blender()
    if obj.type != "MESH" or obj.data is None:
        raise ValueError(f"Expected MESH object, got {obj.type}")

    # Validate & clamp every supplied control parameter strictly within approved ranges
    validated: dict[str, float] = {}
    for key, raw_val in (controls or {}).items():
        if key not in SEMANTIC_CONTROL_LIMITS:
            continue
        lo, hi = SEMANTIC_CONTROL_LIMITS[key]
        validated[key] = max(lo, min(hi, float(raw_val)))

    mesh = obj.data
    info = inspect_anatomical_regions(obj, assign_vertex_groups=True)
    lm = info["landmarks"]

    if not mesh.shape_keys:
        obj.shape_key_add(name="Basis", from_mix=False)
    sk = mesh.shape_keys.key_blocks.get("SK_Stylization")
    if sk is None:
        sk = obj.shape_key_add(name="SK_Stylization", from_mix=False)
    sk.value = 1.0

    # Initialize SK_Stylization from Basis so repeated calls are clean and idempotent
    basis = mesh.shape_keys.key_blocks["Basis"]
    for i in range(len(basis.data)):
        sk.data[i].co = basis.data[i].co.copy()

    inv_mat = obj.matrix_world.inverted()
    mat = obj.matrix_world

    head_c = Vector(lm["head_center"])
    neck_z = lm["neck"][2]
    waist_z = lm["waist"][2]
    hip_z = lm["hips"][2]
    sh_z = lm["shoulder_L"][2]
    h = lm["height_m"]
    head_h = max(lm["head_top_z"] - neck_z, 0.16)

    eye_l = Vector(lm["eye_L"])
    eye_r = Vector(lm["eye_R"])
    chin = Vector(lm["chin"])
    nose_t = Vector(lm["nose_tip"])
    ear_l = Vector(lm["ear_L"])
    ear_r = Vector(lm["ear_R"])
    hand_l = Vector(lm["hand_L"])
    hand_r = Vector(lm["hand_R"])

    def _rbf(p: Vector, c: Vector, rad: float) -> float:
        d = (p - c).length
        return math.exp(-((d / max(rad, 1e-4)) ** 2)) if d < rad * 2.4 else 0.0

    moved = 0
    for i, bco in enumerate(basis.data):
        wp = mat @ bco.co
        delta = Vector((0.0, 0.0, 0.0))

        # 1. Head scale around neck pivot (essential for anime/stylized 6.0-6.5 head proportions)
        hs = validated.get("head_scale", 0.0)
        if abs(hs) > 1e-4 and wp.z > neck_z - 0.02:
            w_neck = min(1.0, max(0.0, (wp.z - (neck_z - 0.02)) / 0.05))
            pivot = Vector((0.0, head_c.y, neck_z))
            delta += (wp - pivot) * (hs * 0.45 * w_neck)

        # 2. Neck length
        nl = validated.get("neck_length", 0.0)
        if abs(nl) > 1e-4 and wp.z > sh_z:
            w_n = min(1.0, max(0.0, (wp.z - sh_z) / max(0.04, neck_z - sh_z)))
            delta.z += nl * 0.045 * w_n

        # 3. Eye size & separation (forward + lateral widening of orbital socket)
        es = validated.get("eye_size", 0.0)
        ep = validated.get("eye_separation", 0.0)
        if abs(es) > 1e-4 or abs(ep) > 1e-4:
            for side_sign, ec in ((1.0, eye_l), (-1.0, eye_r)):
                w = _rbf(wp, ec, head_h * 0.16)
                if w > 1e-4:
                    delta += (wp - ec) * (es * 0.35 * w)
                    delta.x += side_sign * ep * 0.015 * w

        # 4. Jaw width & chin length
        jw = validated.get("jaw_width", 0.0)
        cl = validated.get("chin_length", 0.0)
        if (abs(jw) > 1e-4 or abs(cl) > 1e-4) and neck_z <= wp.z <= eye_l.z:
            w_jaw = _rbf(wp, chin, head_h * 0.34)
            delta.x += wp.x * jw * 0.40 * w_jaw
            delta.z -= cl * 0.028 * w_jaw
            delta.y += -cl * 0.012 * w_jaw

        # 5. Nose size
        ns = validated.get("nose_size", 0.0)
        if abs(ns) > 1e-4:
            w_nose = _rbf(wp, nose_t, head_h * 0.14)
            delta += (wp - nose_t) * (ns * 0.45 * w_nose)
            delta.y += -ns * 0.012 * w_nose

        # 6. Pointed elven ears along outward/back-up vector from ear root
        ear_pt = validated.get("ear_pointedness", 0.0)
        if ear_pt > 1e-4:
            for side_sign, ec in ((1.0, ear_l), (-1.0, ear_r)):
                if abs(wp.x) >= abs(ec.x) * 0.85:
                    w_e = _rbf(wp, ec, head_h * 0.16)
                    if w_e > 1e-4:
                        outer_t = max(0.0, min(1.0, (abs(wp.x) - abs(ec.x) * 0.85) / 0.03))
                        delta += Vector((side_sign * 0.048, 0.036, 0.022)) * (ear_pt * w_e * (0.25 + 0.75 * outer_t))

        # 7. Torso width/waist/hip/length
        sw = validated.get("shoulder_width", 0.0)
        wt = validated.get("waist_taper", 0.0)
        hw = validated.get("hip_width", 0.0)
        tl = validated.get("torso_length", 0.0)
        if abs(sw) > 1e-4 and abs(wp.z - sh_z) < 0.14 * h:
            w_sh = math.exp(-(((wp.z - sh_z) / (0.09 * h)) ** 2))
            delta.x += wp.x * sw * 0.32 * w_sh
        if abs(wt) > 1e-4 and abs(wp.z - waist_z) < 0.12 * h and abs(wp.x) < 0.22 * h:
            w_wa = math.exp(-(((wp.z - waist_z) / (0.075 * h)) ** 2))
            delta.x += wp.x * wt * 0.35 * w_wa
            delta.y += wp.y * wt * 0.25 * w_wa
        if abs(hw) > 1e-4 and abs(wp.z - hip_z) < 0.12 * h and abs(wp.x) < 0.22 * h:
            w_hp = math.exp(-(((wp.z - hip_z) / (0.08 * h)) ** 2))
            delta.x += wp.x * hw * 0.30 * w_hp
            delta.y += wp.y * hw * 0.20 * w_hp
        if abs(tl) > 1e-4 and wp.z > hip_z:
            w_tl = min(1.0, max(0.0, (wp.z - hip_z) / max(0.15, sh_z - hip_z)))
            delta.z += tl * 0.09 * w_tl

        # 8. Hand scale
        hnd_s = validated.get("hand_scale", 0.0)
        if abs(hnd_s) > 1e-4:
            for hc in (hand_l, hand_r):
                w_h = _rbf(wp, hc, 0.11)
                if w_h > 1e-4:
                    delta += (wp - hc) * (hnd_s * 0.40 * w_h)

        # 9. Limb slenderness (legs below hips + arms beyond shoulder x)
        ls = validated.get("limb_slenderness", 0.0)
        if abs(ls) > 1e-4:
            if 0.04 * h < wp.z < hip_z - 0.04 * h:
                leg_cx = math.copysign(0.065 * h, wp.x) if abs(wp.x) > 0.015 else 0.0
                delta.x += (wp.x - leg_cx) * (-ls * 0.22)
                delta.y += wp.y * (-ls * 0.22)

        if delta.length > 1e-6:
            sk.data[i].co = inv_mat @ (wp + delta)
            moved += 1

    mesh.update()
    # Keep object grounded cleanly at min_z == 0.0 after neck/chin/torso changes
    verts_after, _ = _eval_mesh_world_verts(obj)
    min_z_after = min(v.z for v in verts_after)
    if abs(min_z_after) > 1e-4:
        for i in range(len(sk.data)):
            sk.data[i].co.z -= min_z_after
        for i in range(len(basis.data)):
            basis.data[i].co.z -= min_z_after
        mesh.update()

    after_info = inspect_anatomical_regions(obj, assign_vertex_groups=True)

    # Sync companion eye objects (e.g. SM_BaseBody_Eye_L / R) so eyeballs never lag behind deformed sockets
    eye_scale_val = 1.0 + validated.get("eye_size", 0.0) * 0.35 + validated.get("head_scale", 0.0) * 0.45
    eye_sep_dx = validated.get("eye_separation", 0.0) * 0.015
    eye_pivot = Vector((0.0, head_c.y, neck_z))
    hs_val = validated.get("head_scale", 0.0) * 0.45
    dz_head = validated.get("neck_length", 0.0) * 0.045 + validated.get("torso_length", 0.0) * 0.09 - (min_z_after if abs(min_z_after) > 1e-4 else 0.0)
    for side_tag, side_sign in (("L", 1.0), ("R", -1.0)):
        eye_obj = bpy.data.objects.get(f"{obj.name}_Eye_{side_tag}")
        if eye_obj and eye_obj.type == "MESH" and eye_obj.data and len(eye_obj.data.vertices) > 0:
            pts = [v.co for v in eye_obj.data.vertices]
            ec_orig = sum(pts, Vector((0.0, 0.0, 0.0))) / len(pts)
            ec_new = ec_orig + (ec_orig - eye_pivot) * hs_val + Vector((side_sign * eye_sep_dx, 0.0, dz_head))
            for v in eye_obj.data.vertices:
                v.co = ec_new + (v.co - ec_orig) * eye_scale_val
            eye_obj.data.update()

    return {
        "object_name": obj.name,
        "shape_key_name": sk.name,
        "validated_controls": validated,
        "vertices_modified": moved,
        "before_height_m": info["landmarks"]["height_m"],
        "after_height_m": after_info["landmarks"]["height_m"],
        "landmarks_after": after_info["landmarks"],
    }


def extract_garment_from_body(
    body_obj: Any,
    name: str,
    *,
    z_min: float,
    z_max: float,
    x_abs_max: float | None = None,
    y_min: float | None = None,
    y_max: float | None = None,
    surface_offset_m: float = 0.008,
    thickness_m: float = 0.004,
    bottom_flare: float = 0.0,
    top_collar_flare: float = 0.0,
    add_shrinkwrap: bool = False,
    subsurf: int = 1,
    collection: Any | None = None,
) -> Any:
    """Create body-conforming garment geometry by duplicating body faces + offset/flare + Solidify/Subsurf."""
    _require_blender()
    bt.remove_object_if_exists(name)
    col = collection or (body_obj.users_collection[0] if body_obj.users_collection else None)

    verts_w, polys = _eval_mesh_world_verts(body_obj)
    selected_poly_indices: set[int] = set()
    used_vert_indices: set[int] = set()
    for pidx, p in enumerate(polys):
        c = p["center"]
        if not (z_min <= c.z <= z_max):
            continue
        if x_abs_max is not None and abs(c.x) > x_abs_max:
            continue
        if y_min is not None and c.y < y_min:
            continue
        if y_max is not None and c.y > y_max:
            continue
        selected_poly_indices.add(pidx)
        for vi in p["verts"]:
            used_vert_indices.add(vi)

    if not selected_poly_indices:
        raise ValueError(
            f"No body faces matched garment bounds z=[{z_min}, {z_max}], x_abs_max={x_abs_max} on {body_obj.name}."
        )

    # Estimate smooth vertex normals from selected neighborhood
    vert_normals: dict[int, Vector] = {vi: Vector((0.0, 0.0, 0.0)) for vi in used_vert_indices}
    for pidx in selected_poly_indices:
        n = polys[pidx]["normal"]
        for vi in polys[pidx]["verts"]:
            vert_normals[vi] += n
    for vi, n in vert_normals.items():
        if n.length > 1e-6:
            n.normalize()
        else:
            vert_normals[vi] = Vector((0.0, -1.0, 0.0))

    bm = bmesh.new()
    vmap: dict[int, Any] = {}
    z_span = max(z_max - z_min, 1e-4)
    for vi in sorted(used_vert_indices):
        wp = verts_w[vi].copy()
        wn = vert_normals[vi]
        t_z = max(0.0, min(1.0, (wp.z - z_min) / z_span))
        rad_dir = Vector((wp.x, wp.y, 0.0))
        if rad_dir.length > 1e-5:
            rad_dir.normalize()
        flare = ((1.0 - t_z) ** 1.4) * bottom_flare + (t_z ** 2.2) * top_collar_flare
        new_co = wp + wn * surface_offset_m + rad_dir * flare
        vmap[vi] = bm.verts.new(new_co)

    bm.verts.ensure_lookup_table()
    for pidx in sorted(selected_poly_indices):
        try:
            bm.faces.new([vmap[vi] for vi in polys[pidx]["verts"]])
        except ValueError:
            pass

    # Clean loose verts if any failed face creation
    for v in list(bm.verts):
        if not v.link_faces:
            bm.verts.remove(v)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)

    obj = bt._finalize_bmesh_object(name, bm, collection=col, smooth=True, subsurf=0)
    if add_shrinkwrap:
        sw = obj.modifiers.new("Shrinkwrap", "SHRINKWRAP")
        sw.target = body_obj
        sw.wrap_method = "NEAREST_SURFACEPOINT"
        sw.wrap_mode = "OUTSIDE_SURFACE"
        sw.offset = max(0.002, float(surface_offset_m) * 0.5)
    bt.add_modifiers(obj, solidify=thickness_m, solidify_offset=1.0, subsurf=subsurf)
    bt.smart_uv_unwrap(obj)
    return obj


def _ensure_hair_bevel_profile(name: str = "CURVE_HairStrandProfile_v2") -> Any:
    """Create a reusable sculpted thick crescent/arch bevel profile curve (NOT a thin wire or tube!)."""
    old = bpy.data.objects.get(name)
    if old is not None:
        return old
    cdata = bpy.data.curves.new(name + "_Data", type="CURVE")
    cdata.dimensions = "2D"
    cdata.resolution_u = 8
    sp = cdata.splines.new("POLY")
    pts = [
        (0.0, 0.013),
        (0.025, 0.008),
        (0.036, -0.002),
        (0.018, -0.012),
        (0.0, -0.008),
        (-0.018, -0.012),
        (-0.036, -0.002),
        (-0.025, 0.008),
    ]
    sp.points.add(len(pts) - 1)
    for i, (px, py) in enumerate(pts):
        sp.points[i].co = (px, py, 0.0, 1.0)
    sp.use_cyclic_u = True
    pobj = bpy.data.objects.new(name, cdata)
    scol = bt.get_or_create_collection(bpy.context.scene, "COL_Studio")
    scol.objects.link(pobj)
    pobj.hide_render = True
    pobj.hide_viewport = True
    return pobj


def _clamp_hair_point(pt: Any, head_top_z: float) -> Any:
    """Prevent runaway spike hair curve coordinates above crown or below feet."""
    v = Vector(pt)
    return Vector(
        (
            max(-0.45, min(0.45, float(v.x))),
            max(-0.45, min(0.45, float(v.y))),
            max(0.12, min(float(head_top_z) + 0.14, float(v.z))),
        )
    )


def build_layered_curve_hair(
    name_prefix: str,
    body_obj: Any,
    *,
    scalp_offset_m: float = 0.011,
    scalp_thickness_m: float = 0.014,
    primary_locks: list[dict[str, Any]] | None = None,
    secondary_locks: list[dict[str, Any]] | None = None,
    braid_paths: list[dict[str, Any]] | None = None,
    collection: Any | None = None,
) -> dict[str, Any]:
    """Build scalp-conforming base volume + Bezier curve hair locks + optional braids as manifold meshes."""
    _require_blender()
    col = collection or (body_obj.users_collection[0] if body_obj.users_collection else None)
    info = inspect_anatomical_regions(body_obj, assign_vertex_groups=False)
    lm = info["landmarks"]
    head_top_z = lm["head_top_z"]
    eye_z = lm["eye_L"][2]
    neck_z = lm["neck"][2]
    head_cy = lm["head_center"][1]

    # 1. Fitted scalp-conforming primary base cap covering crown + back of skull (prevents bald scalp gaps)
    verts_w, polys = _eval_mesh_world_verts(body_obj)
    scalp_faces: set[int] = set()
    used_verts: set[int] = set()
    for pidx, p in enumerate(polys):
        c = p["center"]
        if c.z < neck_z + 0.02:
            continue
        # Exclude face front below forehead (protect eyes/nose/mouth) while covering crown & rear head
        is_back_or_crown = (c.y > head_cy - 0.015) or (c.z > eye_z + 0.032)
        is_ear_cutout = abs(c.x) > 0.075 and c.z < eye_z + 0.018
        if is_back_or_crown and not is_ear_cutout and c.z <= head_top_z + 0.01:
            scalp_faces.add(pidx)
            for vi in p["verts"]:
                used_verts.add(vi)

    created_objs: list[Any] = []
    if scalp_faces:
        vnorm: dict[int, Vector] = {vi: Vector((0.0, 0.0, 0.0)) for vi in used_verts}
        for pidx in scalp_faces:
            for vi in polys[pidx]["verts"]:
                vnorm[vi] += polys[pidx]["normal"]
        bm = bmesh.new()
        vmap = {}
        for vi in sorted(used_verts):
            n = vnorm[vi].normalized() if vnorm[vi].length > 1e-6 else Vector((0.0, 0.0, 1.0))
            vmap[vi] = bm.verts.new(verts_w[vi] + n * scalp_offset_m)
        bm.verts.ensure_lookup_table()
        for pidx in sorted(scalp_faces):
            try:
                bm.faces.new([vmap[vi] for vi in polys[pidx]["verts"]])
            except ValueError:
                pass
        for v in list(bm.verts):
            if not v.link_faces:
                bm.verts.remove(v)
        # Gently tuck boundary edges inward so the scalp cap blends naturally without a helmet brim
        head_cx_vec = Vector((float(lm["head_center"][0]), float(head_cy), float(lm["head_center"][2])))
        for v in bm.verts:
            if v.is_boundary:
                radial = (head_cx_vec - v.co)
                if radial.length > 1e-4:
                    v.co += radial.normalized() * min(scalp_offset_m * 0.55, 0.006)
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
        scalp_obj = bt._finalize_bmesh_object(f"{name_prefix}_ScalpCap", bm, collection=col, smooth=True, subsurf=0)
        bt.add_modifiers(scalp_obj, solidify=scalp_thickness_m, solidify_offset=1.0, subsurf=1)
        bt.smart_uv_unwrap(scalp_obj)
        created_objs.append(scalp_obj)

    # 2. Layered directional Bezier curve hair locks with custom sculpted bevel profile & per-point radius taper
    prof_obj = _ensure_hair_bevel_profile()
    deps = bpy.context.evaluated_depsgraph_get()
    lock_meshes: list[Any] = []
    all_locks = [("Primary", l) for l in (primary_locks or [])] + [("Secondary", l) for l in (secondary_locks or [])]
    for idx, (tier, spec) in enumerate(all_locks):
        pts = spec.get("points") or []
        if len(pts) < 2:
            continue
        radii = spec.get("radii") or [1.0 - 0.72 * (i / max(1, len(pts) - 1)) for i in range(len(pts))]
        tilts = spec.get("tilts_deg") or [0.0] * len(pts)
        bevel_scale = float(spec.get("width_scale", 1.0))

        cdata = bpy.data.curves.new(f"{name_prefix}_{tier}_{idx}_CurveData", type="CURVE")
        cdata.dimensions = "3D"
        cdata.resolution_u = 8
        cdata.bevel_mode = "OBJECT"
        cdata.bevel_object = prof_obj
        cdata.use_fill_caps = True
        sp = cdata.splines.new("BEZIER")
        sp.bezier_points.add(len(pts) - 1)
        for pi, pt in enumerate(pts):
            bp = sp.bezier_points[pi]
            bp.co = _clamp_hair_point(pt, head_top_z)
            bp.radius = max(0.08, float(radii[min(pi, len(radii) - 1)]) * bevel_scale)
            bp.tilt = math.radians(float(tilts[min(pi, len(tilts) - 1)]))
            bp.handle_left_type = "AUTO"
            bp.handle_right_type = "AUTO"

        cobj = bpy.data.objects.new(f"{name_prefix}_{tier}_{idx}_TmpCurve", cdata)
        bpy.context.scene.collection.objects.link(cobj)
        deps.update()
        eval_c = cobj.evaluated_get(deps)
        me_conv = bpy.data.meshes.new_from_object(eval_c, preserve_all_data_layers=False, depsgraph=deps)
        bpy.data.objects.remove(cobj, do_unlink=True)
        bpy.data.curves.remove(cdata)

        mobj = bpy.data.objects.new(f"{name_prefix}_{tier}_{idx}", me_conv)
        bt.link_to_collection(mobj, col)
        bt.set_smooth(mobj, True)
        bt.smart_uv_unwrap(mobj)
        lock_meshes.append(mobj)

    if lock_meshes:
        joined_locks = bt.join_objects(lock_meshes, f"{name_prefix}_LayeredLocks", collection=col)
        created_objs.append(joined_locks)

    for b_idx, bspec in enumerate(braid_paths or []):
        pts = [_clamp_hair_point(p, head_top_z) for p in (bspec.get("points") or [])]
        if len(pts) >= 2:
            bobj = bt.build_interwoven_braid(
                f"{name_prefix}_Braid_{b_idx}",
                [(float(p.x), float(p.y), float(p.z)) for p in pts],
                radius=float(bspec.get("radius", 0.015)),
                twists=float(bspec.get("twists", 7.0)),
                collection=col,
            )
            created_objs.append(bobj)

    return {
        "created_objects": [o.name for o in created_objs],
        "scalp_cap": f"{name_prefix}_ScalpCap" if scalp_faces else None,
        "lock_count": len(all_locks),
        "braid_count": len(braid_paths or []),
    }


def pose_hand_and_attach_prop(
    body_obj: Any,
    side: str = "L",
    *,
    finger_curl: float = 0.58,
    thumb_oppose: float = 0.45,
    prop_obj: Any | None = None,
) -> dict[str, Any]:
    """Pose hand fingers on the Base Mesh into a natural grip and attach a handheld prop cleanly."""
    _require_blender()
    if body_obj.type != "MESH" or body_obj.data is None:
        raise ValueError("Expected MESH body_obj")
    side_u = side.upper()
    if side_u not in ("L", "R"):
        raise ValueError("side must be 'L' or 'R'")
    side_sign = 1.0 if side_u == "L" else -1.0

    info = inspect_anatomical_regions(body_obj, assign_vertex_groups=False)
    lm = info["landmarks"]
    wrist = Vector(lm[f"wrist_{side_u}"])
    hand_c = Vector(lm[f"hand_{side_u}"])
    palm_dir = (hand_c - wrist)
    if palm_dir.length < 1e-4:
        palm_dir = Vector((side_sign, 0.0, -0.3))
    palm_dir.normalize()

    mesh = body_obj.data
    if not mesh.shape_keys:
        body_obj.shape_key_add(name="Basis", from_mix=False)
    sk = mesh.shape_keys.key_blocks.get("SK_Stylization") or body_obj.shape_key_add(name="SK_Stylization", from_mix=True)
    sk.value = 1.0

    mat = body_obj.matrix_world
    inv_mat = mat.inverted()

    # Deform outer finger vertices smoothly into palm curl toward -Y/inward pocket
    curled_count = 0
    for i, item in enumerate(sk.data):
        wp = mat @ item.co
        rel = wp - wrist
        along = rel.dot(palm_dir)
        dist_hand = (wp - hand_c).length
        if 0.03 < along < 0.19 and dist_hand < 0.13:
            # Thumb lies more forward (-Y) than the four main fingers
            is_thumb = (wp.y < hand_c.y - 0.015) and along < 0.12
            tip_t = max(0.0, min(1.0, (along - 0.04) / 0.12))
            if is_thumb:
                delta = Vector((-side_sign * 0.018, 0.016, 0.010)) * (thumb_oppose * tip_t)
            else:
                delta = Vector((-side_sign * 0.022, -0.028, 0.015)) * (finger_curl * (tip_t ** 1.3))
            sk.data[i].co = inv_mat @ (wp + delta)
            curled_count += 1
    mesh.update()

    # Compute actual grip pocket center from hand vertices
    verts_w, _ = _eval_mesh_world_verts(body_obj)
    finger_pts = [v for v in verts_w if (v - hand_c).length < 0.09]
    grip_pocket = (
        sum(finger_pts, Vector((0, 0, 0))) / len(finger_pts)
        if finger_pts
        else hand_c
    ) + Vector((-side_sign * 0.012, -0.025, 0.0))

    if prop_obj is not None:
        # Align prop center smoothly into the hand grip pocket so fingers/thumb embrace its edge
        pverts_w, _ = _eval_mesh_world_verts(prop_obj)
        if pverts_w:
            min_p = Vector((min(v.x for v in pverts_w), min(v.y for v in pverts_w), min(v.z for v in pverts_w)))
            max_p = Vector((max(v.x for v in pverts_w), max(v.y for v in pverts_w), max(v.z for v in pverts_w)))
            cur_center = 0.5 * (min_p + max_p)
            # Offset edge slightly inward so hand grips the inner corner naturally without burying it in the palm
            target_edge_offset = Vector((-side_sign * 0.035, -0.030, -0.015))
            prop_obj.location += (grip_pocket + target_edge_offset - cur_center)

    return {
        "side": side_u,
        "curled_vertices": curled_count,
        "grip_pocket_world": [round(grip_pocket.x, 4), round(grip_pocket.y, 4), round(grip_pocket.z, 4)],
        "prop_attached": prop_obj.name if prop_obj is not None else None,
    }


def pose_arm_for_prop_hold(
    body_obj: Any,
    side: str = "L",
    *,
    shoulder_pitch_deg: float = 32.0,
    shoulder_inward_deg: float = 18.0,
    elbow_bend_deg: float = 72.0,
) -> dict[str, Any]:
    """Smoothly pose the Base Mesh arm forward and inward around shoulder/elbow via SK_Stylization."""
    _require_blender()
    from mathutils import Quaternion

    if body_obj.type != "MESH" or body_obj.data is None:
        raise ValueError("Expected MESH body_obj")
    side_u = side.upper()
    if side_u not in ("L", "R"):
        raise ValueError("side must be 'L' or 'R'")
    side_sign = 1.0 if side_u == "L" else -1.0

    info = inspect_anatomical_regions(body_obj, assign_vertex_groups=False)
    lm = info["landmarks"]
    sh = Vector(lm[f"shoulder_{side_u}"])
    el = Vector(lm[f"elbow_{side_u}"])
    wr = Vector(lm[f"wrist_{side_u}"])

    arm_dir = (el - sh)
    if arm_dir.length < 1e-4:
        arm_dir = Vector((side_sign * 0.45, 0.0, -0.88))
    arm_dir.normalize()

    mesh = body_obj.data
    if not mesh.shape_keys:
        body_obj.shape_key_add(name="Basis", from_mix=False)
    sk = mesh.shape_keys.key_blocks.get("SK_Stylization") or body_obj.shape_key_add(name="SK_Stylization", from_mix=True)
    sk.value = 1.0

    mat = body_obj.matrix_world
    inv_mat = mat.inverted()

    # Stage 1: Rotate upper arm + forearm + hand forward (-Y) & slightly inward around shoulder pivot
    # Rotating (side_sign*X, 0, -Z) around (-1, 0, 0) swings downward arm forward into -Y!
    q_pitch = Quaternion(Vector((-1.0, 0.0, 0.0)), math.radians(float(shoulder_pitch_deg)))
    q_in = Quaternion(Vector((0.0, 0.0, -side_sign)), math.radians(float(shoulder_inward_deg)))
    q_shoulder = q_in @ q_pitch

    moved_sh = 0
    for i, item in enumerate(sk.data):
        wp = mat @ item.co
        if wp.x * side_sign < abs(sh.x) - 0.02:
            continue
        along = (wp - sh).dot(arm_dir)
        perp = ((wp - sh) - arm_dir * along).length
        if along > -0.04 and perp < 0.13 and wp.z <= sh.z + 0.08:
            t = max(0.0, min(1.0, (along + 0.03) / 0.08))
            w = t * t * (3.0 - 2.0 * t)
            if w > 1e-4:
                rot_wp = sh + (q_shoulder @ (wp - sh))
                sk.data[i].co = inv_mat @ wp.lerp(rot_wp, w)
                moved_sh += 1
    mesh.update()

    # Re-locate elbow & forearm axis after shoulder rotation
    el_rot = sh + (q_shoulder @ (el - sh))
    wr_rot = sh + (q_shoulder @ (wr - sh))
    fore_dir = (wr_rot - el_rot)
    if fore_dir.length < 1e-4:
        fore_dir = arm_dir
    fore_dir.normalize()

    # Bend forearm smoothly upward & forward (-Y/+Z, slight inward toward torso) around rotated elbow
    bend_axis = Vector((-1.0, 0.0, -0.25 * side_sign)).normalized()
    q_elbow = Quaternion(bend_axis, math.radians(float(elbow_bend_deg)))

    moved_el = 0
    for i, item in enumerate(sk.data):
        wp = mat @ item.co
        if wp.x * side_sign < abs(sh.x) - 0.04:
            continue
        along_f = (wp - el_rot).dot(fore_dir)
        perp_f = ((wp - el_rot) - fore_dir * along_f).length
        if along_f > -0.02 and perp_f < 0.11:
            t = max(0.0, min(1.0, (along_f + 0.02) / 0.07))
            w = t * t * (3.0 - 2.0 * t)
            if w > 1e-4:
                rot_wp = el_rot + (q_elbow @ (wp - el_rot))
                sk.data[i].co = inv_mat @ wp.lerp(rot_wp, w)
                moved_el += 1
    mesh.update()

    after_lm = inspect_anatomical_regions(body_obj, assign_vertex_groups=True)["landmarks"]
    return {
        "side": side_u,
        "shoulder_vertices_moved": moved_sh,
        "elbow_vertices_moved": moved_el,
        f"elbow_{side_u}": after_lm[f"elbow_{side_u}"],
        f"wrist_{side_u}": after_lm[f"wrist_{side_u}"],
        f"hand_{side_u}": after_lm[f"hand_{side_u}"],
    }


def audit_base_mesh_integrity(
    scene: Any,
    *,
    body_name: str = "SM_BaseBody",
    expected_height_m: float = 1.65,
    export_collection: str = "COL_Export",
) -> dict[str, Any]:
    """Verify Base Mesh scale, grounding, orbital eye Z alignment, and outlier bounds deterministically."""
    _require_blender()
    issues: list[str] = []
    col = bpy.data.collections.get(export_collection)
    body = bpy.data.objects.get(body_name)
    if body is None or body.type != "MESH":
        issues.append(f"Base mesh object {body_name!r} is missing.")
        return {"ok": False, "issues": issues}

    verts_b, _ = _eval_mesh_world_verts(body)
    if not verts_b:
        issues.append(f"Base mesh {body_name!r} has 0 vertices.")
        return {"ok": False, "issues": issues}

    bz_min = min(v.z for v in verts_b)
    bz_max = max(v.z for v in verts_b)
    bh = bz_max - bz_min
    if not (1.35 <= bh <= 1.95):
        issues.append(f"{body_name} height {bh:.3f}m out of valid range [1.35, 1.95] (never rescale SM_BaseBody!).")
    if not (-0.03 <= bz_min <= 0.05):
        issues.append(f"{body_name} min_z={bz_min:.3f}m is ungrounded (expected ~0.0).")

    # Check companion eyes stay seated in head band (never dropped to floor!)
    head_band_lo = bz_max - 0.35
    head_band_hi = bz_max + 0.05
    for ename in (f"{body_name}_Eye_L", f"{body_name}_Eye_R"):
        eobj = bpy.data.objects.get(ename)
        if eobj is not None and eobj.type == "MESH":
            everts, _ = _eval_mesh_world_verts(eobj)
            if everts:
                ez_mid = 0.5 * (min(v.z for v in everts) + max(v.z for v in everts))
                if not (head_band_lo <= ez_mid <= head_band_hi):
                    issues.append(
                        f"{ename} dropped out of head sockets (z_mid={ez_mid:.3f}m, expected [{head_band_lo:.2f}, {head_band_hi:.2f}])."
                    )

    # Check COL_Export for studio object leaks, extreme spikes, or floor debris
    objs = list(col.all_objects) if col else list(scene.objects)
    deps = bpy.context.evaluated_depsgraph_get()
    for o in objs:
        if o.type != "MESH" or o.hide_render:
            continue
        if o.name.startswith("SM_Studio_") or getattr(o, "is_shadow_catcher", False):
            issues.append(f"Studio object {o.name!r} must NOT be in {export_collection}.")
            continue
        eo = o.evaluated_get(deps)
        corners = [eo.matrix_world @ Vector(c) for c in eo.bound_box]
        zs = [c.z for c in corners]
        xs = [c.x for c in corners]
        ys = [c.y for c in corners]
        oz_min, oz_max = min(zs), max(zs)
        if oz_max - oz_min > 2.30 or (max(xs) - min(xs)) > 2.00 or (max(ys) - min(ys)) > 2.00:
            issues.append(f"Mesh {o.name!r} has extreme spike/extent bounds z=[{oz_min:.2f}..{oz_max:.2f}].")
        if oz_min < -0.08:
            issues.append(f"Mesh {o.name!r} extends below ground plane (min_z={oz_min:.2f}m).")

    return {"ok": len(issues) == 0, "issues": issues, "body_height_m": round(bh, 4), "body_min_z_m": round(bz_min, 4)}
