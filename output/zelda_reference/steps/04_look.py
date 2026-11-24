import numpy as np
D.joinpath('textures').mkdir(exist_ok=True)
N=512
Y,X=np.indices((N,N),dtype=np.float32);X/=N;Y/=N
def tex(name,kind,strength):
 m=M[name];nt=m.node_tree;bs=next(n for n in nt.nodes if n.type=='BSDF_PRINCIPLED')
 rng=np.random.default_rng(sum(ord(c) for c in name)*21)
 base=np.array(m.diffuse_color[:3],dtype=np.float32)
 f=np.zeros((N,N),dtype=np.float32)
 for band in (2,4,8,16,32,64):
  for j in range(6):
   a,b=rng.integers(-band,band+1,size=2);f+=np.sin(2*pi*(a*X+b*Y)+rng.uniform(0,2*pi))*(band/2)**-.33*.11
 if kind=='weave':f+=.19*np.sin(2*pi*104*X)*np.sin(2*pi*126*Y)+.11*np.sin(2*pi*6*Y+np.sin(2*pi*X*3))
 if kind=='hide': f+=.13*np.sin(2*pi*(60*X+27*Y)+1.5*np.sin(2*pi*15*Y))
 if kind=='hair': f+=.19*np.sin(2*pi*19*X+np.sin(2*pi*Y)*.7)+.10*np.sin(2*pi*42*X)
 if kind=='stone': f+=.16*np.maximum(0,np.sin(2*pi*14*X+np.sin(2*pi*21*Y))*np.sin(2*pi*9*Y))
 rgba=np.ones((N,N,4),dtype=np.float32);rgba[:,:,:3]=np.clip((1+strength*f[:,:,None])*base,0,1)
 rgba[:,:,:3]=np.where(rgba[:,:,:3]<.0031308,12.92*rgba[:,:,:3],1.055*rgba[:,:,:3]**(1/2.4)-.055)
 n='T_Zelda_'+name+'_BaseColor';im=bpy.data.images.get(n) or bpy.data.images.new(n,width=N,height=N,alpha=False)
 im.colorspace_settings.name='sRGB';im.pixels.foreach_set(rgba.ravel());im.update();im.filepath_raw=str(D/'textures'/(n+'.png'));im.file_format='PNG';im.save();im.pack()
 node=next((q for q in nt.nodes if q.type=='TEX_IMAGE'),None) or nt.nodes.new('ShaderNodeTexImage');node.image=im;node.label='Original procedural pigment tile';node.location=(-400,180);nt.links.new(node.outputs['Color'],bs.inputs['Base Color'])
 if kind in {'weave','hide','stone'}:
  bump=next((q for q in nt.nodes if q.type=='BUMP'),None) or nt.nodes.new('ShaderNodeBump');bump.location=(-160,-80);bump.inputs['Strength'].default_value=.16 if kind!='stone' else .20;bump.inputs['Distance'].default_value=.0007;nt.links.new(node.outputs['Color'],bump.inputs['Height']);nt.links.new(bump.outputs[0],bs.inputs['Normal'])
for row in [('Azure','weave',.57),('AzureDark','weave',.46),('Ivory','weave',.54),('LinenShadow','weave',.50),('Pants','weave',.65),('Leather','hide',.70),('SlateEdge','stone',.80),('Honey','hair',.40),('Hair','hair',.48)]:tex(*row)
for name in ['Azure','AzureDark','Ivory','LinenShadow','Pants','PantsHigh']:
 q=next(a for a in M[name].node_tree.nodes if a.type=='BSDF_PRINCIPLED');q.inputs['Sheen Weight'].default_value=.17;q.inputs['Roughness'].default_value=.77
for name in ['Hair','Honey','Shine','Ochre']:
 q=next(a for a in M[name].node_tree.nodes if a.type=='BSDF_PRINCIPLED');q.inputs['Roughness'].default_value=.56;q.inputs['Specular IOR Level'].default_value=.28
q=next(a for a in M['Skin'].node_tree.nodes if a.type=='BSDF_PRINCIPLED');q.inputs['Subsurface Weight'].default_value=.072;q.inputs['Subsurface Scale'].default_value=.013
for name,k in [('Glow',2.2),('Gem',.26)]:
 q=next(a for a in M[name].node_tree.nodes if a.type=='BSDF_PRINCIPLED');q.inputs['Emission Color'].default_value=M[name].diffuse_color;q.inputs['Emission Strength'].default_value=k

def light(n,pos,target,power,size,color):
 o=S.objects.get('LGT_Study_'+n)
 if o is None:
  data=bpy.data.lights.new('LGT_Study_'+n,'AREA');o=bpy.data.objects.new(data.name,data);collection('Lighting').objects.link(o)
 o.location=pos;o.rotation_euler=(Vector(target)-o.location).to_track_quat('-Z','Y').to_euler();o.data.energy=power;o.data.shape='DISK';o.data.size=size;o.data.color=color
light('Key',(-1.15,-1.6,2.38),(0,0,1.04),94,1.33,(1,.91,.78))
light('Fill',(1.2,-1.2,1.25),(0,0,1.12),24,1.65,(.82,.90,1))
light('Rim',(.22,.77,2.39),(0,0,1.10),43,.90,(1,.79,.51))
S.view_settings.look='AgX - Medium High Contrast';S.view_settings.exposure=.30
S.render.image_settings.file_format='PNG';S.render.image_settings.color_mode='RGBA'
def render_to(path,pct,samples):
 S.render.resolution_percentage=pct;S.cycles.samples=samples;S.render.filepath=str(D/path);bpy.ops.render.render(write_still=True,scene=S.name)
print('Texture images:',[(im.name,tuple(im.size),bool(im.packed_file)) for im in bpy.data.images if im.name.startswith('T_Zelda_')])
bpy.data.libraries.write(str(D/'steps'/'04_look.blend'),{S},fake_user=True)
render_to('steps/hero_v1.png',60,20)
