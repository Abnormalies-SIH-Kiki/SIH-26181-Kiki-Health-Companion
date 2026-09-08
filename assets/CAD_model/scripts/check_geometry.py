#!/usr/bin/env python3
"""Check exported STL topology and nominal assembly interference.

Requires trimesh, numpy, networkx and manifold3d. Checks this V1 parameter set;
update transforms/envelopes if changing SCAD dimensions. Physical fits, connector
locations, optical contact and PLA strength are outside these checks.
"""
from pathlib import Path
from itertools import combinations
import json
import numpy as np
import trimesh

ROOT = Path(__file__).resolve().parent
meshes = {}
report = {'mesh_checks': {}, 'intersections_mm3': {}, 'intentional_spring_contact': {}, 'limitations': [
    'Dimensions and transforms correspond to the shipped final defaults.',
    'Electronics envelopes omit unknown component and solder geometry.',
    'Case model is a nominal cylinder; bezel and port locations need physical checks.',
    'No physical print, strength, skin-contact or strap-length validation performed.'
]}
for path in sorted((ROOT / 'STL').glob('*.stl')):
    m = trimesh.load_mesh(path)
    data = {'watertight': bool(m.is_watertight), 'consistent_winding': bool(m.is_winding_consistent),
            'connected_solids': len(m.split()), 'volume_mm3': float(m.volume),
            'dimensions_mm': m.extents.round(4).tolist(), 'min_z_mm': float(m.bounds[0,2])}
    assert data['watertight'] and data['consistent_winding']
    assert data['connected_solids'] == (14 if path.stem == 'print_layout' else 1)
    assert m.volume > 0 and m.bounds[0,2] > -0.001
    report['mesh_checks'][path.stem] = data
    meshes[path.stem] = m

assembly = {}
for name, offset in [('tray',[0,0,5.0]), ('shoe',[0,0,0]),
                     ('adapter',[0,0,17.3]), ('keeper',[0,25.2,3.35]), ('shim_064',[0,0,19.7])]:
    assembly[name] = meshes[name].copy().apply_translation(offset)
for i,(x,y) in enumerate([(x,y) for x in [-22.8,22.8] for y in [-24,24]]):
    m = meshes['pin'].copy()
    m.apply_transform(trimesh.transformations.euler_matrix(np.pi,0,np.pi/2))
    assembly[f'pin_{i+1}'] = m.apply_translation([x,y,21.7])

for name, dims, center in [('battery',[33,43,8],[0,-7.5,10.6]),
                          ('sensor_pcb',[18,14,1.6],[0,25.2,2.2]),
                          ('strap',[130,22,1.8],[0,-7.5,3.1])]:
    assembly[name] = trimesh.creation.box(dims).apply_translation(center)
assembly['case'] = trimesh.creation.cylinder(radius=25.5,height=12.1,sections=200).apply_translation([0,0,26.39])

failures = []
for (na,a),(nb,b) in combinations(assembly.items(),2):
    if np.any(a.bounds[1] <= b.bounds[0]) or np.any(b.bounds[1] <= a.bounds[0]):
        continue
    intersection = trimesh.boolean.intersection([a,b],engine='manifold')
    volume = max(0,float(intersection.volume)) if not intersection.is_empty else 0
    report['intersections_mm3'][na+' / '+nb] = round(volume,6)
    if {na,nb} == {'adapter','case'}:
        # Four spring pads intentionally intrude 0.18mm into the nominal cylinder.
        # This checks their geometric scale, not the strength of a printed spring.
        report['intentional_spring_contact'][na+' / '+nb] = volume
        if not (0 < volume < 4.0):
            failures.append((na,nb,volume))
    elif volume > 0.01:
        failures.append((na,nb,volume))
report['passed'] = not failures
(ROOT/'checks'/'geometry_report.json').write_text(json.dumps(report,indent=2)+'\n')
print('Validated nine single-part watertight STLs and the 14-solid print plate')
print('Nominal assembly interference failures:',failures)
if failures:
    raise SystemExit(1)
