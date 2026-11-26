"""Blender-side measurement utilities executed by the harness (QA only; never creates asset geometry).

Imported inside Blender. Objects are identified through the naming contract every work order
states: export meshes are named ``SM_<Region>_<Part>`` (e.g. ``SM_Head``, ``SM_Eye_L``,
``SM_Ear_R``, ``SM_Hair_Bangs``).
"""

from __future__ import annotations

import re
from typing import Any

try:  # pragma: no cover - only available inside Blender
    import bpy
    from mathutils import Matrix, Vector
    from mathutils.bvhtree import BVHTree
except Exception:  # pragma: no cover
    bpy = None

DEFAULT_PRIMITIVE_RE = re.compile(r"^(Cube|Sphere|UVSphere|Icosphere|Cylinder|Cone|Torus|Plane|Circle|Suzanne|Mesh)(\.\d{3})?$")
DUP_SUFFIX_RE = re.compile(r"\.\d{3}$")


def _export_meshes(scene: Any, collection: str = "COL_Export") -> list[Any]:
    col = bpy.data.collections.get(collection)
    if col is None:
        return []
    return [o for o in col.all_objects if o.type == "MESH" and o.name in scene.objects]


def _world_verts(obj: Any, group_index: int | None = None) -> list[Any]:
    deps = bpy.context.evaluated_depsgraph_get()
    ev = obj.evaluated_get(deps)
    mesh = ev.to_mesh()
    try:
        mw = obj.matrix_world
        return [mw @ v.co for v in mesh.vertices
                if group_index is None or any(g.group == group_index and g.weight > 0.02 for g in v.groups)]
    finally:
        ev.to_mesh_clear()


def _bvh(obj: Any) -> Any:
    deps = bpy.context.evaluated_depsgraph_get()
    ev = obj.evaluated_get(deps)
    mesh = ev.to_mesh()
    try:
        mw = obj.matrix_world
        verts = [mw @ v.co for v in mesh.vertices]
        polys = [tuple(p.vertices) for p in mesh.polygons]
        return BVHTree.FromPolygons(verts, polys) if polys else None
    finally:
        ev.to_mesh_clear()


def snapshot(scene: Any) -> dict[str, Any]:
    meshes = _export_meshes(scene)
    tris = 0
    for o in meshes:
        tris += sum(max(0, len(p.vertices) - 2) for p in o.data.polygons)
    return {"objects": sorted(o.name for o in scene.objects), "export_meshes": sorted(o.name for o in meshes),
            "export_triangles_base": tris}


def geometry_guard(scene: Any, before: dict[str, Any], *, max_new_objects: int = 40, max_export_meshes: int = 90,
                   max_triangles: int = 220000) -> dict[str, Any]:
    """Detect accumulation after a producer script. Returns problems (fatal) and warnings."""
    after = snapshot(scene)
    new = sorted(set(after["objects"]) - set(before["objects"]))
    problems: list[str] = []
    warnings: list[str] = []
    dups = [n for n in new if DUP_SUFFIX_RE.search(n)]
    if dups:
        problems.append(f"duplicate objects created {dups[:8]} - delete the old object before rebuilding it")
    prims = [n for n in after["export_meshes"] if DEFAULT_PRIMITIVE_RE.match(n)]
    if prims:
        problems.append(f"unnamed default primitives in COL_Export {prims[:8]} - name every mesh SM_<Region>_<Part>")
    if len(new) > max_new_objects:
        problems.append(f"{len(new)} new objects in one script (limit {max_new_objects}) - join repeated details")
    if len(after["export_meshes"]) > max_export_meshes:
        problems.append(f"{len(after['export_meshes'])} export meshes (limit {max_export_meshes})")
    if after["export_triangles_base"] > max_triangles:
        problems.append(f"{after['export_triangles_base']} base triangles (limit {max_triangles})")
    bad_names = [n for n in after["export_meshes"] if not n.startswith("SM_")]
    if bad_names:
        warnings.append(f"meshes not following SM_<Region>_<Part>: {bad_names[:8]}")
    return {"ok": not problems, "problems": problems, "warnings": warnings, "new_objects": new[:60],
            "removed_objects": sorted(set(before["objects"]) - set(after["objects"]))[:60],
            "export_mesh_count": len(after["export_meshes"]), "export_triangles_base": after["export_triangles_base"]}


