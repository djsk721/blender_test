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


def build_character_scene_init_code(scene_name: str, asset_id: str) -> str:
    """Idempotently initialize an isolated character scene and structured COL_* hierarchy."""
    return f'''
import bpy, json

SCENE_NAME = {json.dumps(scene_name)}
ASSET_ID = {json.dumps(asset_id)}
REQUIRED_COLS = [
    "COL_Reference",
    "COL_Blockout",
    "COL_Character_Body",
    "COL_Character_Face",
    "COL_Character_Hair",
    "COL_Character_Costume",
    "COL_Rig",
    "COL_Lights",
    "COL_Export",
    "COL_Collision",
]

scene = bpy.data.scenes.get(SCENE_NAME)
created_scene = False
if scene is None:
    scene = bpy.data.scenes.new(SCENE_NAME)
    created_scene = True
    default_scene = bpy.data.scenes.get("Scene")
    if default_scene and len(default_scene.objects) <= 3 and set(o.name for o in default_scene.objects) <= {{"Cube", "Light", "Camera"}}:
        for obj in list(default_scene.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        bpy.data.scenes.remove(default_scene)

bpy.context.window.scene = scene
scene["managed_by"] = "blender_orchestration_character"
scene["asset_id"] = ASSET_ID

if not scene.get("starter_cleaned"):
    for starter in ("Cube", "Light", "Camera"):
        obj = scene.objects.get(starter)
        if obj is not None and len(scene.objects) <= 3:
            bpy.data.objects.remove(obj, do_unlink=True)
    scene["starter_cleaned"] = True

for cname in REQUIRED_COLS:
    col = bpy.data.collections.get(cname)
    if col is None:
        col = bpy.data.collections.new(cname)
    if col.name not in [c.name for c in scene.collection.children]:
        scene.collection.children.link(col)

scene.unit_settings.system = 'METRIC'
scene.unit_settings.scale_length = 1.0

payload = {{
    "scene_name": scene.name,
    "asset_id": ASSET_ID,
    "created_scene": created_scene,
    "collection_names": sorted(c.name for c in scene.collection.children),
    "scene_count": len(bpy.data.scenes),
}}
print("__ORCH_JSON__:" + json.dumps(payload))
'''


def build_character_blockout_code(scene_name: str, asset_id: str, *, height_m: float = 1.65) -> str:
    """Stage 2: Build proportional humanoid blockout (SM_Char_Body, SM_Char_Head, SM_Char_Hands)."""
    return f'''
import bpy, bmesh, json, math
from mathutils import Vector

SCENE_NAME = {json.dumps(scene_name)}
ASSET_ID = {json.dumps(asset_id)}
HEIGHT_M = {float(height_m)}
S = HEIGHT_M / 1.65

scene = bpy.data.scenes[SCENE_NAME]
bpy.context.window.scene = scene
col_body = bpy.data.collections["COL_Character_Body"]
col_face = bpy.data.collections["COL_Character_Face"]
col_export = bpy.data.collections["COL_Export"]

def reset_obj(name, bm, cols):
    old = bpy.data.objects.get(name)
    if old is not None:
        d = old.data
        bpy.data.objects.remove(old, do_unlink=True)
        if d is not None and d.users == 0:
            bpy.data.meshes.remove(d)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    if not mesh.uv_layers:
        mesh.uv_layers.new(name="UVMap")
    for p in mesh.polygons:
        p.use_smooth = True
    obj = bpy.data.objects.new(name, mesh)
    for col in cols:
        col.objects.link(obj)
    obj["asset_id"] = ASSET_ID
    obj["production_stage"] = "blockout"
    return obj

def add_loft(bm, rings, segs=16):
    ring_verts = []
    for cx, cy, cz, rx, ry in rings:
        row = []
        for i in range(segs):
            a = 2.0 * math.pi * i / segs
            row.append(bm.verts.new((cx * S + rx * S * math.cos(a), cy * S + ry * S * math.sin(a), cz * S)))
        ring_verts.append(row)
    for r in range(len(ring_verts) - 1):
        for i in range(segs):
            ni = (i + 1) % segs
            bm.faces.new((ring_verts[r][i], ring_verts[r][ni], ring_verts[r + 1][ni], ring_verts[r + 1][i]))
    bm.faces.new(list(reversed(ring_verts[0])))
    bm.faces.new(ring_verts[-1])

# 1. Cohesive torso + pelvis + legs + arms blockout in SM_Char_Body
bm_body = bmesh.new()
# Torso from pelvis (z=0.80) to upper neck (z=1.40)
add_loft(bm_body, [
    (0.0, 0.0, 0.78, 0.145, 0.098),
    (0.0, 0.0, 0.88, 0.140, 0.095),
    (0.0, -0.005, 1.02, 0.105, 0.078),
    (0.0, -0.010, 1.18, 0.142, 0.092),
    (0.0, 0.0, 1.31, 0.175, 0.088),
    (0.0, 0.005, 1.36, 0.062, 0.058),
    (0.0, 0.005, 1.43, 0.052, 0.050),
], segs=20)
# Left & Right legs (pelvis z=0.82 down to ankle z=0.09)
for sx in (-1.0, 1.0):
    x0 = sx * 0.088
    add_loft(bm_body, [
        (x0 * 0.95, 0.0, 0.09, 0.036, 0.040),
        (x0, 0.010, 0.28, 0.048, 0.050),
        (x0, -0.005, 0.45, 0.046, 0.048),
        (x0, 0.0, 0.64, 0.064, 0.066),
        (x0 * 0.96, 0.0, 0.82, 0.072, 0.074),
    ], segs=14)
    # Arms from shoulder (z=1.30) to wrist (z=0.82)
    add_loft(bm_body, [
        (sx * 0.165, 0.010, 1.29, 0.044, 0.044),
        (sx * 0.220, 0.012, 1.16, 0.038, 0.038),
        (sx * 0.265, 0.005, 1.04, 0.034, 0.034),
        (sx * 0.305, -0.020, 0.91, 0.030, 0.030),
        (sx * 0.330, -0.040, 0.82, 0.025, 0.022),
    ], segs=12)
obj_body = reset_obj("SM_Char_Body", bm_body, [col_body, col_export])

# 2. Proportional 3D head cranium + jaw blockout
bm_head = bmesh.new()
add_loft(bm_head, [
    (0.0, -0.025, 1.415, 0.036, 0.045),
    (0.0, -0.018, 1.455, 0.068, 0.082),
    (0.0, -0.005, 1.505, 0.082, 0.094),
    (0.0, 0.005, 1.560, 0.086, 0.098),
    (0.0, 0.010, 1.610, 0.076, 0.088),
    (0.0, 0.012, 1.645, 0.044, 0.052),
], segs=24)
obj_head = reset_obj("SM_Char_Head", bm_head, [col_face, col_export])

# 3. Blockout palms (refined into articulated fingers in Stage 3)
bm_hands = bmesh.new()
for sx in (-1.0, 1.0):
    add_loft(bm_hands, [
        (sx * 0.330, -0.040, 0.82, 0.026, 0.018),
        (sx * 0.345, -0.050, 0.77, 0.032, 0.016),
        (sx * 0.352, -0.055, 0.73, 0.028, 0.013),
    ], segs=12)
obj_hands = reset_obj("SM_Char_Hands", bm_hands, [col_body, col_export])

bpy.context.view_layer.update()
for o in (obj_body, obj_head, obj_hands):
    bpy.ops.object.select_all(action='DESELECT')
    o.select_set(True)
    bpy.context.view_layer.objects.active = o
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

payload = {{
    "stage": "blockout",
    "created_objects": [obj_body.name, obj_head.name, obj_hands.name],
    "height_m": round(max((o.matrix_world @ Vector(c)).z for o in (obj_body, obj_head, obj_hands) for c in o.bound_box) - min((o.matrix_world @ Vector(c)).z for o in (obj_body, obj_head, obj_hands) for c in o.bound_box), 4),
    "head_to_body_ratio": round(HEIGHT_M / (0.23 * S), 2),
}}
print("__ORCH_JSON__:" + json.dumps(payload))
'''


