"""Asset-agnostic reusable Blender 5.3 modeling, texturing, lookdev, rigging, and export toolkit.

Imported by specialist agent scripts running inside Blender via MCP (`execute_blender_code`).
Contains strictly general-purpose 3D CAD/organic/sculpting/shading/rendering/rigging utilities
with zero asset-specific hardcoding so any character, creature, prop, or environment can use it.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Callable

try:
    import bpy  # type: ignore
    import bmesh  # type: ignore
    from mathutils import Euler, Matrix, Vector  # type: ignore
except ImportError:  # Allow importing module outside Blender for inspection/tests
    bpy = None
    bmesh = None
    Euler = Matrix = Vector = None

try:
    import numpy as np  # type: ignore
except ImportError:
    np = None


def _require_blender() -> None:
    if bpy is None or bmesh is None:
        raise RuntimeError("blender_toolkit functions must execute inside Blender (bpy/bmesh required).")


# ---------------------------------------------------------------------------
# 1. Scene, Collection, and Object Hygiene
# ---------------------------------------------------------------------------


def get_or_create_collection(scene: Any, name: str) -> Any:
    _require_blender()
    col = bpy.data.collections.get(name)
    if col is None:
        col = bpy.data.collections.new(name)
    if col.name not in scene.collection.children:
        scene.collection.children.link(col)
    return col


def ensure_clean_scene(
    scene_name: str,
    *,
    export_collection: str = "COL_Export",
    rig_collection: str = "COL_Rig",
    studio_collection: str = "COL_Studio",
) -> Any:
    """Create or reset a dedicated named scene with standard collections."""
    _require_blender()
    if scene_name in bpy.data.scenes:
        scene = bpy.data.scenes[scene_name]
        for obj in list(scene.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
    else:
        scene = bpy.data.scenes.new(scene_name)

    if bpy.context.window:
        bpy.context.window.scene = scene

    scene.unit_settings.system = "METRIC"
    scene.unit_settings.scale_length = 1.0
    get_or_create_collection(scene, export_collection)
    get_or_create_collection(scene, rig_collection)
    get_or_create_collection(scene, studio_collection)
    return scene


def link_to_collection(obj: Any, col: Any) -> Any:
    _require_blender()
    for c in list(obj.users_collection):
        c.objects.unlink(obj)
    col.objects.link(obj)
    return obj


def remove_object_if_exists(name: str) -> None:
    _require_blender()
    old = bpy.data.objects.get(name)
    if old is not None:
        data = old.data
        bpy.data.objects.remove(old, do_unlink=True)
        if data is not None and getattr(data, "users", 1) == 0:
            if isinstance(data, bpy.types.Mesh):
                bpy.data.meshes.remove(data)
            elif isinstance(data, bpy.types.Armature):
                bpy.data.armatures.remove(data)


def set_smooth(obj: Any, smooth: bool = True) -> None:
    """Blender 5.x-safe smooth/flat polygon shading."""
    _require_blender()
    if obj.type != "MESH" or obj.data is None:
        return
    mesh = obj.data
    n = len(mesh.polygons)
    if n > 0:
        mesh.polygons.foreach_set("use_smooth", [bool(smooth)] * n)
    mesh.update()


def add_modifiers(
    obj: Any,
    *,
    solidify: float | None = None,
    solidify_offset: float = 1.0,
    solidify_even_offset: bool = False,
    bevel: float | None = None,
    bevel_segments: int = 2,
    subsurf: int | None = None,
    mirror_x: bool = False,
) -> Any:
    _require_blender()
    if mirror_x:
        m = obj.modifiers.new("MirrorX", "MIRROR")
        m.use_axis = (True, False, False)
        m.use_clip = True
        m.merge_threshold = 0.001
    if solidify is not None and solidify > 0.0:
        m = obj.modifiers.new("Solidify", "SOLIDIFY")
        m.thickness = float(solidify)
        m.offset = float(solidify_offset)
        m.use_even_offset = bool(solidify_even_offset)
        if hasattr(m, "thickness_clamp"):
            m.thickness_clamp = 0.4
    if bevel is not None and bevel > 0.0:
        m = obj.modifiers.new("Bevel", "BEVEL")
        m.width = float(bevel)
        m.segments = int(bevel_segments)
        m.limit_method = "ANGLE"
        m.angle_limit = math.radians(32.0)
    if subsurf is not None and subsurf > 0:
        m = obj.modifiers.new("Subsurf", "SUBSURF")
        m.levels = int(subsurf)
        m.render_levels = int(subsurf)
        m.subdivision_type = "CATMULL_CLARK"
    return obj


def _finalize_bmesh_object(
    name: str,
    bm: Any,
    *,
    collection: Any | None = None,
    smooth: bool = True,
    subsurf: int = 0,
) -> Any:
    _require_blender()
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    remove_object_if_exists(name)
    mesh = bpy.data.meshes.new(name + "_Mesh")
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    if collection is not None:
        collection.objects.link(obj)
    else:
        bpy.context.scene.collection.objects.link(obj)
    set_smooth(obj, smooth=smooth)
    if subsurf > 0:
        add_modifiers(obj, subsurf=subsurf)
    return obj


def join_objects(
    objects: list[Any],
    name: str,
    *,
    collection: Any | None = None,
    remove_sources: bool = True,
    smooth: bool = True,
) -> Any:
    """Headless-safe BMesh join of multiple mesh objects into a single clean world-space mesh."""
    _require_blender()
    valid = [o for o in objects if o is not None and o.type == "MESH" and o.data is not None and len(o.data.polygons) > 0]
    if not valid:
        raise ValueError("join_objects requires at least one non-empty mesh object.")
    deps = bpy.context.evaluated_depsgraph_get()
    bm = bmesh.new()
    first_mat = None
    for o in valid:
        if first_mat is None and len(o.data.materials) > 0 and o.data.materials[0] is not None:
            first_mat = o.data.materials[0]
        eo = o.evaluated_get(deps)
        em = eo.to_mesh()
        em.transform(eo.matrix_world)
        bm.from_mesh(em)
        eo.to_mesh_clear()
    if remove_sources:
        for o in valid:
            if o.name != name:
                remove_object_if_exists(o.name)
    obj = _finalize_bmesh_object(name, bm, collection=collection, smooth=smooth, subsurf=0)
    if first_mat is not None:
        assign_material(obj, first_mat)
    return obj


def srgb_to_linear(rgb: tuple[float, float, float]) -> tuple[float, float, float]:
    """Convert sRGB (0..1) channel values to physically accurate linear RGB for Blender Principled BSDF."""
    out = []
    for c in rgb[:3]:
        v = max(0.0, min(1.0, float(c)))
        out.append(v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4)
    return (out[0], out[1], out[2])


def hex_to_srgb(hex_color: str, alpha: float = 1.0) -> tuple[float, float, float, float]:
    """Convert a '#RRGGBB' hex swatch to normalized sRGB RGBA (ideal for 2D painted image textures)."""
    h = str(hex_color).strip().lstrip("#")
    if len(h) != 6:
        raise ValueError(f"Expected 6-digit hex color, got: {hex_color!r}")
    return (int(h[0:2], 16) / 255.0, int(h[2:4], 16) / 255.0, int(h[4:6], 16) / 255.0, float(alpha))


def hex_to_linear(hex_color: str) -> tuple[float, float, float]:
    """Convert a '#RRGGBB' sRGB hex swatch directly to linear RGB for Blender Principled BSDF."""
    r, g, b, _ = hex_to_srgb(hex_color)
    return srgb_to_linear((r, g, b))


# ---------------------------------------------------------------------------
# 2. General-Purpose Organic & Hard-Surface BMesh Geometry Builders
# ---------------------------------------------------------------------------


def loft_sections(
    name: str,
    sections: list[dict[str, Any] | list[tuple[float, float, float]]],
    *,
    segments: int = 24,
    closed_u: bool = True,
    cap_start: bool = True,
    cap_end: bool = True,
    smooth: bool = True,
    subsurf: int = 1,
    collection: Any | None = None,
) -> Any:
    """Loft a sequence of 3D cross-section rings into a watertight quad-dominant mesh.

    Each section may be either:
    - dict: {center:(x,y,z), rx:float, ry:float, rot:(rx,ry,rz), squash_front:1.0,
             squash_back:1.0, flat_bottom:0.0, pinch_x:0.0, offset_y_fn:callable}
    - list of explicit (x,y,z) ring vertices (all sections must share point count).
    """
    _require_blender()
    if len(sections) < 2:
        raise ValueError("loft_sections requires at least 2 sections.")

    bm = bmesh.new()
    rings: list[list[Any]] = []

    for sec in sections:
        if isinstance(sec, dict):
            cx, cy, cz = sec.get("center", (0.0, 0.0, 0.0))
            rx = float(sec.get("rx", 0.1))
            ry = float(sec.get("ry", rx))
            rot = sec.get("rot", (0.0, 0.0, 0.0))
            sf = float(sec.get("squash_front", 1.0))
            sb = float(sec.get("squash_back", 1.0))
            fb = float(sec.get("flat_bottom", 0.0))
            rot_mat = Euler((math.radians(rot[0]), math.radians(rot[1]), math.radians(rot[2])), "XYZ").to_matrix()
            ring_verts = []
            for i in range(segments):
                ang = (2.0 * math.pi * i) / segments
                lx = math.cos(ang) * rx
                ly = math.sin(ang) * ry
                # -Y is forward in Blender standard character orientation
                if ly < 0:
                    ly *= sf
                else:
                    ly *= sb
                lz = 0.0
                if fb > 0.0 and ly > 0.0:
                    ly *= (1.0 - 0.35 * fb)
                p = rot_mat @ Vector((lx, ly, lz)) + Vector((cx, cy, cz))
                ring_verts.append(bm.verts.new(p))
            rings.append(ring_verts)
        else:
            ring_verts = [bm.verts.new(Vector(pt)) for pt in sec]
            rings.append(ring_verts)

    n_seg = len(rings[0])
    for r_idx in range(len(rings) - 1):
        r0 = rings[r_idx]
        r1 = rings[r_idx + 1]
        if len(r0) != n_seg or len(r1) != n_seg:
            bm.free()
            raise ValueError("All loft sections must have the same vertex count.")
        upper = n_seg if closed_u else n_seg - 1
        for i in range(upper):
            j = (i + 1) % n_seg
            try:
                bm.faces.new((r0[i], r0[j], r1[j], r1[i]))
            except ValueError:
                pass

    def _cap_ring(ring: list[Any], reverse: bool) -> None:
        if not closed_u or len(ring) < 3:
            return
        center_pos = sum((v.co for v in ring), Vector((0.0, 0.0, 0.0))) / len(ring)
        cv = bm.verts.new(center_pos)
        for i in range(len(ring)):
            j = (i + 1) % len(ring)
            verts = (ring[j], ring[i], cv) if reverse else (ring[i], ring[j], cv)
            try:
                bm.faces.new(verts)
            except ValueError:
                pass

    if cap_start:
        _cap_ring(rings[0], reverse=True)
    if cap_end:
        _cap_ring(rings[-1], reverse=False)

    return _finalize_bmesh_object(name, bm, collection=collection, smooth=smooth, subsurf=subsurf)


def sculpt_tapered_ribbon(
    name: str,
    spine_pts: list[tuple[float, float, float]],
    widths: list[float],
    thicknesses: list[float],
    *,
    arch: float = 0.35,
    twist_deg: list[float] | None = None,
    up_hint: tuple[float, float, float] = (0.0, -1.0, 0.0),
    smooth: bool = True,
    subsurf: int = 1,
    collection: Any | None = None,
) -> Any:
    """Create a sculpted volumetric ribbon/clump/strand with crescent cross-section and tapered tips."""
    _require_blender()
    n_pts = len(spine_pts)
    if n_pts < 2 or len(widths) != n_pts or len(thicknesses) != n_pts:
        raise ValueError("spine_pts, widths, and thicknesses must share length >= 2.")

    pts = [Vector(p) for p in spine_pts]
    hint = Vector(up_hint).normalized()
    bm = bmesh.new()
    rings: list[list[Any]] = []

    # 6-vertex diamond/crescent cross-section (not a circular tube)
    profile_uv = [
        (-1.0, 0.0),
        (-0.45, 0.85 + arch * 0.45),
        (0.45, 0.85 + arch * 0.45),
        (1.0, 0.0),
        (0.45, -0.55 + arch * 0.25),
        (-0.45, -0.55 + arch * 0.25),
    ]

    prev_norm = None
    for idx in range(n_pts):
        if idx == 0:
            tan = (pts[1] - pts[0]).normalized()
        elif idx == n_pts - 1:
            tan = (pts[-1] - pts[-2]).normalized()
        else:
            tan = (pts[idx + 1] - pts[idx - 1]).normalized()
        if tan.length < 1e-6:
            tan = Vector((0.0, 0.0, -1.0))

        if prev_norm is None:
            ref = hint if abs(tan.dot(hint)) < 0.92 else Vector((1.0, 0.0, 0.0))
            side = tan.cross(ref).normalized()
            norm = side.cross(tan).normalized()
        else:
            norm = (prev_norm - tan * prev_norm.dot(tan))
            if norm.length < 1e-6:
                norm = tan.orthogonal()
            norm.normalize()
            side = tan.cross(norm).normalized()
        prev_norm = norm

        if twist_deg is not None and idx < len(twist_deg):
            ang = math.radians(float(twist_deg[idx]))
            ca, sa = math.cos(ang), math.sin(ang)
            side, norm = side * ca + norm * sa, -side * sa + norm * ca

        w = max(0.0008, float(widths[idx]) * 0.5)
        th = max(0.0005, float(thicknesses[idx]) * 0.5)
        ring = [bm.verts.new(pts[idx] + side * (u * w) + norm * (v * th)) for (u, v) in profile_uv]
        rings.append(ring)

    n_seg = len(profile_uv)
    for r_idx in range(n_pts - 1):
        r0, r1 = rings[r_idx], rings[r_idx + 1]
        for i in range(n_seg):
            j = (i + 1) % n_seg
            try:
                bm.faces.new((r0[i], r0[j], r1[j], r1[i]))
            except ValueError:
                pass

    # Tapered root and tip caps
    root_tip = bm.verts.new(pts[0] - (pts[1] - pts[0]).normalized() * max(0.001, thicknesses[0] * 0.35))
    end_tip = bm.verts.new(pts[-1] + (pts[-1] - pts[-2]).normalized() * max(0.002, widths[-1] * 0.6))
    for i in range(n_seg):
        j = (i + 1) % n_seg
        try:
            bm.faces.new((rings[0][j], rings[0][i], root_tip))
        except ValueError:
            pass
        try:
            bm.faces.new((rings[-1][i], rings[-1][j], end_tip))
        except ValueError:
            pass

    return _finalize_bmesh_object(name, bm, collection=collection, smooth=smooth, subsurf=subsurf)


def create_tube_along_curve(
    name: str,
    points: list[tuple[float, float, float]],
    radii: list[float] | list[tuple[float, float]],
    *,
    segments: int = 14,
    cap_ends: bool = True,
    smooth: bool = True,
    subsurf: int = 1,
    collection: Any | None = None,
) -> Any:
    """Sweep elliptical/circular cross-sections along a 3D polyline using parallel-transport frames."""
    _require_blender()
    n_pts = len(points)
    if n_pts < 2 or len(radii) != n_pts:
        raise ValueError("points and radii must share length >= 2.")

    pts = [Vector(p) for p in points]
    rings_xyz: list[list[tuple[float, float, float]]] = []
    prev_n = None
    for idx in range(n_pts):
        if idx == 0:
            t = (pts[1] - pts[0]).normalized()
        elif idx == n_pts - 1:
            t = (pts[-1] - pts[-2]).normalized()
        else:
            t = (pts[idx + 1] - pts[idx - 1]).normalized()
        if t.length < 1e-6:
            t = Vector((0.0, 0.0, 1.0))

        if prev_n is None:
            ref = Vector((0.0, -1.0, 0.0)) if abs(t.y) < 0.9 else Vector((1.0, 0.0, 0.0))
            b = t.cross(ref).normalized()
            n = b.cross(t).normalized()
        else:
            n = prev_n - t * prev_n.dot(t)
            if n.length < 1e-6:
                n = t.orthogonal()
            n.normalize()
            b = t.cross(n).normalized()
        prev_n = n

        rad = radii[idx]
        if isinstance(rad, (tuple, list)):
            rx, ry = float(rad[0]), float(rad[1])
        else:
            rx = ry = float(rad)
        ring = []
        for s in range(segments):
            ang = 2.0 * math.pi * s / segments
            p = pts[idx] + b * (math.cos(ang) * rx) + n * (math.sin(ang) * ry)
            ring.append((p.x, p.y, p.z))
        rings_xyz.append(ring)

    return loft_sections(
        name,
        rings_xyz,
        segments=segments,
        closed_u=True,
        cap_start=cap_ends,
        cap_end=cap_ends,
        smooth=smooth,
        subsurf=subsurf,
        collection=collection,
    )


def create_lathe_mesh(
    name: str,
    profile_rz: list[tuple[float, float]],
    *,
    center: tuple[float, float, float] = (0.0, 0.0, 0.0),
    rot: tuple[float, float, float] = (0.0, 0.0, 0.0),
    scale_xy: tuple[float, float] = (1.0, 1.0),
    segments: int = 28,
    cap_start: bool = True,
    cap_end: bool = True,
    smooth: bool = True,
    subsurf: int = 1,
    collection: Any | None = None,
) -> Any:
    """Revolve a (radius, z) profile around local Z with optional elliptical XY scaling."""
    _require_blender()
    cx, cy, cz = center
    sx, sy = scale_xy
    rot_mat = Euler((math.radians(rot[0]), math.radians(rot[1]), math.radians(rot[2])), "XYZ").to_matrix()
    sections = []
    for r, z in profile_rz:
        ring = []
        for i in range(segments):
            ang = 2.0 * math.pi * i / segments
            p = rot_mat @ Vector((math.cos(ang) * r * sx, math.sin(ang) * r * sy, z)) + Vector((cx, cy, cz))
            ring.append((p.x, p.y, p.z))
        sections.append(ring)
    return loft_sections(
        name,
        sections,
        segments=segments,
        closed_u=True,
        cap_start=cap_start,
        cap_end=cap_end,
        smooth=smooth,
        subsurf=subsurf,
        collection=collection,
    )


def create_beveled_panel(
    name: str,
    profile_2d: list[tuple[float, float]],
    depth: float,
    *,
    location: tuple[float, float, float] = (0.0, 0.0, 0.0),
    rotation: tuple[float, float, float] = (0.0, 0.0, 0.0),
    bevel_width: float = 0.003,
    bevel_segments: int = 2,
    smooth: bool = True,
    collection: Any | None = None,
) -> Any:
    """Extrude a 2D polygon in XZ (depth along Y) into a crisp beveled 3D panel/plate/prop."""
    _require_blender()
    if len(profile_2d) < 3:
        raise ValueError("profile_2d requires at least 3 vertices.")
    bm = bmesh.new()
    half = depth * 0.5
    front = [bm.verts.new(Vector((x, -half, z))) for (x, z) in profile_2d]
    back = [bm.verts.new(Vector((x, half, z))) for (x, z) in profile_2d]
    try:
        bm.faces.new(front)
        bm.faces.new(list(reversed(back)))
    except ValueError:
        pass
    n = len(profile_2d)
    for i in range(n):
        j = (i + 1) % n
        try:
            bm.faces.new((front[i], back[i], back[j], front[j]))
        except ValueError:
            pass

    obj = _finalize_bmesh_object(name, bm, collection=collection, smooth=smooth, subsurf=0)
    obj.location = Vector(location)
    obj.rotation_euler = Euler(tuple(math.radians(a) for a in rotation), "XYZ")
    if bevel_width > 0.0:
        add_modifiers(obj, bevel=bevel_width, bevel_segments=bevel_segments)
    return obj


def create_quad_sphere(
    name: str,
    center: tuple[float, float, float],
    radii: tuple[float, float, float],
    *,
    rotation: tuple[float, float, float] = (0.0, 0.0, 0.0),
    subdivisions: int = 3,
    smooth: bool = True,
    collection: Any | None = None,
) -> Any:
    """Create a 100%-quad ellipsoid by subdividing a cube and normalizing onto ellipsoid radii."""
    _require_blender()
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=2.0)
    if subdivisions > 0:
        bmesh.ops.subdivide_edges(bm, edges=list(bm.edges), cuts=max(1, 2 ** subdivisions - 1), use_grid_fill=True)
    rx, ry, rz = radii
    rot_mat = Euler(tuple(math.radians(a) for a in rotation), "XYZ").to_matrix()
    c_vec = Vector(center)
    for v in bm.verts:
        u = v.co.normalized()
        p = Vector((u.x * rx, u.y * ry, u.z * rz))
        v.co = (rot_mat @ p) + c_vec
    return _finalize_bmesh_object(name, bm, collection=collection, smooth=smooth, subsurf=0)


def deform_mesh_sculpt(obj: Any, control_points: list[dict[str, Any]]) -> dict[str, Any]:
    """Apply smooth Gaussian RBF sculpting displacements to an existing mesh in place."""
    _require_blender()
    if obj.type != "MESH" or obj.data is None:
        raise ValueError(f"Object {obj.name} is not a mesh.")
    mesh = obj.data
    inv_mat = obj.matrix_world.inverted()
    mat = obj.matrix_world

    displacements: list[float] = []
    for v in mesh.vertices:
        wpos = mat @ v.co
        total_delta = Vector((0.0, 0.0, 0.0))
        for cp in control_points:
            c = Vector(cp["center"])
            rad = max(1e-4, float(cp.get("radius", 0.05)))
            d = Vector(cp["delta"])
            dist = (wpos - c).length
            if dist < rad * 2.5:
                w = math.exp(-((dist / rad) ** 2))
                total_delta += d * w
            if cp.get("mirror_x", False) and abs(c.x) > 1e-5:
                cm = Vector((-c.x, c.y, c.z))
                dm = Vector((-d.x, d.y, d.z))
                dist_m = (wpos - cm).length
                if dist_m < rad * 2.5:
                    wm = math.exp(-((dist_m / rad) ** 2))
                    total_delta += dm * wm
        if total_delta.length > 1e-7:
            v.co = inv_mat @ (wpos + total_delta)
            displacements.append(total_delta.length)

    mesh.update()
    set_smooth(obj, smooth=True)
    mean_d = sum(displacements) / len(displacements) if displacements else 0.0
    max_d = max(displacements) if displacements else 0.0
    return {
        "object": obj.name,
        "vertices_moved": len(displacements),
        "mean_displacement_m": round(mean_d, 6),
        "max_displacement_m": round(max_d, 6),
    }


def _catmull_rom_sample(pts: list[Vector], radii: list[tuple[float, float]], subdiv_per_seg: int = 4) -> tuple[list[Vector], list[tuple[float, float]]]:
    """Smoothly interpolate 3D waypoints and elliptical (rx, ry) radii via Catmull-Rom spline."""
    if len(pts) < 2:
        return pts, radii
    out_p: list[Vector] = []
    out_r: list[tuple[float, float]] = []
    n = len(pts)
    for i in range(n - 1):
        p0 = pts[max(0, i - 1)]
        p1 = pts[i]
        p2 = pts[i + 1]
        p3 = pts[min(n - 1, i + 2)]
        r1 = radii[i]
        r2 = radii[i + 1]
        steps = subdiv_per_seg if i < n - 2 else subdiv_per_seg + 1
        for s in range(steps):
            t = s / float(subdiv_per_seg)
            t2, t3 = t * t, t * t * t
            pos = 0.5 * (
                (2.0 * p1)
                + (-p0 + p2) * t
                + (2.0 * p0 - 5.0 * p1 + 4.0 * p2 - p3) * t2
                + (-p0 + 3.0 * p1 - 3.0 * p2 + p3) * t3
            )
            rx = r1[0] * (1.0 - t) + r2[0] * t
            ry = r1[1] * (1.0 - t) + r2[1] * t
            out_p.append(pos)
            out_r.append((rx, ry))
    return out_p, out_r


def build_continuous_limb(
    name: str,
    waypoints: list[tuple[float, float, float]],
    radii_xy: list[tuple[float, float] | float],
    *,
    segments: int = 20,
    subdiv_per_seg: int = 4,
    subsurf: int = 1,
    collection: Any | None = None,
) -> Any:
    """Build a smooth, organically tapered continuous limb/torso/tail via Catmull-Rom loft (zero stacked balls/kinks)."""
    _require_blender()
    pts = [Vector(p) for p in waypoints]
    r_pairs = [(float(r[0]), float(r[1])) if isinstance(r, (tuple, list)) else (float(r), float(r)) for r in radii_xy]
    sp, sr = _catmull_rom_sample(pts, r_pairs, subdiv_per_seg=subdiv_per_seg)
    return create_tube_along_curve(
        name,
        [(p.x, p.y, p.z) for p in sp],
        sr,
        segments=segments,
        cap_ends=True,
        smooth=True,
        subsurf=subsurf,
        collection=collection,
    )


def build_articulated_hand(
    name_prefix: str,
    wrist_pos: tuple[float, float, float],
    forward_dir: tuple[float, float, float],
    up_dir: tuple[float, float, float] = (0.0, 0.0, 1.0),
    *,
    side: str = "R",
    scale: float = 1.0,
    finger_curls: tuple[float, float, float, float, float] = (0.18, 0.14, 0.18, 0.22, 0.26),
    finger_spread: float = 0.14,
    glove_coverage: float = 0.0,
    collection: Any | None = None,
) -> dict[str, Any]:
    """Build a guaranteed-connected sculpted wrist + palm + 5 multi-segment articulated fingers (+ optional fingerless glove).

    Eliminates detached fingertips completely for any humanoid or stylized creature.
    """
    _require_blender()
    wpos = Vector(wrist_pos)
    fwd = Vector(forward_dir).normalized()
    up_hint = Vector(up_dir).normalized()
    if abs(fwd.dot(up_hint)) > 0.95:
        up_hint = Vector((0.0, -1.0, 0.0)) if abs(fwd.y) < 0.9 else Vector((1.0, 0.0, 0.0))
    sign = 1.0 if side.upper() == "L" else -1.0
    lat = fwd.cross(up_hint).normalized() * sign  # points toward thumb side
    up = lat.cross(fwd).normalized() * sign  # points toward back of hand

    sc = float(scale)
    palm_len = 0.072 * sc
    palm_w = 0.068 * sc
    palm_th = 0.022 * sc

    # Sculpted palm loft from wrist to knuckles
    palm_pts = [
        wpos - fwd * (0.015 * sc),
        wpos + fwd * (0.015 * sc),
        wpos + fwd * (0.048 * sc),
        wpos + fwd * (palm_len),
    ]
    palm_radii = [
        (0.024 * sc, 0.018 * sc),
        (palm_w * 0.44, palm_th * 0.52),
        (palm_w * 0.52, palm_th * 0.50),
        (palm_w * 0.48, palm_th * 0.40),
    ]
    palm_obj = create_tube_along_curve(
        f"{name_prefix}_Palm",
        [(p.x, p.y, p.z) for p in palm_pts],
        palm_radii,
        segments=14,
        cap_ends=True,
        smooth=True,
        subsurf=1,
        collection=collection,
    )
    created = [palm_obj.name]

    # 4 fingers (index, middle, ring, pinky) rooted INSIDE knuckle line so ZERO gap can occur
    f_specs = [
        ("Index", 0.36, 0.072, 0.0082, finger_curls[1], 1.25),
        ("Middle", 0.12, 0.079, 0.0085, finger_curls[2], 0.35),
        ("Ring", -0.12, 0.073, 0.0080, finger_curls[3], -0.45),
        ("Pinky", -0.36, 0.059, 0.0069, finger_curls[4], -1.35),
    ]
    for fname, lat_frac, flen, frad, curl, spread_mul in f_specs:
        base = wpos + fwd * (palm_len * 0.88) + lat * (palm_w * 0.88 * lat_frac)
        fdir0 = (fwd + lat * (finger_spread * spread_mul)).normalized()
        pts_f = [base - fdir0 * (0.010 * sc)]
        cur_p = base
        cur_d = fdir0
        seg_lens = [flen * 0.42 * sc, flen * 0.32 * sc, flen * 0.26 * sc]
        for seg_i, sl in enumerate(seg_lens):
            ang = float(curl) * (0.55 + 0.45 * seg_i)
            cur_d = (cur_d * math.cos(ang) - up * math.sin(ang)).normalized()
            cur_p = cur_p + cur_d * sl
            pts_f.append(cur_p)
        r_f = [frad * sc * 1.05, frad * sc * 0.94, frad * sc * 0.80, frad * sc * 0.56]
        f_obj = create_tube_along_curve(
            f"{name_prefix}_{fname}",
            [(p.x, p.y, p.z) for p in pts_f],
            r_f,
            segments=10,
            cap_ends=True,
            smooth=True,
            subsurf=1,
            collection=collection,
        )
        created.append(f_obj.name)

    # Articulated thumb rooted inside radial palm base
    t_base = wpos + fwd * (palm_len * 0.36) + lat * (palm_w * 0.44) - up * (0.005 * sc)
    t_dir0 = (fwd * 0.62 + lat * 0.74 - up * 0.22).normalized()
    t_curl = float(finger_curls[0])
    t_p1 = t_base + t_dir0 * (0.032 * sc)
    t_dir1 = (t_dir0 * math.cos(t_curl) - up * math.sin(t_curl) - lat * 0.22 * t_curl).normalized()
    t_p2 = t_p1 + t_dir1 * (0.027 * sc)
    t_obj = create_tube_along_curve(
        f"{name_prefix}_Thumb",
        [(p.x, p.y, p.z) for p in (t_base - t_dir0 * (0.010 * sc), t_base, t_p1, t_p2)],
        [0.0108 * sc, 0.0098 * sc, 0.0086 * sc, 0.0064 * sc],
        segments=10,
        cap_ends=True,
        smooth=True,
        subsurf=1,
        collection=collection,
    )
    created.append(t_obj.name)

    glove_name = None
    if glove_coverage > 0.05:
        # Cap fingerless glove cleanly at knuckle line so all 5 articulated fingers extend cleanly with zero skin poke-through
        cov = max(0.35, min(0.86, float(glove_coverage)))
        clr = 0.0045 * sc
        g_pts = [
            wpos - fwd * (0.024 * sc),
            wpos + fwd * (0.012 * sc),
            wpos + fwd * (palm_len * (cov * 0.65)),
            wpos + fwd * (palm_len * cov),
        ]
        g_radii = [
            (0.026 * sc + clr, 0.020 * sc + clr),
            (palm_w * 0.46 + clr, palm_th * 0.56 + clr),
            (palm_w * 0.54 + clr, palm_th * 0.56 + clr),
            (palm_w * 0.50 + clr * 0.8, palm_th * 0.48 + clr * 0.8),
        ]
        g_obj = create_tube_along_curve(
            f"{name_prefix}_Glove",
            [(p.x, p.y, p.z) for p in g_pts],
            g_radii,
            segments=16,
            cap_ends=False,
            smooth=True,
            subsurf=1,
            collection=collection,
        )
        add_modifiers(g_obj, solidify=0.0032 * sc, solidify_offset=1.0)
        glove_name = g_obj.name
        created.append(glove_name)

    return {"palm": palm_obj.name, "glove": glove_name, "objects": created}


def build_stylized_head_base(
    name_prefix: str,
    center: tuple[float, float, float],
    *,
    height: float = 0.22,
    jaw_taper: float = 0.72,
    eye_scale: float = 1.0,
    ear_style: str = "pointed",
    ear_length: float = 0.055,
    head_tilt_deg: tuple[float, float, float] = (0.0, 0.0, 0.0),
    collection: Any | None = None,
) -> dict[str, Any]:
    """Build a sculpted quad-dominant stylized head in exact symmetric local space, then transform cleanly.

    Eliminates mirror_x asymmetry and floating eyelids by sculpting recessed sockets, nose, lips, chin,
    eyeball-hugging almond upper/lower eyelid rims, brows, and cupped ears in local space before applying
    `head_tilt_deg` + `center` rigidly to the entire assembly. Also returns normalized front-face `uv_landmarks`.
    """
    _require_blender()
    h = float(height)
    c_vec = Vector(center)
    rot_e = Euler(tuple(math.radians(a) for a in head_tilt_deg), "XYZ")
    rot_m = rot_e.to_matrix()

    def _to_world(lp: Vector) -> Vector:
        return (rot_m @ lp) + c_vec

    # 1. Sculpted head base loft in local symmetric space (cranium -> forehead -> cheekbones -> jaw -> soft chin)
    sections = []
    z_specs = [
        (-0.48, 0.11 * jaw_taper, 0.11, 0.020, 0.88),  # soft rounded chin
        (-0.40, 0.23 * jaw_taper, 0.23, 0.005, 0.92),  # lower jaw
        (-0.26, 0.34 * jaw_taper, 0.34, -0.012, 0.96), # mouth / lower cheek
        (-0.11, 0.40, 0.41, -0.015, 0.98),             # soft stylized cheekbones
        (0.04, 0.415, 0.445, -0.010, 1.00),            # eye line / temples
        (0.21, 0.415, 0.455, 0.005, 1.00),             # brow / forehead
        (0.37, 0.375, 0.415, 0.020, 1.00),             # upper cranium
        (0.49, 0.22, 0.25, 0.025, 1.00),               # crown
    ]
    for zf, rxf, ryf, y_off, sf in z_specs:
        sections.append(
            {
                "center": (0.0, y_off * h, zf * h),
                "rx": rxf * h,
                "ry": ryf * h,
                "rot": (0.0, 0.0, 0.0),
                "squash_front": sf,
            }
        )
    head_obj = loft_sections(
        f"{name_prefix}_Head",
        sections,
        segments=36,
        cap_start=True,
        cap_end=True,
        smooth=True,
        subsurf=0,
        collection=collection,
    )
    # Apply Catmull-Clark level 2 BEFORE RBF facial sculpting so dense ~4,900 base vertices capture
    # crisp nose bridge/tip, nostrils, lip cleft, chin, and orbital socket anatomy cleanly!
    bm_h = bmesh.new()
    bm_h.from_mesh(head_obj.data)
    bmesh.ops.subdivide_edges(bm_h, edges=list(bm_h.edges), cuts=1, use_grid_fill=True, smooth=1.0)
    bmesh.ops.subdivide_edges(bm_h, edges=list(bm_h.edges), cuts=1, use_grid_fill=True, smooth=1.0)
    bmesh.ops.recalc_face_normals(bm_h, faces=list(bm_h.faces))
    bm_h.to_mesh(head_obj.data)
    bm_h.free()
    head_obj.data.update()
    set_smooth(head_obj, True)

    # 2. Crisp symmetric local-space Gaussian RBF sculpting snapped onto actual head front surface!
    def _front_y_at(lx: float, lz: float, default_y: float) -> float:
        best_y = default_y
        best_d2 = 1e9
        for v in head_obj.data.vertices:
            if v.co.y < 0.0:
                d2 = (v.co.x - lx) ** 2 + (v.co.z - lz) ** 2
                if d2 < best_d2:
                    best_d2 = d2
                    best_y = v.co.y
        return best_y

    eye_x = 0.162 * h
    eye_z = 0.026 * h
    eye_y = -0.348 * h  # seated slightly deeper inside orbital socket so eyeballs never bulge!
    sculpt_cps = [
        # Deep recessed orbital sockets (exact mirror_x in local space!)
        {"center": (eye_x, _front_y_at(eye_x, eye_z, -0.40 * h), eye_z), "radius": 0.106 * h, "delta": (0.0, 0.068 * h, 0.0), "mirror_x": True},
        # Inner orbital corner recess
        {"center": (eye_x * 0.55, _front_y_at(eye_x * 0.55, eye_z + 0.01 * h, -0.41 * h), eye_z + 0.01 * h), "radius": 0.060 * h, "delta": (0.0, 0.032 * h, 0.0), "mirror_x": True},
        # Gentle brow ridge above eye
        {"center": (eye_x * 0.92, _front_y_at(eye_x * 0.92, eye_z + 0.118 * h, -0.41 * h), eye_z + 0.118 * h), "radius": 0.085 * h, "delta": (0.0, -0.020 * h, 0.005 * h), "mirror_x": True},
        # Soft stylized cheekbones below outer eye
        {"center": (0.245 * h, _front_y_at(0.245 * h, -0.095 * h, -0.33 * h), -0.095 * h), "radius": 0.110 * h, "delta": (0.014 * h, -0.020 * h, 0.004 * h), "mirror_x": True},
        # Petite anime nose bridge & delicate pointed tip snapped strictly onto front mesh surface
        {"center": (0.0, _front_y_at(0.0, -0.025 * h, -0.41 * h), -0.025 * h), "radius": 0.030 * h, "delta": (0.0, -0.026 * h, 0.002 * h)},
        {"center": (0.0, _front_y_at(0.0, -0.098 * h, -0.40 * h), -0.098 * h), "radius": 0.028 * h, "delta": (0.0, -0.048 * h, -0.002 * h)},
        # Subtle nose wings
        {"center": (0.038 * h, _front_y_at(0.038 * h, -0.108 * h, -0.39 * h), -0.108 * h), "radius": 0.024 * h, "delta": (0.008 * h, -0.014 * h, 0.0), "mirror_x": True},
        # Clean philtrum, delicate upper lip, crisp recessed mouth parting cleft, soft lower lip (all surface-snapped!)
        {"center": (0.0, _front_y_at(0.0, -0.175 * h, -0.36 * h), -0.175 * h), "radius": 0.028 * h, "delta": (0.0, 0.012 * h, 0.0)},
        {"center": (0.0, _front_y_at(0.0, -0.212 * h, -0.35 * h), -0.212 * h), "radius": 0.034 * h, "delta": (0.0, -0.020 * h, 0.003 * h)},
        {"center": (0.0, _front_y_at(0.0, -0.238 * h, -0.34 * h), -0.238 * h), "radius": 0.034 * h, "delta": (0.0, 0.026 * h, 0.0)},
        {"center": (0.040 * h, _front_y_at(0.040 * h, -0.238 * h, -0.33 * h), -0.238 * h), "radius": 0.028 * h, "delta": (0.0, 0.018 * h, 0.0), "mirror_x": True},
        {"center": (0.0, _front_y_at(0.0, -0.268 * h, -0.32 * h), -0.268 * h), "radius": 0.036 * h, "delta": (0.0, -0.024 * h, -0.002 * h)},
        # Chin groove & soft rounded anime chin
        {"center": (0.0, _front_y_at(0.0, -0.335 * h, -0.29 * h), -0.335 * h), "radius": 0.044 * h, "delta": (0.0, 0.020 * h, 0.0)},
        {"center": (0.0, _front_y_at(0.0, -0.415 * h, -0.24 * h), -0.415 * h), "radius": 0.068 * h, "delta": (0.0, -0.022 * h, -0.005 * h)},
    ]
    sculpt_stats = deform_mesh_sculpt(head_obj, sculpt_cps)

    # Project distortion-free front planar UV in local space BEFORE tilting!
    head_xs = [v.co.x for v in head_obj.data.vertices]
    head_zs = [v.co.z for v in head_obj.data.vertices]
    u0, u1 = min(head_xs), max(head_xs)
    v0, v1 = min(head_zs), max(head_zs)
    project_planar_uv(head_obj, axis="Y", bounds=(u0, u1, v0, v1))

    def _uv_pt(lx: float, lz: float) -> list[float]:
        return [round((lx - u0) / max(u1 - u0, 1e-5), 4), round((lz - v0) / max(v1 - v0, 1e-5), 4)]

    # 3. Expressive large stylized eyeballs + crisp open almond upper/lower lids + arched brows + cupped ears
    created = [head_obj.name]
    eye_r = 0.074 * h * float(eye_scale)
    eye_rx, eye_ry, eye_rz = eye_r * 1.12, eye_r * 0.84, eye_r * 0.96
    eye_names: list[str] = []
    lid_names: list[str] = []
    brow_names: list[str] = []
    ear_names: list[str] = []
    child_objs: list[Any] = []

    def _eye_shell(sx: float, theta_u: float, phi_v: float, offset_r: float = 0.0022) -> tuple[float, float, float]:
        """Exact point on the front hemisphere of eyeball ellipsoid so lids ALWAYS hug the eye with zero gap."""
        ex = sx * eye_x + sx * math.sin(theta_u) * math.cos(phi_v) * (eye_rx + offset_r)
        ey = eye_y - math.cos(theta_u) * math.cos(phi_v) * (eye_ry + offset_r)
        ez = eye_z + math.sin(phi_v) * (eye_rz + offset_r)
        return (ex, ey, ez)

    for side_lbl, sx in (("L", 1.0), ("R", -1.0)):
        # Seated eyeball recessed inside socket
        e_obj = create_quad_sphere(
            f"{name_prefix}_Eye_{side_lbl}",
            (sx * eye_x, eye_y, eye_z),
            (eye_rx, eye_ry, eye_rz),
            subdivisions=3,
            smooth=True,
            collection=collection,
        )
        project_planar_uv(e_obj, axis="Y")
        eye_names.append(e_obj.name)
        child_objs.append(e_obj)
        created.append(e_obj.name)

        # Crisp, high-arched upper lash/eyelid rim (leaves ~85% of iris open and alert — NEVER sleepy!)
        ul_pts = [
            _eye_shell(sx, -1.04, 0.02, 0.0014 * h / 0.22),
            _eye_shell(sx, -0.52, 0.56, 0.0026 * h / 0.22),
            _eye_shell(sx, 0.04, 0.72, 0.0030 * h / 0.22),
            _eye_shell(sx, 0.58, 0.62, 0.0028 * h / 0.22),
            _eye_shell(sx, 1.08, 0.16, 0.0018 * h / 0.22),
        ]
        ul_radii = [
            (0.0055 * h, 0.0045 * h),
            (0.0105 * h, 0.0075 * h),
            (0.0115 * h, 0.0080 * h),
            (0.0110 * h, 0.0075 * h),
            (0.0065 * h, 0.0050 * h),
        ]
        ul_obj = create_tube_along_curve(
            f"{name_prefix}_UpperLid_{side_lbl}",
            ul_pts,
            ul_radii,
            segments=12,
            cap_ends=True,
            smooth=True,
            subsurf=1,
            collection=collection,
        )
        smart_uv_unwrap(ul_obj)
        lid_names.append(ul_obj.name)
        child_objs.append(ul_obj)
        created.append(ul_obj.name)

        # Delicate lower eyelid rim hugging lower eyeball arc
        ll_pts = [
            _eye_shell(sx, -0.94, -0.10, 0.0012 * h / 0.22),
            _eye_shell(sx, -0.35, -0.58, 0.0018 * h / 0.22),
            _eye_shell(sx, 0.30, -0.56, 0.0018 * h / 0.22),
            _eye_shell(sx, 0.96, -0.04, 0.0012 * h / 0.22),
        ]
        ll_radii = [
            (0.0040 * h, 0.0032 * h),
            (0.0062 * h, 0.0050 * h),
            (0.0062 * h, 0.0050 * h),
            (0.0040 * h, 0.0032 * h),
        ]
        ll_obj = create_tube_along_curve(
            f"{name_prefix}_LowerLid_{side_lbl}",
            ll_pts,
            ll_radii,
            segments=10,
            cap_ends=True,
            smooth=True,
            subsurf=1,
            collection=collection,
        )
        smart_uv_unwrap(ll_obj)
        lid_names.append(ll_obj.name)
        child_objs.append(ll_obj)
        created.append(ll_obj.name)

        # Expressive arched 3D eyebrow seated directly against the brow ridge
        br_pts = [
            (sx * (eye_x - eye_r * 1.12), -0.415 * h, eye_z + eye_r * 1.05),
            (sx * (eye_x - eye_r * 0.20), -0.432 * h, eye_z + eye_r * 1.32),
            (sx * (eye_x + eye_r * 0.55), -0.412 * h, eye_z + eye_r * 1.34),
            (sx * (eye_x + eye_r * 1.24), -0.368 * h, eye_z + eye_r * 1.10),
        ]
        br_radii = [
            (0.0135 * h, 0.0065 * h),
            (0.0155 * h, 0.0075 * h),
            (0.0125 * h, 0.0065 * h),
            (0.0065 * h, 0.0045 * h),
        ]
        br_obj = create_tube_along_curve(
            f"{name_prefix}_Brow_{side_lbl}",
            br_pts,
            br_radii,
            segments=10,
            cap_ends=True,
            smooth=True,
            subsurf=1,
            collection=collection,
        )
        smart_uv_unwrap(br_obj)
        brow_names.append(br_obj.name)
        child_objs.append(br_obj)
        created.append(br_obj.name)

        # Sculpted stylized ears (human or graceful pointed) with inner concha bowl recess
        el = float(ear_length) if ear_style == "pointed" else 0.032
        ear_pts = [
            (sx * 0.35 * h, 0.015 * h, -0.015 * h),
            (sx * (0.41 * h + el * 0.38), 0.045 * h, 0.015 * h),
            (sx * (0.43 * h + el * 0.78), 0.075 * h, 0.055 * h if ear_style == "pointed" else 0.030 * h),
            (sx * (0.44 * h + el * 1.05), 0.095 * h, 0.098 * h if ear_style == "pointed" else 0.034 * h),
        ]
        ear_obj = sculpt_tapered_ribbon(
            f"{name_prefix}_Ear_{side_lbl}",
            ear_pts,
            [0.145 * h, 0.120 * h, 0.070 * h, 0.014 * h if ear_style == "pointed" else 0.055 * h],
            [0.048 * h, 0.036 * h, 0.022 * h, 0.010 * h],
            arch=0.45,
            up_hint=(0.0, -1.0, 0.0),
            subsurf=0,
            collection=collection,
        )
        bm_e = bmesh.new()
        bm_e.from_mesh(ear_obj.data)
        bmesh.ops.subdivide_edges(bm_e, edges=list(bm_e.edges), cuts=1, use_grid_fill=True, smooth=0.6)
        bmesh.ops.recalc_face_normals(bm_e, faces=list(bm_e.faces))
        bm_e.to_mesh(ear_obj.data)
        bm_e.free()
        ear_obj.data.update()
        set_smooth(ear_obj, True)
        deform_mesh_sculpt(
            ear_obj,
            [
                {
                    "center": (sx * (0.41 * h + el * 0.30), 0.020 * h, 0.008 * h),
                    "radius": 0.068 * h,
                    "delta": (0.0, 0.028 * h, 0.0),
                }
            ],
        )
        smart_uv_unwrap(ear_obj)
        ear_names.append(ear_obj.name)
        child_objs.append(ear_obj)
        created.append(ear_obj.name)

    # 4. Bake world transform cleanly to all vertices so downstream world-space bounds match!
    xf_mat = Matrix.Translation(c_vec) @ rot_m.to_4x4()
    for obj in [head_obj] + child_objs:
        obj.data.transform(xf_mat)
        obj.data.update()

    return {
        "head": head_obj.name,
        "eyes": eye_names,
        "lids": lid_names,
        "brows": brow_names,
        "ears": ear_names,
        "objects": created,
        "sculpt_stats": sculpt_stats,
        "eye_centers": {
            "L": tuple(_to_world(Vector((eye_x, eye_y, eye_z)))),
            "R": tuple(_to_world(Vector((-eye_x, eye_y, eye_z)))),
        },
        "landmarks_world": {
            "crown": tuple(_to_world(Vector((0.0, 0.025 * h, 0.49 * h)))),
            "forehead": tuple(_to_world(Vector((0.0, -0.43 * h, 0.25 * h)))),
            "nose_tip": tuple(_to_world(Vector((0.0, -0.50 * h, -0.102 * h)))),
            "mouth": tuple(_to_world(Vector((0.0, -0.43 * h, -0.246 * h)))),
            "chin": tuple(_to_world(Vector((0.0, -0.34 * h, -0.45 * h)))),
            "neck_base": tuple(_to_world(Vector((0.0, 0.03 * h, -0.45 * h)))),
        },
        "uv_landmarks": {
            "eye_L": _uv_pt(eye_x, eye_z),
            "eye_R": _uv_pt(-eye_x, eye_z),
            "brow_L": _uv_pt(eye_x, eye_z + eye_r * 1.32),
            "brow_R": _uv_pt(-eye_x, eye_z + eye_r * 1.32),
            "cheek_L": _uv_pt(0.22 * h, -0.09 * h),
            "cheek_R": _uv_pt(-0.22 * h, -0.09 * h),
            "nose_tip": _uv_pt(0.0, -0.102 * h),
            "mouth_center": _uv_pt(0.0, -0.246 * h),
            "chin": _uv_pt(0.0, -0.415 * h),
        },
    }


def build_articulated_arm_chain(
    name_prefix: str,
    shoulder: tuple[float, float, float],
    elbow: tuple[float, float, float],
    wrist: tuple[float, float, float],
    *,
    side: str = "R",
    upper_radii: tuple[float, float, float] = (0.056, 0.042, 0.036),
    forearm_radii: tuple[float, float] = (0.035, 0.023),
    cuff_flare: float = 0.012,
    cuff_length: float = 0.055,
    hand_scale: float = 1.0,
    finger_curls: tuple[float, float, float, float, float] = (0.18, 0.14, 0.18, 0.22, 0.26),
    finger_spread: float = 0.14,
    glove_coverage: float = 0.0,
    hand_up_hint: tuple[float, float, float] = (0.0, 0.0, 1.0),
    collection: Any | None = None,
) -> dict[str, Any]:
    """Build ONE guaranteed-continuous anatomical arm chain: shoulder -> elbow (upper sleeve) -> cuff -> forearm -> hand.

    Mathematically prevents double sleeves, dangling tails past elbows, disconnected cuffs, or detached fingertips.
    """
    _require_blender()
    sh = Vector(shoulder)
    el = Vector(elbow)
    wr = Vector(wrist)
    u_vec = el - sh
    f_vec = wr - el
    if u_vec.length < 1e-4 or f_vec.length < 1e-4:
        raise ValueError("shoulder, elbow, and wrist must be distinct 3D positions.")
    u_dir = u_vec.normalized()
    f_dir = f_vec.normalized()

    # 1. Upper sleeve / upper arm smoothly anchored into torso and tapering cleanly inside the elbow cuff!
    inward = Vector((-1.0 if side.upper() == "L" else 1.0, 0.0, 0.0))
    sh_anchor = sh + inward * 0.042 - u_dir * 0.014
    u_pts = [
        tuple(sh_anchor),
        tuple(sh + inward * 0.010),
        tuple(sh.lerp(el, 0.35)),
        tuple(sh.lerp(el, 0.75)),
        tuple(el - u_dir * 0.004),
    ]
    r_sh, r_mid, r_el = float(upper_radii[0]), float(upper_radii[1]), float(upper_radii[2])
    u_rads = [
        (r_sh * 0.92, r_sh * 0.88),
        (r_sh * 0.98, r_sh * 0.92),
        (r_mid * 1.00, r_mid * 0.95),
        (r_el * 1.02, r_el * 0.98),
        (r_el * 0.82, r_el * 0.80),
    ]
    upper_obj = build_continuous_limb(
        f"{name_prefix}_UpperArm_{side}",
        u_pts,
        u_rads,
        segments=20,
        subdiv_per_seg=3,
        subsurf=1,
        collection=collection,
    )
    smart_uv_unwrap(upper_obj)
    created = [upper_obj.name]

    # 2. Forearm / undersleeve cleanly rooted INSIDE the elbow cuff along elbow -> wrist (zero ball-joint protrusion!)
    r_f0, r_f1 = float(forearm_radii[0]), float(forearm_radii[1])
    f_pts = [
        tuple(el + f_dir * 0.006),
        tuple(el + f_dir * 0.036),
        tuple(el.lerp(wr, 0.55)),
        tuple(wr + f_dir * 0.012),
    ]
    f_rads = [
        (r_f0 * 0.84, r_f0 * 0.82),
        (r_f0 * 0.98, r_f0 * 0.94),
        ((r_f0 + r_f1) * 0.50, (r_f0 + r_f1) * 0.46),
        (r_f1 * 1.00, r_f1 * 0.88),
    ]
    forearm_obj = build_continuous_limb(
        f"{name_prefix}_Forearm_{side}",
        f_pts,
        f_rads,
        segments=18,
        subdiv_per_seg=3,
        subsurf=1,
        collection=collection,
    )
    smart_uv_unwrap(forearm_obj)
    created.append(forearm_obj.name)

    # 3. Seamless elbow cuff collar funnel concealing elbow bend cleanly
    cuff_name = None
    if cuff_flare > 0.0 and cuff_length > 0.0:
        elbow_mid = (u_dir - f_dir)
        c_dir = f_dir
        c_pts = [
            tuple(el - c_dir * (cuff_length * 0.22)),
            tuple(el + c_dir * (cuff_length * 0.25)),
            tuple(el + c_dir * (cuff_length * 0.95)),
        ]
        c_rads = [
            (r_el * 0.96, r_el * 0.94),
            (r_el + cuff_flare * 0.85, r_el + cuff_flare * 0.82),
            (r_f0 + cuff_flare * 0.55, r_f0 + cuff_flare * 0.52),
        ]
        cuff_obj = create_tube_along_curve(
            f"{name_prefix}_Cuff_{side}",
            c_pts,
            c_rads,
            segments=20,
            cap_ends=False,
            smooth=True,
            subsurf=1,
            collection=collection,
        )
        add_modifiers(cuff_obj, solidify=0.0045, solidify_offset=1.0)
        smart_uv_unwrap(cuff_obj)
        cuff_name = cuff_obj.name
        created.append(cuff_name)

    # 4. Connected articulated 5-finger hand (+ optional fingerless glove)
    hand_info = build_articulated_hand(
        f"{name_prefix}_Hand_{side}",
        tuple(wr),
        tuple(f_dir),
        up_dir=hand_up_hint,
        side=side,
        scale=hand_scale,
        finger_curls=finger_curls,
        finger_spread=finger_spread,
        glove_coverage=glove_coverage,
        collection=collection,
    )
    for hname in hand_info["objects"]:
        ho = bpy.data.objects.get(hname)
        if ho is not None:
            smart_uv_unwrap(ho)
    created.extend(hand_info["objects"])
    palm_ctr = wr + f_dir * (0.048 * hand_scale)
    return {
        "upper_arm": upper_obj.name,
        "forearm": forearm_obj.name,
        "cuff": cuff_name,
        "hand": hand_info,
        "objects": created,
        "joints": {"shoulder": tuple(sh), "elbow": tuple(el), "wrist": tuple(wr), "palm_center": tuple(palm_ctr)},
    }


def build_sculpted_hair_sheet(
    name: str,
    root_curve: list[tuple[float, float, float]],
    tip_curve: list[tuple[float, float, float]],
    *,
    mid_curve: list[tuple[float, float, float]] | None = None,
    rows: int = 10,
    cols: int = 24,
    thickness: float = 0.024,
    wave_amp: float = 0.007,
    wave_freq: float = 6.0,
    tip_scallop: float = 0.10,
    outward_hint: tuple[float, float, float] = (0.0, 1.0, 0.15),
    subsurf: int = 1,
    collection: Any | None = None,
) -> Any:
    """Build a thick, continuous sculpted hair/cape/mane volume sheet with strand ridges and scalloped tips.

    Eliminates spiky disconnected hair blades with empty background gaps.
    """
    _require_blender()
    if len(root_curve) < 2 or len(tip_curve) < 2:
        raise ValueError("root_curve and tip_curve each require >= 2 points.")

    def _sample_poly(pts: list[tuple[float, float, float]], n_out: int) -> list[Vector]:
        vecs = [Vector(p) for p in pts]
        lens = [0.0]
        for a, b in zip(vecs, vecs[1:]):
            lens.append(lens[-1] + (b - a).length)
        total = max(lens[-1], 1e-6)
        out = []
        for i in range(n_out):
            target = total * (i / max(n_out - 1, 1))
            seg = 0
            while seg < len(lens) - 2 and lens[seg + 1] < target:
                seg += 1
            denom = max(lens[seg + 1] - lens[seg], 1e-6)
            t = max(0.0, min(1.0, (target - lens[seg]) / denom))
            out.append(vecs[seg].lerp(vecs[seg + 1], t))
        return out

    roots = _sample_poly(root_curve, cols)
    tips = _sample_poly(tip_curve, cols)
    if mid_curve and len(mid_curve) >= 2:
        mids = _sample_poly(mid_curve, cols)
    else:
        out_v = Vector(outward_hint).normalized()
        mids = [r.lerp(t, 0.5) + out_v * (0.04 * (t - r).length) for r, t in zip(roots, tips)]

    bm = bmesh.new()
    out_hint_v = Vector(outward_hint).normalized()
    front_grid: list[list[Any]] = []
    back_grid: list[list[Any]] = []

    for ri in range(rows):
        v_raw = ri / float(max(rows - 1, 1))
        f_row = []
        b_row = []
        for ci in range(cols):
            u = ci / float(max(cols - 1, 1))
            # Natural scalloped lock tips along trailing edge
            scallop = 1.0 - float(tip_scallop) * (0.5 + 0.5 * math.cos(u * math.pi * wave_freq * 2.0))
            v = min(1.0, v_raw * (1.0 if v_raw < 0.65 else (0.65 + (v_raw - 0.65) * scallop) / max(v_raw, 1e-5)))
            # Quadratic Bezier from root -> mid -> tip
            p = ((1.0 - v) ** 2) * roots[ci] + (2.0 * (1.0 - v) * v) * mids[ci] + (v ** 2) * tips[ci]
            # Local normal approx
            p_next_u = ((1.0 - v) ** 2) * roots[min(cols - 1, ci + 1)] + (2.0 * (1.0 - v) * v) * mids[min(cols - 1, ci + 1)] + (v ** 2) * tips[min(cols - 1, ci + 1)]
            p_prev_u = ((1.0 - v) ** 2) * roots[max(0, ci - 1)] + (2.0 * (1.0 - v) * v) * mids[max(0, ci - 1)] + (v ** 2) * tips[max(0, ci - 1)]
            tan_u = (p_next_u - p_prev_u)
            tan_v = 2.0 * (1.0 - v) * (mids[ci] - roots[ci]) + 2.0 * v * (tips[ci] - mids[ci])
            nrm = tan_u.cross(tan_v)
            if nrm.length < 1e-6:
                nrm = out_hint_v
            else:
                nrm.normalize()
                if nrm.dot(out_hint_v) < 0:
                    nrm = -nrm

            # Sculpted volumetric strand ridges + taper near tip
            taper = math.sin(v_raw * math.pi * 0.85 + 0.15)
            ridge = math.sin(u * math.pi * wave_freq * 2.0) * float(wave_amp) * (0.35 + 0.65 * taper)
            half_th = max(0.0018, float(thickness) * 0.5 * (1.0 - 0.82 * (v_raw ** 1.6)))
            f_row.append(bm.verts.new(p + nrm * (half_th + ridge)))
            b_row.append(bm.verts.new(p - nrm * (half_th * 0.75)))
        front_grid.append(f_row)
        back_grid.append(b_row)

    # Front, back, and stitched perimeter faces for watertight solid hair sheet
    for ri in range(rows - 1):
        for ci in range(cols - 1):
            try:
                bm.faces.new((front_grid[ri][ci], front_grid[ri][ci + 1], front_grid[ri + 1][ci + 1], front_grid[ri + 1][ci]))
                bm.faces.new((back_grid[ri][ci + 1], back_grid[ri][ci], back_grid[ri + 1][ci], back_grid[ri + 1][ci + 1]))
            except ValueError:
                pass
    for ci in range(cols - 1):
        try:
            bm.faces.new((front_grid[0][ci + 1], front_grid[0][ci], back_grid[0][ci], back_grid[0][ci + 1]))
            bm.faces.new((front_grid[-1][ci], front_grid[-1][ci + 1], back_grid[-1][ci + 1], back_grid[-1][ci]))
        except ValueError:
            pass
    for ri in range(rows - 1):
        try:
            bm.faces.new((front_grid[ri][0], front_grid[ri + 1][0], back_grid[ri + 1][0], back_grid[ri][0]))
            bm.faces.new((front_grid[ri + 1][-1], front_grid[ri][-1], back_grid[ri][-1], back_grid[ri + 1][-1]))
        except ValueError:
            pass

    obj = _finalize_bmesh_object(name, bm, collection=collection, smooth=True, subsurf=subsurf)
    smart_uv_unwrap(obj)
    return obj


def build_interwoven_braid(
    name: str,
    spine_pts: list[tuple[float, float, float]],
    *,
    radius: float = 0.015,
    twists: float = 7.0,
    samples: int = 44,
    segments: int = 10,
    collection: Any | None = None,
) -> Any:
    """Weave 3 interlocking sinusoidal helical strands along any 3D curve into ONE solid braided mesh."""
    _require_blender()
    if len(spine_pts) < 2:
        raise ValueError("build_interwoven_braid requires at least 2 spine points.")
    vecs = [Vector(p) for p in spine_pts]
    # Resample spine smoothly
    lens = [0.0]
    for a, b in zip(vecs, vecs[1:]):
        lens.append(lens[-1] + (b - a).length)
    total = max(lens[-1], 1e-6)
    pts: list[Vector] = []
    for i in range(samples):
        tgt = total * (i / max(samples - 1, 1))
        seg = 0
        while seg < len(lens) - 2 and lens[seg + 1] < tgt:
            seg += 1
        t = (tgt - lens[seg]) / max(lens[seg + 1] - lens[seg], 1e-6)
        pts.append(vecs[seg].lerp(vecs[seg + 1], max(0.0, min(1.0, t))))

    # Parallel transport frame along spine
    frames: list[tuple[Vector, Vector, Vector]] = []
    prev_n = None
    for i, p in enumerate(pts):
        tan = (pts[min(samples - 1, i + 1)] - pts[max(0, i - 1)]).normalized()
        if tan.length < 1e-6:
            tan = Vector((1.0, 0.0, 0.0))
        if prev_n is None:
            ref = Vector((0.0, 0.0, 1.0)) if abs(tan.z) < 0.9 else Vector((0.0, -1.0, 0.0))
            b = tan.cross(ref).normalized()
            n = b.cross(tan).normalized()
        else:
            n = (prev_n - tan * prev_n.dot(tan))
            n = tan.orthogonal().normalized() if n.length < 1e-6 else n.normalized()
            b = tan.cross(n).normalized()
        prev_n = n
        frames.append((tan, b, n))

    strand_objs = []
    r_strand = float(radius) * 0.58
    amp_b = float(radius) * 0.72
    amp_n = float(radius) * 0.36
    for s_idx in range(3):
        phase = s_idx * (2.0 * math.pi / 3.0)
        s_pts = []
        s_rads = []
        for i, p in enumerate(pts):
            u = i / float(max(samples - 1, 1))
            ang = u * float(twists) * 2.0 * math.pi + phase
            _, b, n = frames[i]
            off = b * (math.sin(ang) * amp_b) + n * (math.cos(2.0 * ang) * amp_n)
            s_pts.append(tuple(p + off))
            taper = 0.72 + 0.28 * math.sin(u * math.pi)
            s_rads.append((r_strand * taper, r_strand * 0.85 * taper))
        so = create_tube_along_curve(
            f"__tmp_{name}_s{s_idx}",
            s_pts,
            s_rads,
            segments=segments,
            cap_ends=True,
            smooth=True,
            subsurf=0,
            collection=collection,
        )
        strand_objs.append(so)

    braid = join_objects(strand_objs, name, collection=collection, remove_sources=True, smooth=True)
    smart_uv_unwrap(braid)
    return braid


# ---------------------------------------------------------------------------
# 3. UV Unwrapping, Procedural 2D Texture Painting & Stylized PBR Materials
# ---------------------------------------------------------------------------


def smart_uv_unwrap(obj: Any, *, angle_limit_deg: float = 66.0, island_margin: float = 0.02) -> bool:
    """Create UV layer cleanly using cube/box projection in BMesh (headless-safe, no viewport required)."""
    _require_blender()
    if obj.type != "MESH" or obj.data is None or len(obj.data.polygons) == 0:
        return False
    mesh = obj.data
    if not mesh.uv_layers:
        mesh.uv_layers.new(name="UVMap")
    uv_layer = mesh.uv_layers.active.data

    # Compute object local bounds for normalized triplanar/box UV mapping
    xs = [v.co.x for v in mesh.vertices]
    ys = [v.co.y for v in mesh.vertices]
    zs = [v.co.z for v in mesh.vertices]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    min_z, max_z = min(zs), max(zs)
    span = max(max_x - min_x, max_y - min_y, max_z - min_z, 1e-4)

    for poly in mesh.polygons:
        nx, ny, nz = abs(poly.normal.x), abs(poly.normal.y), abs(poly.normal.z)
        for li in poly.loop_indices:
            co = mesh.vertices[mesh.loops[li].vertex_index].co
            if ny >= nx and ny >= nz:
                u = (co.x - min_x) / span
                v = (co.z - min_z) / span
            elif nx >= ny and nx >= nz:
                u = (co.y - min_y) / span
                v = (co.z - min_z) / span
            else:
                u = (co.x - min_x) / span
                v = (co.y - min_y) / span
            uv_layer[li].uv = (u, v)
    mesh.update()
    return True


def project_planar_uv(
    obj: Any,
    *,
    axis: str = "Y",
    bounds: tuple[float, float, float, float] | None = None,
) -> bool:
    """Project clean normalized [0..1] planar UVs along Y (front face), X (side), or Z (top)."""
    _require_blender()
    if obj.type != "MESH" or obj.data is None or len(obj.data.polygons) == 0:
        return False
    mesh = obj.data
    if not mesh.uv_layers:
        mesh.uv_layers.new(name="UVMap")
    uv_layer = mesh.uv_layers.active.data
    mat = obj.matrix_world

    coords = []
    for v in mesh.vertices:
        w = mat @ v.co
        if axis.upper() == "Y":
            coords.append((w.x, w.z))
        elif axis.upper() == "X":
            coords.append((w.y, w.z))
        else:
            coords.append((w.x, w.y))

    if bounds is not None:
        u0, u1, v0, v1 = bounds
    else:
        us = [c[0] for c in coords]
        vs = [c[1] for c in coords]
        u0, u1 = min(us), max(us)
        v0, v1 = min(vs), max(vs)
    du = max(u1 - u0, 1e-5)
    dv = max(v1 - v0, 1e-5)

    for poly in mesh.polygons:
        for li in poly.loop_indices:
            vi = mesh.loops[li].vertex_index
            cu, cv = coords[vi]
            uv_layer[li].uv = ((cu - u0) / du, (cv - v0) / dv)
    mesh.update()
    return True


def create_painted_image_texture(
    name: str,
    width: int,
    height: int,
    ops: list[dict[str, Any]],
    *,
    base_color: tuple[float, float, float, float] = (0.5, 0.5, 0.5, 1.0),
    save_path: str | None = None,
    pack: bool = True,
) -> Any:
    """Render a crisp procedural RGBA texture into a packed bpy.data.image using numpy."""
    _require_blender()
    if np is None:
        raise RuntimeError("numpy is required inside Blender for create_painted_image_texture.")

    canvas = np.zeros((height, width, 4), dtype=np.float32)
    canvas[:, :, 0] = base_color[0]
    canvas[:, :, 1] = base_color[1]
    canvas[:, :, 2] = base_color[2]
    canvas[:, :, 3] = base_color[3] if len(base_color) > 3 else 1.0

    yy, xx = np.mgrid[0:height, 0:width].astype(np.float32)
    u = (xx + 0.5) / float(width)
    v = (yy + 0.5) / float(height)

    def _blend(mask: Any, col: tuple[float, ...]) -> None:
        a = np.clip(mask, 0.0, 1.0) * (float(col[3]) if len(col) > 3 else 1.0)
        a3 = a[:, :, None]
        rgb = np.array(col[:3], dtype=np.float32)[None, None, :]
        canvas[:, :, :3] = canvas[:, :, :3] * (1.0 - a3) + rgb * a3
        canvas[:, :, 3] = np.clip(canvas[:, :, 3] + a * (1.0 - canvas[:, :, 3]), 0.0, 1.0)

    for op in ops:
        kind = str(op.get("op", "")).lower()
        if kind == "linear_gradient":
            axis = str(op.get("axis", "y")).lower()
            s0 = float(op.get("start", 0.0))
            s1 = float(op.get("end", 1.0))
            c0 = np.array(op["c0"][:4] if len(op["c0"]) >= 4 else (*op["c0"][:3], 1.0), dtype=np.float32)
            c1 = np.array(op["c1"][:4] if len(op["c1"]) >= 4 else (*op["c1"][:3], 1.0), dtype=np.float32)
            coord = v if axis == "y" else u
            t = np.clip((coord - s0) / max(s1 - s0, 1e-5), 0.0, 1.0)[:, :, None]
            grad = c0[None, None, :] * (1.0 - t) + c1[None, None, :] * t
            a = grad[:, :, 3:4]
            canvas[:, :, :3] = canvas[:, :, :3] * (1.0 - a) + grad[:, :, :3] * a
        elif kind == "rect":
            x0, y0, x1, y1 = float(op["x0"]), float(op["y0"]), float(op["x1"]), float(op["y1"])
            f = max(float(op.get("feather", 0.002)), 1e-4)
            mx = np.clip((u - x0) / f, 0.0, 1.0) * np.clip((x1 - u) / f, 0.0, 1.0)
            my = np.clip((v - y0) / f, 0.0, 1.0) * np.clip((y1 - v) / f, 0.0, 1.0)
            _blend(mx * my, tuple(op["color"]))
        elif kind == "ellipse":
            cx, cy = float(op["cx"]), float(op["cy"])
            rx, ry = max(float(op["rx"]), 1e-4), max(float(op["ry"]), 1e-4)
            ang = math.radians(float(op.get("angle_deg", 0.0)))
            du, dv = u - cx, v - cy
            ru = du * math.cos(ang) + dv * math.sin(ang)
            rv = -du * math.sin(ang) + dv * math.cos(ang)
            d = np.sqrt((ru / rx) ** 2 + (rv / ry) ** 2)
            f = max(float(op.get("feather", 0.02)), 1e-4)
            mask = np.clip((1.0 - d) / f, 0.0, 1.0)
            _blend(mask, tuple(op["color"]))
        elif kind == "ring":
            cx, cy = float(op["cx"]), float(op["cy"])
            rx, ry = max(float(op["rx"]), 1e-4), max(float(op["ry"]), 1e-4)
            th = max(float(op.get("thickness", 0.08)), 1e-4)
            d = np.sqrt(((u - cx) / rx) ** 2 + ((v - cy) / ry) ** 2)
            f = max(float(op.get("feather", 0.02)), 1e-4)
            mask = np.clip((th - np.abs(d - 1.0)) / f, 0.0, 1.0)
            _blend(mask, tuple(op["color"]))
        elif kind == "radial_gradient":
            cx, cy = float(op["cx"]), float(op["cy"])
            rx, ry = max(float(op["rx"]), 1e-4), max(float(op["ry"]), 1e-4)
            d = np.clip(np.sqrt(((u - cx) / rx) ** 2 + ((v - cy) / ry) ** 2), 0.0, 1.0)
            ic = np.array(op["inner_color"][:4] if len(op["inner_color"]) >= 4 else (*op["inner_color"][:3], 1.0), dtype=np.float32)
            oc = np.array(op["outer_color"][:4] if len(op["outer_color"]) >= 4 else (*op["outer_color"][:3], 1.0), dtype=np.float32)
            t = d[:, :, None]
            grad = ic[None, None, :] * (1.0 - t) + oc[None, None, :] * t
            inside = (d < 1.0).astype(np.float32)[:, :, None]
            a = grad[:, :, 3:4] * inside
            canvas[:, :, :3] = canvas[:, :, :3] * (1.0 - a) + grad[:, :, :3] * a
        elif kind == "arc":
            cx, cy = float(op["cx"]), float(op["cy"])
            rx, ry = max(float(op["rx"]), 1e-4), max(float(op["ry"]), 1e-4)
            th = max(float(op.get("thickness", 0.06)), 1e-4)
            a0 = float(op.get("a0_deg", 0.0))
            a1 = float(op.get("a1_deg", 180.0))
            du, dv = (u - cx) / rx, (v - cy) / ry
            d = np.sqrt(du ** 2 + dv ** 2)
            ang = (np.degrees(np.arctan2(dv, du)) + 360.0) % 360.0
            a0m, a1m = a0 % 360.0, a1 % 360.0
            if a0m <= a1m:
                in_ang = (ang >= a0m) & (ang <= a1m)
            else:
                in_ang = (ang >= a0m) | (ang <= a1m)
            f = max(float(op.get("feather", 0.02)), 1e-4)
            mask = np.clip((th - np.abs(d - 1.0)) / f, 0.0, 1.0) * in_ang.astype(np.float32)
            _blend(mask, tuple(op["color"]))
        elif kind == "line":
            x0, y0, x1, y1 = float(op["x0"]), float(op["y0"]), float(op["x1"]), float(op["y1"])
            th = max(float(op.get("thickness", 0.01)), 1e-4)
            dx, dy = x1 - x0, y1 - y0
            l2 = max(dx * dx + dy * dy, 1e-8)
            t = np.clip(((u - x0) * dx + (v - y0) * dy) / l2, 0.0, 1.0)
            px, py = x0 + t * dx, y0 + t * dy
            dist = np.sqrt((u - px) ** 2 + (v - py) ** 2)
            f = max(float(op.get("feather", 0.004)), 1e-4)
            mask = np.clip((th - dist) / f, 0.0, 1.0)
            _blend(mask, tuple(op["color"]))
        elif kind == "polyline":
            pts_2d = op.get("points") or []
            th = max(float(op.get("thickness", 0.01)), 1e-4)
            f = max(float(op.get("feather", 0.004)), 1e-4)
            if len(pts_2d) >= 2:
                acc = np.zeros_like(u)
                for (x0, y0), (x1, y1) in zip(pts_2d, pts_2d[1:]):
                    dx, dy = float(x1) - float(x0), float(y1) - float(y0)
                    l2 = max(dx * dx + dy * dy, 1e-8)
                    t = np.clip(((u - float(x0)) * dx + (v - float(y0)) * dy) / l2, 0.0, 1.0)
                    px, py = float(x0) + t * dx, float(y0) + t * dy
                    dist = np.sqrt((u - px) ** 2 + (v - py) ** 2)
                    acc = np.maximum(acc, np.clip((th - dist) / f, 0.0, 1.0))
                _blend(acc, tuple(op["color"]))

    old = bpy.data.images.get(name)
    if old is not None:
        bpy.data.images.remove(old)
    img = bpy.data.images.new(name, width=width, height=height, alpha=True)
    try:
        img.colorspace_settings.name = "sRGB"
    except Exception:
        pass
    img.pixels.foreach_set(canvas.ravel())
    if save_path:
        p = Path(save_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        img.filepath_raw = str(p)
        img.file_format = "PNG"
        img.save()
    if pack:
        try:
            img.pack()
        except Exception:
            pass
    return img


def make_pbr_material(
    name: str,
    base_color: tuple[float, float, float] | tuple[float, float, float, float] | str,
    *,
    roughness: float = 0.45,
    metallic: float = 0.0,
    subsurface: float = 0.0,
    coat: float = 0.0,
    specular: float = 0.5,
    emission_color: tuple[float, float, float] | str | None = None,
    emission_strength: float = 0.0,
    emission_texture: Any | None = None,
    ao_darken: float = 0.28,
    ao_distance: float = 0.16,
    rim_color: tuple[float, float, float] | str | None = None,
    rim_strength: float = 0.0,
    noise_bump: float = 0.0,
    noise_scale: float = 35.0,
    image_texture: Any | None = None,
) -> Any:
    """Create a rich, glTF-compatible Blender 5.3 stylized PBR material.

    `base_color`, `emission_color`, and `rim_color` accept either linear RGB tuples OR '#RRGGBB' sRGB hex strings
    (automatically decoded to linear RGB so colors stay rich and saturated under AgX).
    """
    _require_blender()
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name=name)
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()

    def _to_lin_rgb(c: Any) -> tuple[float, float, float]:
        if isinstance(c, str):
            return hex_to_linear(c)
        return (float(c[0]), float(c[1]), float(c[2]))

    out = nt.nodes.new("ShaderNodeOutputMaterial")
    out.location = (650, 0)
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (300, 0)

    lin_base = _to_lin_rgb(base_color)
    rgba = (lin_base[0], lin_base[1], lin_base[2], 1.0)
    bsdf.inputs["Base Color"].default_value = rgba
    bsdf.inputs["Roughness"].default_value = float(roughness)
    bsdf.inputs["Metallic"].default_value = float(metallic)
    if "Specular IOR Level" in bsdf.inputs:
        bsdf.inputs["Specular IOR Level"].default_value = float(specular)
    if "Subsurface Weight" in bsdf.inputs and subsurface > 0.0:
        bsdf.inputs["Subsurface Weight"].default_value = float(subsurface)
    if "Subsurface Scale" in bsdf.inputs and subsurface > 0.0:
        bsdf.inputs["Subsurface Scale"].default_value = 0.015
    if "Coat Weight" in bsdf.inputs and coat > 0.0:
        bsdf.inputs["Coat Weight"].default_value = float(coat)
    if emission_texture is not None and emission_strength > 0.0:
        etex = nt.nodes.new("ShaderNodeTexImage")
        etex.location = (-120, -140)
        etex.image = emission_texture
        if "Emission Color" in bsdf.inputs:
            nt.links.new(etex.outputs["Color"], bsdf.inputs["Emission Color"])
        if "Emission Strength" in bsdf.inputs:
            bsdf.inputs["Emission Strength"].default_value = float(emission_strength)
    elif emission_color is not None and emission_strength > 0.0:
        lin_em = _to_lin_rgb(emission_color)
        if "Emission Color" in bsdf.inputs:
            bsdf.inputs["Emission Color"].default_value = (lin_em[0], lin_em[1], lin_em[2], 1.0)
        if "Emission Strength" in bsdf.inputs:
            bsdf.inputs["Emission Strength"].default_value = float(emission_strength)

    # Source color node (Image Texture or solid Base Color)
    color_socket = None
    if image_texture is not None:
        tex = nt.nodes.new("ShaderNodeTexImage")
        tex.location = (-380, 120)
        tex.image = image_texture
        color_socket = tex.outputs["Color"]

    # Subtle AO cavity darkening for sculpted depth readability
    if ao_darken > 0.0:
        ao = nt.nodes.new("ShaderNodeAmbientOcclusion")
        ao.location = (-200, 260)
        ao.samples = 8
        ao.inputs["Distance"].default_value = float(ao_distance)
        if color_socket is not None:
            nt.links.new(color_socket, ao.inputs["Color"])
        else:
            ao.inputs["Color"].default_value = rgba

        mix_ao = nt.nodes.new("ShaderNodeMix")
        mix_ao.data_type = "RGBA"
        mix_ao.blend_type = "MULTIPLY"
        mix_ao.location = (40, 160)
        mix_ao.inputs["Factor"].default_value = float(ao_darken)
        if color_socket is not None:
            nt.links.new(color_socket, mix_ao.inputs[6])
        else:
            mix_ao.inputs[6].default_value = rgba
        nt.links.new(ao.outputs["AO"], mix_ao.inputs[7])
        color_socket = mix_ao.outputs[2]

    # Optional Fresnel rim tint for stylized form separation
    if rim_color is not None and rim_strength > 0.0:
        lin_rim = _to_lin_rgb(rim_color)
        lw = nt.nodes.new("ShaderNodeLayerWeight")
        lw.location = (-160, -80)
        lw.inputs["Blend"].default_value = 0.35
        mix_rim = nt.nodes.new("ShaderNodeMix")
        mix_rim.data_type = "RGBA"
        mix_rim.blend_type = "MIX"
        mix_rim.location = (150, 80)
        mix_rim.inputs["Factor"].default_value = float(min(0.45, rim_strength))
        if color_socket is not None:
            nt.links.new(color_socket, mix_rim.inputs[6])
        else:
            mix_rim.inputs[6].default_value = rgba
        mix_rim.inputs[7].default_value = (lin_rim[0], lin_rim[1], lin_rim[2], 1.0)
        color_socket = mix_rim.outputs[2]

    if color_socket is not None:
        nt.links.new(color_socket, bsdf.inputs["Base Color"])

    if noise_bump > 0.0:
        noise = nt.nodes.new("ShaderNodeTexNoise")
        noise.location = (-220, -240)
        noise.inputs["Scale"].default_value = float(noise_scale)
        noise.inputs["Detail"].default_value = 5.0
        bump = nt.nodes.new("ShaderNodeBump")
        bump.location = (40, -240)
        bump.inputs["Strength"].default_value = float(noise_bump)
        bump.inputs["Distance"].default_value = 0.004
        nt.links.new(noise.outputs["Fac"], bump.inputs["Height"])
        nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])

    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    return mat


def assign_material(obj: Any, mat: Any) -> None:
    _require_blender()
    if obj.type != "MESH" or obj.data is None:
        return
    obj.data.materials.clear()
    obj.data.materials.append(mat)


# ---------------------------------------------------------------------------
# 4. Scene Audit, Studio Lighting/Cameras & Clay/Lookdev Multi-View Renderer
# ---------------------------------------------------------------------------


def _is_studio_object(obj: Any) -> bool:
    lname = obj.name.lower()
    if (
        obj.name.startswith(("SM_Studio_", "LGT_Studio_", "CAM_Studio_", "CURVE_"))
        or getattr(obj, "is_shadow_catcher", False)
        or any(tok in lname for tok in ("backdrop", "studio_floor", "studio_wall", "cyclorama", "bg_plane"))
    ):
        return True
    ucols = list(getattr(obj, "users_collection", []) or [])
    if ucols and all("studio" in c.name.lower() for c in ucols):
        return True
    return False


def _compute_collection_bounds(scene: Any, export_collection: str = "COL_Export") -> dict[str, Any]:
    col = bpy.data.collections.get(export_collection)
    meshes = [
        o
        for o in (col.all_objects if col else scene.objects)
        if o.type == "MESH" and not o.hide_render and not _is_studio_object(o)
    ]
    if not meshes:
        return {"empty": True, "min": (0.0, 0.0, 0.0), "max": (0.0, 0.0, 1.0), "center": (0.0, 0.0, 0.5), "size": (0.5, 0.5, 1.0)}
    deps = bpy.context.evaluated_depsgraph_get()

    def _box(o: Any) -> tuple[float, float, float, float, float, float]:
        eo = o.evaluated_get(deps)
        cs = [eo.matrix_world @ Vector(corner) for corner in eo.bound_box]
        return (
            min(c.x for c in cs),
            min(c.y for c in cs),
            min(c.z for c in cs),
            max(c.x for c in cs),
            max(c.y for c in cs),
            max(c.z for c in cs),
        )

    # If SM_BaseBody is present, exclude tight outlier spikes so studio lighting/camera framing stays anchored to character
    anchor_box = None
    body_obj = next((o for o in meshes if o.name == "SM_BaseBody"), None)
    if body_obj is not None:
        anchor_box = _box(body_obj)

    xs, ys, zs = [], [], []
    for o in meshes:
        b = _box(o)
        if anchor_box is not None and o is not body_obj:
            if (
                b[0] < anchor_box[0] - 0.85
                or b[1] < anchor_box[1] - 0.85
                or b[2] < anchor_box[2] - 0.04
                or b[3] > anchor_box[3] + 0.85
                or b[4] > anchor_box[4] + 0.85
                or b[5] > anchor_box[5] + 0.30
            ):
                continue
        xs.extend((b[0], b[3]))
        ys.extend((b[1], b[4]))
        zs.extend((b[2], b[5]))

    if not xs and anchor_box is not None:
        xs = [anchor_box[0], anchor_box[3]]
        ys = [anchor_box[1], anchor_box[4]]
        zs = [anchor_box[2], anchor_box[5]]

    mn = (min(xs), min(ys), min(zs))
    mx = (max(xs), max(ys), max(zs))
    ctr = ((mn[0] + mx[0]) * 0.5, (mn[1] + mx[1]) * 0.5, (mn[2] + mx[2]) * 0.5)
    sz = (max(mx[0] - mn[0], 0.05), max(mx[1] - mn[1], 0.05), max(mx[2] - mn[2], 0.05))
    return {"empty": False, "min": mn, "max": mx, "center": ctr, "size": sz}


def get_scene_audit(scene: Any, *, export_collection: str = "COL_Export") -> dict[str, Any]:
    """Return deterministic mesh, topology, UV, material, and bounding audit of the scene."""
    _require_blender()
    col = bpy.data.collections.get(export_collection)
    objs = list(col.all_objects) if col else list(scene.objects)
    mesh_objs = [o for o in objs if o.type == "MESH" and not o.hide_render and not _is_studio_object(o)]
    deps = bpy.context.evaluated_depsgraph_get()

    meshes_info = []
    total_tris = 0
    total_polys = 0
    total_quads = 0
    all_mats: set[str] = set()
    uv_missing: list[str] = []

    for o in mesh_objs:
        eo = o.evaluated_get(deps)
        em = eo.to_mesh()
        polys = len(em.polygons)
        quads = sum(1 for p in em.polygons if len(p.vertices) == 4)
        tris = sum(max(1, len(p.vertices) - 2) for p in em.polygons)
        total_polys += polys
        total_quads += quads
        total_tris += tris
        corners = [eo.matrix_world @ Vector(c) for c in eo.bound_box]
        bmin = [round(min(c[i] for c in corners), 4) for i in range(3)]
        bmax = [round(max(c[i] for c in corners), 4) for i in range(3)]
        mats = [m.name for m in o.data.materials if m is not None]
        all_mats.update(mats)
        has_uv = len(o.data.uv_layers) > 0
        if not has_uv:
            uv_missing.append(o.name)
        meshes_info.append(
            {
                "name": o.name,
                "vertices": len(em.vertices),
                "polygons": polys,
                "triangles": tris,
                "quad_ratio": round(quads / max(polys, 1), 3),
                "has_uv": has_uv,
                "materials": mats,
                "bbox_min": bmin,
                "bbox_max": bmax,
            }
        )
        eo.to_mesh_clear()

    bounds = _compute_collection_bounds(scene, export_collection)
    armatures = [o.name for o in scene.objects if o.type == "ARMATURE"]
    actions = [a.name for a in bpy.data.actions]
    return {
        "scene_name": scene.name,
        "mesh_count": len(meshes_info),
        "total_triangles": total_tris,
        "total_polygons": total_polys,
        "quad_ratio": round(total_quads / max(total_polys, 1), 3),
        "material_count": len(all_mats),
        "materials": sorted(all_mats),
        "uv_missing_objects": uv_missing,
        "armatures": armatures,
        "actions": actions,
        "min_z_m": round(bounds["min"][2], 4),
        "max_z_m": round(bounds["max"][2], 4),
        "height_m": round(bounds["size"][2], 4),
        "bbox_min": [round(v, 4) for v in bounds["min"]],
        "bbox_max": [round(v, 4) for v in bounds["max"]],
        "meshes": meshes_info,
    }


def _min_free_gpu_vram_mib() -> int | None:
    """Return max free GPU VRAM in MiB across visible NVIDIA GPUs (None if nvidia-smi unavailable)."""
    import subprocess
    for exe in ("nvidia-smi", "/usr/bin/nvidia-smi"):
        try:
            out = subprocess.check_output(
                [exe, "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
                text=True,
                timeout=3.0,
            )
            vals = [int(line.strip()) for line in out.splitlines() if line.strip().isdigit()]
            if vals:
                return max(vals)
        except Exception:
            continue
    return None


def _switch_cycles_to_cpu(scene: Any) -> str:
    scene.cycles.device = "CPU"
    try:
        scene.cycles.denoiser = "OPENIMAGEDENOISE"
    except Exception:
        pass
    try:
        prefs = bpy.context.preferences.addons["cycles"].preferences
        prefs.compute_device_type = "NONE"
    except Exception:
        pass
    return "CPU"


def _configure_render_engine(scene: Any, *, res: tuple[int, int], samples: int, use_gpu: bool = True) -> str:
    scene.render.engine = "CYCLES"
    scene.render.resolution_x = int(res[0])
    scene.render.resolution_y = int(res[1])
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.film_transparent = False
    scene.cycles.samples = int(samples)
    scene.cycles.use_adaptive_sampling = True
    scene.cycles.adaptive_threshold = 0.02
    scene.cycles.use_denoising = True
    scene.cycles.max_bounces = 6
    scene.cycles.diffuse_bounces = 3
    scene.cycles.glossy_bounces = 3

    # AgX color management calibrated at exposure 0.0 so saturated colors stay rich (zero pastel blowout!)
    try:
        scene.view_settings.view_transform = "AgX"
        try:
            scene.view_settings.look = "AgX - Medium High Contrast"
        except Exception:
            pass
    except Exception:
        scene.view_settings.view_transform = "Standard"
    scene.view_settings.exposure = 0.0

    free_mib = _min_free_gpu_vram_mib() if use_gpu else 0
    if use_gpu and (free_mib is None or free_mib < 6000):
        # Yield GPU cleanly to host workloads (e.g. Strata reserving 4096 MiB) rather than triggering CUDA/OptiX OOM!
        return _switch_cycles_to_cpu(scene)

    effective = "CPU"
    if use_gpu:
        try:
            prefs = bpy.context.preferences.addons["cycles"].preferences
            for backend in ("OPTIX", "CUDA"):
                try:
                    prefs.compute_device_type = backend
                    prefs.get_devices()
                    gpu_devs = [d for d in prefs.devices if d.type == backend]
                    if gpu_devs:
                        for d in prefs.devices:
                            d.use = (d.type == backend)
                        scene.cycles.device = "GPU"
                        try:
                            scene.cycles.denoiser = "OPTIX" if backend == "OPTIX" else "OPENIMAGEDENOISE"
                        except Exception:
                            pass
                        effective = f"GPU:{backend}"
                        break
                except Exception:
                    continue
        except Exception:
            pass
    if effective == "CPU":
        return _switch_cycles_to_cpu(scene)
    return effective


def _ensure_export_meshes_renderable(scene: Any, export_collection: str = "COL_Export", studio_collection: str = "COL_Studio") -> None:
    """Guarantee all asset meshes are in COL_Export and enabled for rendering (prevents blank renders)."""
    col = get_or_create_collection(scene, export_collection)
    col.hide_render = False
    col.hide_viewport = False
    # Ensure collection is NOT excluded in active View Layer
    def _unexclude(layer_col: Any) -> None:
        if layer_col.collection.name == export_collection:
            layer_col.exclude = False
            layer_col.hide_viewport = False
        for ch in layer_col.children:
            _unexclude(ch)
    for vl in scene.view_layers:
        _unexclude(vl.layer_collection)

    # Actively evict any studio/shadow-catcher objects accidentally linked to COL_Export, and adopt real asset meshes
    scol = get_or_create_collection(scene, studio_collection)
    studio_objs = set(scol.all_objects)
    for obj in list(col.all_objects):
        if _is_studio_object(obj) or obj in studio_objs:
            try:
                col.objects.unlink(obj)
            except Exception:
                pass
            if obj not in studio_objs:
                scol.objects.link(obj)
                studio_objs.add(obj)

    export_objs = set(col.all_objects)
    for obj in list(scene.objects):
        if obj.type == "MESH" and obj not in studio_objs and not _is_studio_object(obj):
            if obj not in export_objs:
                col.objects.link(obj)
            obj.hide_render = False
            obj.hide_viewport = False


def clean_loose_geometry(scene: Any, *, export_collection: str = "COL_Export") -> dict[str, Any]:
    """Remove stray loose vertices and wire edges (eliminates needle spikes) across COL_Export meshes."""
    _require_blender()
    col = bpy.data.collections.get(export_collection)
    objs = [o for o in (col.all_objects if col else scene.objects) if o.type == "MESH" and o.data is not None]
    removed_verts = 0
    removed_edges = 0
    touched = []
    for o in objs:
        bm = bmesh.new()
        bm.from_mesh(o.data)
        loose_e = [e for e in bm.edges if len(e.link_faces) == 0]
        loose_v = [v for v in bm.verts if len(v.link_faces) == 0]
        if loose_e or loose_v:
            if loose_e:
                bmesh.ops.delete(bm, geom=loose_e, context="EDGES")
                removed_edges += len(loose_e)
            loose_v2 = [v for v in bm.verts if len(v.link_faces) == 0]
            if loose_v2:
                bmesh.ops.delete(bm, geom=loose_v2, context="VERTS")
                removed_verts += len(loose_v2)
            bm.to_mesh(o.data)
            o.data.update()
            touched.append(o.name)
        bm.free()
    return {"removed_verts": removed_verts, "removed_edges": removed_edges, "touched_objects": touched}


def audit_geometry_anomalies(scene: Any, *, export_collection: str = "COL_Export") -> dict[str, Any]:
    """Detect floating micro-fragments, needle spikes, grounding drift, and duplicate armatures."""
    _require_blender()
    audit = get_scene_audit(scene, export_collection=export_collection)
    meshes = audit.get("meshes") or []
    floating_orphans: list[str] = []
    needle_spikes: list[str] = []

    boxes = []
    for m in meshes:
        bmin = Vector(m["bbox_min"])
        bmax = Vector(m["bbox_max"])
        sz = bmax - bmin
        dims = sorted([max(sz.x, 1e-5), max(sz.y, 1e-5), max(sz.z, 1e-5)])
        boxes.append((m["name"], bmin, bmax, dims))
        # Needle spike check: tiny cross-section with > 22:1 elongation and tiny mesh
        if dims[2] > 0.015 and dims[1] < 0.004 and (dims[2] / max(dims[1], 1e-5)) > 22.0:
            needle_spikes.append(m["name"])

    def _box_gap(a_min: Vector, a_max: Vector, b_min: Vector, b_max: Vector) -> float:
        dx = max(0.0, b_min.x - a_max.x, a_min.x - b_max.x)
        dy = max(0.0, b_min.y - a_max.y, a_min.y - b_max.y)
        dz = max(0.0, b_min.z - a_max.z, a_min.z - b_max.z)
        return math.sqrt(dx * dx + dy * dy + dz * dz)

    for i, (name_i, bmin_i, bmax_i, dims_i) in enumerate(boxes):
        if dims_i[2] < 0.055 and len(boxes) > 1:
            min_gap = min(
                _box_gap(bmin_i, bmax_i, bmin_j, bmax_j)
                for j, (_, bmin_j, bmax_j, _) in enumerate(boxes)
                if j != i
            )
            if min_gap > 0.028:
                floating_orphans.append(name_i)

    min_z = float(audit.get("min_z_m", 0.0))
    grounding_drift_m = round(abs(min_z), 4)
    armatures = list(audit.get("armatures") or [])
    return {
        "floating_orphans": floating_orphans,
        "needle_spikes": needle_spikes,
        "grounding_drift_m": grounding_drift_m,
        "grounding_ok": grounding_drift_m <= 0.015,
        "mesh_count": len(meshes),
        "excessive_fragmentation": len(meshes) > 65,
        "duplicate_armatures": armatures if len(armatures) > 1 else [],
        "uv_missing_count": len(audit.get("uv_missing_objects") or []),
    }


def _setup_studio_lights_and_world(scene: Any, bounds: dict[str, Any], *, mode: str = "lookdev", studio_collection: str = "COL_Studio") -> None:
    col = get_or_create_collection(scene, studio_collection)
    # Purge ALL studio backdrops/walls, rogue scene lights, and custom lookdev cameras so nothing blocks studio framing or overexposes renders
    for obj in list(scene.objects):
        if (
            obj.type == "LIGHT"
            or _is_studio_object(obj)
            or (obj.type == "CAMERA" and obj.name.startswith(("CAM_Studio_", "CAM_Look", "look_p")))
        ):
            bpy.data.objects.remove(obj, do_unlink=True)
    for c in list(bpy.data.collections):
        if (
            c.name != studio_collection
            and c.name not in ("COL_Export", "COL_Rig")
            and "studio" in c.name.lower()
            and len(c.objects) == 0
            and len(c.children) == 0
        ):
            try:
                if c.name in scene.collection.children:
                    scene.collection.children.unlink(c)
                bpy.data.collections.remove(c)
            except Exception:
                pass

    # Pro lookdev studio world: Light Path Is Camera Ray decouples clean neutral backdrop from controlled ambient lighting!
    world = scene.world or bpy.data.worlds.new("WLD_Studio")
    scene.world = world
    world.use_nodes = True
    nt = world.node_tree
    nt.nodes.clear()
    tex_coord = nt.nodes.new("ShaderNodeTexCoord")
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    if mode == "clay":
        ramp.color_ramp.elements[0].position = 0.0
        ramp.color_ramp.elements[0].color = (0.36, 0.39, 0.44, 1.0)
        ramp.color_ramp.elements[1].position = 0.78
        ramp.color_ramp.elements[1].color = (0.56, 0.60, 0.66, 1.0)
        cam_str = 0.85
        env_str = 0.24
    else:
        ramp.color_ramp.elements[0].position = 0.0
        ramp.color_ramp.elements[0].color = (0.68, 0.71, 0.76, 1.0)
        ramp.color_ramp.elements[1].position = 0.78
        ramp.color_ramp.elements[1].color = (0.86, 0.88, 0.91, 1.0)
        cam_str = 0.92
        env_str = 0.22
    bg_cam = nt.nodes.new("ShaderNodeBackground")
    bg_cam.inputs["Strength"].default_value = cam_str
    bg_env = nt.nodes.new("ShaderNodeBackground")
    bg_env.inputs["Color"].default_value = (0.88, 0.91, 0.96, 1.0)
    bg_env.inputs["Strength"].default_value = env_str
    lp = nt.nodes.new("ShaderNodeLightPath")
    mix_sh = nt.nodes.new("ShaderNodeMixShader")
    out = nt.nodes.new("ShaderNodeOutputWorld")
    nt.links.new(tex_coord.outputs["Generated"], sep.inputs["Vector"])
    nt.links.new(sep.outputs["Z"], ramp.inputs["Fac"])
    nt.links.new(ramp.outputs["Color"], bg_cam.inputs["Color"])
    nt.links.new(lp.outputs["Is Camera Ray"], mix_sh.inputs["Fac"])
    nt.links.new(bg_env.outputs["Background"], mix_sh.inputs[1])
    nt.links.new(bg_cam.outputs["Background"], mix_sh.inputs[2])
    nt.links.new(mix_sh.outputs["Shader"], out.inputs["Surface"])

    ctr = Vector(bounds["center"])
    mn = Vector(bounds["min"])
    span = max(bounds["size"][0], bounds["size"][1], bounds["size"][2], 0.5)

    # Grounded studio contact shadow catcher (kept strictly in COL_Studio, excluded from COL_Export)
    shadow_z = mn.z - 0.001
    exp_col = bpy.data.collections.get("COL_Export")
    body_obj = bpy.data.objects.get("SM_BaseBody")
    if body_obj is not None and body_obj.type == "MESH" and (exp_col is None or body_obj.name in exp_col.all_objects):
        deps = bpy.context.evaluated_depsgraph_get()
        eo = body_obj.evaluated_get(deps)
        body_min_z = min((eo.matrix_world @ Vector(corner)).z for corner in eo.bound_box)
        shadow_z = min(0.0, body_min_z) - 0.001

    bm_g = bmesh.new()
    bmesh.ops.create_grid(bm_g, x_segments=2, y_segments=2, size=span * 2.5)
    g_mesh = bpy.data.meshes.new("SM_Studio_ShadowCatcher_Mesh")
    bm_g.to_mesh(g_mesh)
    bm_g.free()
    g_obj = bpy.data.objects.new("SM_Studio_ShadowCatcher", g_mesh)
    g_obj.location = Vector((ctr.x, ctr.y, shadow_z))
    g_obj.is_shadow_catcher = True
    col.objects.link(g_obj)

    def _add_area(name: str, offset: tuple[float, float, float], energy: float, size: float, color: tuple[float, float, float]) -> None:
        ld = bpy.data.lights.new(name, type="AREA")
        ld.energy = float(energy) * (span ** 2)
        ld.size = float(size) * span
        ld.color = color
        lo = bpy.data.objects.new(name, ld)
        lo.location = ctr + Vector(offset) * span
        direction = ctr - lo.location
        lo.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
        col.objects.link(lo)

    # Physically balanced 3:1 key-to-fill studio rig (~1.25 total diffuse irradiance -> deep shadows + rich colors!)
    _add_area("LGT_Studio_Key", (-1.15, -1.65, 1.25), 54.0, 0.95, (1.0, 0.96, 0.91))
    _add_area("LGT_Studio_Fill", (1.55, -1.25, 0.55), 17.0, 1.55, (0.86, 0.93, 1.0))
    _add_area("LGT_Studio_Rim", (0.65, 1.68, 1.35), 62.0, 0.80, (1.0, 0.98, 0.94))
    _add_area("LGT_Studio_Top", (0.0, -0.30, 1.85), 11.0, 1.20, (1.0, 1.0, 1.0))


def render_standard_views(
    scene: Any,
    out_dir: str,
    prefix: str,
    *,
    views: list[str] | None = None,
    mode: str = "lookdev",
    res: tuple[int, int] = (768, 960),
    samples: int = 32,
    export_collection: str = "COL_Export",
    studio_collection: str = "COL_Studio",
    focus_boxes: dict[str, dict[str, Any]] | None = None,
    use_gpu: bool = True,
) -> dict[str, Any]:
    """Render standardized studio multi-view + closeup PNGs cleanly in clay or full lookdev mode."""
    _require_blender()
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    scene.use_nodes = False  # prevent broken compositor trees from blanking renders
    _ensure_export_meshes_renderable(scene, export_collection=export_collection, studio_collection=studio_collection)
    bounds = _compute_collection_bounds(scene, export_collection)
    engine_device = _configure_render_engine(scene, res=res, samples=samples, use_gpu=use_gpu)
    _setup_studio_lights_and_world(scene, bounds, mode=mode, studio_collection=studio_collection)

    col = get_or_create_collection(scene, studio_collection)
    ctr = Vector(bounds["center"])
    mn = Vector(bounds["min"])
    mx = Vector(bounds["max"])
    h = max(bounds["size"][2], 0.2)
    w_xy = max(bounds["size"][0], bounds["size"][1], 0.2)
    full_span = max(h, w_xy * 1.15)
    dist_full = full_span * 1.72

    # Neutral clay override material for geometry-only reviews (non-destructive via ViewLayer override)
    clay_mat = None
    prev_override = scene.view_layers[0].material_override
    if mode == "clay":
        clay_mat = make_pbr_material(
            "MAT_Studio_ClayOverride",
            (0.68, 0.67, 0.65),
            roughness=0.58,
            metallic=0.0,
            ao_darken=0.35,
            ao_distance=max(0.08, h * 0.12),
        )
        scene.view_layers[0].material_override = clay_mat

    default_views = views or ["front", "three_quarter", "side", "rear", "closeup_top", "closeup_mid", "closeup_bottom"]

    cam_specs: dict[str, tuple[Vector, Vector, float]] = {
        "front": (ctr + Vector((0.0, -dist_full * 1.12, h * 0.04)), ctr, 55.0),
        "three_quarter": (ctr + Vector((dist_full * 0.52, -dist_full * 1.08, h * 0.06)), ctr, 58.0),
        "three_quarter_left": (ctr + Vector((-dist_full * 0.52, -dist_full * 1.08, h * 0.06)), ctr, 58.0),
        "side": (ctr + Vector((dist_full * 1.12, 0.0, h * 0.04)), ctr, 55.0),
        "rear": (ctr + Vector((0.0, dist_full * 1.12, h * 0.05)), ctr, 55.0),
        "closeup_top": (
            Vector((ctr.x + h * 0.14, mn.y - h * 0.52, mn.z + h * 0.86)),
            Vector((ctr.x, ctr.y, mn.z + h * 0.85)),
            65.0,
        ),
        "closeup_mid": (
            Vector((ctr.x + h * 0.20, mn.y - h * 0.68, mn.z + h * 0.58)),
            Vector((ctr.x, ctr.y, mn.z + h * 0.56)),
            60.0,
        ),
        "closeup_bottom": (
            Vector((ctr.x + h * 0.22, mn.y - h * 0.68, mn.z + h * 0.20)),
            Vector((ctr.x, ctr.y, mn.z + h * 0.18)),
            56.0,
        ),
    }

    for k, fb in (focus_boxes or {}).items():
        tgt = Vector(fb["target"])
        off = Vector(fb["offset"])
        lens = float(fb.get("lens", 60.0))
        cam_specs[k] = (tgt + off, tgt, lens)

    rendered: list[dict[str, Any]] = []
    cam_data = bpy.data.cameras.new("CAM_Studio_RenderData")
    cam_data.clip_start = 0.01
    cam_data.clip_end = 500.0
    cam_obj = bpy.data.objects.new("CAM_Studio_Render", cam_data)
    col.objects.link(cam_obj)
    scene.camera = cam_obj

    try:
        for vname in default_views:
            if vname not in cam_specs:
                continue
            pos, tgt, lens = cam_specs[vname]
            cam_data.lens = lens
            cam_obj.location = pos
            cam_obj.rotation_euler = (tgt - pos).to_track_quat("-Z", "Y").to_euler()
            fpath = out_path / f"{prefix}_{vname}.png"
            scene.render.filepath = str(fpath)
            try:
                bpy.ops.render.render(write_still=True, scene=scene.name)
            except RuntimeError as r_exc:
                msg = str(r_exc).lower()
                if "memory" in msg or "cuda" in msg or "optix" in msg or "device" in msg:
                    engine_device = _switch_cycles_to_cpu(scene)
                    bpy.ops.render.render(write_still=True, scene=scene.name)
                else:
                    raise
            rendered.append(
                {
                    "view": vname,
                    "mode": mode,
                    "path": str(fpath),
                    "bytes": fpath.stat().st_size if fpath.is_file() else 0,
                    "resolution": list(res),
                }
            )
    finally:
        scene.view_layers[0].material_override = prev_override
        bpy.data.objects.remove(cam_obj, do_unlink=True)
        bpy.data.cameras.remove(cam_data)

    return {
        "mode": mode,
        "engine_device": engine_device,
        "renders": rendered,
        "bounds": bounds,
    }


# ---------------------------------------------------------------------------
# 5. General Rigging, Animation & GLB Export/Reimport Validation
# ---------------------------------------------------------------------------


def create_rig_and_bind(
    scene: Any,
    bone_specs: list[dict[str, Any]],
    target_meshes: list[str] | None = None,
    *,
    armature_name: str = "ARM_Asset",
    export_collection: str = "COL_Export",
    rig_collection: str = "COL_Rig",
) -> dict[str, Any]:
    """Create a clean hierarchical armature from bone specs and bind smooth inverse-distance weights."""
    _require_blender()
    if bpy.context.window and bpy.context.window.scene != scene:
        bpy.context.window.scene = scene
    remove_object_if_exists(armature_name)
    rcol = get_or_create_collection(scene, rig_collection)
    rcol.hide_viewport = False
    rcol.hide_render = False

    def _unexclude_col(layer_col: Any, target_name: str) -> None:
        if layer_col.collection.name == target_name:
            layer_col.exclude = False
            layer_col.hide_viewport = False
        for ch in layer_col.children:
            _unexclude_col(ch, target_name)

    for vl in scene.view_layers:
        _unexclude_col(vl.layer_collection, rig_collection)

    arm_data = bpy.data.armatures.new(armature_name + "_Data")
    arm_obj = bpy.data.objects.new(armature_name, arm_data)
    rcol.objects.link(arm_obj)
    arm_obj.hide_viewport = False
    arm_obj.hide_render = False
    try:
        arm_obj.hide_set(False)
    except Exception:
        pass

    vl = scene.view_layers[0] if scene.view_layers else bpy.context.view_layer
    try:
        if bpy.context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
    except Exception:
        pass
    for o in vl.objects:
        try:
            o.select_set(False)
        except Exception:
            pass
    vl.objects.active = arm_obj
    arm_obj.select_set(True)
    vl.update()

    with bpy.context.temp_override(scene=scene, view_layer=vl, active_object=arm_obj, object=arm_obj):
        bpy.ops.object.mode_set(mode="EDIT")
        edit_bones: dict[str, Any] = {}
        for bs in bone_specs:
            eb = arm_data.edit_bones.new(str(bs["name"]))
            eb.head = Vector(bs["head"])
            eb.tail = Vector(bs["tail"])
            if (eb.tail - eb.head).length < 1e-4:
                eb.tail = eb.head + Vector((0.0, 0.0, 0.05))
            eb.use_deform = bool(bs.get("deform", True))
            edit_bones[eb.name] = eb
        for bs in bone_specs:
            p = bs.get("parent")
            if p and p in edit_bones:
                edit_bones[str(bs["name"])].parent = edit_bones[p]
        bpy.ops.object.mode_set(mode="OBJECT")

    deform_bones = [
        (b["name"], Vector(b["head"]), Vector(b["tail"]))
        for b in bone_specs
        if bool(b.get("deform", True))
    ]

    ecol = bpy.data.collections.get(export_collection)
    candidates = [o for o in (ecol.all_objects if ecol else scene.objects) if o.type == "MESH"]
    if target_meshes:
        wanted = set(target_meshes)
        candidates = [o for o in candidates if o.name in wanted]

    def _pt_seg_dist(p: Vector, a: Vector, b: Vector) -> float:
        ab = b - a
        l2 = ab.length_squared
        if l2 < 1e-8:
            return (p - a).length
        t = max(0.0, min(1.0, (p - a).dot(ab) / l2))
        return (p - (a + ab * t)).length

    bound_meshes: list[str] = []
    _rigid_markers = ("prop", "slate", "tablet", "sword", "weapon", "staff", "lantern", "shield")
    for mobj in candidates:
        mobj.vertex_groups.clear()
        vg_map = {bn: mobj.vertex_groups.new(name=bn) for (bn, _, _) in deform_bones}
        mat_w = mobj.matrix_world
        is_rigid_prop = any(mk in mobj.name.lower() for mk in _rigid_markers)
        if is_rigid_prop and len(mobj.data.vertices) > 0 and deform_bones:
            # Bind rigid held props 100% to the single closest hand/wrist (or nearest deform) bone so they never stretch or lag
            ctr_w = sum((mat_w @ v.co for v in mobj.data.vertices), Vector((0.0, 0.0, 0.0))) / len(mobj.data.vertices)
            hand_bones = [b for b in deform_bones if any(hk in b[0].lower() for hk in ("hand", "wrist"))]
            pool = hand_bones or deform_bones
            best_bn = min(pool, key=lambda b: _pt_seg_dist(ctr_w, b[1], b[2]))[0]
            vg_map[best_bn].add(list(range(len(mobj.data.vertices))), 1.0, "REPLACE")
        else:
            for v in mobj.data.vertices:
                wp = mat_w @ v.co
                dists = [(bn, _pt_seg_dist(wp, bh, bt)) for (bn, bh, bt) in deform_bones]
                dists.sort(key=lambda x: x[1])
                top = dists[:3]
                weights = [(bn, 1.0 / ((d + 0.015) ** 3.0)) for (bn, d) in top]
                total = sum(w for _, w in weights) or 1.0
                for bn, w in weights:
                    norm_w = w / total
                    if norm_w >= 0.02:
                        vg_map[bn].add([v.index], norm_w, "REPLACE")
        # Add or reuse Armature modifier
        mod = next((m for m in mobj.modifiers if m.type == "ARMATURE"), None) or mobj.modifiers.new("Armature", "ARMATURE")
        mod.object = arm_obj
        mobj.parent = arm_obj
        mobj.matrix_parent_inverse = arm_obj.matrix_world.inverted()
        bound_meshes.append(mobj.name)

    return {
        "armature_name": arm_obj.name,
        "bone_count": len(bone_specs),
        "deform_bone_count": len(deform_bones),
        "bound_meshes": bound_meshes,
    }


def create_cycle_animation(
    armature_obj: Any,
    keyframe_specs: dict[int, dict[str, tuple[float, float, float]]],
    *,
    action_name: str = "ACT_Cycle",
    frame_start: int = 1,
    frame_end: int = 24,
) -> dict[str, Any]:
    """Keyframe Euler pose rotations (degrees) on an armature for a clean looped animation cycle."""
    _require_blender()
    if armature_obj.animation_data is None:
        armature_obj.animation_data_create()
    old = bpy.data.actions.get(action_name)
    if old is not None:
        bpy.data.actions.remove(old)
    action = bpy.data.actions.new(name=action_name)
    armature_obj.animation_data.action = action

    scene = bpy.context.scene
    scene.frame_start = int(frame_start)
    scene.frame_end = int(frame_end)

    for pb in armature_obj.pose.bones:
        pb.rotation_mode = "XYZ"

    # Guarantee clean loop closure
    if frame_start in keyframe_specs and frame_end not in keyframe_specs:
        keyframe_specs = dict(keyframe_specs)
        keyframe_specs[frame_end] = dict(keyframe_specs[frame_start])

    keyed_bones: set[str] = set()
    for frame, bone_rots in sorted(keyframe_specs.items()):
        scene.frame_set(int(frame))
        for bname, deg_xyz in bone_rots.items():
            pb = armature_obj.pose.bones.get(bname)
            if pb is None:
                continue
            pb.rotation_euler = Euler(tuple(math.radians(a) for a in deg_xyz), "XYZ")
            pb.keyframe_insert(data_path="rotation_euler", frame=int(frame))
            keyed_bones.add(bname)

    scene.frame_set(int(frame_start))
    return {
        "action_name": action.name,
        "frame_start": int(frame_start),
        "frame_end": int(frame_end),
        "keyed_bones": sorted(keyed_bones),
        "loop_closed": (frame_start in keyframe_specs and frame_end in keyframe_specs),
    }


def export_and_verify_glb(
    scene: Any,
    glb_path: str,
    *,
    export_collection: str = "COL_Export",
    rig_collection: str = "COL_Rig",
) -> dict[str, Any]:
    """Pack textures, export GLB, and verify by re-importing into a fresh temporary scene."""
    _require_blender()
    out = Path(glb_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        bpy.ops.file.pack_all()
    except Exception:
        pass

    bpy.ops.object.select_all(action="DESELECT")
    selected = []
    selected_meshes = []
    seen_sel: set[str] = set()
    for cname in (export_collection, rig_collection):
        col = bpy.data.collections.get(cname)
        if col:
            for obj in col.all_objects:
                if obj.type in ("MESH", "ARMATURE") and not obj.hide_render and not _is_studio_object(obj):
                    if obj.name not in seen_sel:
                        obj.select_set(True)
                        seen_sel.add(obj.name)
                        selected.append(obj.name)
                        if obj.type == "MESH":
                            selected_meshes.append(obj.name)
    for obj in scene.objects:
        if obj.type == "ARMATURE" and not obj.hide_render and not _is_studio_object(obj) and obj.name not in seen_sel:
            obj.select_set(True)
            seen_sel.add(obj.name)
            selected.append(obj.name)

    if not selected_meshes:
        return {
            "valid": False,
            "glb_path": str(out),
            "file_bytes": 0,
            "is_glb_header": False,
            "exported_objects": [],
            "reimport_mesh_count": 0,
            "reimport_triangles": 0,
            "reimport_material_count": 0,
            "reimport_armature_count": 0,
            "reimport_error": "No exportable meshes found in export collection.",
        }

    bpy.ops.export_scene.gltf(
        filepath=str(out),
        export_format="GLB",
        use_selection=True,
        export_apply=False,
        export_texcoords=True,
        export_normals=True,
        export_materials="EXPORT",
        export_animations=True,
    )

    file_bytes = out.stat().st_size if out.is_file() else 0
    is_glb = False
    if file_bytes >= 12:
        is_glb = out.read_bytes()[:4] == b"glTF"

    # Re-import into a genuinely empty temporary scene to confirm zero data loss
    prev_scene = bpy.context.window.scene if bpy.context.window else scene
    tmp_scene = bpy.data.scenes.new("__GLB_VERIFY_TMP__")
    for obj in list(tmp_scene.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    reimport_meshes = 0
    reimport_tris = 0
    reimport_mats = 0
    reimport_armatures = 0
    reimport_error = None
    try:
        if bpy.context.window:
            bpy.context.window.scene = tmp_scene
        bpy.ops.import_scene.gltf(filepath=str(out))
        mats: set[str] = set()
        for o in tmp_scene.objects:
            if o.type == "MESH":
                reimport_meshes += 1
                reimport_tris += sum(max(1, len(p.vertices) - 2) for p in o.data.polygons)
                mats.update(m.name for m in o.data.materials if m)
            elif o.type == "ARMATURE":
                reimport_armatures += 1
        reimport_mats = len(mats)
    except Exception as exc:
        reimport_error = str(exc)
    finally:
        for o in list(tmp_scene.objects):
            bpy.data.objects.remove(o, do_unlink=True)
        if bpy.context.window:
            bpy.context.window.scene = prev_scene
        bpy.data.scenes.remove(tmp_scene)

    valid = bool(
        len(selected_meshes) > 0
        and is_glb
        and file_bytes > 1024
        and reimport_meshes > 0
        and reimport_tris > 0
        and reimport_error is None
    )
    return {
        "valid": valid,
        "glb_path": str(out),
        "file_bytes": file_bytes,
        "is_glb_header": is_glb,
        "exported_objects": selected,
        "reimport_mesh_count": reimport_meshes,
        "reimport_triangles": reimport_tris,
        "reimport_material_count": reimport_mats,
        "reimport_armature_count": reimport_armatures,
        "reimport_error": reimport_error,
    }


def collect_resource_snapshot() -> dict[str, Any]:
    """Collect lightweight GPU VRAM/temperature/utilization and system RAM metrics."""
    import os
    import subprocess

    snap: dict[str, Any] = {"gpus": [], "min_free_vram_mib": None, "ram_available_mib": None, "ram_total_mib": None}
    for exe in ("nvidia-smi", "/usr/bin/nvidia-smi"):
        try:
            out = subprocess.check_output(
                [
                    exe,
                    "--query-gpu=index,name,memory.total,memory.used,memory.free,temperature.gpu,utilization.gpu",
                    "--format=csv,noheader,nounits",
                ],
                text=True,
                timeout=3.0,
            )
            gpus = []
            for line in out.splitlines():
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 7:
                    gpus.append(
                        {
                            "index": int(parts[0]),
                            "name": parts[1],
                            "vram_total_mib": int(parts[2]),
                            "vram_used_mib": int(parts[3]),
                            "vram_free_mib": int(parts[4]),
                            "temp_c": int(parts[5]),
                            "util_pct": int(parts[6]),
                        }
                    )
            if gpus:
                snap["gpus"] = gpus
                snap["min_free_vram_mib"] = min(g["vram_free_mib"] for g in gpus)
                break
        except Exception:
            continue

    meminfo = Path("/proc/meminfo")
    if meminfo.is_file():
        for line in meminfo.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.startswith("MemTotal:"):
                snap["ram_total_mib"] = int(line.split()[1]) // 1024
            elif line.startswith("MemAvailable:"):
                snap["ram_available_mib"] = int(line.split()[1]) // 1024
    if os.path.exists("/dev/nvidia0"):
        snap["nvml_device_present"] = True
    return snap
