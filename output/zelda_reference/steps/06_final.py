def rm(n):
 o=S.objects.get('SM_Study_'+n)
 if o:bpy.data.objects.remove(o,do_unlink=True)
for n in ['Corset_SashOverlay','Belt_Corset','Corset_RaisedTop','Corset_RaisedHem']+[f'Corset_Stem{i:02d}' for i in range(12)]+['Corset_Filigree'+str(i) for i in range(3)]:rm(n)
v=[];f=[];uv=[];N=56;L=12
def belt(a,t,offset=0):
 front=max(0,-sin(a));lr=cos(a);z=282+8*front+3*lr+t*(42-13*lr-4*front)
 x=233-11*t+(52+14*t+offset)*lr;d=9+(39+4*sin(pi*t)+offset)*sin(a)
 return (x,z,d)
for j in range(L):
 for i in range(N):v.append(P(*belt(i*2*pi/N,j/(L-1))))
for j in range(L-1):
 for i in range(N):
  k=j*N+i;l=j*N+(i+1)%N;f.append((k,l,l+N,k+N));uv.append(((i/N,j/(L-1)),((i+1)/N,j/(L-1)),((i+1)/N,(j+1)/(L-1)),(i/N,(j+1)/(L-1))))
waist=obj('Corset_Fitted',v,f,'Leather','Costume',uv);md=waist.modifiers.new('Leather thickness','SOLIDIFY');md.thickness=.003;md.offset=-1
for t,m,r in [(.06,'LeatherLight',2.4),(.26,'Seam',.85),(.91,'Gold',1.45),(.98,'LeatherLight',1.7)]:
 strand('Corset_Welt'+str(t),[belt(2*pi*i/56,t,1) for i in range(57)],r,m,N=8)
for i in range(13):
 a=pi+(.25+i*.205);strand('Corset_Emboss%02d'%i,[belt(a-.035,.66,1.2),belt(a+.025,.53,2.2),belt(a+.032,.36,2.1),belt(a-.01,.28,1.3)],[.22,1.0,1.18,.21],'Seam')
for i in range(3):
 strand('Corset_Scroll'+str(i),[belt(pi+(.11+t*.26+i*.14),.85-.52*sin(pi*t*.84),2.2) for t in (0,.2,.4,.6,.8,1)],[.30,1.2,1.35,1.25,1,.25],'Gold')
for k in ('Long','Raised'):
 rm(k+'_BootPiping');rm(k+'_BootStrap');rm(k+'_BootBuckle')
 if k=='Long':x,z,d=183,702,4
 else:x,z,d=128,588,11
 sweep(k+'_BootCollar',[(x+3,z-8,d+5,13,14),(x+2,z-5,d+1,19,21),(x,z+3,d-2,20,23),(x-2,z+10,d-4,16,20)],'Leather',N=28,flute=.018)
 ell(k+'_BootBuckle',x+14,z+2,d-7,4.5,5.7,3.2,'Bronze',angle=19,N=16,L=8)
rm('Hair_TempleNear')
sweep('Hair_TempleNear',[(276,38,-18,9,9),(279,62,-16,7,8),(276,86,-17,5.6,6),(268,108,-14,3.5,4),(261,123,-14,.25,.25)],'Hair','Hair',N=24,steps=6)
for m,h in [('EyeWhite','e5ceb1'),('Teal','17574e'),('IrisDark','102f2d')]:
 q=next(a for a in M[m].node_tree.nodes if a.type=='BSDF_PRINCIPLED');q.inputs['Base Color'].default_value=rgb(h);M[m].diffuse_color=rgb(h)
for o in collection('Face').objects:
 if o.name.endswith('_UpperLash'):
  o.location.y=-.0006
# Locate pivot on the lowest visible toe while compensating original object transforms.
bpy.context.view_layer.update();mn=min((o.matrix_world@Vector(p)).z for o in S.objects if o.type=='MESH' for p in o.bound_box)
for o in list(R.children):o.location.z-=mn
R.location.z+=mn
S['asset_scope']='Static posed editable reference study; no armature, animation or collision'
S['reference_filename']='images.jpg';S['revision']='V3: fitted leather wrap, clean boot collars, restrained temple lock and eye tones'

cr=S.camera;ca=collection('Cameras');tar=S.objects['EMPTY_CamTarget_Reference'];
def camera(n,pos,target,scale):
 o=S.objects.get(n)
 if o is None:
  d=bpy.data.cameras.new(n);o=bpy.data.objects.new(n,d);ca.objects.link(o)
 o.data.type='ORTHO';o.data.ortho_scale=scale;o.location=pos;o.rotation_euler=(Vector(target)-o.location).to_track_quat('-Z','Y').to_euler();return o
alt=camera('CAM_Study_ThreeQuarter',(2.7,-3.5,1.44),(0,0,.85),1.86)
details=camera('CAM_Study_Detail',(.13,-4,1.42),(.13,0,1.42),.73)
def render(n,cam,w,h,samples):
 S.camera=cam;S.render.resolution_x=w;S.render.resolution_y=h;S.render.resolution_percentage=100;S.cycles.samples=samples;S.render.filepath=str(D/n);bpy.ops.render.render(write_still=True,scene=S.name)
render('hero.png',cr,700,1400,42)
render('detail.png',details,900,900,40)
render('three_quarter.png',alt,850,1220,38)
S.camera=cr;S.render.resolution_x=700;S.render.resolution_y=1400;S.cycles.samples=42;S.render.filepath='//hero.png';
for screen in bpy.data.screens:
 if screen==bpy.context.screen:
  for a in screen.areas:
   if a.type=='VIEW_3D':
    a.spaces.active.region_3d.view_perspective='CAMERA'
bpy.data.libraries.write(str(D/'zelda_reference.blend'),{S},fake_user=True,compress=True)
print('V3 renders and isolated scene saved')
