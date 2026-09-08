#!/usr/bin/env python3
"""Export individual printable parts and reference previews using OpenSCAD."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import subprocess
import json
import shutil

ROOT = Path(__file__).resolve().parent
OPENSCAD = Path('/home/vaibhav/Downloads/OpenSCAD-2021.01-x86_64.AppImage')
SCAD = ROOT / 'Health_Companion.scad'
PARTS = ['tray', 'shoe', 'adapter', 'pin', 'keeper', 'turn_key',
         'shim_032', 'shim_064', 'shim_096']

def make_plate():
    # These parts are separate; concatenation preserves each solid and avoids
    # a needless CGAL union. Layout matches the SCAD view, centred for the bed.
    import trimesh
    positions=[('tray',[-40,50,0]),('shoe',[40,50,0]),('adapter',[-40,-30,0]),
               ('shim_032',[28,-30,0]),('shim_064',[85,-30,0]),('shim_096',[-40,-93,0]),
               ('keeper',[12,-90,0]),('turn_key',[96,-103,0])]
    positions += [('pin',[38+(i%3)*18,-78-(i//3)*20,0]) for i in range(6)]
    parts=[trimesh.load_mesh(ROOT/'STL'/(name+'.stl')).apply_translation(offset)
           for name,offset in positions]
    plate=trimesh.util.concatenate(parts)
    centre=plate.bounds.mean(axis=0)
    plate.apply_translation([-centre[0],-centre[1],0])
    assert len(plate.split())==14 and plate.is_watertight
    assert max(plate.extents[:2])<210
    plate.export(ROOT/'STL'/'print_layout.stl')
    shutil.copy2(ROOT/'STL'/'print_layout.stl',ROOT/'PRINT_ALL_FINAL.stl')
    individual=ROOT/'Individual_Parts'
    individual.mkdir(exist_ok=True)
    for name in PARTS:
        shutil.copy2(ROOT/'STL'/(name+'.stl'),individual/(name+'.stl'))
    print('Exported print_layout (14 separate solids)',flush=True)

def export(part):
    out = ROOT / ('checks' if part=='pin_core' else 'STL') / (part + '.stl')
    result = subprocess.run([str(OPENSCAD), '-o', str(out), '-D',
                             'part=' + json.dumps(part), str(SCAD)],
                            capture_output=True, text=True)
    (ROOT / 'checks' / (part + '.log')).write_text(result.stdout + result.stderr)
    if result.returncode or not out.exists() or 'ERROR:' in result.stderr:
        raise RuntimeError(part + '\n' + result.stderr)
    return part

if __name__ == '__main__':
    (ROOT / 'STL').mkdir(exist_ok=True)
    (ROOT / 'checks').mkdir(exist_ok=True)
    with ThreadPoolExecutor(max_workers=3) as pool:
        for part in pool.map(export, PARTS):
            print('Exported', part, flush=True)
    make_plate()
    export('pin_core')
