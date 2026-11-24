def rem(n):
 o=S.objects.get('SM_Study_'+n)
 if o:bpy.data.objects.remove(o,do_unlink=True)
def shade(n,h):
 m=M[n];m.diffuse_color=rgb(h);q=next(a for a in m.node_tree.nodes if a.type=='BSDF_PRINCIPLED');q.inputs['Base Color'].default_value=m.diffuse_color
 im=next((a.image for a in m.node_tree.nodes if a.type=='TEX_IMAGE'),None)
 if im:print(n,'texture still bound',im.name)
def adjust_tile(n,mults):
 import numpy as np
 im=bpy.data.images['T_Zelda_'+n+'_BaseColor'];a=np.empty(len(im.pixels),dtype=np.float32);im.pixels.foreach_get(a);a=a.reshape((-1,4));a[:,:3]*=np.array(mults,dtype=np.float32);im.pixels.foreach_set(a.ravel());im.update();im.save();im.pack()
for n,factors in [('Azure',(.62,.83,1.00)),('Hair',(.88,.73,.47)),('Honey',(.93,.74,.46)),('Pants',(.78,.74,.67)),('Leather',(.80,.75,.68)),('LinenShadow',(.82,.80,.73)),('SlateEdge',(.60,.56,.51))]:adjust_tile(n,factors)
shade('Shine','e7a426');shade('GoldLight','dca74c');shade('Bronze','835021');shade('Glove','211712');shade('Ochre','95500c')
S.world.node_tree.nodes.get('Background').inputs[1].default_value=.20;S.view_settings.exposure=.19
S.objects['LGT_Study_Rim'].data.energy=30

# Replace intersections with deliberately nested cloth ends.
sweep('Sleeve_Left',[(207,157,20,24,22),(184,175,21,23,22),(169,208,17,19,18),(160,247,11,16,15),(150,273,4,13,12),(142,287,1,10,10)],'Azure',N=28,flute=.036)
sweep('Sleeve_Right',[(284,139,15,16,17),(308,136,11,27,28),(332,158,3,29,30),(351,190,-12,31,28),(353,209,-38,24,22),(343,220,-51,16,15)],'Azure',N=32,flute=.035)
sweep('LinenSleeve_Right',[(354,199,-47,9,12),(352,208,-54,23,19),(342,223,-65,27,23),(324,225,-74,26,20),(306,222,-81,20,17),(281,214,-87,14,14)],'Ivory',N=32,flute=.073)
sweep('Leg_Raised_Trouser',[(197,350,1,40,33),(189,389,-4,37,34),(181,427,-18,32,31),(175,460,-30,31,29),(165,486,-28,25,25),(157,503,-20,21,20)],'Pants',N=28,flute=.041)
sweep('Leg_Long_Trouser',[(271,348,24,41,38),(265,380,19,47,42),(251,428,13,40,37),(235,472,-4,34,32),(220,516,-19,27,26),(216,544,-18,23,20)],'Pants',N=28,flute=.043)
sweep('Leg_Long_Shin',[(219,548,-16,24,23),(211,584,-9,24,22),(197,622,-1,19,18),(181,670,5,15,15),(175,700,4,12,13)],'Skin','Character')
sweep('Leg_Raised_Shin',[(150,510,-8,20,19),(146,539,2,18,17),(130,573,11,13,13),(122,593,12,11,12)],'Skin','Character')
sweep('Long_Gaiter',[(216,562,-15,30,28),(214,578,-12,28,27),(207,602,-4,26,25),(199,621,1,22,22)],'Leather',N=30,steps=5,flute=.027)
sweep('Raised_Gaiter',[(153,515,-10,26,26),(151,530,-5,25,24),(144,550,3,20,22)],'Leather',N=30,flute=.035)
sweep('Corset_SashOverlay',[(229,287,-5,42,29),(229,294,-8,48,40),(223,306,-8,55,45),(212,321,-7,57,42),(204,328,-1,54,35)],'Leather',N=36,flute=.02)
q=S.objects['SM_Study_Corset_SashOverlay'];
for v in q.data.vertices:
 x=v.co.x/K+193.5;z=782-v.co.z/K
 if z>309:v.co.z+=K*max(0,x-158)*((z-309)/19)*.10
for n in ('Long_Boot_Toecap','Raised_Boot_Toecap'):
 o=S.objects['SM_Study_'+n];vavg=sum((v.co for v in o.data.vertices),Vector())/len(o.data.vertices)
 for v in o.data.vertices:v.co.y=vavg.y+(v.co.y-vavg.y)*.70
# Slightly darker low lights, and moving clumps rather than repeated parallel cords.
for o in list(collection('Hair').objects):
 if o.name.startswith('SM_Study_Hair_Contour_'):
  k=int(o.name[-1]);
  if k!=2 or any(t in o.name for t in ('BackFall','RightUndertow')):
   bpy.data.objects.remove(o,do_unlink=True)
  else:
   o.location.y=.005;o.data.materials[0]=M['Honey']
for title,rows,name in [('LeftHalo',[(218,100,50,12,12),(170,116,58,10,10),(131,110,50,2.8,3),(90,113,38,.2,.2)],'Hair'),('WindStrayA',[(173,171,46,11,9),(100,198,33,7,8),(48,235,25,5,4),(25,292,25,.18,.20)],'Hair'),('WindStrayB',[(169,223,57,10,9),(107,257,43,5,7),(67,283,34,4,4),(40,316,27,.15,.20)],'Honey'),('WindStrayC',[(167,266,55,8,8),(121,298,44,4,4),(107,326,36,4,4),(87,343,33,.15,.18)],'Ochre')]:sweep('Hair_'+title,rows,name,'Hair',N=16,steps=6,flute=.03)
rem('Hair_LeftFlyawayB')
sweep('Hair_LeftFlyawayB',[(172,139,42,7,8),(120,131,39,6,6),(81,137,28,4,5),(49,157,22,.2,.3)],'Honey','Hair',N=18,steps=6,flute=.04)

# Accent on chest and fitted cuff edges.
disk('Chest_Brooch',[(185,174),(192,171),(199,175),(207,175),(211,182),(204,190),(193,189),(183,185)],-35,6,'Bronze',bevel=1.3)
ell('Chest_Brooch_Gem',195,181,-40,5,4,2,'GoldLight',angle=15,N=18,L=9)
for n,ps in [('ChevronA',[(195,195,-33),(210,181,-32),(226,178,-34)]),('ChevronB',[(224,196,-36),(242,190,-33),(256,173,-29)])]:strand('Tunic_'+n,ps,[.4,1.1,.4],'AzureLight')
ell('Wrist_Free_Join',80,346,-30,11,8,8,'Glove','Character',-23,N=22)
q=S.objects['SM_Study_Slate_LuminousIris'];q.data.materials[0]=M['Glow']

bpy.context.view_layer.update()
S.render.resolution_percentage=65;S.cycles.samples=26;S.render.filepath=str(D/'steps'/'hero_v2.png')
bpy.ops.render.render(write_still=True,scene=S.name)
print('Revision rendered',S.render.filepath)
