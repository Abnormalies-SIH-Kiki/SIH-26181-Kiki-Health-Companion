#!/usr/bin/env python3
"""Render CAD previews and a labelled design sheet; no generated concept imagery."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import subprocess
from PIL import Image, ImageChops, ImageDraw, ImageFont

ROOT=Path(__file__).resolve().parent
APP='/home/vaibhav/Downloads/OpenSCAD-2021.01-x86_64.AppImage'
VIEWS=[
 ('assembly.png','Health_Companion.scad','assembly','0,0,12,58,0,35,165','1600,1200'),
 ('exploded.png','Health_Companion.scad','exploded','0,0,20,62,0,35,220','1500,1500'),
 ('underside.png','Health_Companion.scad','assembly','0,0,12,230,0,35,165','1400,1100'),
 ('side_team.png','checks/side_preview.scad',None,'0,0,11,70,0,35,110','1400,850'),
 ('side_abnormalies.png','checks/side_preview.scad',None,'0,0,11,70,0,215,110','1400,850'),
 ('print_layout.png','Health_Companion.scad','print_layout','0,0,0,0,0,0,220','1500,1500'),
]
def render(v):
 name,source,part,camera,size=v
 cmd=[APP,'-o',str(ROOT/name),'--imgsize='+size,'--viewall','--autocenter',
      '--projection=o','--camera='+camera,'--colorscheme=Tomorrow']
 if part: cmd+=['-D','part="'+part+'"']
 cmd+=[str(ROOT/source)]
 r=subprocess.run(cmd,capture_output=True,text=True)
 if r.returncode or 'ERROR:' in r.stderr: raise RuntimeError(r.stderr)
 return name

def crop(path):
 im=Image.open(path).convert('RGB')
 bg=Image.new('RGB',im.size,im.getpixel((0,0)))
 mask=ImageChops.difference(im,bg).convert('L').point(lambda x:255 if x>12 else 0)
 box=mask.getbbox()
 if box: im=im.crop((max(0,box[0]-24),max(0,box[1]-24),min(im.width,box[2]+24),min(im.height,box[3]+24)))
 return im

if __name__=='__main__':
 with ThreadPoolExecutor(max_workers=3) as pool:
  for name in pool.map(render,VIEWS): print('Rendered',name,flush=True)
 canvas=Image.new('RGB',(2000,1700),'#f8f8f8')
 d=ImageDraw.Draw(canvas)
 font_path=ROOT/'fonts'/'Orbitron-Bold.ttf'
 title=ImageFont.truetype(str(font_path),42)
 body=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',25)
 small=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',21)
 d.text((65,45),'A3SCV / FOREARM COMPANION',font=title,fill='#162937')
 d.text((65,110),'Final print revision  |  60 x 72 mm base  |  PLA  |  original 51 mm case retained',font=body,fill='#48616b')
 tiles=[('assembly.png',(30,180,990,880),'01  ASSEMBLED'),
        ('exploded.png',(1010,180,1970,1120),'02  EXPLODED'),
        ('side_team.png',(30,940,990,1270),'03  OPPOSING SIDE ENGRAVINGS'),
        ('side_abnormalies.png',(30,1320,990,1640),None),
        ('underside.png',(1010,1190,1970,1640),'04  SKIN SIDE / SENSOR WINDOW')]
 for name,(x0,y0,x1,y1),label in tiles:
  if label: d.text((x0+30,y0),label,font=small,fill='#37515e')
  im=crop(ROOT/name)
  im.thumbnail((x1-x0-30,y1-y0-50))
  canvas.paste(im,(x0+(x1-x0-im.width)//2,y0+40+(y1-y0-40-im.height)//2))
 canvas.save(ROOT/'design_sheet.png')
 print('Rendered design_sheet.png')
