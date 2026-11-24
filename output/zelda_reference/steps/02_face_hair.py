from mathutils.bvhtree import BVHTree
head=S.objects['SM_Study_Head_Base']; base=[v.co.copy() for v in head.data.vertices]
def g(x,z,a,b,rx,rz):return math.exp(-((x-a)/rx)**2-((z-b)/rz)**2)
max_delta=0
for v,p in zip(head.data.vertices,base):
 x=p.x/K+193.5;z=782-p.z/K;d=p.y/K
 w=max(0,min(1,(10-d)/22));delta=w*(-5*g(x,z,243,91,6,19)-10*g(x,z,244,102,7,6)-4*g(x,z,249,117,11,5)-3*g(x,z,258,98,13,11)-3*g(x,z,219,101,11,12)+3*g(x,z,215,91,15,8)+3*g(x,z,246,82,15,8)-2.5*g(x,z,247,124,13,6))
 v.co.y+=delta*K;max_delta=max(max_delta,abs(delta*K))
head.data.update()
def raytable(o):return BVHTree.FromPolygons([v.co for v in o.data.vertices],[p.vertices[:] for p in o.data.polygons])
FH=raytable(head)
def face(x,z):
 a=FH.ray_cast(P(x,z,-250),(0,1,0))[0]
 return a.y/K if a is not None else -10
def facial_line(n,points,r,m):return strand(n,[(x,z,face(x,z)-d) for x,z,d in points],r,m,'Face',8)
def eye(n,x,z,rx,ry,tilt):
 theta=tilt*pi/180
 def pt(a,b,offset):
  X=x+a*cos(theta)-b*sin(theta);Z=z+a*sin(theta)+b*cos(theta);return P(X,Z,face(X,Z)-offset)
 def surface(label,sx,sz,dx,dz,m,depth,alm=False):
  vs=[pt(dx,dz,depth+1.2)];fs=[];N=40;L=6
  for j in range(1,L+1):
   u=j/L
   for i in range(N):
    a=i*2*pi/N;q=cos(a);b=sin(a);b=b*(.67+.33*abs(b)) if alm else b
    vs.append(pt(dx+sx*u*q,dz+sz*u*b,depth+1.2*(1-u*u)))
  for i in range(N):fs.append((0,1+i,1+(i+1)%N))
  for j in range(L-1):
   for i in range(N):
    k=1+j*N+i;l=1+j*N+(i+1)%N;fs.append((k,k+N,l+N,l))
  o=obj(n+label,vs,fs,m,'Face');mod=o.modifiers.new('Closed shell','SOLIDIFY');mod.thickness=.00045
 surface('_LashBase',rx+.6,ry+1,0,-.65,'Ink',1.4,True)
 surface('_Sclera',rx-.75,ry-.8,0,.35,'EyeWhite',2.0,True)
 surface('_Limbal',ry*.80,ry*.87,1.5,-.05,'IrisDark',3.1)
 surface('_Iris',ry*.61,ry*.71,1.5,.15,'Teal',3.7)
 surface('_Pupil',ry*.42,ry*.60,1.15,-.65,'Ink',4.2)
 surface('_Glint',1.55,1.55,2.85,-2.6,'Catch',5.1)
 eyeends=[(-rx-1,-.4),(-.7*rx,-.68*ry),(.02*rx,-ry),(.7*rx,-.53*ry),(rx+1.5,-1.4)]
 strand(n+'_UpperLash',[(p.x/K+193.5,782-p.z/K,p.y/K) for p in [pt(a,b,3.2) for a,b in eyeends]],[.18,.75,1.05,.87,.12],'Ink','Face')
