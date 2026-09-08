# Wearable Kiki health pipeline

The wearable is additive. It does not modify `gateway/legacy_kiki` and it does
not create or alter care schedules.

## Data path

1. The ESP32-S3 samples MAX30102 on the isolated expansion I2C bus (SDA GPIO18,
   SCL GPIO17, address `0x57`) and derives wrist heart rate, experimental
   uncalibrated SpO2, steps, wear/activity state, battery, and fall state.
   On-demand readings drain the hardware FIFO in bursts with bounded I2C retry
   and bus recovery, then use one uninterrupted 10-second window. The proven
   peak-to-peak path is the rate source; whole-waveform autocorrelation is only
   an independent confidence check and never overrides the reported BPM. An
   explicitly detected alternating-notch waveform uses paired intervals only
   with enough peaks, stable timing, and strong RED/IR agreement. Recovered I2C timeouts and FIFO
   overflows count against the window instead of disappearing inside retries.
   Register operations use a 30 ms timeout, while a full 192-byte FIFO drain
   gets 120 ms (it needs about 70 ms at the hardened 25 kHz bus rate).
2. Up to three unacknowledged JSON batches survive a reboot in NVS. Normal
   health batches are bulk WebSocket traffic; fall checks are urgent. A batch
   leaves the queue only after `health_telemetry_ack` says the Pi accepted it.
3. The laptop gateway relays batches to the Raspberry Pi service and polls its
   seven-day summary every 30 seconds. No HTTP request occurs in Kiki's speaking
   path.
4. Desktop Kiki receives a compact `Wearable:` suffix in the existing
   append-only `Now/Power/Body` context row. It changes for heart-rate changes
   of at least 5 bpm/band, 500-step milestones, wear/activity/fall changes, or a
   new advisory. This preserves llama.cpp prompt-cache prefix behavior.
5. The Pi independently rechecks the same ordinary-consensus or explicit
   paired-peak path, plus perfusion, channel correlation, and I2C glitches. A stale or
   buggy firmware quality label therefore cannot enter desktop Kiki's context.
   Signal schema `estimator_version: 2` also prevents measurements queued by a
   pre-repair firmware build from becoming trusted after an upgrade.
6. The Pi stores telemetry in `state/care_plan.json` under a cross-process file
   lock. Pi Kiki sees the same facts through the existing change-gated
   `CARE NOW` row and Unified Idle Mind.

## Run on the Pi

```bash
sudo install -m 0644 scripts/kiki-health.service /etc/systemd/system/kiki-health.service
sudo systemctl daemon-reload
sudo systemctl enable --now kiki-health.service
curl http://127.0.0.1:8091/healthz
```

Dashboard: `http://<pi-address>:8091/`

If the Pi service is protected, put the same token in the Pi environment as
`KIKI_HEALTH_TOKEN` and on the laptop as `KIKI_GATEWAY_HEALTH_TOKEN`. Set the
laptop destination with `KIKI_GATEWAY_HEALTH_URL`; the deployed Tailscale
default is `http://kiki-1:8091`.

## Safety boundaries

- Only a `GOOD`/`FAIR` wrist heart rate that also passes the Pi's independent
  waveform-coherence checks enters the trusted trend list.
- SpO2 is always marked `calibrated: false`, is shown as experimental, and is
  never sufficient for an urgent alert.
- Fall escalation requires freefall, a hard landing within 900 ms, confirmed
  wear state, eight seconds of stillness, then no touch/movement response for
  30 seconds. Touch or clear movement cancels it.
- Email/WhatsApp provider acceptance is logged separately from delivery; Kiki
  does not claim that a message was delivered without a receipt.
- No emergency service is contacted automatically.
