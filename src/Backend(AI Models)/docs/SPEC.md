# Personal Health Telemetry & ESP32 Edge-AI System — Executable Specification (v2)

This revision closes the loopholes identified in the review (cold-start, baseline drift, HR/activity confounding, PPG motion artifacts, missing fall data, no fall confirmation, narrow activity taxonomy, sensor sync, and resource budgeting) with concrete, implementable parameters. Everything below is designed to run on dummy data first, then transfer directly to real QMI8658 / MAX30101 hardware with minimal changes.

---

## 1. Sensor Sync & Buffering Strategy (fixes: sample-rate mismatch)

**Rates:**
- QMI8658: sample at **50 Hz** (accel + gyro, 6 values/sample). Good balance of fall-detection fidelity vs. RAM/power.
- MAX30101: effective HR/SpO₂ output is **1 Hz** after internal averaging (raw PPG is much faster but the algorithm's HR/SpO₂ output only updates ~1×/sec).

**Sync approach — no true synchronization needed, just correct tagging:**
```
IMU ring buffer:  50 samples/sec → 1-second window = 50 rows
HR/SpO2 buffer:   1 sample/sec

Every 1 second (on HR/SpO2 tick):
    take the most recent completed 1-sec IMU window
    compute IMU-derived features for that window
    pair with the HR/SpO2 sample that arrived in the same tick
    → emit one fused "1Hz feature record"
```
This gives you a **1 Hz fused feature stream** for the rule-based and decision-tree stages, while the **raw 50 Hz IMU stream** is separately buffered into a rolling window for the CNN (see §5). No hardware timer sync is required — software tagging by arrival order is sufficient at these rates.

**Dummy data implication:** the generator must emit IMU samples at 50 Hz and HR/SpO₂ samples at 1 Hz, not the same rate for both — this was implicit before but must be explicit or the sync logic can't be tested.

---

## 2. Cold-Start / Baseline Bootstrap Policy (fixes: loophole #1)

Personal baselines cannot exist on first boot. Use a **3-phase bootstrap**:

| Phase | Duration | Behavior |
|---|---|---|
| **Phase 0 — Population prior** | Sample 0 to ~5 min | Use fixed generic thresholds (HR 50–100 bpm resting-normal, SpO₂ ≥ 95%). No personalization yet. Alerts still fire on gross deviations (e.g., HR > 140 at rest, SpO₂ < 90%). |
| **Phase 1 — Blended baseline** | ~5 min to ~24 hrs (or first N=500 valid samples) | Baseline = weighted average of population prior and accumulating personal mean/std, weight shifting linearly toward personal data as N grows: `weight_personal = min(N / 500, 1.0)` |
| **Phase 2 — Fully personalized** | After N ≥ 500 valid samples | Pure personal baseline (see §3 for dual-timescale version) takes over. |

**Persistence check** (already specified conceptually, now concrete): an anomaly is only flagged if the deviation holds for **≥ 3 consecutive fused samples (≥ 3 seconds)** — this rejects single-sample noise spikes without needing a separate signal-quality model.

---

## 3. Dual-Timescale Baseline (fixes: loophole #2 — drift masking real trends)

Maintain **two rolling baselines per metric (HR, SpO₂)**:

```
Short baseline:  rolling mean/std over last 30 minutes (fast-adapting)
Long baseline:   rolling mean/std over last 14 days   (slow-adapting)
```

- **Acute anomaly** = current reading deviates from the **short** baseline by > 2.5σ, persisted 3+ samples → immediate alert (catches sudden events).
- **Trend anomaly** = the **short baseline itself** has drifted from the **long baseline** by > 1.5σ over a sustained period (e.g., 6+ hours) → separate, lower-urgency "trend" flag (catches gradual deterioration that acute detection would miss because it silently absorbed).

This directly solves the drift-vs-masking tradeoff: fast baseline handles spikes, slow baseline catches things the fast one would otherwise normalize away.

Memory cost: 2 floats (mean, M2 for Welford's online variance) × 2 timescales × 2 metrics = **16 bytes** total. Use **Welford's algorithm** for streaming mean/variance — no need to store raw history.

```c
// Welford's online update (per baseline, per metric)
void update_baseline(baseline_t *b, float x) {
    b->n += 1;
    float delta = x - b->mean;
    b->mean += delta / b->n;
    float delta2 = x - b->mean;
    b->M2 += delta * delta2;
    b->std = sqrtf(b->M2 / b->n);
}
```
For the 30-min short baseline, use a fixed-size circular buffer (1800 samples at 1 Hz) with exact windowed recompute, or an exponential moving average (EMA) with α ≈ 2/(N+1) as a cheaper approximation if RAM is tight.

---

## 4. Activity-Conditioned HR/SpO₂ Anomaly Detection (fixes: loophole #3 — confounding & hidden dependency)

The pipeline is **not actually parallel** — make the dependency explicit and correct in the architecture:

```
IMU features → Decision Tree → activity_state
                                    │
HR/SpO2 + activity_state → Baseline lookup (per-activity baseline) → anomaly check
```

**Implementation:** maintain **separate baselines per activity state** (Resting, Walking, Running, Inactive — expanded in §6). Four small baseline structs instead of one:

```c
baseline_t hr_baseline[NUM_ACTIVITY_STATES];   // one per activity
baseline_t spo2_baseline[NUM_ACTIVITY_STATES];
```

When a fused sample arrives: classify activity first (Decision Tree, <1ms), then update/check the HR & SpO₂ baseline **for that specific activity state**. This removes false positives from exercise-elevated HR being compared against a resting baseline, and costs only 4× the 16 bytes from §3 = **64 bytes** — trivial.

**Misclassification safety net:** if activity confidence is low (see §6), widen the anomaly threshold (e.g., 2.5σ → 3.5σ) for that sample rather than trusting a possibly-wrong activity label outright.

---

## 5. PPG Motion-Artifact Rejection (fixes: loophole #4)

Before HR/SpO₂ values reach the baseline check, gate them with an IMU-based signal-quality check:

```
IF acceleration_variance(current 1-sec window) > MOTION_THRESHOLD:
    mark HR/SpO2 sample as "low_confidence"
    → do NOT feed into baseline update (avoids corrupting the baseline)
    → do NOT trigger anomaly alert from this sample alone
    → still log the raw value for offline review
ELSE:
    process normally
```
`MOTION_THRESHOLD` is tuned empirically per activity state (higher tolerance during Walking/Running than Resting, since some motion is expected — use the accel-variance feature already computed for the Decision Tree, no extra computation needed).

This single check simultaneously fixes the "PPG motion artifact" problem for HR (previously unhandled) and formalizes the SpO₂ signal-quality step that was only conceptually mentioned.

---

## 6. Expanded Activity Taxonomy + Confidence Score (fixes: loophole #7)

Expand from 3 classes to **5**, still cheap for a Decision Tree:

```
Resting | Inactive (still, no wrist motion) | Walking | Running | Transition
```
`Transition` catches ambiguous walk↔run boundary windows instead of forcing a hard misclassification. The tree should output a **confidence** alongside the label — easiest implementation is leaf-node purity (fraction of training samples in that leaf matching the majority class), precomputed offline and stored as a constant per leaf:

```c
typedef struct {
    activity_t label;
    float confidence;   // 0.0–1.0, precomputed leaf purity
} activity_result_t;
```
Low confidence (< 0.7) triggers the widened-anomaly-threshold behavior from §4 and is itself logged as a "transition" event rather than asserted as fact.

**Dummy generator update:** add a `Transition` synthetic state that ramps acceleration/gyro magnitude linearly between Walking and Running profiles over a randomized 5–15 second window, with HR interpolating accordingly.

---

## 7. Synthetic Fall-Event Generation (fixes: loophole #5 — the critical one)

The CNN cannot be trained without representative fall examples. Define **explicit synthetic fall signatures** with three phases, based on real fall biomechanics (impact → orientation change → post-fall stillness):

```
Phase A — Free-fall / pre-impact (0.2–0.4s):
    acceleration magnitude drops toward ~0g (brief weightlessness)

Phase B — Impact spike (0.05–0.15s):
    acceleration magnitude spikes to 3–8g
    gyroscope magnitude spikes sharply (rotational disturbance)

Phase C — Post-impact stillness (2–5s):
    acceleration magnitude settles near 1g (static, lying down)
    gyroscope magnitude drops near 0
    → this phase is what distinguishes a real fall from a dropped device
      or a hard sit-down, which lack sustained post-impact stillness
```

**Generator logic (pseudocode):**
```python
def generate_fall_window(fs=50, duration_s=4):
    t = np.arange(0, duration_s, 1/fs)
    acc = np.ones_like(t)  # baseline 1g
    gyro = np.zeros_like(t)

    # Phase A: free-fall dip
    a_start = int(0.5 * fs)
    a_end = a_start + int(random.uniform(0.2, 0.4) * fs)
    acc[a_start:a_end] = np.linspace(1.0, 0.1, a_end - a_start)

    # Phase B: impact spike
    b_end = a_end + int(random.uniform(0.05, 0.15) * fs)
    acc[a_end:b_end] = random.uniform(3.0, 8.0)
    gyro[a_end:b_end] = random.uniform(150, 400)  # deg/s spike

    # Phase C: post-impact stillness
    acc[b_end:] = 1.0 + np.random.normal(0, 0.02, len(acc[b_end:]))
    gyro[b_end:] = np.random.normal(0, 2, len(gyro[b_end:]))

    return acc, gyro  # then split into x/y/z components with randomized axis orientation
```

**Negative (hard) examples — equally important, prevents false positives:**
- Dropped device (impact spike, but device — not body — so no preceding "free-fall dip from standing height" pattern and erratic post-drop motion, not stillness)
- Hard sit-down (moderate spike, no free-fall phase, immediate return to low-motion "sitting" baseline, not "lying still")
- Jumping (spike, but rhythmic/repeated, and no stillness phase)
- Vigorous arm gesture (gyro spike without matching accel spike)

Generate a **balanced dataset**: e.g. 500 fall examples, 2000 non-fall examples (normal activity windows), 500 hard-negative examples (drop/sit/jump/gesture) — hard negatives are what actually make the CNN precise rather than merely sensitive.

If more realism is needed before real-hardware testing, supplement with public datasets (**SisFall**, **MobiFall**, **UMAFall**) resampled to 50 Hz to match this pipeline — optional but recommended before trusting benchmark numbers.

---

## 8. Post-Fall Confirmation Stage (fixes: loophole #6)

The CNN's raw output should not directly trigger an alert. Add a **confirmation state machine**:

```
CNN outputs fall_probability > 0.8
        ↓
Enter "SUSPECTED_FALL" state
        ↓
Monitor next 5 seconds of IMU data
        ↓
    ┌───────────────────────┬───────────────────────┐
    ↓                       ↓                        ↓
Sustained stillness    Normal motion resumes    No new data (device
(accel ≈1g, gyro≈0      within 5s (person got     powered off/knocked)
for ≥3s)                 up normally)
    ↓                       ↓                        ↓
CONFIRMED_FALL          FALSE_ALARM,               CONFIRMED_FALL
→ alert                cancel, log as              (worst-case assumption
                        near-miss for               → alert; device-loss
                        model review                 itself is also signal)
```
This mirrors how real commercial fall detectors (Apple Watch, etc.) work and is the single highest-value fix for false-positive rate — a bare single-window CNN classification will otherwise alarm on jumps, hard footsteps, and device drops.

---

## 9. Resource Budget (fixes: loophole #10 — verify before committing)

Rough ESP32 (dual-core, 520KB SRAM typical, e.g. ESP32-WROOM) budget estimate to sanity-check before implementation:

| Component | Estimated RAM | Notes |
|---|---|---|
| IMU ring buffer (50Hz × 4s window × 6 floats) | ~4.8 KB | for CNN input window |
| Fused feature buffer (1Hz × short-term history) | ~2 KB | |
| Baselines (Welford stats, ×4 activities ×2 metrics ×2 timescales) | < 1 KB | negligible, per §3–4 |
| Decision Tree (compiled comparisons) | < 1 KB | flash-resident, not RAM |
| TFLite Micro interpreter overhead | ~20–30 KB | fixed cost |
| Tensor arena (CNN activations) | **20–60 KB** | depends on model size — must be measured, not assumed |
| WiFi/BLE stack (if concurrently active) | ~40–80 KB | often the largest consumer — budget for this explicitly |
| **Total estimate** | **~90–180 KB** | leaves headroom on a 520KB part, but tight on smaller variants (e.g. ESP32-C3 with 400KB) |

**Action item before finalizing CNN architecture:** keep the model to **≤ 2 Conv1D layers, ≤ 16 filters each, small dense head** — this keeps tensor arena in the tens-of-KB range. Measure actual arena size via `tflite::RecordingMicroAllocator` during development rather than assuming.

---

## 10. Revised Unified Pipeline (incorporating all fixes)

```
                         SENSOR DATA (dummy or real)
                                   │
                    ┌──────────────┴──────────────┐
                    ▼                              ▼
            QMI8658 @ 50Hz                  MAX30101 @ 1Hz
                    │                              │
                    ▼                              │
         IMU windowing + feature                   │
         extraction (1Hz fused +                   │
         50Hz raw for CNN)                          │
                    │                              │
         Motion-variance gate ─────────────────────┤ (§5: reject
                    │                              │  low-confidence
                    ▼                              │  PPG samples)
            Decision Tree                          │
         (activity + confidence)                   │
                    │                              │
                    ├──────────────────────────────┤
                    ▼                              ▼
        Per-activity HR/SpO2 baseline check (§3, §4)
        (dual-timescale: acute + trend)
                    │
                    ▼
        Persistence + bootstrap-phase logic (§2)
                    │
        ┌───────────┴───────────┐
        ▼                       ▼
  HR/SpO2 anomaly          50Hz IMU window
  (acute / trend)                │
                                  ▼
                            Tiny 1D CNN (INT8)
                                  │
                                  ▼
                        fall_probability > 0.8?
                                  │
                                  ▼
                    Post-fall confirmation FSM (§8)
                                  │
                                  ▼
                          CONFIRMED_FALL / none
        │                       │
        └───────────┬───────────┘
                     ▼
              Event Aggregation
                     ▼
              Trend Detection
                     ▼
              Risk / Alert Score
                     ▼
              Structured JSON → Kiki
```

---

## 11. What to Build First (execution order)

1. **Dummy data generator** — implement all activity states (Resting/Inactive/Walking/Running/Transition) + fall/hard-negative signatures from §6–7. Get this right first; everything downstream depends on realistic synthetic data.
2. **Decision Tree** — train offline on generated IMU features, export as compiled comparisons.
3. **Baseline + bootstrap + persistence logic** — pure C, no ML, fastest to validate end-to-end.
4. **Motion-variance gate** — wire into the baseline update path.
5. **CNN** — train offline (fall + non-fall + hard negatives), quantize INT8, convert to TFLite Micro, measure actual tensor arena.
6. **Confirmation FSM** — wire CNN output into the 5-second post-fall check.
7. **Integration + ESP32 benchmarking** (§9 targets) — measure latency/RAM/flash/power against the budget table and adjust CNN size if over budget.

This order lets you validate each stage on dummy data independently before integration, and keeps the CNN — the most resource-sensitive and hardest-to-debug component — last.