eye('Eye_Far',214,92,10.7,5.9,15)
eye('Eye_Near',246,82,12.2,7.2,-19)
facial_line('Brow_Far',[(202,82,.4),(206,80,1),(211,81,1),(219,85,.8)],[.35,1.4,1.25,.2],'Ochre')
facial_line('Brow_Near',[(234,75,.6),(240,69,1),(249,66,1),(257,67,.4)],[.26,1.55,1.55,.2],'Ochre')
facial_line('Nostril',[(242,106,.45),(245,107,.6),(247,106,.35)],[.13,.48,.14],'Crease')
facial_line('Lower_Lip',[(249,119,.5),(253,120,1.1),(258,117,.7)],[.25,1.35,.20],'Lip')
facial_line('Mouth_Line',[(248,117,.45),(252,116,1.05),(255,115,1.15),(258,115,.6)],[.12,.53,.63,.13],'Crease')
def ear(tag,root,mid,tip,scale):
 x,z,d=root;xx,zz,dd=mid;tx,tz,td=tip
 sweep('Ear_'+tag,[(*root,10*scale,7*scale),((x+xx)/2,(z+zz)/2,(d+dd)/2,13*scale,7*scale),(*mid,9*scale,5*scale),(*tip,.16,.16)],'Skin','Face',N=22)
 sweep('Ear_Inner_'+tag,[(x,z,d-6*scale,2,1),(xx,zz,dd-4.8*scale,5.3*scale,1.3),(tx*.80+xx*.20,tz*.80+zz*.20,td*.8+dd*.2-1.2,.2,.4)],'Ear','Face',N=16)
ear('Near',(270,79,-9),(288,61,-9),(310,41,1),1)
ear('Far',(199,103,12),(191,96,12),(182,82,17),.65)

# Fitted scalp cap: edge height sweeps above brows and drops at temple/back.
v=[];f=[];uv=[];N=64;L=15
for i in range(L):
 u=(i+1)/L
 for j in range(N):
  a=j*2*pi/N
  n=max(0,-sin(a));near=max(0,cos(a));edge=1.98-.92*n+.52*near*n-.20*max(0,-cos(a))*n+.16*max(0,sin(a));p=u*edge
  v.append(P(238+53*sin(p)*cos(a),56-56*cos(p)-9*n*(1-u),16+52*sin(p)*sin(a)))
for i in range(L-1):
 for j in range(N):
  k=i*N+j;q=i*N+(j+1)%N;f.append((k,q,q+N,k+N));uv.append(((j/N,i/L),((j+1)/N,i/L),((j+1)/N,(i+1)/L),(j/N,(i+1)/L)))
f.extend([tuple(reversed(range(N))),tuple(range((L-1)*N,L*N))]);uv.extend([tuple((.5+.45*cos(2*pi*j/N),.5+.45*sin(2*pi*j/N)) for j in range(N))]*2)
obj('Hair_Scalpcap',v,f,'Hair','Hair',uv,1)
locks=[
 ('BackFall',[(261,47,46,23,29),(285,82,47,28,31),(313,124,41,24,24),(329,157,32,14,13),(332,186,21,.4,1)],'Hair'),
 ('RightUndertow',[(276,111,50,21,23),(310,166,49,33,20),(334,218,35,21,17),(334,262,24,12,12),(314,303,15,.3,.4)],'Ochre'),
 ('LeftMantle',[(250,79,44,30,32),(218,113,52,36,30),(162,138,53,25,29),(113,153,46,21,22),(71,179,43,15,17),(35,203,40,11,10),(14,232,31,.35,.4)],'Honey'),
 ('LeftMain',[(241,100,54,25,30),(202,139,62,31,29),(142,156,64,31,28),(94,177,55,26,20),(48,215,45,21,14),(17,254,28,8,7),(6,281,20,.35,.35)],'Hair'),
 ('LeftBlade',[(233,113,67,25,22),(188,151,63,36,29),(133,181,57,31,26),(84,211,47,23,21),(48,251,40,15,16),(36,287,26,.36,.52)],'Honey'),
 ('LeftBelow',[(236,129,75,20,24),(193,178,68,30,28),(152,205,55,37,29),(108,249,45,28,23),(81,280,38,18,14),(63,307,27,.35,.5)],'Hair'),
 ('LeftRake',[(216,143,79,17,16),(190,195,75,23,23),(159,239,63,26,24),(127,275,54,21,20),(100,308,52,15,12),(79,327,44,.3,.4)],'Honey'),
 ('LeftBottom',[(217,151,84,20,27),(197,205,77,24,22),(174,255,69,26,22),(149,303,58,19,18),(124,336,50,12,12),(112,353,42,.3,.4)],'Hair'),
 ('LeftFlyawayA',[(217,111,39,7,12),(167,124,40,7,8),(130,110,34,3.5,5),(104,108,30,2,2),(89,112,28,.15,.2)],'Honey'),
 ('LeftFlyawayB',[(172,139,22,9,8),(120,130,24,8,7),(81,137,23,6,5),(49,157,20,.2,.3)],'Shine'),
 ('RightFlyaway',[(287,71,25,15,12),(305,95,22,12,9),(323,128,17,7,7),(329,151,13,.2,.2)],'Honey'),
 ('TempleFar',[(196,46,-8,7,8),(196,78,0,6,7),(196,114,-5,8,9),(208,140,-16,5,5),(219,150,-16,.35,.35)],'Hair'),
 ('TempleNear',[(276,37,-18,11,10),(276,59,-20,8,9),(272,86,-20,6.5,7),(265,112,-18,4,4),(262,126,-20,.20,.25)],'Honey')]
