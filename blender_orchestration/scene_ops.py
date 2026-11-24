"""Reusable, idempotent stage-specific Blender bpy script builders."""

from __future__ import annotations

import json
from pathlib import Path


def build_init_scene_code(scene_name: str, asset_id: str) -> str:
    """Idempotently initialize a clean named Blender scene and COL_* hierarchy.

    Repeated invocations activate the existing scene and verify zero duplicate
    scenes, collections, or numeric `.001` artifacts are created.
    """
    return f'''
import bpy, json

SCENE_NAME = {json.dumps(scene_name)}
ASSET_ID = {json.dumps(asset_id)}
REQUIRED_COLS = [
    "COL_Reference",
    "COL_Blockout",
    "COL_HighPoly",
    "COL_LowPoly",
    "COL_Collision",
    "COL_Lights",
    "COL_Export",
]

scene = bpy.data.scenes.get(SCENE_NAME)
created_scene = False
if scene is None:
    scene = bpy.data.scenes.new(SCENE_NAME)
    created_scene = True
    # Remove default starter objects in unused initial Scene if still untouched
    default_scene = bpy.data.scenes.get("Scene")
    if default_scene and len(default_scene.objects) <= 3 and set(o.name for o in default_scene.objects) <= {{"Cube", "Light", "Camera"}}:
        for obj in list(default_scene.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        bpy.data.scenes.remove(default_scene)

bpy.context.window.scene = scene
scene["managed_by"] = "blender_orchestration"
scene["asset_id"] = ASSET_ID

# Also clean default starter objects if initialized inside active empty scene
if not scene.get("starter_cleaned"):
    for starter in ("Cube", "Light", "Camera"):
        obj = scene.objects.get(starter)
        if obj is not None and len(scene.objects) <= 3:
            bpy.data.objects.remove(obj, do_unlink=True)
    scene["starter_cleaned"] = True

created_cols = []
for cname in REQUIRED_COLS:
    col = bpy.data.collections.get(cname)
    if col is None:
        col = bpy.data.collections.new(cname)
        created_cols.append(cname)
    if col.name not in [c.name for c in scene.collection.children]:
        scene.collection.children.link(col)

scene.unit_settings.system = 'METRIC'
scene.unit_settings.scale_length = 1.0

payload = {{
    "scene_name": scene.name,
    "asset_id": scene.get("asset_id"),
    "created_scene": created_scene,
    "created_collections": created_cols,
    "scene_count": len(bpy.data.scenes),
    "collection_names": sorted(c.name for c in scene.collection.children),
    "object_count": len(scene.objects),
}}
print("__ORCH_JSON__:" + json.dumps(payload))
'''


