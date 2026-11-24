import bpy,bmesh,json,struct
from pathlib import Path
from mathutils import Vector
D=Path('/home/jhkim/tools/blender_test/output/zelda_reference')
win=bpy.context.window;restore=win.scene
with bpy.data.libraries.load(str(D/'zelda_reference.blend')) as (src,dst):
 assert src.scenes==['Zelda_Reference_Study'],src.scenes
 dst.scenes=src.scenes
source=dst.scenes[0];win.scene=source
dg=bpy.context.evaluated_depsgraph_get()
export=bpy.data.scenes.new('QA_Export_Staging');export.unit_settings.system='METRIC'
orig=next(o for o in source.objects if o.type=='EMPTY' and o.name.startswith('ROOT_Reference_Character')).matrix_world.translation.copy()
root=bpy.data.objects.new('Zelda_Asset',None);export.collection.objects.link(root)
for o in source.objects:
 if o.type!='MESH':continue
 e=o.evaluated_get(dg);me=bpy.data.meshes.new_from_object(e,preserve_all_data_layers=True,depsgraph=dg)
 k=bpy.data.objects.new(o.name,me);export.collection.objects.link(k);k.parent=root
 world=e.matrix_world.copy();world.translation-=orig;me.transform(world)
 n=k.name
 ratio=.25 if 'Corset_Welt' in n or 'Slate_LuminousIris' in n else .45 if 'Hair_Scalpcap' in n else .53 if any(s in n for s in ('Braid_','InlayDot_','Bodice_Rib','Corset_Emboss','Corset_Scroll','Finger_Glove_','SlateFingerCap','BeltRivet')) else 1
 if ratio!=1:
  dec=k.modifiers.new('Static export simplification','DECIMATE');dec.ratio=ratio
win.scene=export; bpy.context.view_layer.update()
def inspect(scene):
 bpy.context.view_layer.update();deps=bpy.context.evaluated_depsgraph_get();count=tr=nm=loose=0;mats=set();ims=set();ps=[]
 for o in scene.objects:
  if o.type!='MESH': continue
  count+=1;e=o.evaluated_get(deps);m=e.to_mesh();m.calc_loop_triangles();tr+=len(m.loop_triangles);ps.extend(e.matrix_world@v.co for v in m.vertices)
  b=bmesh.new();b.from_mesh(m);nm+=sum(not k.is_manifold for k in b.edges);loose+=sum(not x.link_edges for x in b.verts);b.free()
  for a in m.materials:
   if not a:continue
   mats.add(a.name)
   if a.use_nodes:ims.update(n.image.name for n in a.node_tree.nodes if n.type=='TEX_IMAGE' and n.image)
  e.to_mesh_clear()
 return dict(meshes=count,triangles=tr,nonmanifold_edges=nm,loose=loose,materials=len(mats),images=len(ims),min_m=[min(p[i] for p in ps) for i in range(3)],max_m=[max(p[i] for p in ps) for i in range(3)])
pre=inspect(export)
assert pre['triangles']<=100000 and pre['nonmanifold_edges']==0 and pre['loose']==0,pre
path=D/'zelda_reference.glb'
bpy.ops.export_scene.gltf(filepath=str(path),export_format='GLB',export_yup=True,export_apply=True,export_materials='EXPORT',export_cameras=False,export_lights=False,export_animations=False,use_active_scene=True)
with path.open('rb') as f:
 magic,v,n=struct.unpack('<4sII',f.read(12));assert magic==b'glTF' and v==2 and n==path.stat().st_size
 ln,chunk=struct.unpack('<I4s',f.read(8));assert chunk==b'JSON';g=json.loads(f.read(ln))
 assert len(g['images'])==9 and all('bufferView' in t and 'uri' not in t for t in g['images'])
 assert not any(g.get(k) for k in ('animations','skins','cameras'))
rt=bpy.data.scenes.new('QA_GLTF_Fresh');win.scene=rt
bpy.ops.import_scene.gltf(filepath=str(path));post=inspect(rt)
assert pre['triangles']==post['triangles'] and pre['meshes']==post['meshes'],(pre,post)
assert max(abs(a-b) for k in ['min_m','max_m'] for a,b in zip(pre[k],post[k]))<1e-5,(pre,post)
data=dict(archive_source_scene_isolated=True,pre_export=pre,fresh_scene_reimport=post,glb_bytes=path.stat().st_size,embedded_images=len(g['images']),assets_only=True,animations=0,armatures=0,glb_profile='GLB v2, +Y up, metres, pivot at bottom toe, uncompressed',visual_clearance='REVIEW CHECKPOINT — NOT A RELEASE')
(D/'export_validation.json').write_text(json.dumps(data,indent=2));win.scene=restore
print(data)
