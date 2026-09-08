# Agent-controlled watch instructor

Status: built and checked on 2026-09-07; **not deployed or flashed**. The user
will explicitly say when to flash over OTA. Do not queue `pending_ota.txt`,
replace the laptop's gateway files, or restart its gateway before that.

Final OTA build ID: `369ba888`; application image: 2,068,608 bytes.

## Behavior

The Cerebras care agent selects an explicit `instructor` object alongside its
spoken response. The fields are `move`, `side` (anatomical left/right/both),
`pattern` (repeat/hold), and `period_seconds` (4–12). No keyword matching, IMU
motion, touch action, or playlist chooses a movement. A positive `hold_seconds`
sets its duration. Non-exercise sessions do not get the animation catalogue.

The watch shows a seated, shaded 3D instructor with teal sportswear, a stable
chair, depth-tested limbs and a fixed three-quarter camera. Six supported
movements are front arm raises to shoulder height, lateral raises to just below
shoulder height, elbow curls, shoulder rolls, seated marching, and torso turns.
Both-sided marching and turns alternate sides. Holds ease into a pose; shoulder
rolls must repeat. Unsupported instructions keep their existing voice behavior;
the agent is told to disclose that no matching visual exists.

Geometry references: [NHS sitting exercises](https://www.nhs.uk/live-well/exercise/sitting-exercises/)
and [North Tees chair exercises](https://www.nth.nhs.uk/resources/chair-exercises/).
These informed the seated posture, controlled arm movement and fixed hips during
turns. The animations are illustrative, not a clinically validated assessment
of a person's form. Wrist evidence remains separate from the demonstration.

## Ownership and timing

`care/instructor.py` defines the catalogue, model instructions and validation.
`care/agent.py` admits a demo only on a successful physical turn with a positive
hold, no question and a continuing session. It clears stale directives. The
chosen demo is saved with the transcript in `care/plan.py`; a forced form retry
replays that prior command, rather than a newly proposed different exercise.

`session.py` sends `exercise_instructor` events:

1. `prepare`: show the starting posture while Kiki speaks.
2. `start`: start motion after speech drains, for the timed hold only.
3. `stop`: close after the hold, cancellation, failure or session end.

Each command carries a unique `demo_id`. A start without its still-active
preparation is rejected. Stops for older demonstrations cannot close a newer
one. Speech-drain timeout suppresses the demo. Visual delivery errors are
best-effort and do not fail the spoken response. The local End session button
stops playback immediately and sends the existing care end action; the gateway
now also cancels its active speech/hold task on that action.
The button sends the urgent `cancel_turn` event first, so microphone backlog
cannot delay cancellation.

`kiki_instructor.cpp` owns an independent LVGL screen. It validates the wire
fields again and restores the normal screen on Stop, link loss, a fall check,
60-second preparation timeout or the animation deadline (at most 120 seconds).
The existing face-render timer yields while the instructor is visible.
`kiki_instructor_model.cpp/.hpp` contain the platform-independent 3D rig and
analytic ellipsoid renderer used by both the watch and the host preview.

The renderer allocates 587,520 bytes of colour/depth buffers in PSRAM only,
releases them when closed, and allocates nothing per frame. No new internal-RAM
frame buffers, graphics dependencies, asset downloads or network services.
Rendering runs on the LVGL timer at up to 10 fps and slows if a frame is costly.
Actual on-device frame rate and simultaneous audio remain to be checked after
the authorized OTA; host checks cannot establish those hardware properties.

## Verification and known baseline issue

- Full gateway suite: 489 tests pass, including 34 instructor tests for malformed
  output, speech/hold ordering, retries, cancellation and failed visual delivery.
- ESP-IDF firmware builds; the 5 MiB OTA slot has approximately 61% spare space.
- Host rig checks cover all six moves, both patterns, all sides, constant bone
  lengths, fixed hips, floor clearance, held poses and alternating marches.
- Existing panel-state, TX-priority, IMU-wire, protocol-frame, motion-classifier
  and dance-routine tests pass.
- Existing `test_fall_detector.cpp` fails its expectation that a worn 3g impact
  alone cannot trigger. The deployed detector explicitly allows such impacts.
  Both files are byte-identical to the laptop copies. No fall algorithm or
  thresholds were changed for the instructor.
- No live Cerebras request, physical display/audio test, gateway deployment or
  OTA has been performed for this change.

## Pending deployment

The prepared files are under `build/exercise-instructor-20260907/`. The package
contains the OTA image, the four required gateway files, checksums, previews,
and verification logs. The firmware and gateway changes should be deployed
together after the user authorizes flashing. New firmware understands the old
gateway; old firmware ignores these new events. Back up the running gateway
files and previous image before replacing them, preserve the existing
environment and care-state directory, and use the existing OTA workflow.

At inspection the gateway was PID 8147, managed by the laptop server manager,
listening on 8765. The inactive `kiki-esp32-gateway.service` and the root
`KikiESP32/gateway.log` were stale. Current log:
`/home/vaibhav/kiki_servers/logs/gateway.log`. Re-resolve the process at deployment.

Baseline laptop gateway SHA-256 values (verify again before replacement):

```
8fc5e9dadd956b779faa584e3dd65abf96262dc7e5fefc5cc2906e8def47e2b4  care/agent.py
98beca9c5508bb6ce32c500131c22f74a42340b24c9d588db6a269ecd3f3c74f  care/plan.py
c23563061c037675bc1f1003623a9448a11ee33781daac2171c6637cbd73e66f  session.py
```
