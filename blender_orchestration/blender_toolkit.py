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
        m.use_even_offset = True
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

    old = bpy.data.images.get(name)
    if old is not None:
        bpy.data.images.remove(old)
    img = bpy.data.images.new(name, width=width, height=height, alpha=True)
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
    base_color: tuple[float, float, float] | tuple[float, float, float, float],
    *,
    roughness: float = 0.45,
    metallic: float = 0.0,
    subsurface: float = 0.0,
    coat: float = 0.0,
    specular: float = 0.5,
    emission_color: tuple[float, float, float] | None = None,
    emission_strength: float = 0.0,
    ao_darken: float = 0.22,
    ao_distance: float = 0.16,
    rim_color: tuple[float, float, float] | None = None,
    rim_strength: float = 0.0,
    noise_bump: float = 0.0,
    noise_scale: float = 35.0,
    image_texture: Any | None = None,
) -> Any:
    """Create a rich, glTF-compatible Blender 5.3 stylized PBR material."""
    _require_blender()
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name=name)
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()

    out = nt.nodes.new("ShaderNodeOutputMaterial")
    out.location = (650, 0)
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (300, 0)

    rgba = (float(base_color[0]), float(base_color[1]), float(base_color[2]), 1.0)
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
    if emission_color is not None and emission_strength > 0.0:
        if "Emission Color" in bsdf.inputs:
            bsdf.inputs["Emission Color"].default_value = (
                float(emission_color[0]),
                float(emission_color[1]),
                float(emission_color[2]),
                1.0,
            )
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
        mix_rim.inputs[7].default_value = (float(rim_color[0]), float(rim_color[1]), float(rim_color[2]), 1.0)
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


def _compute_collection_bounds(scene: Any, export_collection: str = "COL_Export") -> dict[str, Any]:
    col = bpy.data.collections.get(export_collection)
    meshes = [o for o in (col.all_objects if col else scene.objects) if o.type == "MESH" and not o.hide_render]
    if not meshes:
        return {"empty": True, "min": (0.0, 0.0, 0.0), "max": (0.0, 0.0, 1.0), "center": (0.0, 0.0, 0.5), "size": (0.5, 0.5, 1.0)}
    deps = bpy.context.evaluated_depsgraph_get()
    xs, ys, zs = [], [], []
    for o in meshes:
        eo = o.evaluated_get(deps)
        for corner in eo.bound_box:
            wc = eo.matrix_world @ Vector(corner)
            xs.append(wc.x)
            ys.append(wc.y)
            zs.append(wc.z)
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
    mesh_objs = [o for o in objs if o.type == "MESH" and not o.hide_render]
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

    # AgX color management
    try:
        scene.view_settings.view_transform = "AgX"
        scene.view_settings.look = "AgX - Medium High Contrast"
    except Exception:
        scene.view_settings.view_transform = "Standard"
    scene.view_settings.exposure = 0.15

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
                        effective = f"GPU:{backend}"
                        break
                except Exception:
                    continue
        except Exception:
            pass
    if effective == "CPU":
        scene.cycles.device = "CPU"
    return effective