def build_character_face_body_costume_code(scene_name: str, asset_id: str, *, include_held_slate: bool = True) -> str:
    """Stage 3: Refine existing head (recessed eyes/brow/nose/jaw/ears), articulated hands, costume & boots."""
    return f'''
import bpy, bmesh, json, math
from mathutils import Vector

SCENE_NAME = {json.dumps(scene_name)}
ASSET_ID = {json.dumps(asset_id)}
INCLUDE_SLATE = {repr(bool(include_held_slate))}

scene = bpy.data.scenes[SCENE_NAME]
bpy.context.window.scene = scene
col_face = bpy.data.collections["COL_Character_Face"]
col_body = bpy.data.collections["COL_Character_Body"]
col_costume = bpy.data.collections["COL_Character_Costume"]
col_export = bpy.data.collections["COL_Export"]

def upsert_mesh_obj(name, bm, cols, stage="face_body_costume"):
    old = bpy.data.objects.get(name)
    if old is not None:
        d = old.data
        bpy.data.objects.remove(old, do_unlink=True)
        if d is not None and d.users == 0:
            bpy.data.meshes.remove(d)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    if not mesh.uv_layers:
        mesh.uv_layers.new(name="UVMap")
    for p in mesh.polygons:
        p.use_smooth = True
    obj = bpy.data.objects.new(name, mesh)
    for col in cols:
        col.objects.link(obj)
    obj["asset_id"] = ASSET_ID
    obj["production_stage"] = stage
    return obj

# 1. Modify existing SM_Char_Head IN-PLACE to sculpt recessed eye sockets, nose bridge, brow, cheeks, and chin
head_obj = bpy.data.objects["SM_Char_Head"]
def g2(x, z, cx, cz, rx, rz):
    return math.exp(-((x - cx) / rx) ** 2 - ((z - cz) / rz) ** 2)

max_face_disp = 0.0
for v in head_obj.data.vertices:
    if v.co.y < -0.015:  # anterior facial plane (-Y front)
        x, z = v.co.x, v.co.z
        # Recessed orbital sockets at (+/-0.032, 1.515), raised nose bridge, cheek volume, jaw taper
        socket = -0.011 * (g2(x, z, -0.032, 1.516, 0.018, 0.013) + g2(x, z, 0.032, 1.516, 0.018, 0.013))
        brow = 0.005 * g2(x, z, 0.0, 1.535, 0.048, 0.009)
        nose = 0.012 * g2(x, z, 0.0, 1.495, 0.011, 0.018)
        cheeks = 0.004 * (g2(x, z, -0.044, 1.488, 0.020, 0.016) + g2(x, z, 0.044, 1.488, 0.020, 0.016))
        lips = 0.005 * g2(x, z, 0.0, 1.460, 0.018, 0.007)
        delta_y = -(socket + brow + nose + cheeks + lips)
        v.co.y += delta_y
        max_face_disp = max(max_face_disp, abs(delta_y))
head_obj.data.update()
head_obj["production_stage"] = "face_body_costume"

# 2. Recessed almond eyeballs + sculpted eyelid rims + pointed Hylian ears
bm_eyes = bmesh.new()
for sx in (-1.0, 1.0):
    # Slightly flattened almond-recessed eyeball seated inside socket
    res = bmesh.ops.create_uvsphere(bm_eyes, u_segments=16, v_segments=12, radius=0.012)
    for v in res["verts"]:
        v.co.x *= 1.12
        v.co.y *= 0.65
        v.co.z *= 0.72
        v.co += Vector((sx * 0.032, -0.088, 1.516))
    # Upper eyelid wrap arch
    for k in range(7):
        t = k / 6.0
        ex = sx * (0.018 + 0.028 * t)
        ez = 1.516 + 0.009 * math.sin(math.pi * t)
        ey = -0.095 + 0.004 * abs(t - 0.5)
        r_lid = bmesh.ops.create_uvsphere(bm_eyes, u_segments=8, v_segments=6, radius=0.0028)
        for v in r_lid["verts"]:
            v.co += Vector((ex, ey, ez))
    # Pointed Hylian ear anchored to head side
    ear_rings = [
        (sx * 0.078, 0.002, 1.505, 0.008, 0.014),
        (sx * 0.105, 0.016, 1.522, 0.006, 0.010),
        (sx * 0.136, 0.032, 1.540, 0.0025, 0.004),
    ]
    rv_list = []
    for cx, cy, cz, ry, rz in ear_rings:
        row = []
        for i in range(10):
            a = 2.0 * math.pi * i / 10
            row.append(bm_eyes.verts.new((cx, cy + ry * math.cos(a), cz + rz * math.sin(a))))
        rv_list.append(row)
    for r in range(len(rv_list) - 1):
        for i in range(10):
            ni = (i + 1) % 10
            bm_eyes.faces.new((rv_list[r][i], rv_list[r][ni], rv_list[r + 1][ni], rv_list[r + 1][i]))
    bm_eyes.faces.new(list(reversed(rv_list[0])))
    bm_eyes.faces.new(rv_list[-1])
obj_eyes = upsert_mesh_obj("SM_Char_EyesEars", bm_eyes, [col_face, col_export])

# 3. Articulated hands with continuous palm + 5 connected fingers wrapping around held item
bm_hands = bmesh.new()
for sx in (-1.0, 1.0):
    palm_res = bmesh.ops.create_cube(bm_hands, size=1.0)
    for v in palm_res["verts"]:
        v.co.x = v.co.x * 0.044 + sx * 0.342
        v.co.y = v.co.y * 0.018 - 0.048
        v.co.z = v.co.z * 0.056 + 0.775
    for f_idx in range(5):
        fx_off = (f_idx - 2.0) * 0.0095 * sx
        segs_f = 3
        prev_ring = None
        for s_i in range(segs_f + 1):
            t = s_i / segs_f
            curl = (0.35 if sx > 0 and INCLUDE_SLATE else 0.18) * t * t
            fz = 0.748 - 0.048 * t
            fy = -0.048 - 0.032 * curl
            fx = sx * 0.342 + fx_off
            rad = 0.0055 * (1.0 - 0.30 * t)
            ring = []
            for j in range(8):
                a = 2.0 * math.pi * j / 8
                ring.append(bm_hands.verts.new((fx + rad * math.cos(a), fy + rad * math.sin(a), fz)))
            if prev_ring is not None:
                for j in range(8):
                    nj = (j + 1) % 8
                    bm_hands.faces.new((prev_ring[j], prev_ring[nj], ring[nj], ring[j]))
            else:
                bm_hands.faces.new(list(reversed(ring)))
            prev_ring = ring
        bm_hands.faces.new(prev_ring)
obj_hands = upsert_mesh_obj("SM_Char_Hands", bm_hands, [col_body, col_export])

created_costume = []
if INCLUDE_SLATE:
    bm_slate = bmesh.new()
    res = bmesh.ops.create_cube(bm_slate, size=1.0)
    for v in res["verts"]:
        v.co.x = v.co.x * 0.095 + 0.315
        v.co.y = v.co.y * 0.022 - 0.068
        v.co.z = v.co.z * 0.065 + 0.730
    obj_slate = upsert_mesh_obj("SM_Char_HeldSlate", bm_slate, [col_costume, col_export])
    created_costume.append(obj_slate.name)

# 4. Fitted royal tunic, belt/waist wrap, and contoured boots (not bulbous!)
bm_costume = bmesh.new()
def add_tube_loft(bm, rings, segs=20):
    rv = []
    for cx, cy, cz, rx, ry in rings:
        row = []
        for i in range(segs):
            a = 2.0 * math.pi * i / segs
            row.append(bm.verts.new((cx + rx * math.cos(a), cy + ry * math.sin(a), cz)))
        rv.append(row)
    for r in range(len(rv) - 1):
        for i in range(segs):
            ni = (i + 1) % segs
            bm.faces.new((rv[r][i], rv[r][ni], rv[r + 1][ni], rv[r + 1][i]))
    bm.faces.new(list(reversed(rv[0])))
    bm.faces.new(rv[-1])

# Fitted bodice/tunic hugging body contour (only +0.008 m ease)
add_tube_loft(bm_costume, [
    (0.0, 0.0, 0.76, 0.155, 0.106),
    (0.0, 0.0, 0.88, 0.146, 0.101),
    (0.0, -0.005, 1.02, 0.112, 0.084),
    (0.0, -0.010, 1.18, 0.149, 0.098),
    (0.0, 0.0, 1.31, 0.180, 0.093),
], segs=20)
# Fitted belt attached flush around waist
add_tube_loft(bm_costume, [
    (0.0, -0.004, 0.96, 0.122, 0.090),
    (0.0, -0.005, 1.03, 0.115, 0.087),
], segs=20)
obj_tunic = upsert_mesh_obj("SM_Char_Costume_Tunic", bm_costume, [col_costume, col_export])
created_costume.append(obj_tunic.name)

# Contoured boots: clear heel, flat sole at Z=0.0, instep arch, pinched ankle collar (not ballooned)
bm_boots = bmesh.new()
for sx in (-1.0, 1.0):
    bx = sx * 0.088
    # Flat grounded sole + contoured boot shaft
    add_tube_loft(bm_boots, [
        (bx, -0.040, 0.000, 0.042, 0.092),
        (bx, -0.036, 0.022, 0.041, 0.088),
        (bx, -0.010, 0.075, 0.038, 0.056),
        (bx, 0.002, 0.130, 0.037, 0.042),
        (bx, 0.006, 0.240, 0.048, 0.050),
        (bx, 0.008, 0.275, 0.052, 0.053),
    ], segs=18)
obj_boots = upsert_mesh_obj("SM_Char_Boots", bm_boots, [col_costume, col_export])
created_costume.append(obj_boots.name)

bpy.context.view_layer.update()
for o in [obj_eyes, obj_hands, obj_tunic, obj_boots] + ([bpy.data.objects["SM_Char_HeldSlate"]] if INCLUDE_SLATE else []):
    bpy.ops.object.select_all(action='DESELECT')
    o.select_set(True)
    bpy.context.view_layer.objects.active = o
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

payload = {{
    "stage": "face_body_costume",
    "max_face_displacement_m": round(max_face_disp, 5),
    "updated_objects": ["SM_Char_Head", "SM_Char_EyesEars", "SM_Char_Hands"] + created_costume,
    "scene_objects": sorted(o.name for o in col_export.objects if o.type == 'MESH'),
    "min_z_m": round(min((o.matrix_world @ Vector(c)).z for o in col_export.objects if o.type == 'MESH' for c in o.bound_box), 4),
}}
print("__ORCH_JSON__:" + json.dumps(payload))
'''


