FINAL SENSOR-CONTACT REVISION

Print these two files, one copy each, at 100% scale:
  PRINT_SHOE_LONG_SIDE_SENSOR_CONTACT.stl
  PRINT_KEEPER_SENSOR_CONTACT.stl

Use the existing 0.16 mm Health Companion profile. Print both in their
supplied orientation with supports off. Reuse the printed tray, adapter,
pins and case shim. Replace the previously printed sensor keeper with the
new keeper in this revision.

Sensor assumptions:
  Breakout PCB: 18 x 14 mm nominal; pocket is 20 x 16 mm.
  PCB thickness: 1.6 mm nominal; clearance also checks at 1.9 mm.
  MAX30102 optical package: 5.6 x 3.3 x 1.55 mm.

The PCB component side faces the wrist. Seat it fully against the 0.64 mm
ledge, with the black MAX30102 cover glass visible through the skin opening.
The new keeper matches the lowered seat. Orient its C-shaped wire opening
toward the speaker-wire channel on the long side.

Modeled cover-glass projection beyond the shoe's skin plane:
  0.91 mm when the PCB rests on its ledge.
  0.56 mm if the PCB uses the full 0.35 mm keeper allowance.

The long-side speaker exit remains 5 mm wide and 3 mm deep. The short side
has no speaker-wire exit.

Wear the strap snug enough that the sensor cannot lift away from the skin.
No rigid enclosure can guarantee contact with an arbitrarily loose strap;
the added projection increases contact margin but cannot bridge an unlimited
air gap. Do not place printed plastic or tape over the black cover glass.

Checks: both STL meshes are single watertight solids; overall shoe dimensions
remain unchanged; nominal and 1.9 mm PCB thickness envelopes clear the keeper.
CAD checks do not replace a physical fit and signal-quality check.