def relink_stray_meshes(scene: Any, before: dict[str, Any]) -> list[str]:
    """New meshes created outside COL_Export are linked into it so they are audited and exported."""
    col = bpy.data.collections.get("COL_Export")
    moved = []
    if col is None:
        return moved
    managed = {c.name for c in (bpy.data.collections.get("COL_Studio"), bpy.data.collections.get("COL_Rig"), col) if c}
    for name in sorted(set(o.name for o in scene.objects) - set(before["objects"])):
        o = scene.objects.get(name)
        if o is None or o.type != "MESH" or o.name.startswith("SM_Studio"):
            continue
        if not any(c.name in managed for c in o.users_collection):
            col.objects.link(o)
            moved.append(o.name)
    return moved


def _find(meshes: list[Any], *tokens: str) -> list[Any]:
    out = []
    for o in meshes:
        low = o.name.lower()
        if any(t in low for t in tokens):
            out.append(o)
    return out


def _bbox(verts: list[Any]) -> tuple[list[float], list[float]]:
    return ([min(v[i] for v in verts) for i in range(3)], [max(v[i] for v in verts) for i in range(3)])


def _base_head(meshes: list[Any]) -> tuple[Any, list[Any]]:
    body = next((o for o in meshes if o.name == 'SM_BaseBody'), None)
    group = body.vertex_groups.get('ANAT_Head') if body else None
    return body, _world_verts(body, group.index) if group else []


def head_region(scene: Any) -> dict[str, Any]:
    """Use a separate head mesh, the continuous body's landmark group, or a framing fallback."""
    meshes = _export_meshes(scene)
    heads = [o for o in meshes if o.name.lower().startswith("sm_head")]
    verts: list[Any] = []
    for o in heads:
        verts.extend(_world_verts(o))
    source = "SM_Head"
    if not verts:
        _, verts = _base_head(meshes)
        source = 'SM_BaseBody/ANAT_Head'
    if not verts:
        source = "top_slice"
        allv: list[Any] = []
        for o in meshes:
            allv.extend(_world_verts(o))
        if not allv:
            return {"found": False}
        zmax = max(v.z for v in allv)
        zmin = min(v.z for v in allv)
        cut = zmax - 0.13 * (zmax - zmin)
        verts = [v for v in allv if v.z >= cut]
    mn, mx = _bbox(verts)
    return {"found": True, "source": source, "min": mn, "max": mx,
            "center": [(mn[i] + mx[i]) / 2 for i in range(3)], "height": mx[2] - mn[2]}


def face_focus_boxes(scene: Any) -> dict[str, dict[str, Any]]:
    hr = head_region(scene)
    if not hr.get("found"):
        return {}
    c = hr["center"]
    h = max(hr["height"], 0.12)
    target = (c[0], c[1], c[2] - 0.05 * h)
    d = h * 4.2
    return {
        "face_front": {"target": target, "offset": (0.0, -d, 0.02 * h), "lens": 85.0},
        "face_three_quarter": {"target": target, "offset": (d * 0.66, -d * 0.74, 0.04 * h), "lens": 85.0},
        "face_side": {"target": target, "offset": (d, 0.0, 0.02 * h), "lens": 85.0},
    }