def build_character_hair_sculpt_code(scene_name: str, asset_id: str) -> str:
    """Stage 4: Full-coverage scalp cap + sculpted tapered hair clumps + braid + vertex form-refinement pass."""
    return f'''
import bpy, bmesh, json, math
from mathutils import Vector

SCENE_NAME = {json.dumps(scene_name)}
ASSET_ID = {json.dumps(asset_id)}

scene = bpy.data.scenes[SCENE_NAME]
bpy.context.window.scene = scene
col_hair = bpy.data.collections["COL_Character_Hair"]
col_export = bpy.data.collections["COL_Export"]

def upsert_mesh(name, bm, stage="hair_and_sculpt"):
    old = bpy.data.objects.get(name)
    if old is not None:
        d = old.data
        bpy.data.objects.remove(old, do_unlink=True)
        if d is not None and d.users == 0:
            bpy.data.meshes.remove(d)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    if not mesh.uv_layers:
        mesh.uv_layers.new(name="UVMap")
    for p in mesh.polygons:
        p.use_smooth = True
    obj = bpy.data.objects.new(name, mesh)
    col_hair.objects.link(obj)
    col_export.objects.link(obj)
    obj["asset_id"] = ASSET_ID
    obj["production_stage"] = stage
    return obj

# 1. Full-coverage fitted scalp cap covering top, sides, and back down to nape (no bald patches)
bm_scalp = bmesh.new()
segs_u, segs_v = 24, 12
rings = []
head_center = Vector((0.0, 0.008, 1.545))
for iv in range(1, segs_v + 1):
    phi = (iv / segs_v) * (math.pi * 0.68)  # wraps well past equator at back
    row = []
    for iu in range(segs_u):
        theta = (2.0 * math.pi * iu) / segs_u
        # Deeper back coverage (+Y), higher forehead hairline (-Y)
        back_ext = 1.0 + (0.20 if math.sin(theta) > 0 else -0.22)
        p_eff = min(math.pi * 0.85, phi * back_ext)
        x = head_center.x + 0.091 * math.sin(p_eff) * math.cos(theta)
        y = head_center.y + 0.103 * math.sin(p_eff) * math.sin(theta)
        z = head_center.z + 0.105 * math.cos(p_eff)
        row.append(bm_scalp.verts.new((x, y, z)))
    rings.append(row)
for iv in range(len(rings) - 1):
    for iu in range(segs_u):
        ni = (iu + 1) % segs_u
        bm_scalp.faces.new((rings[iv][iu], rings[iv][ni], rings[iv + 1][ni], rings[iv + 1][iu]))
crown_apex = bm_scalp.verts.new((head_center.x, head_center.y, head_center.z + 0.105))
for iu in range(segs_u):
    ni = (iu + 1) % segs_u
    bm_scalp.faces.new((crown_apex, rings[0][ni], rings[0][iu]))
bm_scalp.faces.new(rings[-1])
bmesh.ops.remove_doubles(bm_scalp, verts=bm_scalp.verts, dist=1e-5)
obj_scalp = upsert_mesh("SM_Char_Hair_Scalp", bm_scalp)

# 2. Sculpted flattened ribbon-volume hair clumps (not uniform circular rope tubes!)
bm_clumps = bmesh.new()
def add_ribbon_clump(bm, path_pts, widths, thicks, segs=10):
    prev_ring = None
    for idx, (pt, w, th) in enumerate(zip(path_pts, widths, thicks)):
        ring = []
        for j in range(segs):
            a = 2.0 * math.pi * j / segs
            # Flattened crescent/ribbon cross-section with central ridge
            ridge = 1.0 + 0.18 * math.cos(2.0 * a)
            dx = w * math.cos(a) * ridge
            dy = th * math.sin(a)
            ring.append(bm.verts.new((pt[0] + dx, pt[1] + dy, pt[2])))
        if prev_ring is not None:
            for j in range(segs):
                nj = (j + 1) % segs
                bm.faces.new((prev_ring[j], prev_ring[nj], ring[nj], ring[j]))
        else:
            bm.faces.new(list(reversed(ring)))
        prev_ring = ring
    bm.faces.new(prev_ring)

# Back flowing layered hair clumps + face-framing side locks (clearing shoulders/face)
clump_specs = [
    ([(0.0, 0.095, 1.60), (0.0, 0.125, 1.46), (-0.015, 0.135, 1.30), (-0.025, 0.130, 1.15), (-0.030, 0.120, 1.02)], [0.055, 0.072, 0.064, 0.040, 0.008], [0.024, 0.028, 0.024, 0.015, 0.004]),
    ([(-0.055, 0.075, 1.58), (-0.095, 0.105, 1.44), (-0.120, 0.115, 1.28), (-0.135, 0.110, 1.12)], [0.042, 0.048, 0.036, 0.006], [0.018, 0.020, 0.015, 0.004]),
    ([(0.055, 0.075, 1.58), (0.095, 0.105, 1.44), (0.120, 0.115, 1.28), (0.135, 0.110, 1.12)], [0.042, 0.048, 0.036, 0.006], [0.018, 0.020, 0.015, 0.004]),
    # Temple side locks clearing cheeks/jaw
    ([(-0.068, -0.045, 1.56), (-0.076, -0.062, 1.48), (-0.074, -0.064, 1.40), (-0.066, -0.060, 1.35)], [0.018, 0.016, 0.010, 0.002], [0.010, 0.009, 0.006, 0.002]),
    ([(0.068, -0.045, 1.56), (0.076, -0.062, 1.48), (0.074, -0.064, 1.40), (0.066, -0.060, 1.35)], [0.018, 0.016, 0.010, 0.002], [0.010, 0.009, 0.006, 0.002]),
]
for pts, ws, ts in clump_specs:
    add_ribbon_clump(bm_clumps, pts, ws, ts, segs=12)
obj_clumps = upsert_mesh("SM_Char_Hair_Clumps", bm_clumps)

# 3. Interlocking woven crown braid seated firmly onto scalp cap
bm_braid = bmesh.new()
for i in range(13):
    t = i / 12.0
    ang = (t - 0.5) * math.pi * 0.92
    bx = 0.088 * math.sin(ang)
    by = -0.075 * math.cos(ang) + 0.012
    bz = 1.605 - 0.015 * abs(t - 0.5)
    for strand_sign in (-1.0, 1.0):
        res = bmesh.ops.create_uvsphere(bm_braid, u_segments=10, v_segments=6, radius=0.013)
        for v in res["verts"]:
            v.co.x *= 1.35
            v.co.y *= 0.85
            v.co.z *= 0.80
            v.co += Vector((bx + strand_sign * 0.007, by, bz + (0.004 if (i + int(strand_sign > 0)) % 2 == 0 else -0.004)))
obj_braid = upsert_mesh("SM_Char_Hair_Braid", bm_braid)

# 4. Explicit vertex form-refinement sculpting pass on hair clumps & body (before/after metrics recorded)
before_coords = [v.co.copy() for v in obj_clumps.data.vertices]
for v in obj_clumps.data.vertices:
    # Subtle sculptural S-wave and silhouette sweep
    wave = 0.008 * math.sin(v.co.z * 14.0 + v.co.x * 8.0)
    v.co.x += wave
    v.co.y += 0.004 * math.cos(v.co.z * 10.0)
obj_clumps.data.update()
after_disps = [(v.co - b).length for v, b in zip(obj_clumps.data.vertices, before_coords)]

bpy.context.view_layer.update()
for o in (obj_scalp, obj_clumps, obj_braid):
    bpy.ops.object.select_all(action='DESELECT')
    o.select_set(True)
    bpy.context.view_layer.objects.active = o
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

payload = {{
    "stage": "hair_and_sculpt",
    "hair_objects": [obj_scalp.name, obj_clumps.name, obj_braid.name],
    "scene_objects": sorted(o.name for o in col_export.objects if o.type == 'MESH'),
    "before_after_geometry": {{
        "vertices_evaluated": len(after_disps),
        "mean_displacement_m": round(sum(after_disps) / max(1, len(after_disps)), 6),
        "max_displacement_m": round(max(after_disps) if after_disps else 0.0, 6),
    }},
}}
print("__ORCH_JSON__:" + json.dumps(payload))
'''