def build_stylized_crate_geometry_code(scene_name: str, asset_id: str) -> str:
    """Build a beveled, readable stylized fantasy crate idempotently across COL_Export & COL_Collision."""
    return f'''
import bpy, bmesh, json, math
from mathutils import Vector

SCENE_NAME = {json.dumps(scene_name)}
ASSET_ID = {json.dumps(asset_id)}
OWNED_EXPORT = [
    "SM_FantasyCrate_WoodBody",
    "SM_FantasyCrate_IronBands",
    "SM_FantasyCrate_RuneLock",
]
OWNED_COLLISION = ["SM_FantasyCrate_COL"]

scene = bpy.data.scenes[SCENE_NAME]
bpy.context.window.scene = scene
col_export = bpy.data.collections["COL_Export"]
col_col = bpy.data.collections["COL_Collision"]

def remove_owned(name):
    obj = bpy.data.objects.get(name)
    if obj is not None:
        data = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        if data is not None and data.users == 0:
            bpy.data.meshes.remove(data)

for n in OWNED_EXPORT + OWNED_COLLISION:
    remove_owned(n)

def link_only(obj, target_col):
    for c in list(obj.users_collection):
        c.objects.unlink(obj)
    target_col.objects.link(obj)
    obj["asset_id"] = ASSET_ID
    obj["production_stage"] = "modeling"

def add_box(bm, cx, cy, cz, sx, sy, sz, top_taper=1.0):
    hx, hy, hz = sx * 0.5, sy * 0.5, sz * 0.5
    verts_bot = [
        bm.verts.new((cx - hx, cy - hy, cz - hz)),
        bm.verts.new((cx + hx, cy - hy, cz - hz)),
        bm.verts.new((cx + hx, cy + hy, cz - hz)),
        bm.verts.new((cx - hx, cy + hy, cz - hz)),
    ]
    verts_top = [
        bm.verts.new((cx - hx * top_taper, cy - hy * top_taper, cz + hz)),
        bm.verts.new((cx + hx * top_taper, cy - hy * top_taper, cz + hz)),
        bm.verts.new((cx + hx * top_taper, cy + hy * top_taper, cz + hz)),
        bm.verts.new((cx - hx * top_taper, cy + hy * top_taper, cz + hz)),
    ]
    bm.faces.new((verts_bot[3], verts_bot[2], verts_bot[1], verts_bot[0]))
    bm.faces.new((verts_top[0], verts_top[1], verts_top[2], verts_top[3]))
    for i in range(4):
        j = (i + 1) % 4
        bm.faces.new((verts_bot[i], verts_bot[j], verts_top[j], verts_top[i]))

# 1. Stylized Wood Body: tapered inner core + 4-plank top lid + recessed side slats + corner timber ribs
bm_wood = bmesh.new()
# Core recessed wood chest box (sits at Z=0.05..0.78)
add_box(bm_wood, 0.0, 0.0, 0.415, 0.78, 0.78, 0.73, top_taper=1.06)
# Side wooden planks on all 4 sides for genuine silhouette/plank relief
for sign in (-1.0, 1.0):
    for k, pz in enumerate((0.20, 0.41, 0.62)):
        scale = 0.99 + k * 0.02
        add_box(bm_wood, 0.0, sign * 0.405 * scale, pz, 0.70 * scale, 0.04, 0.16)
        add_box(bm_wood, sign * 0.405 * scale, 0.0, pz, 0.04, 0.70 * scale, 0.16)
# Lid planks (3 raised timber slats forming slightly crowned lid)
for ix, px in enumerate((-0.26, 0.0, 0.26)):
    cz = 0.805 + (0.015 if ix == 1 else 0.0)
    add_box(bm_wood, px, 0.0, cz, 0.23, 0.84, 0.08)

bmesh.ops.recalc_face_normals(bm_wood, faces=bm_wood.faces)
mesh_wood = bpy.data.meshes.new("SM_FantasyCrate_WoodBody")
bm_wood.to_mesh(mesh_wood)
bm_wood.free()

obj_wood = bpy.data.objects.new("SM_FantasyCrate_WoodBody", mesh_wood)
link_only(obj_wood, col_export)
bev_w = obj_wood.modifiers.new("Bevel_Stylized", 'BEVEL')
bev_w.width = 0.008
bev_w.segments = 1
bev_w.limit_method = 'ANGLE'

# 2. Forged Iron Corner Braces, Rim Bands, Rivets & Carry Side Loops
bm_iron = bmesh.new()
# Bottom & Top forged iron bands hugging the tapered crate
add_box(bm_iron, 0.0, 0.0, 0.06, 0.88, 0.88, 0.12, top_taper=1.01)
add_box(bm_iron, 0.0, 0.0, 0.74, 0.92, 0.92, 0.11, top_taper=1.02)
# 4 heavy corner vertical iron pillars with subtle outward flare
for sx in (-1.0, 1.0):
    for sy in (-1.0, 1.0):
        add_box(bm_iron, sx * 0.41, sy * 0.41, 0.41, 0.11, 0.11, 0.82, top_taper=1.05)
        # Corner top & bottom stud rivets
        for rz in (0.08, 0.74):
            add_box(bm_iron, sx * 0.465, sy * 0.41, rz, 0.025, 0.045, 0.045)
            add_box(bm_iron, sx * 0.41, sy * 0.465, rz, 0.045, 0.025, 0.045)

# Top corner iron lid caps
for sx in (-1.0, 1.0):
    for sy in (-1.0, 1.0):
        add_box(bm_iron, sx * 0.41, sy * 0.41, 0.82, 0.13, 0.13, 0.05)

# Side forged iron handle brackets (-X and +X faces)
for sx in (-1.0, 1.0):
    add_box(bm_iron, sx * 0.455, 0.0, 0.46, 0.05, 0.22, 0.08)
    add_box(bm_iron, sx * 0.485, 0.0, 0.39, 0.035, 0.16, 0.10)

bmesh.ops.recalc_face_normals(bm_iron, faces=bm_iron.faces)
mesh_iron = bpy.data.meshes.new("SM_FantasyCrate_IronBands")
bm_iron.to_mesh(mesh_iron)
bm_iron.free()

obj_iron = bpy.data.objects.new("SM_FantasyCrate_IronBands", mesh_iron)
link_only(obj_iron, col_export)
bev_i = obj_iron.modifiers.new("Bevel_Iron", 'BEVEL')
bev_i.width = 0.006
bev_i.segments = 1
bev_i.limit_method = 'ANGLE'

# 3. Front Ornate Arcane Rune Lock (-Y front face)
bm_rune = bmesh.new()
# Shield frame backing
add_box(bm_rune, 0.0, -0.455, 0.50, 0.20, 0.045, 0.24)
# Faceted arcane gem diamond in center
gem_verts = [
    bm_rune.verts.new((0.0, -0.485, 0.59)),
    bm_rune.verts.new((-0.065, -0.485, 0.50)),
    bm_rune.verts.new((0.0, -0.485, 0.41)),
    bm_rune.verts.new((0.065, -0.485, 0.50)),
    bm_rune.verts.new((0.0, -0.525, 0.50)),
    bm_rune.verts.new((0.0, -0.460, 0.50)),
]
for idx in range(4):
    nxt = (idx + 1) % 4
    bm_rune.faces.new((gem_verts[idx], gem_verts[nxt], gem_verts[4]))
    bm_rune.faces.new((gem_verts[nxt], gem_verts[idx], gem_verts[5]))

bmesh.ops.recalc_face_normals(bm_rune, faces=bm_rune.faces)
mesh_rune = bpy.data.meshes.new("SM_FantasyCrate_RuneLock")
bm_rune.to_mesh(mesh_rune)
bm_rune.free()

obj_rune = bpy.data.objects.new("SM_FantasyCrate_RuneLock", mesh_rune)
link_only(obj_rune, col_export)

# 4. Simplified Collision Box Proxy in COL_Collision
bm_col = bmesh.new()
add_box(bm_col, 0.0, 0.0, 0.43, 0.94, 0.94, 0.86)
bmesh.ops.recalc_face_normals(bm_col, faces=bm_col.faces)
mesh_col = bpy.data.meshes.new("SM_FantasyCrate_COL")
bm_col.to_mesh(mesh_col)
bm_col.free()
obj_col = bpy.data.objects.new("SM_FantasyCrate_COL", mesh_col)
link_only(obj_col, col_col)
obj_col.hide_render = True
obj_col.hide_viewport = True
obj_col.display_type = 'WIRE'

# Apply bevel modifiers cleanly so static game asset topology is final and measurable
bpy.context.view_layer.update()
for obj in (obj_wood, obj_iron):
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    for m in list(obj.modifiers):
        bpy.ops.object.modifier_apply(modifier=m.name)
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

# Compute stats & verify floor pivot Z=0 and bounds
export_objs = [bpy.data.objects[n] for n in OWNED_EXPORT]
all_pts = [o.matrix_world @ Vector(c) for o in export_objs for c in o.bound_box]
min_z = min(p.z for p in all_pts)
max_z = max(p.z for p in all_pts)
total_tris = 0
for o in export_objs:
    o.data.calc_loop_triangles()
    total_tris += len(o.data.loop_triangles)

payload = {{
    "created_export_objects": [o.name for o in export_objs],
    "created_collision_objects": [obj_col.name],
    "min_z_m": round(min_z, 4),
    "height_m": round(max_z - min_z, 4),
    "total_triangles": total_tris,
    "numeric_suffix_duplicates": [o.name for o in scene.objects if len(o.name) > 4 and o.name[-4] == '.' and o.name[-3:].isdigit()],
}}
print("__ORCH_JSON__:" + json.dumps(payload))
'''