for n,rows,m in locks:sweep('Hair_'+n,rows,m,'Hair',N=20,steps=6,flute=.075)
for name,rows,m in locks[:8]:
 # Secondary taper lines follow the clump surfaces without adding floating hair cards.
 for i,frac in enumerate([-.36,.17,.50]):
  rr=[]
  for j,(x,z,d,w,h) in enumerate(rows):
   a=rows[max(0,j-1)];b=rows[min(len(rows)-1,j+1)];dx=b[0]-a[0];dz=b[1]-a[1];q=math.hypot(dx,dz);rr.append((x+dz/q*w*frac,z-dx/q*w*frac,d-h*sqrt(1-frac*frac)*1.02,max(.08,w*.085),max(.09,h*.085)))
  sweep('Hair_Contour_'+name+str(i),rr,'Shine' if i==2 else ('Honey' if m=='Hair' else 'Hair'),'Hair',N=8,steps=5)
for i,(x,z,d,a,w,h) in enumerate([(191,41,-19,-38,8,5),(197,33,-33,-32,10,6),(206,28,-43,-30,12,6),(217,21,-48,-22,13,6),(229,17,-47,-12,14,6),(242,14,-43,9,13,6),(253,14,-35,20,12,5),(265,19,-27,32,11,5),(274,26,-21,50,10,5)]):
 ell('Hair_Braid_A%02d'%i,x,z,d,w,h,5,'Honey','Hair',a,N=18,L=9)
 ell('Hair_Braid_B%02d'%i,x+2,z+5,d-1,w*.83,h*.86,5.4,'Shine' if i%3==1 else 'Honey','Hair',a+48,N=18,L=9)
sweep('Hair_Swept_Fringe',[(188,57,-9,2,4),(197,47,-23,7,9),(215,39,-34,8,11),(237,35,-38,10,10),(254,31,-34,7,7),(265,28,-28,.45,.7)],'Honey','Hair',N=26,steps=6)
strand('Hair_Dark_Pin',[(248,50,face(248,50)-3),(252,40,face(252,40)-6),(254,49,face(254,49)-4)],[.7,1.05,.7],'Ink','Hair')
bpy.context.view_layer.update()
print('Face refinement max metres',round(max_delta,5),'hair meshes',len(collection('Hair').objects),'total meshes',sum(o.type=='MESH' for o in S.objects))
bpy.data.libraries.write(str(D/'steps'/'02_character.blend'),{S},fake_user=True)