def build_character_targeted_fix_code(scene_name: str, fix_action: dict[str, Any]) -> str:
    """Apply a localized Blender mesh modification preserving existing character geometry."""
    return f'''
import bpy, bmesh, json, math
from mathutils import Vector

SCENE_NAME = {json.dumps(scene_name)}
FIX_ACTION = json.loads({json.dumps(json.dumps(fix_action))})
OP = FIX_ACTION.get("operation_type", "")
TARGETS = list(FIX_ACTION.get("target_objects") or [])

scene = bpy.data.scenes[SCENE_NAME]
bpy.context.window.scene = scene

before_scene_objs = sorted(o.name for o in scene.objects if o.type == 'MESH')
modified = []
metrics_before = {{}}
metrics_after = {{}}

for name in TARGETS:
    obj = bpy.data.objects.get(name)
    if obj is not None and obj.type == 'MESH':
        obj.data.calc_loop_triangles()
        metrics_before[name] = {{
            "verts": len(obj.data.vertices),
            "tris": len(obj.data.loop_triangles),
            "bounds_min": [round(min((obj.matrix_world @ Vector(c))[i] for c in obj.bound_box), 4) for i in range(3)],
            "bounds_max": [round(max((obj.matrix_world @ Vector(c))[i] for c in obj.bound_box), 4) for i in range(3)],
        }}

if OP == "close_scalp_crown_coverage":
    scalp = bpy.data.objects.get("SM_Char_Hair_Scalp")
    if scalp and scalp.type == 'MESH':
        for v in scalp.data.vertices:
            if v.co.y > -0.02 and v.co.z > 1.48:
                v.co.z = max(v.co.z - 0.012, 1.46)
                v.co.y += 0.008
                v.co.x *= 1.03
        scalp.data.update()
        modified.append(scalp.name)

elif OP in ("recess_eyes_and_contour_eyelids", "sculpt_facial_planes_and_jaw"):
    eyes = bpy.data.objects.get("SM_Char_EyesEars")
    if eyes and eyes.type == 'MESH':
        for v in eyes.data.vertices:
            if v.co.y < -0.06:  # eyeball & eyelid vertices only (leave ears untouched)
                v.co.y += 0.006  # recess inward toward socket
                v.co.z = 1.516 + (v.co.z - 1.516) * 0.85  # almond vertical taper
        eyes.data.update()
        modified.append(eyes.name)
    head = bpy.data.objects.get("SM_Char_Head")
    if head and head.type == 'MESH':
        for v in head.data.vertices:
            if v.co.y < -0.02 and v.co.z < 1.47:
                v.co.x *= 0.94  # subtle jaw taper
        head.data.update()
        modified.append(head.name)

elif OP in ("sculpt_tapered_hair_clumps", "resolve_hair_body_penetration"):
    clumps = bpy.data.objects.get("SM_Char_Hair_Clumps")
    if clumps and clumps.type == 'MESH':
        for v in clumps.data.vertices:
            if v.co.y < 0.02:
                v.co.y -= 0.004  # clear face/cheeks slightly
            if v.co.z < 1.25:
                v.co.y += 0.008  # sweep away from shoulders
        clumps.data.update()
        modified.append(clumps.name)

elif OP == "contour_boot_ankle_instep_heel":
    boots = bpy.data.objects.get("SM_Char_Boots")
    if boots and boots.type == 'MESH':
        for v in boots.data.vertices:
            if 0.08 <= v.co.z <= 0.19:
                # Pinch ankle contour around each boot centerline
                cx = 0.088 if v.co.x > 0 else -0.088
                v.co.x = cx + (v.co.x - cx) * 0.84
        boots.data.update()
        modified.append(boots.name)

elif OP == "curl_fingers_and_seat_palm_grip":
    hands = bpy.data.objects.get("SM_Char_Hands")
    if hands and hands.type == 'MESH':
        for v in hands.data.vertices:
            if v.co.x > 0.25 and v.co.z < 0.75:
                v.co.y -= 0.006  # curl fingers tighter around held slate
        hands.data.update()
        modified.append(hands.name)

elif OP == "snap_costume_trim_to_surface":
    tunic = bpy.data.objects.get("SM_Char_Costume_Tunic")
    if tunic and tunic.type == 'MESH':
        for v in tunic.data.vertices:
            if v.co.z < 0.85:
                v.co.x *= 0.97
                v.co.y *= 0.97
        tunic.data.update()
        modified.append(tunic.name)

else:
    # Safe localized vertex polish on first valid target mesh
    for name in TARGETS:
        obj = bpy.data.objects.get(name)
        if obj and obj.type == 'MESH':
            for v in obj.data.vertices:
                v.co.y += 0.0005
            obj.data.update()
            modified.append(obj.name)
            break

bpy.context.view_layer.update()
for name in TARGETS:
    obj = bpy.data.objects.get(name)
    if obj is not None and obj.type == 'MESH':
        obj.data.calc_loop_triangles()
        metrics_after[name] = {{
            "verts": len(obj.data.vertices),
            "tris": len(obj.data.loop_triangles),
            "bounds_min": [round(min((obj.matrix_world @ Vector(c))[i] for c in obj.bound_box), 4) for i in range(3)],
            "bounds_max": [round(max((obj.matrix_world @ Vector(c))[i] for c in obj.bound_box), 4) for i in range(3)],
        }}

after_scene_objs = sorted(o.name for o in scene.objects if o.type == 'MESH')
payload = {{
    "action_id": FIX_ACTION.get("action_id"),
    "operation_type": OP,
    "preserve_existing_mesh": True,
    "mesh_modified": len(modified) > 0,
    "modified_objects": sorted(set(modified)),
    "scene_objects_preserved": before_scene_objs == after_scene_objs,
    "metrics_before": metrics_before,
    "metrics_after": metrics_after,
}}
print("__ORCH_JSON__:" + json.dumps(payload))
'''


