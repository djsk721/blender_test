import bpy, bmesh, math, json
from pathlib import Path
from mathutils import Vector
from math import sin,cos,pi,sqrt
D=Path('/home/jhkim/tools/blender_test/output/zelda_reference')
S=bpy.data.scenes['Zelda_Reference_Study']
R=S.objects['ROOT_Reference_Character']
K=.00216
def P(x,z,d=0): return Vector(((x-193.5)*K,d*K,(782-z)*K))
def srgb(x): return x/12.92 if x<.04045 else ((x+.055)/1.055)**2.4
def rgb(h): return tuple(srgb(int(h[i:i+2],16)/255) for i in (0,2,4))+(1,)
def mat(n,h,rough=.56,metal=0):
 m=bpy.data.materials.get('MAT_Study_'+n) or bpy.data.materials.new('MAT_Study_'+n);m.use_nodes=True;m.diffuse_color=rgb(h)
 p=next(a for a in m.node_tree.nodes if a.type=='BSDF_PRINCIPLED');p.inputs['Base Color'].default_value=m.diffuse_color;p.inputs['Roughness'].default_value=rough;p.inputs['Metallic'].default_value=metal
 return m
palette={'Skin':'e9af7b','Ear':'cb895f','Lip':'a35c45','Crease':'774633','Ivory':'e4dcc3','LinenShadow':'b5a887','Azure':'0676b6','AzureLight':'1595cb','AzureDark':'084477','Pants':'312923','PantsHigh':'50433a','Leather':'63381f','LeatherLight':'94552a','Seam':'b78248','Gold':'c49035','GoldLight':'e4b859','Hair':'bc791b','Honey':'df9c22','Shine':'efb53b','Ochre':'975313','Glove':'2b2019','Sole':'3a3532','Slate':'322c29','Bronze':'a57138','SlateEdge':'534033','Gem':'198ada','Glow':'47cdff','EyeWhite':'ffefe1','Teal':'357a75','IrisDark':'164a4a','Ink':'211820','Catch':'fff9ec'}
M={n:mat(n,h,.32 if n in ['Gem','Catch','Teal','EyeWhite'] else .54,.55 if n in ['Gold','GoldLight','Bronze'] else .03) for n,h in palette.items()}
def collection(c): return bpy.data.collections['COL_Study_'+c]
def obj(n,v,f,m,c='Costume',uv=None,sub=0,smooth=True):
 n='SM_Study_'+n; old=S.objects.get(n)
 if old: bpy.data.objects.remove(old,do_unlink=True)
 me=bpy.data.meshes.new(n);me.from_pydata(v,[],f);me.update();o=bpy.data.objects.new(n,me);collection(c).objects.link(o);o.parent=R;me.materials.append(M[m]);
 if uv:
  layer=me.uv_layers.new(name='UVMap');
  for poly in me.polygons:
   for j,li in enumerate(poly.loop_indices): layer.data[li].uv=uv[poly.index][j]
 if not uv:
  layer=me.uv_layers.new(name='UVMap')
  for li in me.loops:
   p=me.vertices[li.vertex_index].co;layer.data[li.index].uv=(p.x*8,p.z*8)
 bm=bmesh.new();bm.from_mesh(me);bmesh.ops.recalc_face_normals(bm,faces=list(bm.faces));bm.to_mesh(me);bm.free()
 for p in me.polygons:p.use_smooth=smooth
 if sub:
  q=o.modifiers.new('Surface refinement','SUBSURF');q.levels=q.render_levels=sub
 return o
def cat(a,b,c,d,t): return .5*((2*b)+(-a+c)*t+(2*a-5*b+4*c-d)*t*t+(-a+3*b-3*c+d)*t*t*t)
def interp(rows,steps):
 return [tuple(cat(a,b,c,d,j/steps) for a,b,c,d in zip(rows[max(0,i-1)],rows[i],rows[i+1],rows[min(len(rows)-1,i+2)])) for i in range(len(rows)-1) for j in range(steps)]+[rows[-1]]
def sweep(n,rows,m,c='Costume',N=20,steps=4,flute=0):
 # Ring frame: width mostly in image plane; depth aligns with camera direction.
 rows=interp(rows,steps);v=[];f=[];uv=[];L=len(rows)
 for i,(x,z,d,w,h) in enumerate(rows):
  cent=P(x,z,d);T=(P(*rows[min(L-1,i+1)][:3])-P(*rows[max(0,i-1)][:3])).normalized();U=Vector((-T.z,0,T.x)).normalized();V=T.cross(U).normalized()
  for j in range(N):
   a=j*2*pi/N;g=1+flute*(sin(a*4+i*.32)+.33*cos(a*7-i*.5));v.append(cent+K*g*(max(.12,w)*cos(a)*U+max(.12,h)*sin(a)*V))
 for i in range(L-1):
  for j in range(N):
   a=i*N+j;b=i*N+(j+1)%N;f.append((a,b,b+N,a+N));uv.append(((j/N,i/(L-1)),((j+1)/N,i/(L-1)),((j+1)/N,(i+1)/(L-1)),(j/N,(i+1)/(L-1))))
 f.extend([tuple(reversed(range(N))),tuple(range((L-1)*N,L*N))]);uv.extend([tuple((.5+.45*cos(2*pi*j/N),.5+.45*sin(2*pi*j/N)) for j in range(N))]*2)
 return obj(n,v,f,m,c,uv)