def face_report(scene: Any) -> dict[str, Any]:
    """Automated facial-placement findings: reversed face, embedded/protruding/floating parts."""
    meshes = _export_meshes(scene)
    heads = [o for o in meshes if o.name.lower().startswith("sm_head")]
    findings: list[dict[str, Any]] = []
    if heads:
        head = heads[0]
        hv = _world_verts(head)
    else:
        head, hv = _base_head(meshes)
    if not hv:
        return {"checked": False, "reason": "no SM_Head* mesh or continuous-body ANAT_Head region", "findings": findings}
    tree = _bvh(head)
    hmn, hmx = _bbox(hv)
    hc = [(hmn[i] + hmx[i]) / 2 for i in range(3)]
    eyes = [o for o in _find(meshes, "eye") if not any(t in o.name.lower() for t in ("brow", "lash", "lid"))]
    eye_info = []
    for e in eyes:
        ev = _world_verts(e)
        if not ev:
            continue
        mn, mx = _bbox(ev)
        ctr = [(mn[i] + mx[i]) / 2 for i in range(3)]
        hit = tree.ray_cast(Vector((ctr[0], hmn[1] - 0.3, ctr[2])), Vector((0, 1, 0)), 1.0) if tree else (None,)
        surface_y = hit[0].y if hit and hit[0] is not None else None
        eye_info.append({"name": e.name, "center": [round(x, 4) for x in ctr], "front_y": round(mn[1], 4),
                         "head_surface_y": round(surface_y, 4) if surface_y is not None else None})
        if ctr[1] > hc[1]:
            findings.append({"code": "REVERSED_FACE", "object": e.name, "severity": "CRITICAL",
                             "detail": f"eye centre y={ctr[1]:.3f} is behind head centre y={hc[1]:.3f}; front is -Y"})
        if surface_y is not None:
            if mn[1] > surface_y + 0.004:
                findings.append({"code": "EMBEDDED_EYE", "object": e.name, "severity": "MAJOR",
                                 "detail": f"eye front y={mn[1]:.3f} lies {mn[1] - surface_y:.3f} m behind the head surface"})
            elif mn[1] < surface_y - 0.015:
                findings.append({"code": "PROTRUDING_EYE", "object": e.name, "severity": "MAJOR",
                                 "detail": f"eye pokes {surface_y - mn[1]:.3f} m out of the head surface"})
    if len(eye_info) >= 2:
        xs = sorted(eye_info, key=lambda i: i["center"][0])
        l, r = xs[0]["center"], xs[-1]["center"]
        if abs(l[0] + r[0] - 2 * hc[0]) > 0.012 or abs(l[2] - r[2]) > 0.008:
            findings.append({"code": "ASYMMETRIC_EYES", "object": "eyes", "severity": "MINOR",
                             "detail": f"eye centres {l} / {r} not mirrored about x={hc[0]:.3f}"})
    for part in _find(meshes, "eye", "ear", "nose", "brow", "lash", "lid", "mouth", "lip"):
        if part == head or part in heads or tree is None:
            continue
        pv = _world_verts(part)
        if not pv:
            continue
        step = max(1, len(pv) // 400)
        dmin = min((tree.find_nearest(v)[3] or 9.0) for v in pv[::step])
        if dmin > 0.006:
            findings.append({"code": "FLOATING_FACIAL_PART", "object": part.name, "severity": "MAJOR",
                             "detail": f"closest point is {dmin:.3f} m from the head surface"})
    return {"checked": True, "head": head.name, "head_bounds": [hmn, hmx], "eyes": eye_info, "findings": findings}


def proportion_report(scene: Any) -> dict[str, Any]:
    meshes = _export_meshes(scene)
    allv: list[Any] = []
    for o in meshes:
        allv.extend(_world_verts(o))
    if not allv:
        return {"measured": False}
    mn, mx = _bbox(allv)
    hr = head_region(scene)
    total = mx[2] - mn[2]
    out = {"measured": True, "height_m": round(total, 4), "min_z": round(mn[2], 4),
           "width_m": round(mx[0] - mn[0], 4), "depth_m": round(mx[1] - mn[1], 4)}
    if hr.get("found") and hr["source"] == "SM_Head" and hr["height"] > 0:
        out["head_height_m"] = round(hr["height"], 4)
        out["heads_tall"] = round(total / hr["height"], 2)
    elif hr.get('found') and hr['source'] == 'SM_BaseBody/ANAT_Head' and hr['height'] > 0:
        out['head_measurement_source'] = hr['source']
        out['estimated_head_height_m'] = round(hr['height'], 4)
        out['estimated_heads_tall'] = round(total / hr['height'], 2)
    return out


def character_export_inventory(objects: Any) -> dict[str, Any]:
    """Collect comparable facts from source meshes or a just-reimported GLB scene."""
    meshes = [o for o in objects if o.type == 'MESH' and not o.hide_render]
    verts = [v for o in meshes for v in _world_verts(o)]
    height = (max(v.z for v in verts) - min(v.z for v in verts)) if verts else 0.0
    materials = {m for o in meshes for m in o.data.materials if m}
    textures = {n.image for m in materials if m.use_nodes and m.node_tree for n in m.node_tree.nodes
                if n.type == 'TEX_IMAGE' and n.image}
    rigs = [o for o in objects if o.type == 'ARMATURE' and len(o.data.bones)]
    actions = set()
    for rig in rigs:
        anim = rig.animation_data
        if anim:
            if anim.action:
                actions.add(anim.action.name)
            actions.update(s.action.name for t in anim.nla_tracks for s in t.strips if s.action)
    return {'mesh_count': len(meshes), 'height_m': height, 'material_count': len(materials),
            'unmaterialed_meshes': [o.name for o in meshes if not any(o.data.materials)],
            'texture_count': len(textures), 'missing_images': sorted(i.name for i in textures if not i.has_data),
            'rig_count': len(rigs), 'skins': sum(any(m.type == 'ARMATURE' and m.object in rigs for m in o.modifiers)
                                                for o in meshes), 'actions': sorted(actions)}


def compare_character_export(source: dict[str, Any], imported: dict[str, Any], mode: str) -> dict[str, Any]:
    import re

    actions = {re.sub(r'\.\d{3}$', '', name) for name in imported['actions']}
    checks = {
        'mesh': imported['mesh_count'] >= source['mesh_count'] > 0,
        'scale': source['height_m'] > 0 and abs(imported['height_m'] - source['height_m']) <= 0.05 * source['height_m'],
        'materials': imported['material_count'] > 0 and not imported['unmaterialed_meshes'],
        'textures': imported['texture_count'] >= source['texture_count'] and not imported['missing_images'],
        'rig': mode == 'static' or (imported['rig_count'] == 1 and imported['skins'] > 0),
        'animations': mode != 'animated' or {'ACT_Idle', 'ACT_Walk', 'ACT_Run'} <= actions,
    }
    return {'valid': all(checks.values()), 'checks': checks, 'source': source, 'reimported': imported}


def motion_review(scene: Any, directory: str, *, animation: bool, samples: int) -> dict[str, Any]:
    from .. import blender_toolkit as bt

    rigs = [o for o in scene.objects if o.type == 'ARMATURE']
    if len(rigs) != 1 or not rigs[0].data.bones:
        return {'ok': False, 'reason': 'Expected one non-empty armature'}
    rig = rigs[0]
    deform = {b.name for b in rig.data.bones if b.use_deform}
    for obj in _export_meshes(scene):
        if obj.parent == rig and obj.parent_type == 'BONE':
            continue
        groups = {g.index for g in obj.vertex_groups if g.name in deform}
        if (not any(m.type == 'ARMATURE' and m.object == rig for m in obj.modifiers)
                or any(not any(g.group in groups and g.weight > 0 for g in v.groups) for v in obj.data.vertices)):
            return {'ok': False, 'reason': f'Unbound mesh or unweighted vertices: {obj.name}'}
    renders = []

    def capture(label):
        items = bt.render_standard_views(scene, directory, label, views=['three_quarter'], res=(360, 450),
                                         samples=samples, use_gpu=False)['renders']
        renders.extend([item | {'view': label} for item in items])

    rig.data.pose_position = 'POSE'
    if animation:
        anim = rig.animation_data_create()
        for name in ('ACT_Idle', 'ACT_Walk', 'ACT_Run'):
            action = bpy.data.actions.get(name)
            if not action or not action.slots or action.frame_range[1] <= action.frame_range[0]:
                return {'ok': False, 'reason': f'Missing keyed action: {name}'}
            anim.action = action
            anim.action_slot = action.slots[0]
            low, high = action.frame_range
            for i, fraction in enumerate((0, .25, .5, 1)):
                scene.frame_set(round(low + fraction * (high - low)))
                capture(f'{name}_{i}')
    else:
        if not any(b.matrix_basis != Matrix.Identity(4) for b in rig.pose.bones):
            return {'ok': False, 'reason': 'Missing non-neutral deformation test pose'}
        capture('posed')
        rig.data.pose_position = 'REST'
        capture('rest')
    return {'ok': True, 'renders': renders}