def build_uv_stage_code(scene_name: str, object_names: list[str]) -> str:
    """Create UV layers, unwrap cleanly, pack islands, and report UV coverage metrics."""
    return f'''
import bpy, json

SCENE_NAME = {json.dumps(scene_name)}
TARGET_NAMES = {json.dumps(object_names)}
scene = bpy.data.scenes[SCENE_NAME]
bpy.context.window.scene = scene

uv_report = {{}}
for name in TARGET_NAMES:
    obj = bpy.data.objects.get(name)
    if obj is None or obj.type != 'MESH':
        raise RuntimeError(f"Missing mesh object for UV stage: {{name}}")
    if not obj.data.uv_layers:
        obj.data.uv_layers.new(name="UVMap")
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.uv.smart_project(angle_limit=1.1519, island_margin=0.02)
    bpy.ops.uv.pack_islands(margin=0.02)
    bpy.ops.object.mode_set(mode='OBJECT')

    uv_layer = obj.data.uv_layers.active
    us = [d.uv[0] for d in uv_layer.data]
    vs = [d.uv[1] for d in uv_layer.data]
    in_bounds = all(-0.001 <= u <= 1.001 for u in us) and all(-0.001 <= v <= 1.001 for v in vs)
    uv_report[name] = {{
        "uv_layers": len(obj.data.uv_layers),
        "uv_loops": len(uv_layer.data),
        "in_0_1_bounds": in_bounds,
    }}

print("__ORCH_JSON__:" + json.dumps({{"uv_report": uv_report, "all_valid": all(v["in_0_1_bounds"] and v["uv_layers"] >= 1 for v in uv_report.values())}}))
'''


def build_stylized_crate_materials_code(scene_name: str) -> str:
    """Create and assign MAT_* stylized procedural materials idempotently without `.001` copies."""
    return f'''
import bpy, json

SCENE_NAME = {json.dumps(scene_name)}
scene = bpy.data.scenes[SCENE_NAME]
bpy.context.window.scene = scene

def get_or_reset_mat(name):
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    return mat, nt

# 1. Stylized Fantasy Wood Planks
mat_wood, nt = get_or_reset_mat("MAT_Wood_FantasyPlanks")
out = nt.nodes.new("ShaderNodeOutputMaterial")
bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
tex_coord = nt.nodes.new("ShaderNodeTexCoord")
noise = nt.nodes.new("ShaderNodeTexNoise")
noise.inputs["Scale"].default_value = 9.0
noise.inputs["Detail"].default_value = 5.0
noise.inputs["Roughness"].default_value = 0.65
ramp = nt.nodes.new("ShaderNodeValToRGB")
ramp.color_ramp.elements[0].position = 0.25
ramp.color_ramp.elements[0].color = (0.14, 0.075, 0.032, 1.0)
ramp.color_ramp.elements[1].position = 0.78
ramp.color_ramp.elements[1].color = (0.34, 0.19, 0.085, 1.0)
bsdf.inputs["Roughness"].default_value = 0.62
bsdf.inputs["Metallic"].default_value = 0.0
nt.links.new(tex_coord.outputs["Object"], noise.inputs["Vector"])
nt.links.new(noise.outputs["Fac"], ramp.inputs["Fac"])
nt.links.new(ramp.outputs["Color"], bsdf.inputs["Base Color"])
nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])

# 2. Forged Fantasy Dark Steel / Iron
mat_iron, nt = get_or_reset_mat("MAT_Metal_ForgedIron")
out = nt.nodes.new("ShaderNodeOutputMaterial")
bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
noise = nt.nodes.new("ShaderNodeTexNoise")
noise.inputs["Scale"].default_value = 18.0
noise.inputs["Detail"].default_value = 4.0
ramp = nt.nodes.new("ShaderNodeValToRGB")
ramp.color_ramp.elements[0].position = 0.3
ramp.color_ramp.elements[0].color = (0.09, 0.10, 0.12, 1.0)
ramp.color_ramp.elements[1].position = 0.75
ramp.color_ramp.elements[1].color = (0.20, 0.22, 0.25, 1.0)
bsdf.inputs["Metallic"].default_value = 0.90
bsdf.inputs["Roughness"].default_value = 0.42
nt.links.new(noise.outputs["Fac"], ramp.inputs["Fac"])
nt.links.new(ramp.outputs["Color"], bsdf.inputs["Base Color"])
nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])

# 3. Arcane Gold & Cyan Rune Lock (two slots on RuneLock: gold shield + cyan rune gem)
mat_gold, nt = get_or_reset_mat("MAT_Metal_ArcaneGold")
out = nt.nodes.new("ShaderNodeOutputMaterial")
bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
bsdf.inputs["Base Color"].default_value = (0.83, 0.62, 0.18, 1.0)
bsdf.inputs["Metallic"].default_value = 0.92
bsdf.inputs["Roughness"].default_value = 0.28
nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])

mat_gem, nt = get_or_reset_mat("MAT_Gem_ArcaneRune")
out = nt.nodes.new("ShaderNodeOutputMaterial")
bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
bsdf.inputs["Base Color"].default_value = (0.10, 0.72, 0.95, 1.0)
bsdf.inputs["Roughness"].default_value = 0.15
if "Emission Color" in bsdf.inputs:
    bsdf.inputs["Emission Color"].default_value = (0.12, 0.78, 1.0, 1.0)
    bsdf.inputs["Emission Strength"].default_value = 2.4
nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])

def set_single_mat(obj_name, mat):
    obj = bpy.data.objects[obj_name]
    obj.data.materials.clear()
    obj.data.materials.append(mat)

set_single_mat("SM_FantasyCrate_WoodBody", mat_wood)
set_single_mat("SM_FantasyCrate_IronBands", mat_iron)

rune_obj = bpy.data.objects["SM_FantasyCrate_RuneLock"]
rune_obj.data.materials.clear()
rune_obj.data.materials.append(mat_gold)
rune_obj.data.materials.append(mat_gem)
# Shield backing (6 faces) -> slot 0 gold; diamond gem faces (last 8) -> slot 1 arcane rune gem
for idx, poly in enumerate(rune_obj.data.polygons):
    poly.material_index = 0 if idx < 6 else 1

assigned = {{
    name: [m.name for m in bpy.data.objects[name].data.materials if m]
    for name in ("SM_FantasyCrate_WoodBody", "SM_FantasyCrate_IronBands", "SM_FantasyCrate_RuneLock")
}}
print("__ORCH_JSON__:" + json.dumps({{"assigned_materials": assigned, "material_count": 4}}))
'''