def strand(n,rows,r,m,c='Costume',N=8):
 return sweep(n,[(*p,rr,rr) for p,rr in zip(rows,r if isinstance(r,list) else [r]*len(rows))],m,c,N,4)
def ell(n,x,z,d,w,h,depth,m,c='Costume',angle=0,N=24,L=12):
 v=[];f=[];uv=[];a=angle*pi/180
 for i in range(1,L):
  t=pi*i/L
  for j in range(N):
   q=j*2*pi/N;xx=w*sin(t)*cos(q);zz=h*cos(t);v.append(P(x+xx*cos(a)-zz*sin(a),z+xx*sin(a)+zz*cos(a),d+depth*sin(t)*sin(q)))
 for i in range(L-2):
  for j in range(N):
   k=i*N+j;l=i*N+(j+1)%N;f.append((k,l,l+N,k+N));uv.append(((j/N,(i+1)/L),((j+1)/N,(i+1)/L),((j+1)/N,(i+2)/L),(j/N,(i+2)/L)))
 for axis,start in [(1,0),(-1,(L-2)*N)]:
  idx=len(v);v.append(P(x-axis*h*sin(a),z+axis*h*cos(a),d))
  for j in range(N):f.append((idx,start+j,start+(j+1)%N));uv.append(((j/N,.0 if axis==1 else 1.),(j/N,1/L if axis==1 else 1-1/L),((j+1)/N,1/L if axis==1 else 1-1/L)))
 return obj(n,v,f,m,c,uv)
def disk(n,pts,front,thick,m,c='Costume',bevel=1.5):
 # Closed extruded silhouette, with optional per-vertex front depth.
 v=[P(p[0],p[1],(p[2] if len(p)>2 else front)+side*thick) for side in (0,1) for p in pts];N=len(pts)
 f=[tuple(range(N)),tuple(reversed(range(N,2*N)))]+[(i,(i+1)%N,(i+1)%N+N,i+N) for i in range(N)]
 o=obj(n,v,f,m,c,smooth=False)
 if bevel:
  q=o.modifiers.new('Soft edge','BEVEL');q.width=bevel*K;q.segments=3
  q=o.modifiers.new('Stable normals','WEIGHTED_NORMAL');q.keep_sharp=True
 return o

# Smooth shared underlying torso; garment parts are independent and closed.
sweep('Torso_Tunic',[(247,129,15,21,17),(249,145,13,54,33),(247,172,13,71,43),(239,205,12,61,44),(233,246,13,47,35),(230,286,17,43,31),(221,324,18,65,36),(218,348,20,72,34),(218,352,22,62,32)],'Azure',N=36,flute=.022)
sweep('Neck',[(250,105,7,22,20),(250,122,9,20,18),(250,143,14,24,22),(250,152,14,29,23)],'Skin','Character')
sweep('Hips',[(227,315,20,46,31),(229,337,21,72,40),(229,362,22,75,43),(225,384,23,62,35),(224,392,24,45,25)],'Pants',N=32)
sweep('Leg_Raised_Trouser',[(197,350,0,43,33),(190,391,-4,42,34),(181,432,-19,37,33),(174,468,-32,35,30),(165,491,-27,29,27),(148,515,-7,24,23)],'Pants',N=28,flute=.05)
sweep('Leg_Long_Trouser',[(271,348,24,42,37),(265,382,19,48,43),(251,430,13,42,38),(234,475,-4,34,34),(220,522,-20,28,27),(214,550,-19,25,26)],'Pants',N=28,flute=.053)
sweep('Leg_Raised_Shin',[(150,510,-8,24,23),(147,540,2,21,20),(130,573,13,17,17),(122,600,17,13,15)],'Skin','Character')
sweep('Leg_Long_Shin',[(219,551,-17,28,25),(209,587,-10,28,25),(198,624,-1,22,22),(181,674,8,17,16),(172,706,8,14,16)],'Skin','Character')
sweep('Raised_Boot',[(128,576,15,17,19),(128,589,11,20,23),(117,624,-1,19,25),(107,647,-18,21,29),(104,669,-30,15,23),(108,683,-34,5,11)],'Leather',N=26,flute=.039)
sweep('Long_Boot',[(178,686,8,16,19),(188,704,4,19,22),(178,731,-13,18,25),(164,753,-33,23,31),(164,776,-43,20,22),(171,785,-43,10,13)],'Leather',N=26,flute=.032)
sweep('Sleeve_Left',[(208,158,21,24,23),(184,173,21,22,22),(169,215,18,19,20),(159,254,14,18,17),(142,289,2,17,15),(111,324,-14,16,13),(86,344,-25,12,11)],'Azure',N=28,flute=.055)
sweep('Sleeve_Right',[(291,136,15,28,29),(317,142,13,31,32),(338,171,0,33,31),(350,203,-16,30,27),(356,226,-43,25,23),(338,233,-62,19,22),(303,221,-75,15,18),(267,216,-81,13,16)],'Azure',N=28,flute=.042)
sweep('Head_Base',[(233,7,5,9,9),(237,15,7,31,33),(238,32,6,43,42),(235,49,7,46,44),(235,66,10,46,43),(236,84,11,42,41),(240,101,12,35,35),(244,115,10,27,31),(245,125,5,17,22),(245,132,1,6,10),(245,134,0,1,2)],'Skin','Face',N=48)
bpy.context.view_layer.update()
bpy.data.libraries.write(str(D/'steps'/'01_blockout.blend'),{S},fake_user=True)
print('Blockout mesh count',sum(o.type=='MESH' for o in S.objects))