def _setup_studio_lights_and_world(scene: Any, bounds: dict[str, Any], *, studio_collection: str = "COL_Studio") -> None:
    col = get_or_create_collection(scene, studio_collection)
    for obj in list(col.objects):
        if obj.type in ("LIGHT", "CAMERA") or obj.name.startswith(("LGT_Studio_", "CAM_Studio_", "SM_Studio_")):
            bpy.data.objects.remove(obj, do_unlink=True)

    # Soft studio gradient world (high contrast for both dark & light elements)
    world = scene.world or bpy.data.worlds.new("WLD_Studio")
    scene.world = world
    world.use_nodes = True
    nt = world.node_tree
    nt.nodes.clear()
    bg = nt.nodes.new("ShaderNodeBackground")
    bg.inputs["Color"].default_value = (0.32, 0.35, 0.40, 1.0)
    bg.inputs["Strength"].default_value = 0.55
    out = nt.nodes.new("ShaderNodeOutputWorld")
    nt.links.new(bg.outputs["Background"], out.inputs["Surface"])

    ctr = Vector(bounds["center"])
    span = max(bounds["size"][0], bounds["size"][1], bounds["size"][2], 0.5)

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

    # Balanced 3-point studio lighting (warm key, cool fill, crisp rim)
    _add_area("LGT_Studio_Key", (1.35, -1.65, 1.25), 95.0, 1.1, (1.0, 0.96, 0.90))
    _add_area("LGT_Studio_Fill", (-1.55, -1.25, 0.65), 42.0, 1.5, (0.86, 0.92, 1.0))
    _add_area("LGT_Studio_Rim", (-0.6, 1.75, 1.35), 75.0, 0.9, (0.95, 0.98, 1.0))
    _add_area("LGT_Studio_Top", (0.0, -0.3, 1.85), 28.0, 1.4, (1.0, 1.0, 1.0))


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
    bounds = _compute_collection_bounds(scene, export_collection)
    engine_device = _configure_render_engine(scene, res=res, samples=samples, use_gpu=use_gpu)
    _setup_studio_lights_and_world(scene, bounds, studio_collection=studio_collection)

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
        "front": (ctr + Vector((0.0, -dist_full, h * 0.04)), ctr, 50.0),
        "three_quarter": (ctr + Vector((dist_full * 0.52, -dist_full * 0.86, h * 0.08)), ctr, 50.0),
        "three_quarter_left": (ctr + Vector((-dist_full * 0.52, -dist_full * 0.86, h * 0.08)), ctr, 50.0),
        "side": (ctr + Vector((dist_full * 0.98, 0.0, h * 0.04)), ctr, 50.0),
        "rear": (ctr + Vector((0.0, dist_full, h * 0.05)), ctr, 50.0),
        "closeup_top": (
            Vector((ctr.x + h * 0.16, mn.y - h * 0.55, mn.z + h * 0.85)),
            Vector((ctr.x, ctr.y, mn.z + h * 0.84)),
            65.0,
        ),
        "closeup_mid": (
            Vector((ctr.x + h * 0.22, mn.y - h * 0.72, mn.z + h * 0.58)),
            Vector((ctr.x, ctr.y, mn.z + h * 0.56)),
            58.0,
        ),
        "closeup_bottom": (
            Vector((ctr.x + h * 0.24, mn.y - h * 0.72, mn.z + h * 0.20)),
            Vector((ctr.x, ctr.y, mn.z + h * 0.18)),
            55.0,
        ),
    }

    for k, fb in (focus_boxes or {}).items():
        tgt = Vector(fb["target"])
        off = Vector(fb["offset"])
        lens = float(fb.get("lens", 60.0))
        cam_specs[k] = (tgt + off, tgt, lens)

    rendered: list[dict[str, Any]] = []
    cam_data = bpy.data.cameras.new("CAM_Studio_RenderData")
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
            bpy.ops.render.render(write_still=True, scene=scene.name)
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
    remove_object_if_exists(armature_name)
    rcol = get_or_create_collection(scene, rig_collection)
    arm_data = bpy.data.armatures.new(armature_name + "_Data")
    arm_obj = bpy.data.objects.new(armature_name, arm_data)
    rcol.objects.link(arm_obj)

    bpy.context.view_layer.objects.active = arm_obj
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
    for mobj in candidates:
        mobj.vertex_groups.clear()
        vg_map = {bn: mobj.vertex_groups.new(name=bn) for (bn, _, _) in deform_bones}
        mat_w = mobj.matrix_world
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
    for cname in (export_collection, rig_collection):
        col = bpy.data.collections.get(cname)
        if col:
            for obj in col.all_objects:
                if obj.type in ("MESH", "ARMATURE") and not obj.hide_render:
                    obj.select_set(True)
                    selected.append(obj.name)
                    if obj.type == "MESH":
                        selected_meshes.append(obj.name)

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