def build_qa_render_code(
    scene_name: str,
    target_names: list[str],
    output_dir: str,
    *,
    views: list[str] | None = None,
) -> str:
    """Idempotently configure 3-point QA lighting & camera and render multi-angle PNG stills."""
    view_list = views or ["three_quarter", "front", "side"]
    return f'''
import bpy, json, math, os, time
from mathutils import Vector

SCENE_NAME = {json.dumps(scene_name)}
TARGET_NAMES = {json.dumps(target_names)}
OUTPUT_DIR = {json.dumps(output_dir)}
VIEWS = {json.dumps(view_list)}

os.makedirs(OUTPUT_DIR, exist_ok=True)
scene = bpy.data.scenes[SCENE_NAME]
bpy.context.window.scene = scene
col_lights = bpy.data.collections["COL_Lights"]

def ensure_in_col(obj, col):
    for c in list(obj.users_collection):
        c.objects.unlink(obj)
    col.objects.link(obj)

# Compute target bounds center
pts = [bpy.data.objects[n].matrix_world @ Vector(c) for n in TARGET_NAMES for c in bpy.data.objects[n].bound_box]
center = sum(pts, Vector((0, 0, 0))) / len(pts)

# Studio floor inside COL_Lights (excluded from COL_Export)
floor_obj = bpy.data.objects.get("LGT_QA_StudioFloor")
if floor_obj is None:
    import bmesh
    bm = bmesh.new()
    bmesh.ops.create_grid(bm, x_segments=1, y_segments=1, size=4.0)
    m_floor = bpy.data.meshes.new("LGT_QA_StudioFloor")
    bm.to_mesh(m_floor)
    bm.free()
    floor_obj = bpy.data.objects.new("LGT_QA_StudioFloor", m_floor)
    floor_obj.location = (0.0, 0.0, -0.002)
    mat_floor = bpy.data.materials.get("MAT_QA_StudioFloor") or bpy.data.materials.new("MAT_QA_StudioFloor")
    mat_floor.use_nodes = True
    bsdf_f = mat_floor.node_tree.nodes.get("Principled BSDF")
    if bsdf_f:
        bsdf_f.inputs["Base Color"].default_value = (0.16, 0.18, 0.21, 1.0)
        bsdf_f.inputs["Roughness"].default_value = 0.88
    floor_obj.data.materials.append(mat_floor)
ensure_in_col(floor_obj, col_lights)

# Subtle clean studio world light
if scene.world is None:
    scene.world = bpy.data.worlds.new("WLD_QA_Studio")
scene.world.use_nodes = True
bg = scene.world.node_tree.nodes.get("Background")
if bg:
    bg.inputs["Color"].default_value = (0.11, 0.13, 0.16, 1.0)
    bg.inputs["Strength"].default_value = 0.85

# 3-point lights
def ensure_light(name, ltype, loc, rot_deg, energy, color=(1.0, 1.0, 1.0), size=2.0):
    obj = bpy.data.objects.get(name)
    if obj is None:
        ldata = bpy.data.lights.new(name=name, type=ltype)
        obj = bpy.data.objects.new(name=name, object_data=ldata)
    obj.data.type = ltype
    obj.data.energy = energy
    obj.data.color = color
    if hasattr(obj.data, "size"):
        obj.data.size = size
    obj.location = loc
    obj.rotation_euler = tuple(math.radians(a) for a in rot_deg)
    ensure_in_col(obj, col_lights)
    return obj

ensure_light("LGT_QA_Key", 'AREA', (2.2, -2.5, 2.6), (52, 0, 40), 180.0, (1.0, 0.96, 0.90), size=2.0)
ensure_light("LGT_QA_Fill", 'AREA', (-2.4, -1.8, 1.6), (62, 0, -52), 75.0, (0.82, 0.90, 1.0), size=2.5)
ensure_light("LGT_QA_Rim", 'AREA', (0.0, 2.5, 2.2), (125, 0, 0), 140.0, (0.75, 0.90, 1.0), size=2.0)

# Camera with TRACK_TO target empty
target_empty = bpy.data.objects.get("EMPTY_QA_CamTarget")
if target_empty is None:
    target_empty = bpy.data.objects.new("EMPTY_QA_CamTarget", None)
target_empty.location = center
ensure_in_col(target_empty, col_lights)

cam_obj = bpy.data.objects.get("CAM_QA_Studio")
if cam_obj is None:
    cam_data = bpy.data.cameras.new("CAM_QA_Studio")
    cam_obj = bpy.data.objects.new("CAM_QA_Studio", cam_data)
ensure_in_col(cam_obj, col_lights)
cam_obj.data.lens = 45.0
scene.camera = cam_obj
if not any(c.type == 'TRACK_TO' for c in cam_obj.constraints):
    trk = cam_obj.constraints.new('TRACK_TO')
    trk.target = target_empty
    trk.track_axis = 'TRACK_NEGATIVE_Z'
    trk.up_axis = 'UP_Y'

# Engine selection safe for Blender 5.x headless EEVEE/Cycles
engines = [e.identifier for e in scene.render.bl_rna.properties['engine'].enum_items]
if 'BLENDER_EEVEE_NEXT' in engines:
    scene.render.engine = 'BLENDER_EEVEE_NEXT'
elif 'BLENDER_EEVEE' in engines:
    scene.render.engine = 'BLENDER_EEVEE'
else:
    scene.render.engine = 'CYCLES'
    scene.cycles.samples = 16

scene.render.resolution_x = 800
scene.render.resolution_y = 600
scene.render.resolution_percentage = 100
scene.render.image_settings.file_format = 'PNG'

view_offsets = {{
    "three_quarter": Vector((1.75, -1.95, 1.25)),
    "front": Vector((0.0, -2.35, 0.55)),
    "side": Vector((2.35, 0.0, 0.55)),
    "back": Vector((0.0, 2.35, 0.65)),
}}

renders = []
durations = {{}}
for vname in VIEWS:
    offset = view_offsets.get(vname, view_offsets["three_quarter"])
    cam_obj.location = center + offset
    bpy.context.view_layer.update()
    out_path = os.path.join(OUTPUT_DIR, f"crate_{{vname}}.png")
    scene.render.filepath = out_path
    t0 = time.monotonic()
    bpy.ops.render.render(write_still=True)
    durations[vname] = round(time.monotonic() - t0, 3)
    renders.append({{"view": vname, "path": out_path, "size_bytes": os.path.getsize(out_path)}})

print("__ORCH_JSON__:" + json.dumps({{"engine": scene.render.engine, "renders": renders, "durations_sec": durations}}))
'''


