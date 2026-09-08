#!/usr/bin/env python3
"""Geometric motion/clearance checks, not physical PLA strength certification."""
from pathlib import Path
import json
import numpy as np
import trimesh

ROOT=Path(__file__).resolve().parent
def mesh(name): return trimesh.load_mesh(ROOT/'STL'/f'{name}.stl')
def overlap(a,b):
    m=trimesh.boolean.intersection([a,b],engine='manifold')
    return 0.0 if m.is_empty else max(0,float(m.volume))

shoe=mesh('shoe')
tray=mesh('tray').apply_translation([0,0,5])
adapter=mesh('adapter').apply_translation([0,0,17.3])
keeper=mesh('keeper').apply_translation([0,25.2,3.35])
core=trimesh.load_mesh(ROOT/'checks'/'pin_core.stl')
result={'pin_motion':[], 'envelope_checks':{}, 'case_height_options':[]}

# Outer head ring stays supported; central drive, shaft and lugs move by 0.4mm.
# The two 0.8mm spring leaves provide that movement with 1.2mm underside space.
for angle,deflection in [(0,0),(20,0.4),(40,0.4),(60,0.4),(80,0.4),(90,0),(110,0.4)]:
    m=core.copy().apply_transform(trimesh.transformations.euler_matrix(np.pi,0,np.deg2rad(angle)))
    m.apply_translation([22.8,24,21.7-deflection])
    v=overlap(m,shoe)
    result['pin_motion'].append({'angle_deg':angle,'centre_deflection_mm':deflection,'shoe_intersection_mm3':v})
    if angle<100: assert v<0.015,(angle,v)
    else: assert v>0.1,('Hard stop missing',v)
    for name,part in [('tray',tray),('adapter',adapter)]:
        assert overlap(m,part)<0.015,(angle,name)

for name,dims,centre,parts in [
    ('battery_upper_envelope',[34.4,44.4,8.8],[0,-7.5,11.0],[shoe,tray,adapter]),
    ('sensor_pcb_upper_envelope',[18.5,14.5,1.9],[0,25.2,2.35],[shoe,tray,keeper]),
    ('strap_upper_envelope',[140,23.2,2.6],[0,-7.5,3.5],[shoe,tray])
]:
    m=trimesh.creation.box(dims).apply_translation(centre)
    volumes=[overlap(m,p) for p in parts]
    result['envelope_checks'][name]={'dimensions_mm':dims,'intersections_mm3':volumes}
    assert max(volumes)<0.015,(name,volumes)

for diameter,height,shim in [(50.6,11.8,0.96),(51,12.1,0.64),(51.4,12.4,0.32)]:
    gap=12.1+0.70-height-shim
    result['case_height_options'].append({'case_diameter_mm':diameter,'case_height_mm':height,
        'selected_shim_mm':shim,'clearance_below_hooks_mm':gap,
        'centred_rim_overlap_mm':diameter/2-24.5})
    assert gap>=0.03 and diameter/2-24.5>=0.79

result['passed']=True
result['limits']=['Ideal geometric clearances only; print error is not simulated.',
 'Pin centre motion is prescribed; spring stress, force, fatigue and layer adhesion are not simulated.',
 'Case pads intentionally flex; outer bezel and ports need checking on the physical case.',
 'Sensor component layout/skin contact is not established by the PCB bounding box.']
(ROOT/'checks'/'mechanism_report.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,indent=2))