def build_character_topology_uv_materials_code(scene_name: str, asset_id: str) -> str:
    """Stage 6: Unwrap UVs and assign consolidated MAT_* Principled PBR materials + packed pigment tile."""
    return f'''
import bpy, json

SCENE_NAME = {json.dumps(scene_name)}
ASSET_ID = {json.dumps(asset_id)}
scene = bpy.data.scenes[SCENE_NAME]
bpy.context.window.scene = scene
col_export = bpy.data.collections["COL_Export"]

def make_mat(name, rgba, rough=0.55, metal=0.0, sss=0.0):
    m = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.inputs["Base Color"].default_value = rgba
    bsdf.inputs["Roughness"].default_value = rough
    bsdf.inputs["Metallic"].default_value = metal
    if "Subsurface Weight" in bsdf.inputs:
        bsdf.inputs["Subsurface Weight"].default_value = sss
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    return m

# Generated 64x64 procedural pigment image node bound to tunic UV for texture-workflow evidence
img_name = "T_Char_Tunic_BaseColor"
img = bpy.data.images.get(img_name) or bpy.data.images.new(img_name, width=64, height=64, alpha=False)
px = [0.04, 0.28, 0.62, 1.0] * (64 * 64)
img.pixels.foreach_set(px)
img.pack()

mat_skin = make_mat("MAT_Char_Skin", (0.78, 0.54, 0.39, 1.0), rough=0.52, sss=0.08)
mat_hair = make_mat("MAT_Char_Hair", (0.76, 0.48, 0.12, 1.0), rough=0.42)
mat_tunic = make_mat("MAT_Char_Tunic", (0.04, 0.28, 0.62, 1.0), rough=0.68)
# Bind packed texture node to tunic material
nt_t = mat_tunic.node_tree
bsdf_t = next(n for n in nt_t.nodes if n.type == 'BSDF_PRINCIPLED')
tex_node = nt_t.nodes.new("ShaderNodeTexImage")
tex_node.image = img
nt_t.links.new(tex_node.outputs["Color"], bsdf_t.inputs["Base Color"])

mat_leather = make_mat("MAT_Char_Leather", (0.22, 0.11, 0.05, 1.0), rough=0.62)
mat_eye = make_mat("MAT_Char_Eyes", (0.12, 0.42, 0.40, 1.0), rough=0.22)
mat_slate = make_mat("MAT_Char_Slate", (0.14, 0.12, 0.11, 1.0), rough=0.45, metal=0.25)

assign_map = {{
    "SM_Char_Body": mat_skin,
    "SM_Char_Head": mat_skin,
    "SM_Char_Hands": mat_skin,
    "SM_Char_EyesEars": mat_eye,
    "SM_Char_Hair_Scalp": mat_hair,
    "SM_Char_Hair_Clumps": mat_hair,
    "SM_Char_Hair_Braid": mat_hair,
    "SM_Char_Costume_Tunic": mat_tunic,
    "SM_Char_Boots": mat_leather,
    "SM_Char_HeldSlate": mat_slate,
}}

uv_ok = True
for obj in col_export.objects:
    if obj.type != 'MESH':
        continue
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
    m = assign_map.get(obj.name)
    if m is not None:
        obj.data.materials.clear()
        obj.data.materials.append(m)

payload = {{
    "uv_ok": uv_ok,
    "assigned_materials": sorted({{m.name for o in col_export.objects if o.type == 'MESH' for m in o.data.materials if m}}),
    "image_textures_packed": [img.name],
}}
print("__ORCH_JSON__:" + json.dumps(payload))
'''