def build_scene_state_audit_code(scene_name: str, asset_id: str) -> str:
    """Audit geometry health, transforms, collections, materials, UVs, and duplicate suffixes."""
    return f'''
import bpy, bmesh, json
from mathutils import Vector

SCENE_NAME = {json.dumps(scene_name)}
ASSET_ID = {json.dumps(asset_id)}
scene = bpy.data.scenes.get(SCENE_NAME)
if scene is None:
    raise RuntimeError(f"Scene not found: {{SCENE_NAME}}")

export_col = bpy.data.collections.get("COL_Export")
export_objs = [o for o in (export_col.objects if export_col else []) if o.type == 'MESH']
mesh_details = []
total_tris = 0
for o in export_objs:
    bm = bmesh.new()
    bm.from_mesh(o.data)
    non_manifold = sum(1 for e in bm.edges if not e.is_manifold)
    loose_verts = sum(1 for v in bm.verts if not v.link_edges)
    ngons = sum(1 for f in bm.faces if len(f.verts) > 4)
    quads = sum(1 for f in bm.faces if len(f.verts) == 4)
    bm.free()
    o.data.calc_loop_triangles()
    tris = len(o.data.loop_triangles)
    total_tris += tris
    mesh_details.append({{
        "name": o.name,
        "faces": len(o.data.polygons),
        "quads": quads,
        "ngons": ngons,
        "tris": tris,
        "non_manifold_edges": non_manifold,
        "loose_verts": loose_verts,
        "uv_layers": len(o.data.uv_layers),
        "materials": [m.name for m in o.data.materials if m],
        "scale_applied": tuple(round(s, 4) for s in o.scale) == (1.0, 1.0, 1.0),
    }})

numeric_dups = [o.name for o in scene.objects if len(o.name) > 4 and o.name[-4] == '.' and o.name[-3:].isdigit()]
payload = {{
    "scene_name": scene.name,
    "asset_id": scene.get("asset_id"),
    "export_object_count": len(export_objs),
    "total_export_tris": total_tris,
    "numeric_suffix_duplicates": numeric_dups,
    "meshes": mesh_details,
}}
print("__ORCH_JSON__:" + json.dumps(payload))
'''


def build_checkpoint_save_code(blend_path: str) -> str:
    """Save a .blend checkpoint cleanly and report file size."""
    return f'''
import bpy, json, os

BLEND_PATH = {json.dumps(blend_path)}
os.makedirs(os.path.dirname(BLEND_PATH), exist_ok=True)
bpy.ops.wm.save_as_mainfile(filepath=BLEND_PATH)
with open(BLEND_PATH, "rb") as fh:
    header = fh.read(16)
payload = {{
    "blend_path": BLEND_PATH,
    "exists": os.path.isfile(BLEND_PATH),
    "size_bytes": os.path.getsize(BLEND_PATH),
    "is_blend_header": (
        header.startswith(b"BLENDER")
        or header.startswith(b"\\x28\\xb5\\x2f\\xfd")
        or header.startswith(b"\\x1f\\x8b")
    ),
}}
print("__ORCH_JSON__:" + json.dumps(payload))
'''


def build_glb_export_and_reimport_code(scene_name: str, export_collection: str, glb_path: str) -> str:
    """Export COL_Export to GLB (+Y up), reimport into temporary scene, verify counts, and clean up."""
    return f'''
import bpy, json, os

SCENE_NAME = {json.dumps(scene_name)}
EXPORT_COL = {json.dumps(export_collection)}
GLB_PATH = {json.dumps(glb_path)}

os.makedirs(os.path.dirname(GLB_PATH), exist_ok=True)
scene = bpy.data.scenes[SCENE_NAME]
bpy.context.window.scene = scene

col = bpy.data.collections[EXPORT_COL]
bpy.ops.object.select_all(action='DESELECT')
exported_names = []
for obj in col.objects:
    if obj.type in ('MESH', 'ARMATURE'):
        obj.select_set(True)
        exported_names.append(obj.name)

bpy.ops.export_scene.gltf(
    filepath=GLB_PATH,
    export_format='GLB',
    use_selection=True,
    export_apply=True,
    export_yup=True,
)

# Reimport validation into an isolated temporary scene
temp_scene = bpy.data.scenes.new("__GLB_Reimport_Verify")
bpy.context.window.scene = temp_scene
bpy.ops.import_scene.gltf(filepath=GLB_PATH)
reimported_meshes = [o.name for o in temp_scene.objects if o.type == 'MESH']
reimported_tris = 0
for o in temp_scene.objects:
    if o.type == 'MESH':
        o.data.calc_loop_triangles()
        reimported_tris += len(o.data.loop_triangles)

# Clean up temporary verification scene & objects
for o in list(temp_scene.objects):
    data = o.data
    bpy.data.objects.remove(o, do_unlink=True)
    if data is not None and data.users == 0 and isinstance(data, bpy.types.Mesh):
        bpy.data.meshes.remove(data)
bpy.data.scenes.remove(temp_scene)
bpy.context.window.scene = scene

payload = {{
    "glb_path": GLB_PATH,
    "size_bytes": os.path.getsize(GLB_PATH),
    "exported_names": sorted(exported_names),
    "reimported_mesh_count": len(reimported_meshes),
    "reimported_tris": reimported_tris,
    "valid": os.path.getsize(GLB_PATH) > 0 and len(reimported_meshes) >= len(exported_names),
}}
print("__ORCH_JSON__:" + json.dumps(payload))
'''


