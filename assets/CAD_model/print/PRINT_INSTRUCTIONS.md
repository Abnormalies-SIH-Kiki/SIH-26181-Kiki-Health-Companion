# A3SCV — complete final print revision

Print **PRINT_ALL_FINAL.stl once**, at **100% scale in millimetres**. It contains
14 separate printable pieces, already oriented and arranged. Do not also print
the individual files unless you want additional copies. No test coupons are
included in this print set.

The plate occupies approximately **181 × 205 mm**, before any brim. Centre it
on the bed in the slicer and preserve the supplied orientations.

## What is on the plate

| Individual file | Quantity on plate | Used in the wearable |
|---|---:|---:|
| `tray.stl` | 1 | 1 |
| `shoe.stl` — skin-facing plate | 1 | 1 |
| `adapter.stl` — case-retaining frame | 1 | 1 |
| `keeper.stl` — sensor retaining frame | 1 | 1 |
| `pin.stl` — spring-head locking pin | 6 | 4; two are spares |
| `turn_key.stl` | 1 | Assembly tool |
| `shim_032.stl` | 1 | Optional 0.32 mm seating shim |
| `shim_064.stl` | 1 | Nominal 0.64 mm seating shim |
| `shim_096.stl` | 1 | Optional 0.96 mm seating shim |

The three shims are finished assembly options, supplied in the same print so
you can remove vertical slack without another print. Normally use **one shim**.
Start with the **0.64 mm** version for the confirmed 12.1 mm case thickness.
One, two or three small edge notches identify 0.32, 0.64 and 0.96 mm respectively.

## PLA settings for the Kobra 2 Neo

Assuming a 0.4 mm nozzle:

- **0.16 mm first layer and 0.16 mm subsequent layers.** This matches the shim
  thickness steps. Turn off adaptive layer height for this print.
- **5 walls, 100% infill**, at least 5 top and bottom layers. The small spring
  leaves must be solid, with their slots left open.
- Outer walls about **30 mm/s**, small perimeters about **20 mm/s**. Use your
  established PLA nozzle and bed temperatures.
- **Supports off.** Keep all pieces in the provided orientations. The pin heads
  print flat against the bed; their spring leaves are formed in that plane.
- A **3 mm brim** is optional for adhesion and still fits the 220 mm square bed.
  Remove it fully from mating edges, especially shims, pins and the adapter.
- Let the plate cool before removing the thin shims. Do not scale the STL to
  change fit: that would alter the component pockets and locking mechanism.

## Retention changes from the first revision

- Case hooks overlap the nominal outer rim by **1.0 mm**, with **7 mm-wide** arms.
- Separate spring contact pads reduce radial play. The locating clearance is
  deliberately more generous than the contact-pad position; retention is not
  based on squeezing a nominally exact-size hole.
- Pin bumps are now **0.50 mm radius**. Each pin head has two long, 0.8 mm-thick
  spring leaves that allow its centre to move as the bumps pass the socket roof.
  The designed ramp movement is approximately **0.38 mm**, rather than trying
  to force a rigid pin through an interference fit.
- Two detent pockets receive the bumps at the locked position. Solid stops
  beyond that position block further rotation toward a second release position.
- Larger component pockets, a **24 × 3 mm** strap tunnel and **0.40 mm-per-side**
  adapter clearance allow more dimensional variation.

## Assembly

1. With the electronics powered off, lay the blue strap in the shoe's open
   transverse channel. Its buckle does not need to pass through the tunnel.
2. Place the MAX30102 face-down on the sensor-pocket ledge. Fit the C-shaped
   keeper behind the PCB, with its opening facing the wires. The optical face
   and neighbouring components remain exposed through the broad front window.
3. Place the tray onto the shoe, guiding sensor wires through the access hole.
4. Lay the battery flat in its pocket. Run short insulated wires in the side
   gutters, clear of the pouch edges, joining faces and pin bores.
5. Seat the adapter. Insert four pins with their centre slots pointing **across
   the arm**, aligned with the insertion slots.
6. Use the printed turn key in each pin's **centre slot**. Viewed from the display
   side, turn **counter-clockwise by one quarter-turn** until the detent seats.
   The slot will point **along the arm**. Stop there; do not force the hard stop.
   To open later, turn clockwise back to the insertion orientation.
7. Lay the selected seating shim on the adapter under the original case. Start
   with 0.64 mm. Guide the existing case under the four hooks, supporting its
   rigid case rather than pressing the screen glass.
8. If the hooks will not seat without forcing, use the thinner shim. If the
   seated case has vertical slack, use the thicker one. Use the thickest single
   shim that permits all four hooks to engage without forced preload.
9. Check that the four hooks have caught the rigid outer rim, all four pins are
   in their detents, and the USB port, buttons and microphone openings are clear.
   Confirm retention by hand over a table before fastening the wearable.

The spring ring on a pin head should remain free; do not fill its slots with
glue or force a tool against the spring leaves. Turn the centre with the key.

## Dimensions and verification

Housing footprint: **60 × 72 mm**. Height to the unshimmed case face: **31.8 mm**;
with the nominal shim: **32.44 mm**. Maximum plastic height at the clips:
**33.3 mm**. The original 51 × 12.1 mm protective case stays installed. There
is no speaker housing. All added structural parts and fasteners are printed.

The opposing sides retain **“the abnormalies”** and **“Team A3SCV”** in Orbitron
Bold, engraved 0.65 mm deep. The bundled font is installed locally for OpenSCAD;
the exported STLs already contain the letters and need no font installation.

Digital checks performed:

- Nine individual part meshes are closed, consistently oriented single solids.
  The complete plate contains 14 non-overlapping solids.
- Nominal assembly interference is within the stated numerical threshold,
  apart from the deliberately preloaded case contact pads.
- Pin-core motion clears the housing at sampled positions from insertion to
  the 90-degree detent, with its centre deflected 0.4 mm during the ramp. At
  110 degrees, the hard stop blocks it even with that centre deflection.
- A **34.4 × 44.4 × 8.8 mm battery envelope**, an **18.5 × 14.5 × 1.9 mm PCB
  envelope**, and a **23.2 × 2.6 mm strap cross-section** clear the relevant
  printed parts in the nominal model. The PCB check does not establish the
  locations or heights of every component on the approximately 5 mm module.
- Case-height combinations of **11.8 / 12.1 / 12.4 mm** with the **0.96 / 0.64 /
  0.32 mm** shims leave approximately **0.04 / 0.06 / 0.08 mm** below the hooks
  in ideal geometry. Actual print error changes those gaps; select the shim
  during assembly. Centred hook overlap remains 0.8–1.2 mm for diameters
  50.6–51.4 mm.

**These are geometry checks, not certification of a physical PLA print.** They
cannot guarantee the click force, layer adhesion, actual case-rim contact,
sensor skin contact, or a fail-proof grip. No physical print or force test has
been performed. This package accommodates bounded dimensional uncertainty;
arbitrarily inaccurate measurements cannot be covered by a tolerance value.

`Health_Companion.scad` remains editable. Its default view includes illustrative
electronics and strap; do not export that assembly view for printing. Use the
supplied complete plate or individual STLs. `design_sheet.png` shows the finished
arrangement and `pin_detail.png` shows the revised spring-head fastener.