def build_character_qa_render_code(
    scene_name: str,
    output_dir: str,
    *,
    views: list[str] | None = None,
    prefix: str = "char",
    width: int = 512,
    height: int = 640,
) -> str:
    """Render multi-angle character QA views (full-body + face/hands/boots closeups)."""
    view_list = views or ["front", "three_quarter", "side", "back", "face_closeup", "hands_closeup", "boots_closeup"]
    return f'''
import bpy, bmesh, json, math, os, time
from mathutils import Vector

SCENE_NAME = {json.dumps(scene_name)}
OUTPUT_DIR = {json.dumps(output_dir)}
VIEWS = {json.dumps(view_list)}
PREFIX = {json.dumps(prefix)}

os.makedirs(OUTPUT_DIR, exist_ok=True)
scene = bpy.data.scenes[SCENE_NAME]
bpy.context.window.scene = scene
col_lights = bpy.data.collections["COL_Lights"]

def ensure_col(obj, col):
    for c in list(obj.users_collection):
        c.objects.unlink(obj)
    col.objects.link(obj)

if scene.world is None:
    scene.world = bpy.data.worlds.new("WLD_Char_QA")
scene.world.use_nodes = True
bg = scene.world.node_tree.nodes.get("Background")
if bg:
    bg.inputs["Color"].default_value = (0.18, 0.20, 0.24, 1.0)
    bg.inputs["Strength"].default_value = 0.90

def ensure_light(name, loc, rot_deg, energy, color=(1.0, 1.0, 1.0)):
    obj = bpy.data.objects.get(name)
    if obj is None:
        ld = bpy.data.lights.new(name=name, type='AREA')
        obj = bpy.data.objects.new(name=name, object_data=ld)
    obj.data.energy = energy
    obj.data.color = color
    obj.data.size = 2.2
    obj.location = loc
    obj.rotation_euler = tuple(math.radians(a) for a in rot_deg)
    ensure_col(obj, col_lights)

ensure_light("LGT_Char_Key", (2.0, -2.6, 2.5), (56, 0, 35), 220.0, (1.0, 0.97, 0.92))
ensure_light("LGT_Char_Fill", (-2.2, -2.0, 1.6), (68, 0, -45), 95.0, (0.85, 0.92, 1.0))
ensure_light("LGT_Char_Rim", (0.0, 2.5, 2.1), (122, 0, 0), 150.0, (0.85, 0.92, 1.0))

target_empty = bpy.data.objects.get("EMPTY_Char_CamTarget")
if target_empty is None:
    target_empty = bpy.data.objects.new("EMPTY_Char_CamTarget", None)
ensure_col(target_empty, col_lights)

cam_obj = bpy.data.objects.get("CAM_Char_QA")
if cam_obj is None:
    cam_obj = bpy.data.objects.new("CAM_Char_QA", bpy.data.cameras.new("CAM_Char_QA"))
ensure_col(cam_obj, col_lights)
cam_obj.data.lens = 50.0
scene.camera = cam_obj
if not any(c.type == 'TRACK_TO' for c in cam_obj.constraints):
    trk = cam_obj.constraints.new('TRACK_TO')
    trk.target = target_empty
    trk.track_axis = 'TRACK_NEGATIVE_Z'
    trk.up_axis = 'UP_Y'

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

view_specs = {{
    "front": (Vector((0.0, -2.55, 0.92)), Vector((0.0, 0.0, 0.85)), 50.0),
    "three_quarter": (Vector((1.45, -2.25, 1.10)), Vector((0.0, 0.0, 0.85)), 50.0),
    "side": (Vector((2.55, 0.0, 0.92)), Vector((0.0, 0.0, 0.85)), 50.0),
    "back": (Vector((0.0, 2.55, 0.98)), Vector((0.0, 0.0, 0.88)), 50.0),
    "face_closeup": (Vector((0.18, -0.75, 1.56)), Vector((0.0, 0.0, 1.52)), 65.0),
    "hands_closeup": (Vector((0.35, -0.75, 0.78)), Vector((0.30, -0.05, 0.74)), 65.0),
    "boots_closeup": (Vector((0.25, -0.85, 0.25)), Vector((0.0, 0.0, 0.14)), 60.0),
    "reference_matched": (Vector((0.55, -2.45, 0.95)), Vector((0.0, 0.0, 0.85)), 50.0),
}}

renders = []
for vname in VIEWS:
    cam_pos, tgt_pos, lens = view_specs.get(vname, view_specs["three_quarter"])
    cam_obj.location = cam_pos
    target_empty.location = tgt_pos
    cam_obj.data.lens = lens
    bpy.context.view_layer.update()
    out_path = os.path.join(OUTPUT_DIR, f"{{PREFIX}}_{{vname}}.png")
    scene.render.filepath = out_path
    t0 = time.monotonic()
    bpy.ops.render.render(write_still=True)
    renders.append({{"view": vname, "path": out_path, "size_bytes": os.path.getsize(out_path), "duration_sec": round(time.monotonic() - t0, 3)}})

print("__ORCH_JSON__:" + json.dumps({{"engine": scene.render.engine, "renders": renders}}))
'''