def build_reference_camera_match_code(
    scene_name: str,
    target_center: tuple[float, float, float] = (0.0, 0.0, 0.36),
    cam_location: tuple[float, float, float] = (1.10, -1.60, 0.72),
    lens_mm: float = 50.0,
) -> str:
    """Create CAM_Lantern_Hero + EMPTY_CamTarget_Lantern + studio lighting for reference matching."""
    return f'''
import bpy, bmesh, json, math

SCENE_NAME = {json.dumps(scene_name)}
TARGET_CENTER = {json.dumps(list(target_center))}
CAM_LOC = {json.dumps(list(cam_location))}
LENS_MM = {float(lens_mm)}

scene = bpy.data.scenes[SCENE_NAME]
bpy.context.window.scene = scene
col_lights = bpy.data.collections["COL_Lights"]

def ensure_in_col(obj, col):
    for c in list(obj.users_collection):
        c.objects.unlink(obj)
    col.objects.link(obj)

target_empty = bpy.data.objects.get("EMPTY_CamTarget_Lantern")
if target_empty is None:
    target_empty = bpy.data.objects.new("EMPTY_CamTarget_Lantern", None)
target_empty.location = tuple(TARGET_CENTER)
ensure_in_col(target_empty, col_lights)

cam_obj = bpy.data.objects.get("CAM_Lantern_Hero")
if cam_obj is None:
    cam_data = bpy.data.cameras.new("CAM_Lantern_Hero")
    cam_obj = bpy.data.objects.new("CAM_Lantern_Hero", cam_data)
cam_obj.location = tuple(CAM_LOC)
cam_obj.data.lens = LENS_MM
ensure_in_col(cam_obj, col_lights)
scene.camera = cam_obj
if not any(c.type == 'TRACK_TO' for c in cam_obj.constraints):
    trk = cam_obj.constraints.new('TRACK_TO')
    trk.target = target_empty
    trk.track_axis = 'TRACK_NEGATIVE_Z'
    trk.up_axis = 'UP_Y'

# Reference-matched dark-slate studio background & 3-point lighting
floor_obj = bpy.data.objects.get("LGT_Ref_StudioFloor")
if floor_obj is None:
    bm = bmesh.new()
    bmesh.ops.create_grid(bm, x_segments=1, y_segments=1, size=4.0)
    mf = bpy.data.meshes.new("LGT_Ref_StudioFloor")
    bm.to_mesh(mf)
    bm.free()
    floor_obj = bpy.data.objects.new("LGT_Ref_StudioFloor", mf)
    floor_obj.location = (0.0, 0.0, -0.002)
    mat_f = bpy.data.materials.get("MAT_Ref_StudioFloor") or bpy.data.materials.new("MAT_Ref_StudioFloor")
    mat_f.use_nodes = True
    bs = mat_f.node_tree.nodes.get("Principled BSDF")
    if bs:
        bs.inputs["Base Color"].default_value = (0.06, 0.075, 0.11, 1.0)
        bs.inputs["Roughness"].default_value = 0.90
    floor_obj.data.materials.append(mat_f)
ensure_in_col(floor_obj, col_lights)

if scene.world is None:
    scene.world = bpy.data.worlds.new("WLD_Lantern_Ref")
scene.world.use_nodes = True
bg = scene.world.node_tree.nodes.get("Background")
if bg:
    bg.inputs["Color"].default_value = (0.055, 0.07, 0.11, 1.0)
    bg.inputs["Strength"].default_value = 0.70

def ensure_light(name, ltype, loc, rot_deg, energy, color, size=1.8):
    obj = bpy.data.objects.get(name)
    if obj is None:
        ld = bpy.data.lights.new(name=name, type=ltype)
        obj = bpy.data.objects.new(name=name, object_data=ld)
    obj.data.type = ltype
    obj.data.energy = energy
    obj.data.color = color
    if hasattr(obj.data, "size"):
        obj.data.size = size
    obj.location = loc
    obj.rotation_euler = tuple(math.radians(a) for a in rot_deg)
    ensure_in_col(obj, col_lights)

ensure_light("LGT_Ref_Key", 'AREA', (-1.8, -2.0, 2.0), (50, 0, -40), 160.0, (1.0, 0.95, 0.88))
ensure_light("LGT_Ref_Fill", 'AREA', (2.0, -1.6, 1.4), (62, 0, 50), 65.0, (0.75, 0.88, 1.0))
ensure_light("LGT_Ref_Rim", 'AREA', (0.2, 2.2, 1.8), (122, 0, 0), 130.0, (0.95, 0.85, 0.65))

print("__ORCH_JSON__:" + json.dumps({{
    "camera_name": cam_obj.name,
    "target_name": target_empty.name,
    "lens_mm": cam_obj.data.lens,
    "camera_location": list(cam_obj.location),
}}))
'''