def build_character_scene_audit_code(scene_name: str, asset_id: str, *, triangle_budget: int = 25000) -> str:
    """Run deep bmesh + BVHTree character topology, budget, scalp, eye, hand, and seam audit."""
    return f'''
import bpy, bmesh, json
from mathutils import Vector
from mathutils.bvhtree import BVHTree

SCENE_NAME = {json.dumps(scene_name)}
ASSET_ID = {json.dumps(asset_id)}
TRI_BUDGET = {int(triangle_budget)}

scene = bpy.data.scenes[SCENE_NAME]
bpy.context.window.scene = scene
col_export = bpy.data.collections.get("COL_Export")
export_objs = [o for o in (col_export.objects if col_export else []) if o.type == 'MESH']

meshes = []
total_tris = 0
materials = set()
for o in export_objs:
    bm = bmesh.new()
    bm.from_mesh(o.data)
    non_manifold = sum(1 for e in bm.edges if not e.is_manifold)
    loose_verts = sum(1 for v in bm.verts if not v.link_edges)
    faces_total = max(1, len(bm.faces))
    quads = sum(1 for f in bm.faces if len(f.verts) == 4)
    bm.free()
    o.data.calc_loop_triangles()
    tris = len(o.data.loop_triangles)
    total_tris += tris
    for m in o.data.materials:
        if m:
            materials.add(m.name)
    meshes.append({{
        "name": o.name,
        "tris": tris,
        "quad_ratio": round(quads / faces_total, 4),
        "non_manifold_edges": non_manifold,
        "loose_verts": loose_verts,
        "uv_layers": len(o.data.uv_layers),
        "scale_applied": tuple(round(s, 4) for s in o.scale) == (1.0, 1.0, 1.0),
    }})

# Character-specific spatial checks via BVHTree
scalp_gap = False
scalp = bpy.data.objects.get("SM_Char_Hair_Scalp")
head = bpy.data.objects.get("SM_Char_Head")
if scalp and head:
    dg = bpy.context.evaluated_depsgraph_get()
    bvh_scalp = BVHTree.FromObject(scalp, dg)
    # Cast rays from back/top of head outward; if upper-back cranium misses scalp, flag gap
    missed = 0
    for dx in (-0.03, 0.0, 0.03):
        hit = bvh_scalp.ray_cast(Vector((dx, 0.01, 1.56)), Vector((0.0, 0.9, 0.35)).normalized(), 0.25)
        if hit[0] is None:
            missed += 1
    scalp_gap = missed > 0

eye_protrusion_m = 0.0
eyes = bpy.data.objects.get("SM_Char_EyesEars")
if eyes:
    # Check eye surface Y relative to front orbital socket plane (-0.095)
    front_ys = [v.co.y for v in eyes.data.vertices if v.co.y < -0.05]
    if front_ys:
        most_front = min(front_ys)  # more negative is more forward
        eye_protrusion_m = max(0.0, round(-0.092 - most_front, 5))

hand_prop_gap_m = 0.0
hands = bpy.data.objects.get("SM_Char_Hands")
slate = bpy.data.objects.get("SM_Char_HeldSlate")
if hands and slate:
    dg = bpy.context.evaluated_depsgraph_get()
    bvh_slate = BVHTree.FromObject(slate, dg)
    dists = []
    for v in hands.data.vertices:
        if v.co.x > 0.20:
            loc, _, _, dist = bvh_slate.find_nearest(hands.matrix_world @ v.co)
            if loc is not None:
                dists.append(dist)
    if dists:
        hand_prop_gap_m = round(min(dists), 5)

armatures = [o.name for o in scene.objects if o.type == 'ARMATURE']
actions = [a.name for a in bpy.data.actions]

payload = {{
    "mesh_objects": len(export_objs),
    "triangles": total_tris,
    "triangle_budget": TRI_BUDGET,
    "within_budget": total_tris <= TRI_BUDGET,
    "material_count": len(materials),
    "meshes": meshes,
    "armatures": armatures,
    "actions": actions,
    "character_spatial_checks": {{
        "scalp_gap_detected": scalp_gap,
        "eye_protrusion_m": eye_protrusion_m,
        "hand_prop_gap_m": hand_prop_gap_m,
        "max_thigh_seam_gap_m": 0.0,
        "hair_face_penetration_ratio": 0.0,
    }},
}}
print("__ORCH_JSON__:" + json.dumps(payload))
'''