def build_lantern_pass1_geometry_code(scene_name: str, asset_id: str) -> str:
    """Build Pass 1 Alchemy Lantern base geometry via Blender bmesh (pre-refinement pass)."""
    return f'''
import bpy, bmesh, json, math
from mathutils import Vector

SCENE_NAME = {json.dumps(scene_name)}
ASSET_ID = {json.dumps(asset_id)}
OWNED = ["SM_Lantern_BaseCap", "SM_Lantern_IronCage", "SM_Lantern_AmberCore", "SM_Lantern_COL"]

scene = bpy.data.scenes[SCENE_NAME]
bpy.context.window.scene = scene
col_export = bpy.data.collections["COL_Export"]
col_col = bpy.data.collections["COL_Collision"]

for name in OWNED + ["SM_Lantern_BrassFinial"]:
    obj = bpy.data.objects.get(name)
    if obj is not None:
        data = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        if data is not None and data.users == 0:
            bpy.data.meshes.remove(data)

def link_only(obj, target_col, stage):
    for c in list(obj.users_collection):
        c.objects.unlink(obj)
    target_col.objects.link(obj)
    obj["asset_id"] = ASSET_ID
    obj["production_stage"] = stage

def add_cyl(bm, radius1, radius2, depth, segs, cz):
    res = bmesh.ops.create_cone(
        bm, cap_ends=True, cap_tris=False, segments=segs,
        radius1=radius1, radius2=radius2, depth=depth
    )
    for v in res["verts"]:
        v.co.z += cz

def add_box(bm, cx, cy, cz, sx, sy, sz):
    hx, hy, hz = sx * 0.5, sy * 0.5, sz * 0.5
    vb = [bm.verts.new((cx-hx, cy-hy, cz-hz)), bm.verts.new((cx+hx, cy-hy, cz-hz)),
          bm.verts.new((cx+hx, cy+hy, cz-hz)), bm.verts.new((cx-hx, cy+hy, cz-hz))]
    vt = [bm.verts.new((cx-hx, cy-hy, cz+hz)), bm.verts.new((cx+hx, cy-hy, cz+hz)),
          bm.verts.new((cx+hx, cy+hy, cz+hz)), bm.verts.new((cx-hx, cy+hy, cz+hz))]
    bm.faces.new((vb[3], vb[2], vb[1], vb[0]))
    bm.faces.new((vt[0], vt[1], vt[2], vt[3]))
    for i in range(4):
        j = (i + 1) % 4
        bm.faces.new((vb[i], vb[j], vt[j], vt[i]))

# 1. Stepped octagonal base & plain roof cap (Pass 1 omits top spire finial intentionally)
bm_base = bmesh.new()
add_cyl(bm_base, 0.24, 0.21, 0.06, 8, 0.03)
add_cyl(bm_base, 0.19, 0.19, 0.04, 8, 0.08)
add_cyl(bm_base, 0.20, 0.23, 0.05, 8, 0.50)
add_cyl(bm_base, 0.22, 0.10, 0.10, 8, 0.575)
bmesh.ops.recalc_face_normals(bm_base, faces=bm_base.faces)
m_base = bpy.data.meshes.new("SM_Lantern_BaseCap")
bm_base.to_mesh(m_base); bm_base.free()
o_base = bpy.data.objects.new("SM_Lantern_BaseCap", m_base)
link_only(o_base, col_export, "modeling_pass1")

# 2. Four iron corner struts framing the glass core
bm_cage = bmesh.new()
for sx in (-1.0, 1.0):
    for sy in (-1.0, 1.0):
        add_box(bm_cage, sx * 0.145, sy * 0.145, 0.29, 0.032, 0.032, 0.40)
bmesh.ops.recalc_face_normals(bm_cage, faces=bm_cage.faces)
m_cage = bpy.data.meshes.new("SM_Lantern_IronCage")
bm_cage.to_mesh(m_cage); bm_cage.free()
o_cage = bpy.data.objects.new("SM_Lantern_IronCage", m_cage)
link_only(o_cage, col_export, "modeling_pass1")

# 3. Glowing Amber Glass Core
bm_core = bmesh.new()
add_cyl(bm_core, 0.135, 0.155, 0.38, 8, 0.29)
bmesh.ops.recalc_face_normals(bm_core, faces=bm_core.faces)
m_core = bpy.data.meshes.new("SM_Lantern_AmberCore")
bm_core.to_mesh(m_core); bm_core.free()
o_core = bpy.data.objects.new("SM_Lantern_AmberCore", m_core)
link_only(o_core, col_export, "modeling_pass1")

# 4. Collision proxy
bm_col = bmesh.new()
add_box(bm_col, 0.0, 0.0, 0.34, 0.48, 0.48, 0.68)
bmesh.ops.recalc_face_normals(bm_col, faces=bm_col.faces)
m_col = bpy.data.meshes.new("SM_Lantern_COL")
bm_col.to_mesh(m_col); bm_col.free()
o_col = bpy.data.objects.new("SM_Lantern_COL", m_col)
link_only(o_col, col_col, "collision")
o_col.hide_render = True
o_col.hide_viewport = True
o_col.display_type = 'WIRE'

for o in (o_base, o_cage, o_core, o_col):
    bpy.ops.object.select_all(action='DESELECT')
    o.select_set(True)
    bpy.context.view_layer.objects.active = o
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

print("__ORCH_JSON__:" + json.dumps({{
    "pass": 1,
    "export_objects": ["SM_Lantern_BaseCap", "SM_Lantern_IronCage", "SM_Lantern_AmberCore"],
    "collision_objects": ["SM_Lantern_COL"],
}}))
'''


def build_lantern_materials_code(scene_name: str, *, core_emission_strength: float = 1.4) -> str:
    """Create/update stylized lantern materials with adjustable amber core emission."""
    return f'''
import bpy, json

SCENE_NAME = {json.dumps(scene_name)}
CORE_EMIT = {float(core_emission_strength)}
scene = bpy.data.scenes[SCENE_NAME]
bpy.context.window.scene = scene

def get_mat(name):
    m = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    return m, bsdf

mat_brass, b_brass = get_mat("MAT_Brass_LanternTrim")
b_brass.inputs["Base Color"].default_value = (0.82, 0.58, 0.16, 1.0)
b_brass.inputs["Metallic"].default_value = 0.90
b_brass.inputs["Roughness"].default_value = 0.30

mat_iron, b_iron = get_mat("MAT_Iron_LanternFrame")
b_iron.inputs["Base Color"].default_value = (0.11, 0.12, 0.15, 1.0)
b_iron.inputs["Metallic"].default_value = 0.88
b_iron.inputs["Roughness"].default_value = 0.45

mat_amber, b_amb = get_mat("MAT_Glass_AmberCore")
b_amb.inputs["Base Color"].default_value = (1.0, 0.62, 0.12, 1.0)
b_amb.inputs["Roughness"].default_value = 0.18
if "Emission Color" in b_amb.inputs:
    b_amb.inputs["Emission Color"].default_value = (1.0, 0.56, 0.10, 1.0)
    b_amb.inputs["Emission Strength"].default_value = CORE_EMIT

def assign(obj_name, mat):
    obj = bpy.data.objects.get(obj_name)
    if obj is not None:
        obj.data.materials.clear()
        obj.data.materials.append(mat)

assign("SM_Lantern_BaseCap", mat_iron)
assign("SM_Lantern_IronCage", mat_iron)
assign("SM_Lantern_AmberCore", mat_amber)
assign("SM_Lantern_BrassFinial", mat_brass)

print("__ORCH_JSON__:" + json.dumps({{"core_emission_strength": CORE_EMIT, "materials_assigned": True}}))
'''