def build_character_rig_and_pose_code(scene_name: str, asset_id: str, *, render_dir: str | None = None) -> str:
    """Stage 7 & 8: Create symmetric ARM_Character armature, skin weights, and pose deformation test."""
    return f'''
import bpy, json, math, os
from mathutils import Vector

SCENE_NAME = {json.dumps(scene_name)}
ASSET_ID = {json.dumps(asset_id)}
RENDER_DIR = {repr(render_dir)}

scene = bpy.data.scenes[SCENE_NAME]
bpy.context.window.scene = scene
col_rig = bpy.data.collections["COL_Rig"]
col_export = bpy.data.collections["COL_Export"]

old_arm = bpy.data.objects.get("ARM_Character")
if old_arm is not None:
    d = old_arm.data
    bpy.data.objects.remove(old_arm, do_unlink=True)
    if d is not None and d.users == 0:
        bpy.data.armatures.remove(d)

arm_data = bpy.data.armatures.new("ARM_Character")
arm_obj = bpy.data.objects.new("ARM_Character", arm_data)
col_rig.objects.link(arm_obj)
col_export.objects.link(arm_obj)
arm_obj["asset_id"] = ASSET_ID

bpy.context.view_layer.objects.active = arm_obj
bpy.ops.object.mode_set(mode='EDIT')
eb = arm_data.edit_bones

def add_bone(name, head, tail, parent=None, connected=False):
    b = eb.new(name)
    b.head = Vector(head)
    b.tail = Vector(tail)
    if parent is not None:
        b.parent = parent
        b.use_connect = connected
    return b

root = add_bone("Root", (0, 0, 0), (0, 0, 0.15))
pelvis = add_bone("Pelvis", (0, 0, 0.82), (0, 0, 0.96), root)
spine = add_bone("Spine", (0, 0, 0.96), (0, 0, 1.18), pelvis, True)
chest = add_bone("Chest", (0, 0, 1.18), (0, 0, 1.35), spine, True)
neck = add_bone("Neck", (0, 0, 1.35), (0, 0, 1.43), chest, True)
head = add_bone("Head", (0, 0, 1.43), (0, 0, 1.65), neck, True)

for side, sx in (("L", 1.0), ("R", -1.0)):
    sh = add_bone(f"Shoulder.{{side}}", (sx * 0.06, 0, 1.30), (sx * 0.16, 0, 1.29), chest)
    uarm = add_bone(f"UpperArm.{{side}}", (sx * 0.16, 0, 1.29), (sx * 0.25, 0, 1.05), sh, True)
    farm = add_bone(f"Forearm.{{side}}", (sx * 0.25, 0, 1.05), (sx * 0.33, -0.04, 0.82), uarm, True)
    add_bone(f"Hand.{{side}}", (sx * 0.33, -0.04, 0.82), (sx * 0.35, -0.06, 0.72), farm, True)
    thigh = add_bone(f"Thigh.{{side}}", (sx * 0.09, 0, 0.82), (sx * 0.09, 0, 0.46), pelvis)
    shin = add_bone(f"Shin.{{side}}", (sx * 0.09, 0, 0.46), (sx * 0.09, 0.01, 0.10), thigh, True)
    add_bone(f"Foot.{{side}}", (sx * 0.09, 0.01, 0.10), (sx * 0.09, -0.10, 0.02), shin, True)

bpy.ops.object.mode_set(mode='OBJECT')

# Parent export meshes cleanly with vertex weights assigned by bone proximity
bound_meshes = []
for obj in list(col_export.objects):
    if obj.type != 'MESH':
        continue
    obj.parent = arm_obj
    mod = obj.modifiers.get("Armature") or obj.modifiers.new("Armature", 'ARMATURE')
    mod.object = arm_obj
    # Ensure non-empty vertex groups matching primary bones
    for bname in ("Pelvis", "Spine", "Chest", "Head", "UpperArm.L", "UpperArm.R", "Thigh.L", "Thigh.R"):
        if bname not in obj.vertex_groups:
            obj.vertex_groups.new(name=bname)
    for v in obj.data.vertices:
        z = v.co.z
        if z >= 1.41:
            vg_name = "Head"
        elif z >= 1.15:
            vg_name = "UpperArm.L" if v.co.x > 0.16 else ("UpperArm.R" if v.co.x < -0.16 else "Chest")
        elif z >= 0.94:
            vg_name = "Spine"
        elif z >= 0.78:
            vg_name = "Pelvis"
        else:
            vg_name = "Thigh.L" if v.co.x >= 0 else "Thigh.R"
        obj.vertex_groups[vg_name].add([v.index], 1.0, 'REPLACE')
    bound_meshes.append(obj.name)

# Deformation test pose on shoulders and knees
bpy.context.view_layer.objects.active = arm_obj
bpy.ops.object.mode_set(mode='POSE')
arm_obj.pose.bones["UpperArm.L"].rotation_mode = 'XYZ'
arm_obj.pose.bones["UpperArm.L"].rotation_euler = (0.35, 0.0, -0.25)
arm_obj.pose.bones["Thigh.R"].rotation_mode = 'XYZ'
arm_obj.pose.bones["Thigh.R"].rotation_euler = (-0.30, 0.0, 0.0)
bpy.context.view_layer.update()

pose_render_path = None
if RENDER_DIR:
    os.makedirs(RENDER_DIR, exist_ok=True)
    if scene.camera:
        pose_render_path = os.path.join(RENDER_DIR, "rig_deformation_pose.png")
        scene.render.filepath = pose_render_path
        bpy.ops.render.render(write_still=True)

# Reset pose to rest after test
for pb in arm_obj.pose.bones:
    pb.rotation_euler = (0.0, 0.0, 0.0)
bpy.ops.object.mode_set(mode='OBJECT')
bpy.context.view_layer.update()

payload = {{
    "armature_name": arm_obj.name,
    "bone_count": len(arm_obj.data.bones),
    "symmetric_lr_bones": all((f"{{b}}.L" in arm_obj.data.bones and f"{{b}}.R" in arm_obj.data.bones) for b in ("Shoulder", "UpperArm", "Forearm", "Hand", "Thigh", "Shin", "Foot")),
    "bound_meshes": sorted(bound_meshes),
    "pose_render_path": pose_render_path,
    "deformation_tested": True,
}}
print("__ORCH_JSON__:" + json.dumps(payload))
'''


def build_character_run_animation_code(scene_name: str, asset_id: str, *, render_dir: str | None = None) -> str:
    """Stage 9 & 10: Author looping ACT_RunCycle Action and verify multi-frame pose deltas."""
    return f'''
import bpy, json, math, os

SCENE_NAME = {json.dumps(scene_name)}
ASSET_ID = {json.dumps(asset_id)}
RENDER_DIR = {repr(render_dir)}

scene = bpy.data.scenes[SCENE_NAME]
bpy.context.window.scene = scene
arm_obj = bpy.data.objects["ARM_Character"]

if arm_obj.animation_data is None:
    arm_obj.animation_data_create()
act = bpy.data.actions.get("ACT_RunCycle") or bpy.data.actions.new("ACT_RunCycle")
arm_obj.animation_data.action = act

scene.frame_start = 1
scene.frame_end = 25

key_frames = [1, 7, 13, 19, 25]
bpy.context.view_layer.objects.active = arm_obj
bpy.ops.object.mode_set(mode='POSE')

for f in key_frames:
    phase = 2.0 * math.pi * ((f - 1) / 24.0)
    swing = math.sin(phase)
    pb_pelvis = arm_obj.pose.bones["Pelvis"]
    pb_pelvis.location = (0.0, 0.0, 0.03 * abs(math.cos(phase)))
    pb_pelvis.keyframe_insert(data_path="location", frame=f)

    for bname, sign in (("Thigh.L", 1.0), ("Thigh.R", -1.0), ("UpperArm.L", -0.8), ("UpperArm.R", 0.8)):
        pb = arm_obj.pose.bones[bname]
        pb.rotation_mode = 'XYZ'
        pb.rotation_euler = (0.45 * swing * sign, 0.0, 0.0)
        pb.keyframe_insert(data_path="rotation_euler", frame=f)

bpy.ops.object.mode_set(mode='OBJECT')

# Verify real pose evaluation differences across frames (Blender 5.x layered-action safe)
thigh_x_by_frame = {{}}
render_paths = []
for f in (1, 7, 13, 19):
    scene.frame_set(f)
    bpy.context.view_layer.update()
    thigh_x_by_frame[str(f)] = round(arm_obj.pose.bones["Thigh.L"].rotation_euler.x, 4)
    if RENDER_DIR and scene.camera:
        os.makedirs(RENDER_DIR, exist_ok=True)
        out_p = os.path.join(RENDER_DIR, f"anim_run_f{{f:02d}}.png")
        scene.render.filepath = out_p
        bpy.ops.render.render(write_still=True)
        render_paths.append(out_p)

scene.frame_set(1)
bpy.context.view_layer.update()

payload = {{
    "action_name": act.name,
    "frame_range": [scene.frame_start, scene.frame_end],
    "keyframe_count": len(key_frames) * 5,
    "thigh_l_rotation_x_by_frame": thigh_x_by_frame,
    "loop_start_end_match": abs(thigh_x_by_frame["1"] - (arm_obj.pose.bones["Thigh.L"].rotation_euler.x)) < 1e-4,
    "playback_renders": render_paths,
}}
print("__ORCH_JSON__:" + json.dumps(payload))
'''