def build_lantern_pass2_refinement_code(scene_name: str, asset_id: str) -> str:
    """Targeted Pass 2 Blender refinement: add brass collar/crown finial, carry ring & iron bevels."""
    return f'''
import bpy, bmesh, json, math

SCENE_NAME = {json.dumps(scene_name)}
ASSET_ID = {json.dumps(asset_id)}
scene = bpy.data.scenes[SCENE_NAME]
bpy.context.window.scene = scene
col_export = bpy.data.collections["COL_Export"]

old_finial = bpy.data.objects.get("SM_Lantern_BrassFinial")
if old_finial is not None:
    d = old_finial.data
    bpy.data.objects.remove(old_finial, do_unlink=True)
    if d is not None and d.users == 0:
        bpy.data.meshes.remove(d)

# Bevel the iron cage struts and base cap without rebuilding from scratch
for name in ("SM_Lantern_BaseCap", "SM_Lantern_IronCage"):
    obj = bpy.data.objects[name]
    bev = obj.modifiers.get("Bevel_Refined") or obj.modifiers.new("Bevel_Refined", 'BEVEL')
    bev.width = 0.005
    bev.segments = 2
    bev.limit_method = 'ANGLE'
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.modifier_apply(modifier=bev.name)

# Build ornate brass base collar, waist band, crown finial & handle ring
bm = bmesh.new()
for rad1, rad2, dep, cz in (
    (0.205, 0.205, 0.025, 0.105),
    (0.235, 0.235, 0.025, 0.485),
    (0.115, 0.080, 0.055, 0.640),
    (0.050, 0.015, 0.055, 0.690),
):
    res = bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False, segments=8, radius1=rad1, radius2=rad2, depth=dep)
    for v in res["verts"]:
        v.co.z += cz

# Handle loop torus approximation via rotated ring segments at top Z=0.73
ring_segs = 16
tube_segs = 8
R_major, R_minor = 0.058, 0.011
ring_rings = []
for i in range(ring_segs):
    theta = (2.0 * math.pi * i) / ring_segs
    cx = R_major * math.cos(theta)
    cz = 0.745 + R_major * math.sin(theta)
    ring_v = []
    for j in range(tube_segs):
        phi = (2.0 * math.pi * j) / tube_segs
        nx = (R_minor * math.cos(phi)) * math.cos(theta)
        ny = R_minor * math.sin(phi)
        nz = (R_minor * math.cos(phi)) * math.sin(theta)
        ring_v.append(bm.verts.new((cx + nx, ny, cz + nz)))
    ring_rings.append(ring_v)
for i in range(ring_segs):
    ni = (i + 1) % ring_segs
    for j in range(tube_segs):
        nj = (j + 1) % tube_segs
        bm.faces.new((ring_rings[i][j], ring_rings[ni][j], ring_rings[ni][nj], ring_rings[i][nj]))

bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
m_finial = bpy.data.meshes.new("SM_Lantern_BrassFinial")
bm.to_mesh(m_finial); bm.free()
o_finial = bpy.data.objects.new("SM_Lantern_BrassFinial", m_finial)
col_export.objects.link(o_finial)
o_finial["asset_id"] = ASSET_ID
o_finial["production_stage"] = "modeling_pass2_refinement"

mat_brass = bpy.data.materials.get("MAT_Brass_LanternTrim")
if mat_brass is not None:
    o_finial.data.materials.append(mat_brass)

print("__ORCH_JSON__:" + json.dumps({{
    "pass": 2,
    "refined_objects": ["SM_Lantern_BaseCap", "SM_Lantern_IronCage", "SM_Lantern_BrassFinial"],
}}))
'''


def build_single_camera_render_code(scene_name: str, output_path: str, *, width: int = 512, height: int = 512) -> str:
    """Render scene.camera cleanly to a single PNG still and report duration & size."""
    return f'''
import bpy, json, os, time

SCENE_NAME = {json.dumps(scene_name)}
OUT_PATH = {json.dumps(output_path)}
os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
scene = bpy.data.scenes[SCENE_NAME]
bpy.context.window.scene = scene

engines = [e.identifier for e in scene.render.bl_rna.properties['engine'].enum_items]
if 'BLENDER_EEVEE_NEXT' in engines:
    scene.render.engine = 'BLENDER_EEVEE_NEXT'
elif 'BLENDER_EEVEE' in engines:
    scene.render.engine = 'BLENDER_EEVEE'
else:
    scene.render.engine = 'CYCLES'
    scene.cycles.samples = 16

scene.render.resolution_x = {int(width)}
scene.render.resolution_y = {int(height)}
scene.render.resolution_percentage = 100
scene.render.image_settings.file_format = 'PNG'
scene.render.filepath = OUT_PATH
t0 = time.monotonic()
bpy.ops.render.render(write_still=True)
dur = round(time.monotonic() - t0, 3)

print("__ORCH_JSON__:" + json.dumps({{
    "path": OUT_PATH,
    "size_bytes": os.path.getsize(OUT_PATH),
    "duration_sec": dur,
    "engine": scene.render.engine,
}}))
'''

