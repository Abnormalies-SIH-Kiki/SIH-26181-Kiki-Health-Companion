# Kiki ESP32 — end-to-end code structure

> **Exercise 3D instructor (2026-09-07, built; NOT flashed):** The Cerebras care
> agent explicitly selects a seated 3D demonstration, anatomical side, pace and
> repeat/hold pattern. Six movements render locally on the watch with a PSRAM
> depth buffer. Preparation accompanies speech; motion starts after playback
> drains and ends with the timed hold. Stop, disconnect, fall checks and bounded
> timeouts close the screen. No animation is inferred from speech or wrist data.
> Gateway tests pass (489); host movement and existing panel/audio/motion checks
> pass. A stale fall-detector test disagrees with the unchanged deployed threshold
> rule; details are in `docs/EXERCISE_INSTRUCTOR.md`. No live deployment or OTA:
> the user will say when to flash. Package: `build/exercise-instructor-20260907/`.
> Final OTA build ID: `369ba888` (2,068,608 bytes).

> **Watch-style dashboard (2026-09-06):** Flashed/hash-verified `60761181` adds
> four rounded native LVGL cards (lime activity, pink HR, blue weather, amber
> estimated AQI), a 180x90 mascot, central clock and compact date. Native widgets
> are non-interactive so the existing touch/Stop/settings controls retain their
> events. Label updates are deduplicated; no additional network/model work.
> Firmware build and host panel-state tests pass. Physical visual acceptance
> remains pending. Previous `a045401d` image remains in deploy-dashboard-fall-20260906.
> Fall attempt at 19:36:06 armed a candidate but expired without eight seconds
> stillness; shaking speech is independent. This does not validate fall safety.

> **Idle dashboard / fall repair (2026-09-06, deployed):** The idle
> caption band now shows IST time/date, steps since boot, last valid HR and its
> age, plus cached RPi temperature/humidity and estimated CPCB AQI. Missing or
> stale values are labelled; speech, settings, dance and fall checks take priority.
> `panel_status.py` reads the existing health bridge cache every 30 seconds, with
> no prompt mutation or additional model-cache invalidation. The RPi health
> service exposes its existing EnvironmentProvider through the authenticated
> `/api/health/v1/environment` read-only endpoint (live on Pi).
> `kiki_fall_detector.hpp` consumes raw 50 Hz IMU samples, independent of the
> animation queue. It requires recent wear, >=80 ms below 0.55g, impact >=2.3g
> within 1 second, then 8 seconds stillness and a 30-second explicit check-in.
> A separate low-priority timer avoids the optical task's 40-second acquisition.
> Small movements no longer cancel an alert; touch/voice do. Candidates expire
> after 20 seconds; a 60-second cooldown suppresses duplicate episodes. These
> heuristic thresholds are NOT clinically validated and controlled physical
> acceptance remains pending. Unworn drops intentionally do not trigger alerts.
> 310 gateway tests, 12 RPi service tests and the host detector test pass. Firmware
> builds. Flashed and hash-verified build `a045401d`; its LAN hello was confirmed
> at 19:31:36 and microphone streaming resumed at 1.03x. Physical dashboard and
> fall acceptance are still pending. Previous firmware `46b2c738` and a verified
> 128 KiB boot/partition/NVS read are saved in laptop
> `deploy-backups/dashboard-fall-20260906/`. Full-flash reads failed without writing;
> the actual flash completed successfully and did not erase NVS.

> **Web UI feed repair (2026-09-06, deployed):**
> `observability_bridge.py` publishes normal ESP32 chat turns and model context to
> the existing recorder. Timestamp-based event IDs prevent restart cursor stalls.
> Recording failures are best-effort and cannot abort voice. No legacy source edit.
> Live `/api/events?since=9000` returned the new timestamp-ID startup event.
> Gateway currently runs as transient user unit `kiki-esp32-gateway.service`
> (PID 44950 at deployment) with the usual gateway log; use `systemctl --user`
> to manage this process. All 310 gateway tests passed on the laptop before restart.

> **Context-overflow repair (2026-09-06):** `gateway/kiki_gateway/context_budget.py`
> uses the local model's `/props`, `/apply-template` and `/tokenize` endpoints on
> the same normalized messages as generation, including tool instructions. It
> bounds the gateway reply cap to 512–1200 tokens plus 128 tokens of slack,
> and starts cloud compaction 768 tokens before that ceiling. The cloud task
> belongs to the shared core, is shielded from turn cancellation, and stages its
> result until a new turn boundary. It only replaces the exact summarized prefix
> and preserves newer messages. Prior conversation summaries are replaceable
> rows, not part of the permanent persona. Optional recalled memory is bounded
> when the prefix is built; mandatory persona/tool instructions are not truncated.
> Foreground and tool-result requests are checked before generation. If cloud
> compaction is late/offline, full snapshots are saved under gateway/context-archives
> before old short-term turns are pruned, preserving the current user/tool turn.
> An empty model result is retried once and then gets an explicit spoken failure,
> never a silently stored empty assistant message. Ordinary turns preserve their
> append-only cache prefix; only actual compaction/trimming rebuilds it. The
> original repair passed 302 tests on both Pi and laptop and was deployed.
> User subsequently confirmed voice works well. Legacy source is unchanged.

> **Wearable health (2026-09-03):** `firmware/main/kiki_wearable.*` owns the
> isolated GPIO18/17 MAX30102 bus, burst-FIFO I2C recovery, script-matched
> auto-gain/filtering, a continuous 10-second on-demand
> PPG, periodic stillness-gated PPG, steps, wear state, fall
> ladder, and three-batch NVS retry queue. `gateway/kiki_gateway/health_bridge.py`
> relays to the Pi port-8091 service on a background thread. `LegacyKikiCore`
> adds the material-change summary to its existing append-only
> `Now/Power/Body/Wearable` row; `gateway/legacy_kiki` remains unmodified.
> The sensor bus runs at 25 kHz with supplemental weak pull-ups; every recovered
> transaction timeout and FIFO overflow is counted into the capture verdict.
> FIFO data transactions have a separate 120 ms budget because a full
> 192-byte/32-sample drain itself takes about 70 ms at that bus rate; short
> register transactions retain their 30 ms bound.
> Pulse-interval timing is the rate source; ACF is only an independent
> confidence check and can never replace it. GOOD/FAIR normally requires
> peak/ACF agreement. The explicit alternating-notch path instead requires
> paired intervals, at least eight detected peaks, stable timing, and strong
> RED/IR agreement, so neither a hidden bus fault nor a weak half/double-rate
> estimate can be published as a wearable reading.
> Trusted optical telemetry carries `estimator_version: 2`; the Pi refuses an
> older queued window even if its firmware label claimed FAIR/GOOD.

> **Voice regression repair (2026-09-06):** The original health handler awaited
> the Pi HTTP ingest from the single WebSocket receive loop. Logs at 00:29–00:31
> show repeated 45-second connect timeouts, superseded connections, and microphone
> backlog. `session.py` now puts health batches into a bounded, deduplicated
> background delivery queue; the reader continues processing microphone, Stop,
> ping and `playback_drained` events while the Pi is unavailable. Disconnect
> cancels the delivery task; only Pi acceptance acknowledges a durable device
> batch. `health_bridge.py` uses a separate per-request connection with a
> 3-second connect / 45-second response timeout. `playback_drained` refreshes
> the 15-second reply window when the speaker actually finishes, so buffered
> playback cannot consume it. No firmware or legacy runtime change is needed.
> Deployed at 00:45; all 292 gateway tests pass on Pi and laptop. The physical
> Stop press at 00:46:40 cancelled the active response and the board reported
> `playback aborted; drained=232128 bytes, TX tail flushed`. A natural follow-up
> utterance after completed playback still needs explicit physical acceptance.

Everything needed to understand, build, deploy and debug the current ESP32 Kiki,
in one file. Written for an agent joining cold.

Companion docs: `docs/ARCHITECTURE.md` (why the split is where it is),
`docs/DEPLOY.md` (procedures), `docs/LATENCY.md` (measurements), `handoff.md`
(session history and constraints).

---

## 1. What this is

Kiki is a voice companion. This repo is the **ESP32 variant**: a Waveshare
ESP32-S3-Touch-AMOLED-1.75 is the audio/display body, and a laptop runs the
brain. The board never runs an LLM.

```
you speak
  → ES7210 mics → ESP32 I2S, 48 kHz, 10 ms frames
  → persistent WebSocket (LAN, or the internet via a tunnel)
  → gateway: RNNoise → 48→16 kHz FIR → Silero VAD
       240 ms silence: speculative Whisper   600 ms silence: commit
       the hotword is found in that TRANSCRIPT, not in the audio (§5.3)
  → legacy Kiki runtime: prompt + memory + tools + llama.cpp
  → sentence stream → OmniVoice TTS → PCM
  → same WebSocket → ESP32 jitter buffer → ES8311 speaker
```

The board owns: microphones, speaker, 466×466 AMOLED, capacitive touch, Wi-Fi,
playback buffering, the face, QMI8658 motion personality, and all on-panel UI. The laptop owns: everything
that thinks or remembers.

---

## 2. The three machines

| Machine | Hostname | Role | Path |
|---|---|---|---|
| Raspberry Pi | `kiki-1` (tailnet `100.64.0.11`) | **Repo of record + build host.** ESP-IDF lives here. Nothing at runtime. | `/home/kiki/kiki2/KikiESP32` |
| Laptop | `vaibhav` (tailnet `100.64.0.10`) | **Runs everything at runtime**: gateway, llama.cpp, Whisper, TTS, tunnel. | `/home/vaibhav/KikiESP32` |
| Board | — | The robot. | flashed image |

> **Note:** the laptop is **Linux** (LXQt, `systemd --user`), not Windows. If a
> Windows machine is meant to be in this system, it is not part of the current
> deployment — nothing in this repo targets it.

The laptop deployment is **not a git repo**. Changes are made on the Pi, then
`scp`'d over. `ssh vaibhav@vaibhav` (Tailscale) is the access path — note the
explicit user; plain `ssh vaibhav` tries to log in as `kiki` and is refused.

The Pi's own robot tree, `KikiFast`, is **read-only reference** for this project.

---

## 3. Repository layout

```
KikiESP32/
├── firmware/           ESP-IDF 5.5 project (built on the Pi)
│   ├── main/           all application code — see §4
│   ├── components/     vendored Waveshare BSP (locally patched, see §4.6)
│   ├── managed_components/  esp_websocket_client etc. (idf_component.yml)
│   ├── partitions.csv  flash layout
│   ├── sdkconfig.defaults   checked in — the real config source
│   └── sdkconfig       generated, gitignored, holds Wi-Fi + token secrets
├── gateway/
│   ├── kiki_gateway/   the gateway package — see §5
│   └── legacy_kiki/    the KikiFast runtime, copied to the laptop (gitignored)
├── scripts/            build / flash / run / tunnel / OTA — see §8, §9
├── docs/               ARCHITECTURE, DEPLOY, LATENCY
└── .tools/esp-idf      pinned ESP-IDF checkout (Pi only)
```

---

## 4. ESP32 firmware — `firmware/main/`

### 4.1 Boot sequence (`app_main.cpp`)

Order matters; each step depends on the previous.

1. `nvs_flash_init()` — erase and retry on version mismatch.
2. `ui_start()` — display, touch, LVGL, the face.
3. **Build banner**: `Kiki build <id>` where `<id>` is the first 4 bytes of the
   ELF SHA-256. *Not* the compile date — `esp_app_desc.c` is not reliably
   recompiled, so its date goes stale while the code moves on. This cost hours
   once; the ELF hash cannot lie.
4. Mic queue + `kiki_net_tx` task (priority 17, core 0).
5. `power_init()` — AXP2101 over I²C.
6. `audio_pipeline_start()` — codecs warm before the user finishes provisioning.
7. `motion_start()` — QMI8658 sampling + offline physical reactions; non-fatal.
8. `setup_ensure_wifi()` — **blocks** until a network is up (§6.1).
9. `time_sync(8000)` — **before** the gateway, because TLS needs a real date.
10. `ota_arm_rollback_watchdog()` — if this image arrived by OTA it now has
   180 s to reach the gateway or it reverts.
11. `gateway_client_start()`, `log_remote_start()`, telemetry task.

### 4.2 File map

| File | Responsibility |
|---|---|
| `app_main.cpp` | Boot order, mic uplink task, 5 s telemetry task |
| `wifi_station.cpp/.hpp` | STA connect, scan, NVS credentials, `forgotten` flag, power-save policy |
| `kiki_setup.cpp/.hpp` | On-panel Wi-Fi provisioning: scan list, touch keyboard, self-healing retry |
| `kiki_time.cpp/.hpp` | SNTP. Without it the clock is 1970 and every `wss://` cert is "not yet valid" |
| `gateway_client.cpp/.hpp` | WebSocket client, URI failover, framing, mic decimation, TTS expansion, and the **single owner of the socket** (§6.5) |
| `gateway_tx_priority.hpp` | Which control events overtake audio, and which are dropped first. ESP-IDF-free so `firmware/tests/test_tx_priority.cpp` can pin it |
| `kiki_panel_state.hpp` | What Kiki is doing vs. what her face is doing about it -- two things that were one variable until a mood tag disabled the Stop button (§13.15). ESP-IDF-free, tested by `firmware/tests/test_panel_state.cpp` |
| `protocol.hpp` | Binary frame header + `BinaryKind` enum (mirrors `gateway/protocol.py`) |
| `audio_pipeline.cpp/.hpp` | I²S in/out, ring buffer, adaptive jitter buffer, underrun accounting |
| `kiki_motion.cpp/.hpp` | QMI8658 task, runtime context, local reaction/event dispatch, telemetry |
| `kiki_motion_classifier.cpp/.hpp` | Platform-neutral posture/gesture/affect state machine |
| `kiki_ui.cpp/.hpp` | Face screen, status rows, hold-to-talk button, battery, media row |
| `kiki_face.cpp/.hpp` | The Clawd pixel-crab face, ported from KikiFast's `oled_display.py` |
| `kiki_dance.cpp/.hpp` | Dance mode: the full-panel stage, 26 parametric moves, and the media-sample beat clock (§5.5) |
| `kiki_dance_routine.cpp` | The routine parser alone -- no LVGL, no ESP-IDF, so it is host-testable |
| `kiki_theme.hpp` | One palette for every screen |
| `kiki_settings.cpp/.hpp` | Settings menu: volume, gain, brightness, persistent barge-in toggle, on-demand HR + experimental SpO2 screen, Change Wi-Fi, Shut down |
| `kiki_wizard.cpp/.hpp` | Boot wizard, ported from `startup_config.py` |
| `kiki_power.cpp/.hpp` | AXP2101: battery %, USB/charge state, charge current, soft power-off |
| `kiki_ota.cpp/.hpp` | HTTPS OTA download, 3 retries, rollback watchdog, `ota_result` |
| `kiki_log.cpp/.hpp` | Mirrors `ESP_LOG` to the gateway as `device_log` — debugging without a cable |

### 4.3 Flash layout (`partitions.csv`)

```
nvs       0x009000  0x006000
otadata   0x00F000  0x002000
phy_init  0x011000  0x001000
ota_0     0x020000  0x500000   ← app slot A
ota_1     0x520000  0x500000   ← app slot B
storage   0xA20000  0x5D0000
coredump  0xFF0000  0x010000
```

**The single most important operational fact in this project:** `otadata`
decides which app slot boots. A successful OTA repoints it at the *other* slot.
So after any OTA, flashing only `0x20000` writes the partition that is **not
running**, and the board comes up on the old image looking like the flash
silently failed. Always write `ota_data_initial.bin` to `0xf000` alongside the
app when flashing over USB.

### 4.4 NVS namespaces

| Namespace | Key | Meaning |
|---|---|---|
| `kiki_wifi` | `ssid`, `pass` | Panel-provisioned network (wins over Kconfig) |
| `kiki_wifi` | `forgotten` | Set by "Change Wi-Fi". **Suppresses the Kconfig fallback** — otherwise forgetting silently rejoins the built-in network and the button looks broken |
| `kiki_gw` | `fallback` | Public gateway URI learned from `hello_ack` |
| `kiki_ota` | `pending` | "This image arrived by OTA and must prove itself" |
| `kiki_ui` | `bright` | Screen brightness |
| `kiki_settings` | `barge_in` | User's persistent voice barge-in On/Off choice |

Erasing NVS (`esptool erase-region 0x9000 0x6000`) loses all of the above.
Nothing is fatal: the public URI is re-learned on the next `hello_ack`,
brightness resets, and the Kconfig Wi-Fi credentials take over.

### 4.5 Build-time config (`Kconfig.projbuild`, menu "Kiki ESP32")

`KIKI_WIFI_SSID` · `KIKI_WIFI_PASSWORD` · `KIKI_GATEWAY_URI` (LAN) ·
`KIKI_GATEWAY_URI_FALLBACK` (public) · `KIKI_GATEWAY_TOKEN` ·
`KIKI_AUDIO_VOLUME` · `KIKI_CHARGE_CURRENT_MA` · `KIKI_LOW_POWER` (dims the
screen only — it no longer touches the radio) · `KIKI_LOW_POWER_BRIGHTNESS`.

These live in `sdkconfig`, which is **gitignored** because it holds the Wi-Fi
password and the device token. `sdkconfig.defaults` is checked in and is where
non-secret settings belong.

Notable entries in `sdkconfig.defaults`:

- `CONFIG_ESP_WS_CLIENT_SEPARATE_TX_LOCK` **is not set** — with it enabled the
  component's receive task can close mbedTLS while the TX task is inside
  `mbedtls_ssl_write()`, which null-dereferences and reboots the board on a
  lossy remote link. This was the cause of the "restarts when I ask it
  something" reboots.
- `CONFIG_LWIP_DHCP_GET_NTP_SRV=y` + `CONFIG_LWIP_SNTP_MAX_SERVERS=4` — without
  the first, `esp_netif_sntp_init()` rejects any config asking for DHCP-supplied
  NTP, the clock stays at 1970, and TLS fails everywhere.
- `CONFIG_MBEDTLS_CERTIFICATE_BUNDLE=y` — for `wss://`.
- `CONFIG_ESP_COREDUMP_ENABLE_TO_FLASH=y` — crashes land in the coredump
  partition, readable with `esptool read-flash 0xff0000 0x10000`.

### 4.5a How much internal RAM there actually is

"~297 KiB" appeared in several comments and in this document and was wrong by
most of a factor of two, which matters because it is the number every
"is this small enough to put in internal RAM?" decision was weighed against.

From `idf.py size` on the current build:

| | bytes |
|---|---:|
| DIRAM the linker can see | 341,760 |
| ...used statically by the app (`.text` 94,403 + `.bss` 50,528 + `.data` 27,044) | 171,975 |
| **left for the runtime heap** | **169,785 (~166 KiB)** |
| IRAM | 16,384, **100 % full** |

The chip has 512 KiB of SRAM; the rest is cache and ROM reservations. On
hardware, `heap_caps_get_free_size(MALLOC_CAP_INTERNAL)` read 175,795 B right
after init (`docs/LATENCY.md`, 2026-08-12), so ~170-176 KiB is the real ceiling
and everything else -- task stacks, driver buffers, TLS, LVGL, the Wi-Fi stack
-- comes out of it.

That is the context for §6.5's queue placement. Before that change the board sat
at **19 KiB free with a 7-12 KiB largest DMA block** in steady state: about 11 %
of the pool, and close enough to the `ESP_ERR_NO_MEM` display floods of §4.6 to
be a latent fault of its own. Moving the 66 KiB microphone queue and the new
tx/rx buffers to PSRAM took it to **84 KiB free / 31 KiB largest DMA block**.

PSRAM is the opposite: 8 MB, of which about 7 MB is free even with the enlarged
768 KiB playback ring. Anything that does not need to be DMA-capable or touched
from an ISR belongs there.

### 4.6 The one patched vendor file

**Replacement-board baseline (2026-08-31):** the complete display/touch assembly and its
external I2C pull-ups are present again. The shared bus therefore runs at the BSP's normal
400 kHz, CST9217 touch and the audio codecs are required boot hardware, and the firmware no
longer carries the temporary internal-pull-up, optional-touch, or mute-boot paths. The
AXP2101's battery detection is enabled and percentage comes from its `0xA4` fuel gauge (with
the generic voltage curve only as startup fallback). The TS-pin measurement remains disabled
because Waveshare's own code does that for this board; that is board configuration, not the
old broken-connector workaround.

`firmware/components/waveshare__esp32_s3_touch_amoled_1_75/…_1_75.c` is
vendored and **locally modified**: upstream uses `buffer_height = 50` with
`use_psram = true`, which puts the LVGL draw buffer in PSRAM and forces
`spi_master` to allocate a 46,600-byte DMA-capable *internal* bounce buffer on
every flush. Internal RAM is the scarce resource here (~166 KiB, see below), so the display
failed with `ESP_ERR_NO_MEM` continuously. Now `buffer_height = 16`,
`use_psram = false`. The change is commented in place; do not let a component
update silently revert it.

### 4.7 QMI8658 motion personality

`kiki_motion.cpp` initializes the onboard QMI8658 through the BSP's existing shared I²C
bus using the pinned `waveshare/qmi8658` component. The sensor runs at 8 g / 1024 dps;
the classifier consumes samples at 50 Hz. Initialization is deliberately non-fatal so a
missing or faulty IMU cannot stop voice, display, networking, or OTA.

`kiki_motion_classifier.cpp` contains no ESP-IDF dependencies. It low-pass filters gravity,
extracts linear acceleration/jerk/angular speed, debounces posture, and emits semantic
situations: pickup, upright alert, face-down/upside-down/sideways, wiggle, escalating shake,
post-shake dizziness, rocking, spin, carried motion, bump, freefall/landing, set-down,
boredom/dozing, charging rest, music dance, low-battery tiredness, and wake-from-sleep.
Synthetic host traces live in `firmware/tests/test_motion_classifier.cpp`.

Two tasks keep the latency contract clean: the sampling task only reads/classifies/queues;
a lower-priority reaction task changes the face, requests a procedural local sound, logs,
and sends the debounced `motion_event`. A blocked WebSocket therefore cannot stall sampling.
Ordinary reactions overlay only idle/music/disconnected. Freefall and hard landing may briefly
overlay another face, but local audio still refuses to mix over speech or media. Runtime state
updates cancel the overlay and restore the authoritative gateway face.

The affect state is deterministic: boredom accumulates only face-up and idle; annoyance rises
with repeated shaking and decays during calm; gentle rocking reduces it. At 60 s/180 s/600 s
of untouched table idle Kiki fidgets, becomes visibly bored, then dozes. Touch, speech/runtime
activity, or meaningful motion resets that clock. The IMU cannot literally sense a hand, so
"held vertically" is inferred from upright posture, while the separate carried state also
requires sustained natural micro-motion.

---

## 5. Gateway — `gateway/kiki_gateway/`

Runs on the laptop. Entry point `kiki-esp32-gateway` → `server.run_server()`.

| Module | Responsibility |
|---|---|
| `server.py` | WebSocket server, per-connection `DeviceSession`, close-reason logging, event-loop lag watchdog |
| `session.py` | The whole conversation: hello/auth, mic → VAD → ASR → LLM → TTS, device events, OTA push. The big one |
| `config.py` | `GatewayConfig` dataclass; every field settable via `KIKI_GATEWAY_<UPPER>` |
| `protocol.py` | Binary frame header + `BinaryKind` (mirrors `firmware/main/protocol.hpp`) |
| `audio.py` | `RNNoise48k` denoise, `Decimator48To16` (7.2 kHz windowed-sinc FIR) |
| `silero.py` | Silero VAD (ONNX) |
| `hotword_text.py` | The wake word, matched in Whisper's text: fuzzy/phonetic scoring, per-mode hotwords, where in the sentence it landed (§5.3) |
| `wakeword.py` | openWakeWord "kiki" detector. **Off by default** — the fallback for a Whisper outage, `KIKI_GATEWAY_WAKEWORD_ENABLED=1` |
| `endpointer.py` | Streaming endpointer: speculative commit at 240 ms, conservative at 600 ms |
| `inference.py` | `LegacyKikiCore` (the real runtime), compact live time/power/body context, `DirectLlamaCore`, `WhisperClient`, `TTSClient` |
| `device_tools.py` | Tools the LLM can call that execute **on the board**: `adjust_volume`, `play_music`, `like_current_song`, `play_liked_songs`, `play_last_song`, `control_music`, `set_timer`, `switch_voice` |
| `display_bridge.py` | Routes the legacy runtime's 16×2 LCD + OLED output to the AMOLED |
| `expressions.py` | Face/expression cues |
| `motion_reactions.py` | Per-state speech cooldowns for journal-backed, never-repeat IMU questions |
| `dance.py` | Choreography: the move vocabulary, the cloud agent, the composer, the wire format (§5.5) |
| `beatgrid.py` | Tempo, downbeat and per-beat energy, measured from the audio with numpy alone |
| `config_store.py` | Serves the wizard's questions from the laptop's `config.json` |
| `audio_control.py` | TCP JSON control on `127.0.0.1:8770`: `status`, `gain`, `volume`, `sync`, `speak` |

Two tools sit beside the package, and everything in §6.5 was measured with them:

| Tool | What it is for |
|---|---|
| `tools/slowlink.py` | A bad network on demand: a userspace TCP proxy that shapes one flow (rate per direction, added RTT/jitter, retransmission stalls, scheduled outages). No root, no `tc`, no effect on ssh or the tunnel |
| `tools/fake_device.py` | The board without the board. Speaks the real binary protocol, replays the firmware's jitter buffer from arrival timestamps, and reports time-to-first-word split into **perceived**, **transport** and **gateway-reported** |

`audio_control`'s `speak` is the most useful debugging affordance in the system —
it makes Kiki say arbitrary text without needing a microphone:

```bash
python3 - <<'EOF'
import json, socket
with socket.create_connection(("127.0.0.1", 8770), timeout=90) as c:
    c.sendall((json.dumps({"action": "speak", "text": "hello"}) + "\n").encode())
    print(c.makefile("rb").readline())
EOF
```

### 5.1 Cache-safe live body and battery context

The firmware already reports `battery_percent` in `device_stats` every 5 s.
`DeviceSession._report_battery()` forwards the newest valid 0-100 reading to
`LegacyKikiCore`; `-1`, a missing field, or a malformed value clears it rather
than leaving a stale charge level in the prompt.

The existing periodic clock system anchor also carries the latest percentage
and active ten-point personality band: overcharged optimist (90-100), cocky mischief
(80-89), curious explorer (70-79), classic Kiki (60-69), cozy philosopher
(50-59), deadpan critic (40-49), dramatic complainer (30-39), clingy soft
friend (20-29), melancholic poet (10-19), or sleepy minimalist (0-9).

The same row carries a short semantic IMU summary such as
`Body: upright, moving; picked up 8s ago; calm.` Raw acceleration, gyro values,
numeric annoyance, and device-provided free text never enter the prompt. Posture,
activity, one fresh whitelisted event, and an annoyance band are the only body
fields. Ordinary events expire after 45 seconds; boredom/dozing/power-rest events
remain historical context for at most ten minutes. Telemetry older than 20 seconds
becomes `Body: unavailable.`

Every semantic `motion_event` also has a sparse gateway speech opportunity. Once the board's
local reaction is shown and the device is otherwise idle, `DeviceSession` asks the bundled
legacy runtime to reserve a random unused question for that exact movement state from
`thinking_journal.json`. The journal's permanent normalized asked ledger prevents an exact
question from ever being selected twice; an exhausted state stays silent until Unified Idle
Mind optionally adds fresh lines through `add_movement_questions(state, questions[])`.
The model's `no_action | reflect | light_research | deep_research` choice is unchanged and
adding questions is explicitly optional. A spoken motion question is appended to real model
history and opens the ordinary 15-second follow-up window, so the user's answer has context.
Global and per-state cooldowns prevent motion from becoming a speech stream.

The full dynamic row is only three short lines: `Now: …`,
`Power: 54% on battery (cozy philosopher).`, and `Body: …`. Interpretation policy lives
once in the stable startup prompt; the older verbose `current_time_context`
template is deliberately not repeated on live turns.
The combined row is appended at the configured five-minute interval or before
the next real turn after a meaningful semantic body change. Identical five-second
telemetry is deduplicated, and multiple events before one turn collapse into the
newest state.

No existing history row is rewritten or deleted. `LegacyKikiCore` supplies the suffix whether its own
foreground path or Unified Idle Mind requests that anchor: after the idle
manager is constructed, its per-instance `maybe_inject_time` callback is
replaced by the same gateway method, including the idle-time rewarm behavior.
This append-only shape is load-bearing: an ephemeral row removed after
generation would make the next request differ from llama.cpp's real cached
sequence and force old turns to be prefilled again. The durable system suffix
defines each band's behavior and makes it a noticeable secondary accent; the
user's needs, safety, facts, and Kiki's core identity still win.

An autonomous battery joke is controlled in code, not by asking the model to
remember a cooldown. The first conversational turn starts a monotonic clock.
After 90 minutes, the next turn receives one `BATTERY REMARK OPPORTUNITY` and
may make one short natural joke, including the exact percentage. The opportunity
is consumed when offered even if the model skips it during a serious moment.
Its system row remains in append-only history for cache correctness, but is
worded to apply only to the immediately following user message and to expire as
soon as its assistant reply exists; it therefore cannot authorize later remarks.
Direct battery questions are always answerable.
`KIKI_GATEWAY_BATTERY_REMARK_INTERVAL_SECONDS` overrides the 5400-second default
when intentionally testing the gate.

### 5.2 Configuration

`gateway.env` on the laptop, sourced by `scripts/run_gateway.sh`:

```
KIKI_GATEWAY_WEBUI_PORT=8092
KIKI_GATEWAY_TOKEN=<32-byte hex, must match CONFIG_KIKI_GATEWAY_TOKEN>
KIKI_GATEWAY_PUBLIC_URI_FILE=/home/vaibhav/KikiESP32/public_uri.txt
KIKI_GATEWAY_PENDING_OTA_FILE=/home/vaibhav/KikiESP32/pending_ota.txt
KIKI_GATEWAY_HOTWORDS="kiki,tiki,ki ki,piki"
```

`KIKI_GATEWAY_HOTWORDS` is the fallback name list (§5.3) — the one used by
every mode that does not name its own, so adding spellings here does **not**
stop `rohan` answering to "rohan". Whisper writes the same sound several ways
and the fuzzy score covers most of it, but a spelling it splits into two words
(`ki ki`) can only match as an entry of its own: a single-word hotword is
compared against single tokens. Multi-word entries are matched as a phrase, so
`ki ki` also brings `ti ki` in fuzzily. Quote any value containing a space —
`run_gateway.sh` sources this file.

`KIKI_GATEWAY_HOST` defaults to `0.0.0.0`. Setting it to `127.0.0.1` is the
supported way to **force the board onto the remote path for testing** without
sudo or travelling: the LAN address stops answering, so only the tunnel works.

### 5.3 The hotword lives in the transcript

Background listening is always on here: every utterance is endpointed and sent
to Whisper whether Kiki is awake or not, because idle speech becomes ambient
context. The name is therefore already in **text** before anything has to act
on it, and openWakeWord was answering the same question a second time, worse.
It is off by default now (`KIKI_GATEWAY_WAKEWORD_ENABLED=1` brings it back as a
Whisper-outage fallback); `hotword_text.py` + `session._finalize_turn` own the
wake decision.

Three things follow, and none of them were possible acoustically:

**Near misses wake her.** The score is the better of two measures: raw
`SequenceMatcher` on the letters, and equality of a *phonetic skeleton*
(consonant classes plus a vowel class per vowel run). "hey tiki" matches on
letters (0.75, exactly the default threshold — raising it drops the case this
was asked for), "kicky"/"keeki"/"kikki" match on sound. Only an **exact**
skeleton match scores, and vowel runs keep their colour, because the looser
versions of both answered to "okay", "cookie", "quick" and "geeky". The
rejected list is pinned in `tests/test_hotword_text.py`; treat it as the spec.

**Each mode answers to its own name.** `LegacyKikiCore.active_hotwords()`, read
fresh every utterance so a spoken mode switch takes effect immediately:
`assistant_modes.modes.<mode>.hotwords` → `assistant_modes.hotwords` → **the
mode's own `voice`**. That last step is the convention that makes it work on a
config file nobody edited: a mode owning a voice is a character with its own
name, so `rohan` answers to "rohan" and `jarvis` to "jarvis", while `default`,
`tutor`, `evil` and `senior` leave `voice` empty because they *are* Kiki and
fall through to `KIKI_GATEWAY_HOTWORDS` (default `kiki`). The idle panel row
shows whichever name is live.

**The name may come last.** "what do you think kiki" is not a question on its
own — the question was the previous utterance, seconds earlier and already
filed as ambient. `session._carry_back_text` prepends recent idle speech
(≤25 s, ≤3 utterances) when the geometry says the utterance is an *address*
rather than a question: at most `HOTWORD_CARRY_BACK_MAX_LEAD_WORDS` (6) before
the name and `..._MAX_TRAIL_WORDS` (2) after it. "kiki what's the weather"
fails the trailing test and is answered alone, which is the point — prepending
old chatter there would corrupt a perfectly good question. Carried text is used
once and the buffer is cleared, or it would be prepended to the next question
too. `[BLANK_AUDIO]`-style ASR narration never enters the buffer.

Her name and nothing else ("kiki", "hey kiki") with nothing to carry back opens
the listening window silently — the old wake word's exact behaviour.

**Latency is unchanged**, and that is what `hotword_speculative` is for. Idle
speech is transcribed at the 240 ms speculative endpoint, not only at the
600 ms commit; a hotword found there wakes the panel and prefills the box
immediately. It is not a second Whisper call — the commit path already reuses
that task. Because it is speculative it can be wrong, so the generation is
remembered (`_early_wake_generation`) and the window is closed again if the
final transcript does not confirm the name. A text hotword **never resets the
endpointer**: unlike a button or an acoustic wake it arrives *during* the
sentence it belongs to, and resetting would delete the utterance in flight.

Tests: `tests/test_hotword_text.py` (scoring, false positives, geometry),
`tests/test_hotword_session.py` (the wake decision, carry-back, early wake).

### 5.4 Face down is do-not-disturb

Turning Kiki onto her face is the one instruction that needs no screen, no
button and no sentence, so it is absolute. `session.note_posture()` sees the
posture change and `_enter_do_not_disturb()`:

* **stops what she is doing** — `cancel_turn` aborts generation and TTS and
  calls `device.stop()`, so speech and music both end;
* **closes the listening window** (`awake` is cleared *before* the cancel, so
  the state it settles on is `idle` and not an open window nobody asked for);
* **refuses to start anything of her own** for as long as she stays there.
  One check in `speak_background` covers every autonomous voice route —
  startup lines, worker and idle-mind speech, the journal's movement questions
  — and `ask_proactive_question` refuses separately so the scheduler logs why
  and retries later rather than burning its slot. `_handle_motion_event`
  checks it too, because `face_down` has a question bank of its own and the
  gesture that means *be quiet* must not be the one that speaks.
* Answering a **direct** question still works. Do-not-disturb stops Kiki
  interrupting you; it is not a mute switch on a question you chose to ask.

Two sources feed `note_posture`, and only a *change* acts: `motion_event`
carries the flip as soon as it happens, and `device_stats.imu_posture` (every
5 s) keeps the answer true afterwards — including for a session that connected
while she was already face down, where the event happened before the socket
existed. Only a posture the classifier is sure of ends the mode: `unknown`
is what it reports when it cannot tell, and letting one bad sample read as
"turned back over" would cancel do-not-disturb at random.

The panel says so on the ordinary status rows (`Do Not Disturb` / `Face down`),
sent as an `lcd` event rather than a new `state` name — the state machine that
would have to learn one lives in firmware, and a message explaining why Kiki is
quiet must not require an OTA to appear. `set_state` re-asserts it on every
return to `idle`/`followup`, or answering one direct question would leave
`Say 'kiki'` on a device that is still refusing to start anything. The board's
own `motion_face_down` reaction (the sulk face) is unchanged and needs no
firmware work.

Tests: `tests/test_do_not_disturb.py`.

### 5.4a Why she reconnected, and why it was never the Wi-Fi

Home wifi was fine. Stable wifi far from home, through the same tunnel, was
fine. A phone hotspot or a contended college AP dropped the session every few
seconds to a few minutes. That much was known. What was wrong was the
conclusion drawn from it -- that this was a link-quality problem -- because the
same log, read again on 2026-08-19, showed sessions of **15 to 275 seconds on
the home LAN, at RSSI -23 dBm, with zero Wi-Fi drops**:

```
device dropped the connection (rcvd=None sent=None uptime=15.4s)
device dropped the connection (... sent=1011 keepalive ping timeout ...)
device link health: rssi -23 dBm (ok), wifi drops 0 (last reason 0), ws drops 3
device firmware build 914f5969 ... link=lan
```

The radio was never the problem. A bad link only made an existing defect fire
constantly. See §6.5 for what it actually was and what owns the socket now.

The uplink gating described below is still here and still worth having, but it
is now the **default on any remote link** rather than something the board
switches on after it has already lost a session -- the old "wait for evidence"
trigger could only ever fire *after* the first drop, and it latched for the rest
of the boot so a link that recovered never got its ambient capture back.

The board uploads 16 kHz microphone audio **continuously** (~256 kbps), speech
or not, because the wake word is found in the transcript rather than in the
audio (§5.3). On a remote link that is most of what a phone hotspot has. Audio
therefore goes out around speech using the device VAD, with three guards against
the far worse bug of a deaf Kiki: only AEC-processed frames (the ones that carry
a VAD vote) are ever withheld, a 384 ms preroll is flushed on the way open so
the first syllable survives, and a short burst goes out every ten seconds
regardless. A quiet room's `audio_rate` reading well below 1.00x is therefore
expected on a remote link, not a fault.

Gating is a workaround for a frame costing 1 KiB on the wire, and it is the
first thing that should go once that frame is ~96 bytes of Opus.

**The board's black box.** A disconnect kills the device log that would explain
it, so RSSI, the Wi-Fi drop count and reason, and the websocket drop count ride
across in the next `hello` and are logged as `device link health`; RSSI also
joins the 5 s telemetry. From the gateway side a weak AP, a saturated uplink
and a bad tunnel look identical -- these separate them. Wi-Fi reason 2 is
AUTH_EXPIRE, 8 is the AP disassociating you. **Read these before blaming the
radio**: `wifi drops 0` with a strong RSSI means the fault is above the link.

### 5.4b One Kiki per process, not one per WebSocket

`server.py` builds a `DeviceSession` per connection. It used to build a whole
`LegacyKikiCore` with it -- config, knowledge base, saved summary,
`register_history()` -- and tear it down again on close. The board reconnects by
itself, so on a college AP that ran every 15 to 275 seconds, and each one cost:

* **the conversation**, back to a two-message history;
* **a KV-cache warm**, with `state="warming"` on the panel -- the "Warming up
  model" nobody could explain;
* **a cloud summarization call**, spent on a fragment that was one dropped
  packet in the middle of a sentence;
* **the worker scheduler and Unified Idle Mind**, stopped and restarted -- and
  since `start_background` waits 30 s to settle, a link that dropped inside that
  window meant they never started at all.

None of that is per-connection state. The socket is; the mind is not.
`inference.get_shared_core(config)` builds the runtime **once per process** and
every session attaches to it. A reconnect now goes straight to `idle` with the
conversation intact.

Three details are load-bearing:

1. **Attach is a stack, not a slot.** Sessions genuinely overlap -- the board
   reconnects before its previous socket has been reaped, so the departing
   handler's `finally` runs *after* the new one has attached. A naive detach
   unbinds the live session and leaves Kiki unable to speak, play music or run a
   device tool until the next reconnect, silently. `detach()` promotes whatever
   is still attached. `DisplayBridge.detach()` has the same hazard and the same
   guard, comparing with `==` because a bound method is a fresh object on every
   attribute access and `is` could never match.
2. **Everything device-shaped is late-bound.** The worker scheduler and the
   proactive-question loop outlive every session, so they read
   `_speak_callback` / `_proactive_callback` at call time rather than capturing
   a bound method of a socket that closed hours ago.
3. **The summary is written when the conversation is over, not when the socket
   is.** That is process shutdown (`server.py` installs SIGTERM/SIGINT handlers
   -- a bare `asyncio.Future()` never sees the signal the service manager sends)
   or `idle_summary_seconds` (30 min) with no device connected. It is also
   guarded against re-summarizing history a previous save already covered.

Tests: `gateway/tests/test_shared_core.py`.

### 5.5 Dance mode

"Kiki, dance" turns the whole panel into a stage. The gateway picks a song,
measures its beat grid and writes a routine; the board owns the animation and,
crucially, the clock.

```
"kiki, dance"
  ├ session.respond   -> dance.is_dance_request() fast path (0 ms, no box)
  └ or the `dance` tool the speaking model emits
       -> DeviceToolBridge._dance()
            ├ intro line spoken (covers the wait, never over the music)
            ├ choreography agent (cloud) -> song + routine
            ├ yt-dlp resolve + ffmpeg (the ordinary media path)
            └ beatgrid.analyse_url() -> BPM, downbeat, per-beat energy
       -> dance_start event, THEN the first media frame
```

**The clock is the whole feature.** Beat position comes from
`audio_pipeline_played_samples()` — the media samples the codec has actually
consumed, minus its 30 ms DMA tail — measured against the queued-sample count
at the instant the song's first frame arrived (`dance_note_media_frame`). Wall
time cannot be used: it knows nothing about the prebuffer gate, the adaptive
jitter buffer or a network stall, and any of the three walks the choreography
off the beat and leaves it there. Underrun silence is deliberately not counted,
because it is time the speaker waited rather than music it played.

**The model composes; it does not animate.** `firmware/main/kiki_dance.cpp`
owns 26 hand-built parametric moves with anticipation, squash-and-stretch and
per-move filter strength (a robot has to snap, a body roll has to flow — one
global smoothing value cannot do both). `gateway/kiki_gateway/dance.py` turns a
song into *which* move happens on *which* beat. `MOVES` and `EFFECTS` are a
wire format: the integers in the routine string are indices into the firmware's
`DanceMove`/`DanceEffect` enums, so a move may be appended but never reordered.
`tests/test_dance.py` parses `kiki_dance.hpp` and fails if the two ever drift.

**Three layers, in falling order of authority**: the cloud choreography agent
(same provider and JSON guards as `complex_query`, via `core.brain.fast_cloud`
— the local speaking slot is never touched), the measured beat grid, and the
composer, which fills every gap, extends the routine to the length of the song
and can write the whole thing alone. The composer *always* runs: a dance that
depends on a cloud call succeeding is a dance that sometimes does not happen.

**The routine is a compact string, not JSON.** ~250 entries of
`beat,move,beats,intensity,effect` is about 3.5 KB and parses with `strtol`;
the same thing as a cJSON tree is tens of kilobytes of parse on a board with
~166 KiB of internal heap. Energy is one hex digit per beat and drives the stage
lighting and the equaliser without any DSP on the device.

**Stopping.** Six routes, one exit (`session.end_dance`, idempotent):
a tap anywhere on the stage, turning her upside down (`DANCE_STOP_POSTURE`,
acted on by *both* the board's own classifier and `note_posture`), the song
ending, `cancel_turn` (Stop button, open palm, barge-in), going face down, and
talking to her — `duck()` ends a dance instead of pausing it, because resuming
a routine mid-conversation puts her back on a beat grid the listener has lost.
The board stops locally and instantly on a tap (`audio_pipeline_stop_playback`
before the round trip) and *then* tells the gateway. Two firmware watchdogs
make the dance room impossible to get stuck in: no music within 12 s of the
routine arriving, or 3 s of no samples once it has.

**Audio always wins.** Rendering shares core 0 with the Wi-Fi stack feeding the
song, so `dance_render_frame` backs off to 100 ms frames once the board's
buffer falls below its own prebuffer target and to 200 ms once it is half
empty. A single 60 ms threshold was too blunt: by then the buffer is already
empty half the time.

**Stopping never blocks the display.** The touch handler runs on the LVGL task
holding the display lock, so `dance_stop` only latches the intent — a worker
task does the silencing and the `dance_stop` send (whose 5 s timeout, on a link
congested by the song being stopped, froze the panel for seconds), and the next
render tick restores the screen. `dance_fps` and `dancing` appear
in `device_stats` while a dance runs, and only then.

Config: `KIKI_GATEWAY_DANCE_*` (`dance_enabled`, agent deadline, analysis
seconds, `dance_min_beat_confidence`, `dance_default_bpm`, `dance_intro_line`,
`dance_max_seconds`).

The `dance` tool is registered into the live legacy runtime by
`inference._register_dance_tool`, re-applied on every config reload alongside
the hardware overrides — `gateway/legacy_kiki/` is a gitignored copy of
KikiFast, so a tool defined there would be lost on the next sync.

Tests: `gateway/tests/test_dance.py`, `test_beatgrid.py`,
`test_dance_session.py`, and `firmware/tests/test_dance_routine.cpp`
(host-compiled against `kiki_dance_routine.cpp`, which is kept free of LVGL and
ESP-IDF for exactly that reason).

---

### 5.6 `health_sih` — the SIH health companion

An **additive** mode. Everything in it is inert until Kiki is switched into a
mode that declares the `care` capability; in `default` the whole feature costs
one Python import and one dictionary lookup per turn. That is the design
constraint it was built under, and `tests/test_care_mode.py` plus
`tests/test_care_gateway_wiring.py` are the tests that hold it.

```
gateway/kiki_gateway/care/
  mode.py       the health_sih definition + the capability gate (fails closed)
  runtime.py    CareRuntime: the switch, and the only object the gateway calls
  plan.py       the care plan store -- versioned, file-locked, ON THIS MACHINE
  agent.py      the live care conversation, IMU-grounded
  imu.py        raw wrist motion -> measured features -> prompt evidence
  cadence.py    the hold countdown, played on the BOARD's speaker
  heart_rate.py drives the board's MAX30102 over the WebSocket
  tools.py      get/update_care_plan, start_care_session, measure_heart_rate,
                get_wearable_status, alert_family
  scheduling.py routine events -> the worker scheduler the gateway already runs
  care_now.py   the compact `CARE NOW` row on the live context anchor
  environment.py  Open-Meteo weather + CPCB AQI, no key, no Pi
  alerts.py     WhatsApp + email, with accepted / delivered / failed kept apart
  companion.py  seeded briefing / reflection / hydration routines (off by default)
```

**No Raspberry Pi is required.** The care plan, the wearable telemetry store and
the environment provider all run here. `health_bridge.py` still posts to the Pi's
port-8091 service when it is up, but `session._handle_health_telemetry` now
stores every batch locally FIRST and acknowledges the board on that -- so with
the Pi off, the band's three-batch queue drains instead of retrying forever.

**Why the package is vendored rather than imported.** Most of `plan.py`,
`agent.py`, `care_now.py` and `environment.py` are KikiFast's health stack,
copied and rewired. `gateway/legacy_kiki` (and `gateway/kiki_runtime`) are
snapshots of KikiFast that are re-synced wholesale, and both predate that stack
entirely -- no `core/health/`, no `care_voice_agent`, no `mode_has_capability`.
Vendoring is also what makes the package **root-agnostic**: it depends only on
`get_full_config`, `get_active_mode`, `run_agent_loop`, `fast_cloud` and
`tools.execute_tool`, all of which both trees have.

#### The evidence is wrist motion, not a camera frame

The RPi judges a guided exercise from photographs taken during the hold. This
body has no camera and does have a 50 Hz IMU on the wrist that is doing the
movement, so the substitution is straight across:

| RPi | Here |
|---|---|
| `capture_best_frame_b64()` during the hold | `imu_window_start` -> firmware ring buffer |
| JPEG attached to the Cerebras request | measured features as text in the prompt |
| `visual_observation` / `person_in_frame` | `motion_observation` / `band_worn` |
| byte-identical JPEGs = frozen camera | byte-identical samples = stuck sensor |
| `continuous_vision` on a routine | `motion_tracking` |

`imu.analyse()` computes what the model is told: movements counted, cadence,
degrees swept (the integral of |gyro|), stillness, wrist angle at each end,
intensity, and an impact flag. **Nothing in the prompt is an impression** --
handing a model 500 raw samples asks it to do signal processing in prose, and it
will happily oblige with numbers it did not compute.

The 2026-09-02 grounding repair carries over unchanged, because the failure it
fixed is about the model and not about the sensor: observation before verdict,
`instruction_followed` checked in code, first mismatch corrected out loud with
the microphone still muted, second one stops and asks, and **no evidence is
stated rather than guessed around**. A still wrist when a movement was asked for
is `"no"`, not encouragement.

`kiki_exercise.cpp` on the board records the window and closes it *on the sample
that completes it*, so the window is exactly the interval asked for regardless of
task scheduling. The wire format (int16 milli-g / centi-dps, little-endian,
base64) lives in the ESP-IDF-free `kiki_imu_wire.hpp` and is pinned against the
same literal vector on both sides -- `firmware/tests/test_imu_wire.cpp` and
`tests/test_care_imu.py` -- because a scale or endianness disagreement would not
fail loudly, it would produce plausible motion evidence that is wrong.

#### The care plan on the watch

`firmware/main/kiki_care_ui.cpp`, reached from Settings -> "Care plan". The plan
lives on the laptop; the board renders it and can act on it: tap a routine for
its time, title and the brief Kiki was actually given, then **Start now**,
**Turn off** (off, not gone -- a paused routine is dimmed and unscheduled, not
deleted) or **Delete** (two taps, arming lapses after 4 s, same shape as Shut
down). A live session shows as a red **End session** banner above the list --
one tap, no confirmation, because someone reaching for it wants it to stop now.

The board is a VIEW. It holds no care state beyond the last push, every action
goes to the gateway as `care_action`, and the list is only redrawn when the
gateway sends the whole plan back as `care_plan`. So the screen cannot show a
deletion that did not happen -- the same rule the rest of health mode follows
about never claiming an action succeeded.

Two switches sit beside it in Settings, persisted in NVS and **enforced on the
board**: `Fall alerts` and `Movement checks`. Fall detection runs in firmware
and has to be switchable while the gateway is unreachable, which is exactly
when a false alert is hardest to stop. The detector keeps RUNNING when alerts
are off -- its ladder needs continuous history, so re-arming from cold would
mean the first real fall after switching back on is the one it misses -- only
the alert is suppressed. Both switches are mirrored to the gateway as
`care_options` for one reason: so the care agent says "movement checks are
switched off" instead of "the band did not send a window", which is the
sentence a broken sensor produces.

#### Timing a hold out loud

`cadence.py` renders the per-second countdown and the short cues once, caches
them, and plays them through the board's speaker over the ordinary TTS PCM path
(the RPi spawns `mpv` at a local ALSA sink; there is no speaker on this
machine). The hold blocks with the microphone shut, and the IMU window is armed
first and runs slightly longer, because the movement starts when the
instruction ends rather than when the last tick plays.

With no player registered the hold still **takes the full time and still
records** -- only the beeps are missing. That degradation is deliberate: a
silent hold is survivable, a hold that collapses to a zero-second pause is not.

#### What it does not do

* Never states a heart rate the band did not report. A failed capture is an
  attempt in the care log, never a number (`tests/test_care_session.py`).
* Never claims to see anyone. There is no camera; the prompt says so.
* Never promises a plan edit, a schedule or a message without a verified tool
  result, and `alert_family` reports `delivered` / `accepted` / `failed` per
  channel rather than treating a tool's prose as a receipt.
* Seeded companion routines are OFF by default (`seed_companion_routines`).
  Things that speak on a schedule are switched on deliberately.

#### Where it touches the rest of the gateway

Five places, each a no-op outside health mode: `_apply_hardware_overrides`
(registers the mode and its tool schemas, re-applied on every config reload like
`dance`), `_build_system_prompt` (appends the health layer -- the mode declares
no `system_prompt` of its own, deliberately, or it would lose the battery
personality, the embodied-context row and long-term memory), `_execute_calls`
(care tools intercepted so they reach THIS plan and not the older `senior` file),
`_inject_time_and_battery` (one change-gated `CARE NOW` line on the existing
append-only anchor), and `session.respond` (a live session owns the microphone
until it closes).

Config: nothing is written into the shared `config.json`. Tunables live in
`care/settings.py` with `KIKI_GATEWAY_CARE_*` env overrides; the plan file
defaults to `gateway/care-state/care_plan.json`, deliberately NOT `senior`'s.

Status: the gateway half is deployed and tested (391 pass on both machines, the
same 6 pre-existing failures as before the change). The firmware half **builds
but has not been flashed** -- until it is, no `imu_window` ever arrives and the
coach says it cannot feel any movement, which is the honest degradation.

## 6. Networking

### 6.1 Choosing a network (`kiki_setup.cpp`)

`setup_ensure_wifi()` → `join_known_networks()`:

1. The NVS-saved network, 20 s.
2. The Kconfig network, 20 s — **unless `forgotten` is set**.
3. Otherwise the on-panel picker.

The picker's wait is **not** infinite unless the user asked to be there. If the
board is unattended and its network vanished, it re-tries the known networks
every 25 s; after "Change Wi-Fi" it waits indefinitely, because a human is
standing at the panel. An earlier `portMAX_DELAY` here meant a single transient
Wi-Fi failure parked the board on a setup screen forever, silent, before any
telemetry task existed to say so.

Wi-Fi power save is **off always** (`WIFI_PS_NONE`), on battery too, and
`CONFIG_ESP_WIFI_STA_DISCONNECTED_PM_ENABLE` is disabled so the radio does not
power down while disconnected and delay its own reconnect.


**Kiki remembers up to four networks, and only tries the ones she can hear.**
`wifi_station_remember()` promotes a network to the front of the list on every
successful join -- panel picker, remembered network, or compiled-in fallback
alike, all through `try_network()` so none of them can drift. Boot scans once
and skips any known network the scan cannot see.

This matters because the board moves. With one remembered network, arriving at
college meant a 20-second join timeout for the house network before anything
else was tried, and coming home meant the same for the college AP -- on every
boot, in both directions. The scan costs a couple of seconds; each absent
network costs ten times that.

`wifi_station_forget()` clears *every* slot, not just the first: "Change Wi-Fi"
is a deliberate request for the picker, and leaving remembered networks behind
would let the board quietly rejoin one, which is indistinguishable from the
button being broken.

`firmware/tests/test_known_networks.cpp` pins the promotion rule, including the
two cases that silently lose a network: re-joining a known one must promote
rather than duplicate, and the network currently in use must never be evicted.

**Pinning the board to one network for a test.** `CONFIG_KIKI_WIFI_FORCE_BUILTIN`
makes `join_known_networks()` use the built-in SSID and nothing else -- no saved
network, no fallback. It exists because the fallback is what quietly rescues the
board onto the house Wi-Fi the moment a weak AP drops, which turns a bad-link
test into a good-link one without anything in the log saying so. With it set
there is no rescue: if that network is out of range the board sits on the
provisioning screen. `hello` now also reports the SSID the board is *actually*
associated to, because "which network did it really join" was costing an hour
at a time to answer.

### 6.2 Choosing a gateway (`gateway_client.cpp`)

Three URI slots, tried in order, `kAttemptsBeforeSwitch = 4` failures each:

| Index | Source | Typical |
|---|---|---|
| 0 | `CONFIG_KIKI_GATEWAY_URI` | `ws://192.168.1.10:8765` — LAN |
| 1 | NVS `kiki_gw/fallback`, learned from `hello_ack` | Cloudflare quick tunnel, ~20 ms |
| 2 | `CONFIG_KIKI_GATEWAY_URI_FALLBACK` | Tailscale funnel — slow (~450 ms) but a **stable hostname** |

The board resets to index 0 on every boot. `gateway_client_is_remote()` is
`index != 0` and drives the audio-rate decision (§7).

Slot 2 exists because a Cloudflare *quick* tunnel gets a new hostname every
restart, so the learned URL in slot 1 is dead after every laptop reboot. The
Tailscale funnel's hostname never changes, which makes it the **bootstrap
channel**: the board gets in through it, and `hello_ack` tells it the new fast
address.

**The board then moves to that address immediately** — it does not wait for the
funnel to fail, because the funnel is not failing. Waiting would mean paying
447 ms instead of 20.6 ms for the rest of the session. `store_fallback_uri()`
returns whether the URL actually *changed*, and only a change while sitting on
slot 2 triggers the jump (`g_switch_to`). That guard is what stops a ping-pong:
a tunnel hands out the same hostname for as long as it lives, so a repeat cannot
re-trigger anything, and if the new address turns out to be broken the board
falls back to the funnel, is told the same URL again, and stays put.

It is only ever an upgrade. From the LAN there is nothing better to move to.

`with_explicit_port()` normalises `:443`/`:80` — without it a `wss://host/`
fallback inherited the LAN URI's port 8765 and could never connect.

### 6.2a A polite close is not a disconnect

`esp_websocket_client` raises three different events for "the link went away",
and they do not behave the same:

| event | when | auto-reconnects |
|---|---|---|
| `WEBSOCKET_EVENT_DISCONNECTED` | an established link dropped | yes |
| `WEBSOCKET_EVENT_ERROR` | a connect attempt never completed | yes |
| `WEBSOCKET_EVENT_CLOSED` | the peer sent a **close frame** | **no** |

The board handled the first two and ignored the third for months without
consequence, because the gateway only ever died abruptly. The moment the
gateway learned to shut down *politely* -- a SIGTERM handler that saves the
conversation and closes the server, and `close(1001, "superseded")` when a
reconnecting board evicts its own stale socket -- every deploy locked the board
out permanently.

The symptom is the confusing part: the board stays on Wi-Fi, answers pings, and
runs normally. Only the gateway session is missing, so the panel behaves as if
nothing is wrong while **tap and hold do nothing** -- there is no socket for
their events to travel on. It reads as dead hardware and it is not. Measured
once at eleven minutes; it ends only with a reset.

`WEBSOCKET_EVENT_CLOSED` now asks `failover_task` to stop and start the client
on the **same** address after a second: a polite close says nothing bad about
the address, so rotating to the tunnel would be wrong. The restart cannot happen
in the event callback -- stopping the client from its own task deadlocks, which
is the same constraint the failover switch has always had.

**Anything that makes the gateway close politely must be checked against this.**
It is invisible in the gateway log, which records a clean shutdown and then
simply never sees the device again.

### 6.3 Identifying which path the board is on

Do **not** infer it from the gateway's peer address. `192.168.1.2` and
`.5` both occur on either path. Reliable tells:

- **Serial:** `kiki_wifi: connecting to "<ssid>"` names the network; the DHCP
  lease distinguishes the link (`10.14.73.x` = a phone hotspot, `192.168.1.x` =
  the house LAN).
- **Gateway:** `link=lan` / `link=cloud` / `link=backup` in the
  `device firmware build …` line — the board names the slot it is using in
  `hello`, and only the board actually knows. Consecutive hellos reading
  `backup` then `cloud` are the bootstrap of §6.2 working: the board's own
  "switching now" log is written microseconds before it tears the socket down
  for that switch, so it never arrives, and the hello is the durable record.
  (Firmware predating this field sends nothing and is treated as `lan`, so it
  keeps getting 48 kHz audio.)
- Peer `127.0.0.1` on the gateway does mean "arrived through cloudflared", but
  a local test client looks identical.

### 6.4 The public tunnel

`scripts/run_tunnel.sh` starts a Cloudflare quick tunnel to `127.0.0.1:8765`
and writes `wss://<host>/` into `public_uri.txt`. The gateway reads that file on
every device connection and hands it to the board in `hello_ack`.

It is **supervised by `server_manager.py`** (added by
`scripts/patch_manager_tunnel.py`), so it returns after a laptop reboot like
everything else. Two details that matter:

- `--supervised` keeps the script in the foreground for the tunnel's lifetime.
  The manager tracks the process it launched, so a script that forks and exits
  reads as a dead service and is restarted forever.
- Health is the **presence of `public_uri.txt`**, not a port: cloudflared has no
  local listener, and being alive says nothing about whether it ever got a
  hostname. The script deletes the file at startup so a stale URL cannot make a
  failed tunnel look healthy.
- `cloudflared` is resolved explicitly (`$CLOUDFLARED`, then `PATH`, then
  `~/bin/cloudflared`) because `~/bin` is not on a service's `PATH`.

Cloudflare over Tailscale Funnel was a measured decision: LAN 6.0 ms,
Cloudflare 20.6 ms, Funnel 447 ms — the Funnel ingress is in Dubai.

---

### 6.5 One mutex owned the whole socket

This is the section to read when the board misbehaves on a slow link. Four
separately-reported symptoms -- the talk button hanging, listening arriving
late, speech stuttering, and the session reconnecting with "Warming up model"
-- were one defect seen from four sides.

`esp_websocket_client` is built here with a **single lock**:
`CONFIG_ESP_WS_CLIENT_SEPARATE_TX_LOCK` is deliberately off (§4.5 -- a separate
TX lock races mbedTLS teardown and reboots the board, and `gateway_client.cpp`
has an `#error` so nobody re-enables it by accident). So
`esp_websocket_client_send_*` takes `client->lock`, and so does the client's own
task to read the socket and to answer a PING. One blocked send therefore stops
*everything*:

| What blocked | What the user saw |
|---|---|
| a microphone send waiting on a congested uplink (up to `kSendTimeout`, 5 s) | Kiki's own speech was not drained from the socket -> **the speaker stuttered** |
| ...the same send, holding the lock the PING needs | **`1011 keepalive ping timeout`**, a healthy board reaped |
| the LVGL touch callback calling `send_event()` straight through | **"tap or hold" hung**, and on timeout the event was silently dropped, so the press did nothing |
| `handle_json` / `handle_binary` running *inside* the websocket callback, still holding the lock, where they take `bsp_display_lock(100)` and used to block 250 ms on a full playback ring | the socket froze for 100-250 ms **per frame**, exactly when a stalled link recovered and the gateway dumped its backlog |

`kiki_log.cpp` had already named the mechanism in a comment -- *"a full
websocket send holds the client mutex for its duration, which is what starves
the keepalive and drops the session"* -- and rate-limited itself around it. The
fix generalises that: **the socket has exactly one owner and nothing else ever
touches it.**

**Outbound.** `gateway_client_send_event()` is enqueue-only. `kiki_net_tx`
drains urgent control, then ordinary control, then bulk, then one microphone
frame, and loops. That ordering is the point: a button press overtakes a second
of queued audio instead of waiting behind it. The classes live in
`gateway_tx_priority.hpp`, free of ESP-IDF so `firmware/tests/test_tx_priority.cpp`
can pin them on the host -- putting `cancel_turn` in the wrong class would make
barge-in unreliable and nothing would fail loudly. Queues live in PSRAM, as does
the single staging buffer producers copy through (a `TxEvent` is ~960 bytes and
these callers have 3-4 KiB stacks). A full urgent queue discards the **oldest**
entry: a press from before the last reply is worth less than the one just made.

**Inbound.** The websocket callback copies the assembled message into a PSRAM
ring and returns in microseconds; `kiki_net_rx` does the parsing, the display
work and the playback queueing. One ring, so wire order is preserved -- a
late-processed `audio_stop` is harmless because it carries stream watermarks,
but an `audio_end` reordered ahead of its own PCM would end a reply early.

**The governor.** Every send is timed in one place. A send over 250 ms closes
the microphone gate for a backoff (500 ms, doubling to 4 s) and a prompt one
halves it back. Reversible, unlike the old one-way latch, so a board that walks
back into good Wi-Fi starts listening properly again without a reboot.
`send_worst_ms`, `tx_dropped`, `rx_dropped`, `uplink_thrifty` and `mic_queued`
are in `device_stats`, so this is checkable from the laptop. On the LAN,
`send_worst_ms` reads **2**; the whole failure mode was that number reaching
into the seconds.

**A side effect worth keeping.** The 64-slot microphone queue is ~66 KiB and was
in internal RAM, the pool this board runs out of first (§4.6). Moving it -- and
the tx queues and the rx ring -- to PSRAM changed the board's steady state from
**19 KiB internal free / 7-12 KiB largest DMA block** to **84 KiB / 31 KiB**.
The old figures were close enough to the `ESP_ERR_NO_MEM` display floods of §4.6
to be worth noticing on their own.

**Do not treat a write timeout as flow control on this client** (§8). That trap
is unchanged; what changed is that nothing blocking sits between a person and
the socket any more.


## 7. Audio, and why the rates change

Uncompressed 48 kHz mono s16 is **768 kbps in each direction**. Measured over a
phone hotspot + the public tunnel, the board actually gets **~396 kbps**. Both
directions were roughly 2× oversubscribed, which produced: 74 of 3449 mic frames
arriving, 16.5 s of playback gaps per few replies, and aborted sockets.

The ESP-SR microphone path is now 16 kHz on **every** link; legacy/raw firmware
still changes its microphone rate only on the remote link. TTS remains 48 kHz
on LAN and drops to 16 kHz remotely:

| Direction | LAN | Remote |
|---|---|---|
| Mic → gateway | **kind 4, 16 kHz** after direct ESP-SR AEC | **kind 4, 16 kHz** after direct ESP-SR AEC |
| TTS → board | kind 2, 48 kHz | **kind 5, 16 kHz** — board expands ×3 by linear interpolation |
| Media → board | kind 3, 48 kHz | **kind 6, 16 kHz** — same expansion |

Nothing downstream wanted 48 kHz anyway: the gateway immediately decimates to
16 kHz for the wake word, VAD and Whisper. The only casualty is RNNoise, which
needs 48 kHz input — a fair trade for audio that arrives.

The board tells the gateway which mode to use via `"link"` in `hello`.

**Result on hotspot + tunnel:** mic `rate=100.0/s audio_rate=1.00x` with zero
loss; playback underruns 51 → 2, gap time 16 560 ms → 120 ms.

### 7.0 The jitter buffer is a start gate, and a start gate is TTFW

The board's prebuffer used to be two things at once: the cushion that absorbs
network jitter *and* the gate that decides when the first word comes out. It
grew +60 ms after any stuttered reply, up to a 340 ms ceiling. Measured on the
bench harness: six turns on an **unshaped LAN link** walked it from 100 ms to
300 ms. The punishment for a bad moment was a permanently slower Kiki, and on a
near-saturated link every millisecond of cushion is a millisecond of wall time,
because 340 ms of 16 kHz PCM is 10.9 KB and takes 340 ms to arrive.

The two ideas are now separate:

* **The start gate is fixed at its 100 ms floor** and nothing can raise it, so
  time-to-first-word does not depend on link quality.
* **The cushion is the gateway's send lead**, which is free: pacing only ever
  delays frames *after* the first (`LATENCY.md` records 0.2 -> 0.6 s costing
  nothing). It is per-link -- `playback_lead_seconds` 0.6 on the LAN,
  `playback_lead_seconds_remote` 2.5 -- with its own larger value for music,
  which has no latency requirement at all.
* **A stuttered reply asks for more instead of paying for it.** The board sends
  `request_lead` and the gateway raises that session's lead, up to
  `playback_lead_seconds_max` (5 s).

The playback ring went 128 KiB -> 768 KiB (PSRAM, ~8 MB free) because it has to
hold whatever lead is in flight. It did not before, and the surplus was thrown
away on arrival -- not a gap, **missing words**. Measured before the change on
an unshaped LAN link: 139 KB discarded across six replies, 1.45 s of speech
nobody heard. `dropped_bytes` in `device_stats` is that counter.

Verified with `tools/slowlink.py` + `tools/fake_device.py` at 5 / 2 / 1 Mbps
with 120 ms RTT and 60 ms jitter: audible gaps 3.2-3.6% of speech time -> 0%,
audio discarded on arrival 0.6-1.5 s per row -> **0 bytes**, and `prebuffer_ms`
stays at 100 on every row. With the link additionally severed for 8 s every
minute: 40 losses across six turns, all six answered, TTFW unchanged, and zero
"Warming up model" (§5.4b).

### 7.0a Opus, and why it is a latency fix as much as a bandwidth one

On a remote link Kiki's voice goes out as 16 kHz 16-bit PCM: **256 kbps that has
to arrive faster than it plays, continuously**, or the speaker runs dry. At
-92 dBm through a tunnel it does not. Measured on the real board on a weak open
AP, every reply logged 10-21 underruns and the panel showed "thinking" and then
nothing at all.

The reconnects were the same fact wearing a different hat. The WebSocket
keepalive PING shares one TCP connection with the audio, so ~80 KB of queued
PCM sits *in front of* it. The board answers a PING it has not received yet;
the gateway gives up at `ping_timeout`; every drop in the log reads
`1011 keepalive ping timeout` at uptime 80-100 s, a metronome rather than bad
luck. **A link too slow to speak on is also a link that cannot stay up**, and
one change fixes both.

`BinaryKind::TtsOpus16k` (7) carries the same speech in ~32 kbps, roughly six
times less on the wire once the 24-byte header is counted.

Three things about the shape of this:

- **The board only ever decodes.** Decoding Opus costs about a tenth of what
  encoding does, which is the only reason it fits: the AEC already uses 23 ms of
  every 32 ms frame on core 1, and there was nowhere to put an encoder.
  `handle_binary` runs on `kiki_net_rx` (core 0), so the decoder never competes
  with it. This also removes the CPU bench that used to gate the whole idea.
- **The microphone stays PCM, deliberately.** It is already gated to a ~5% duty
  cycle by the thrifty uplink, so it is not what saturates the link, and putting
  a lossy codec in front of Whisper is a transcription-accuracy question that
  needs a measurement rather than a guess.
- **Negotiated, never assumed.** The board's `hello` carries
  `codecs:["opus","pcm"]`; the gateway sends Opus only to a device that asked
  for it. Firmware older than that list sends nothing and keeps getting PCM, and
  a gateway older than this keeps sending PCM to a board that can decode Opus.
  Neither side has to be upgraded in step with the other.

On quality: what was on the wire was *already* band-limited to 16 kHz, and the
board *already* expands it to 48 kHz with linear interpolation
(`queue_playback_from_16k`) -- a far cruder approximation than anything Opus
adds at 32 kbps. The honest comparison is not Opus against perfect audio, it is
Opus against a reply that never arrived.

**The parser rejected half of every Opus reply.** `parse_audio_frame` ended with
`return (*payload_size % sizeof(int16_t)) == 0;` -- correct for every payload the
protocol had ever carried, because they were all whole 16-bit PCM samples. Opus
packets are arbitrary lengths: about half are odd, and a silent 20 ms frame is
nine bytes. Those frames were dropped before reaching the decoder, so roughly
half of each reply vanished and the speaker cracked its way through the rest.

It defeated four rounds of instrumentation because the rejection happens
*upstream of everything worth measuring*: the frames never reached the decoder
(zero decode errors), never reached the playback ring (zero dropped bytes), and
the gateway's own accounting showed the audio going out ahead of schedule. Every
statistic was clean. The only evidence anywhere was a single rate-limited
`bad binary frame (33 bytes)` line in the device log.

`firmware/tests/test_protocol_frames.cpp` guards it, and was checked against the
original: six failures on the old parser, clean on the new one. **When a new
binary kind is added, that even-length rule is the thing to check first.**

`gateway/kiki_gateway/opus_codec.py` holds the encoder. libopus only accepts
exact frame sizes (2.5/5/10/20/40/60 ms) and the synthesizer's chunks never line
up with any of them, so whatever does not fill a 20 ms frame is carried into the
next chunk -- and flushed at the end of the reply, or that carry would prefix
the *next* one.

### 7.1 Full-duplex AEC and voice barge-in

The board's ES7210 TDM stream is physically `MIC1, MIC3, MIC2, MIC4`. MIC1 and
MIC2 are the front microphones; the PCB routes the ES8311 analog speaker signal
to MIC3 as a clock-synchronous playback reference; MIC4 is unconnected.
`audio_pipeline.cpp` low-passes and decimates the front mic and reference from
48 to 16 kHz, then calls ESP-SR's direct full-duplex high-performance AEC with
aggressive nonlinear echo suppression. A separate very-aggressive ESP WebRTC
VAD supplies the device vote. Music and TTS remain 48 kHz at the codec. The
threaded AFE wrapper is intentionally not used: on the complete display/audio
application it overran its feed ring and produced only 0.7x real-time audio.
The direct processor has a four-frame bounded queue; three consecutive misses
disable AEC and immediately restore raw microphone streaming. A DSP overload
can therefore disable barge-in, but cannot leave Kiki deaf.

**And it re-arms itself.** That fallback used to be permanent for the boot,
which made it a silent, indefinite loss of barge-in: the gateway accepts a
barge-in only from a frame the AEC processed, so with AEC off interrupting her
does nothing and *nothing says why*. Observed on 2026-08-17 — twenty seconds of
shaking fired ~30 motion reactions, each drawing a face and playing a local
sound, which starved the AEC task past its queue three times running; barge-in
then stayed dead for the hour that followed, and the raw fallback delivered only
~0.5x real-time audio on top of it. `maybe_rearm_aec()` now flushes the queue,
the partial chunk and the decimator history, and turns AEC back on under two
conditions:

* a **quiet interval** since the stall — 5 s, doubling per stall to a 120 s
  ceiling, so a genuinely overloaded board settles into raw listening instead of
  flapping. A boot is the only thing that resets the backoff.
* **nothing playing** (`audio_pipeline_is_playing()`). This one is about
  correctness, not thrash: the gateway treats the AEC-processed flag as its
  evidence that a frame cannot contain Kiki's own voice, so re-arming mid-reply
  would hand it output from a filter that has not converged on the echo yet, and
  the likeliest thing it would hear is Kiki interrupting herself. Re-arming only
  in silence makes the restart identical to the one at boot.

Only an overrun arms recovery. An AEC that never started, or whose output buffer
allocation failed (its task is gone, so nothing would drain the queue), stays off
for the boot as before. `aec_rearms` in `device_stats` counts the recoveries and
the board logs `direct AEC re-armed after Nms quiet`.

AEC output carries binary-header flags `AEC_PROCESSED=0x01` and, when applicable,
`DEVICE_VAD_SPEECH=0x02`. During playback the firmware sends only frames with
the first flag; a failed AFE falls back to raw audio but keeps that raw stream
gated, so failure cannot create self-interruption.

The gateway only arms automatic barge-in after real TTS PCM begins and a 600 ms
startup guard expires. It excludes music and requires, simultaneously: the AEC
flag, device VAD, a separate Silero VAD at 0.82, an absolute -42 dBFS floor, a
6 dB adaptive room-noise margin, and six positive votes including three
consecutive votes. On confirmation it sends `audio_stop`, cancels generation,
and feeds the AEC-clean preroll into the ordinary endpointer. The thresholds are
environment-overridable with the `KIKI_GATEWAY_BARGE_IN_*` settings in
`GatewayConfig`; field calibration should tune those values, not weaken the
two-VAD/AEC trust boundary.

The on-panel Settings list has a `Barge-in On/Off` item. Its value is stored in
the board's `kiki_settings/barge_in` NVS key and announced to every new gateway
session. Off is fail-closed: AEC may continue running for ordinary microphone
quality, but no playback-time microphone frames leave the board and the gateway
also refuses detection.

Cancellation isolates response generations at both ends. Each TTS worker owns
an immutable cancellation token, stream ID and sequence counter; beginning a
new listening window never clears an old worker's token, and cancellation is
checked again after the sender's pacing sleep. Breaking out explicitly closes
the old TTS HTTP stream, releasing the synthesizer before the next reply. Every
`audio_stop` names explicit TTS and media stream cutoffs. The board raises its
acceptance watermarks from those IDs before synchronously draining playback.
A FreeRTOS byte ring may have only one outstanding receive, so the playback task
is the sole consumer and also performs cancellation drains. The gateway task
signals it and waits at most 250 ms for acknowledgement, avoiding both a
second-receiver false-empty result and an unbounded WebSocket-handler lock. After
the drain, playback mutes the codec while a 40 ms silence block clears its 30 ms
TX-DMA capacity. Delayed socket PCM, ring-buffer contents, and DMA contents
therefore cannot prepend the cancelled answer to the next response.

---

## 8. Protocol

**Binary frames** — `protocol.hpp` / `protocol.py` must stay in step:

| Kind | Meaning |
|---|---|
| 1 | Mic PCM s16 mono 48 kHz |
| 2 | TTS PCM s16 mono 48 kHz |
| 3 | Media PCM s16 mono 48 kHz |
| 4 | Mic PCM s16 mono **16 kHz** (direct ESP-SR AEC, or legacy remote path) |
| 5 | TTS PCM s16 mono **16 kHz** (remote) |

The header's flags byte is meaningful for mic frames: bit 0 means ESP-SR AEC
processed the payload and bit 1 is the device VAD speech decision. Other frame
kinds currently keep flags at zero.

**Device → gateway events:** `hello` (token, device, firmware, link),
`push_to_talk`, `commit_now`, `cancel_turn`, `sleep`, `media_control`,
`request_lead` (§7.0: a stuttered reply asking to be buffered further ahead),
`set_volume`, `set_gain`, `set_barge_in`, `config_set`, `config_commit`, `motion_event`, `dance_stop`, `device_stats`,
`device_log`, `playback_drained`, `ota_result`, `shutdown`.

**Gateway → device events:** `hello_ack` (fallback_uri, rates, endpoint timings),
`state`, `speech`, `speech_start`, `response_sentence`, `stt_speculative`,
`stt_speculative_invalid`, `expression`, `volume`, `gain`, `timer`,
`tool_result`, `media_started`, `audio_stop` (with TTS/media cutoff IDs), `ambient`, `first_pcm`,
`audio_gap`, `config_options`, `firmware_update`, `dance_start`, `dance_stop`, `error`.

**Auth:** the board sends the token in `hello`; a mismatch is closed with 1008
`unauthorized`. The token is compiled into the firmware image — treat any
publicly-served `.bin` as disclosing it.

**A trap worth knowing:** `esp_websocket_client` treats a short write as fatal
and aborts the *entire connection*, using the timeout the **caller** passed, not
`network_timeout_ms`. Event sends once passed 100 ms, so any moment the socket
was not writable for a tenth of a second tore the session down. All senders now
share `kSendTimeout` (5 s). Shortening it to "drop a frame under congestion"
does the opposite of what it looks like. Congestion is shed by queue depth in
`microphone_network_task`, where a dropped frame is only a dropped frame.

---

## 9. OTA — updating the board over the air

The board fetches its own image over **HTTPS**, so the URL must be reachable
from wherever the board is; the gateway refuses any pending URL that is not
`https://`.

```bash
# on the Pi
./scripts/build_firmware.sh
scp firmware/build/kiki_esp32.bin vaibhav@vaibhav:~/kikifw/

# on the laptop
cd ~/KikiESP32 && set -a && . ./gateway.env && set +a
./scripts/push_ota.sh ~/kikifw/kiki_esp32.bin     # prints the build ID
grep -E 'kiki_ota|device firmware build' gateway.log
./scripts/push_ota.sh --stop
```

`push_ota.sh` serves the image, gives it a throwaway Cloudflare hostname, and
writes the URL to `pending_ota.txt`.

Delivery: pushed at `hello`, **and re-offered every 10 s** while the board stays
connected — otherwise a queued update sits untouched for as long as the link is
healthy. The device retries the download 3× and reports `ota_result` either way.
After installing it reboots; if it cannot reach the gateway within 180 s it
restores the previous image by itself.

**Confirm with the build ID, never the compile date** — `device firmware build
<id>` in the gateway log, the same string the panel shows at boot.

---

## 10. Building and flashing

**Build (Pi only — ESP-IDF 5.5 is at `.tools/esp-idf`):**

```bash
./scripts/build_firmware.sh     # sources export.sh, forces esp_app_desc rebuild
```

**Flash over USB (laptop, board on `/dev/ttyACM0`):**

```bash
esptool --chip esp32s3 --port /dev/ttyACM0 --baud 921600 \
  --before default-reset --after hard-reset write-flash \
  --flash-mode dio --flash-size 16MB --flash-freq 80m \
  0xf000  ota_data_initial.bin \
  0x20000 kiki_esp32.bin
```

Include `0xf000` (§4.3). After a **full erase** you also need
`0x0 bootloader/bootloader.bin` and `0x8000 partition_table/partition-table.bin`.

Avoid a full erase: it takes NVS with it, so Wi-Fi credentials go too and
re-provisioning needs the on-panel setup screen — awkward if the display is what
you are debugging.

---

## 11. Autostart on the laptop

`~/kiki_servers/server_manager.py`, started by the enabled user unit
`kiki-server-manager.service`, supervises:

| Service | Port | Command |
|---|---|---|
| `gateway` | 8765 | `~/KikiESP32/scripts/run_gateway.sh` (`start_timeout` 180 s — it imports the whole legacy runtime first) |
| `llama` | 8080 | llama.cpp server |
| `whisper` | 5555 | `whisper.cpp` server, Hinglish q5_0 model |
| `tts` | 8082 | OmniVoice |
| `gepard` | 8084 | auxiliary service |
| `tunnel` | — | `scripts/run_tunnel.sh --supervised`; health = `public_uri.txt` exists |

Gateway also opens `127.0.0.1:8770` (audio control) and `8092` (legacy Web UI).

Restarting by hand over SSH needs `setsid` — plain `nohup … &` dies with the
session — and `pkill -f "[k]iki-esp32-gateway"`, because the unbracketed pattern
matches the SSH command line and kills its own shell:

```bash
cd ~/KikiESP32 && pkill -f "[k]iki-esp32-gateway"; sleep 5
setsid nohup ./scripts/run_gateway.sh >> gateway.log 2>&1 < /dev/null &
```

Install once with `scripts/install_laptop.sh` (venv, `pip install -e gateway`,
openWakeWord with `--no-deps`, `mcp<2` — 2.x removed `mcp.server.fastmcp` and
the bundled WhatsApp MCP server dies on import, reporting only "unhandled errors
in a TaskGroup").

---

## 12. Debugging

**Which log.** `~/KikiESP32/gateway.log` is live when the gateway was started by
hand; when `server_manager.py` owns it, output goes to
`~/kiki_servers/logs/gateway.log`. Check which process is running before
concluding a log is dead. Legacy `print()` output is block-buffered while
`logging` is not, so the tail is routinely truncated mid-stream — a missing
print is not evidence the code did not run.

**Device logs without a cable.** `kiki_log.cpp` mirrors `ESP_LOG` to the gateway
as `device_log`, shown as `[device] …`. The catch: it dies with the connection,
so whatever explains a disconnect is exactly what never arrives. For that you
need serial.

**Serial.** Opening the port resets the board, so budget a boot at the start of
every capture. Set `dtr=False; rts=False` before opening or the capture resets
the board repeatedly. A timestamping capture script is the difference between
guessing and knowing.

**Signals worth reading:**

- `device_stats` every 5 s: `buffered_ms`, `underruns`, `underrun_ms`,
  `prebuffer_ms`, `heap_free`, `internal_free`, `dma_largest`, battery, plus
  `imu_posture`, `motion_event`, acceleration/gyro/annoyance/event/error counters.
  And the uplink's own vital signs (§6.5): `send_worst_ms` (how long the slowest
  recent socket write took -- while it is in flight nothing is read and no PING
  goes out, so this is the number that predicts a dropped session),
  `tx_dropped`, `rx_dropped`, `uplink_thrifty`, `mic_queued`.
- `prebuffer_ms` must stay at **100**. Anything higher means something is
  raising the start gate again, which is time-to-first-word (§7.0).
- **Reproduce a bad link on the bench** rather than travelling to find one:
  `tools/slowlink.py` in front of the gateway and `tools/fake_device.py` as the
  board. A row of the matrix is one command and gives comparable numbers in two
  minutes.
- `mic stream … rate=/s audio_rate=Nx` — `audio_rate` is the fraction of
  real-time audio actually arriving. Below 1.00 means loss (or, harmlessly,
  suppression while Kiki is speaking).
- `device … dropped the connection (…: rcvd=… sent=… uptime=…)` — `sent=1011
  keepalive ping timeout` is the gateway reaping a peer that stopped answering;
  `rcvd=None sent=None` is abrupt, meaning a reset or a board reboot.
- `event loop blocked for N s` — the gateway shares its loop with the blocking
  legacy runtime; while it is blocked nothing is read from the device socket,
  the board's send buffer fills, and its client can abort the write.
- `kiki_wifi: disconnected from <ssid>, reason <n>` — reason 2 is AUTH_EXPIRE.

**Crashes.** `esptool read-flash 0xff0000 0x10000 core.bin` retrieves the
coredump. A reboot with *no* panic text is not a normal crash signature — look
for a deliberate `esp_restart()` (OTA install, rollback watchdog, Change Wi-Fi,
shutdown) or a driver-level fault like the mbedTLS teardown race in §4.5.

---

## 13. Known sharp edges

1. **`otadata` and the two app slots** (§4.3). The most common way to waste an
   hour on this project.
2. **Compile date lies; the ELF build ID does not** (§4.1).
3. **A short write kills the whole WebSocket** (§8), and until 2026-08-19 a
   *slow* one killed everything else too: one lock covers sends, reads and the
   keepalive, so nothing but `kiki_net_tx` may ever touch the socket (§6.5).
4. **The `forgotten` flag must gate the Kconfig fallback** (§4.4), or "Change
   Wi-Fi" silently rejoins the built-in network.
5. **48 kHz audio does not fit a tunnelled link** (§7).
6. **`playing` is cleared only by `playback_drained`.** If a reply never drains,
   `handle_audio` discards every frame and Kiki goes deaf until the session
   restarts. `speak_background` has a guard for the media-tool case. A successful
   music start is itself the reply: the legacy adapter discards prose emitted
   after a tool call and skips every result-follow-up once device playback is
   active, so TTS can never be started over the song. A failed music start still
   gets a grounded spoken failure on the normal result-follow-up round.
7. **The patched BSP draw buffer** (§4.6) — a component update would revert it
   and bring back the `ESP_ERR_NO_MEM` flood.
8. **The device token is in the firmware image**; anything that serves the `.bin`
   publicly discloses it.
9. **Motion orientation is gravity-relative, not hand-aware.** The portrait-axis sign follows
   Waveshare's reference board mapping. Confirm upright/upside-down on real hardware after a
   board revision or enclosure rotation; telemetry exposes the classified posture and raw
   magnitudes for threshold tuning.
10. **The dance move table is a wire format** (§5.5). `dance.py`'s `MOVES` and
   `kiki_dance.hpp`'s `DanceMove` are the same list; reordering either makes
   Kiki do the wrong move rather than fail, which is why a test parses the
   header. Append only.
9. **`sdkconfig.defaults` does nothing once `sdkconfig` exists.** IDF reads the
   defaults only when generating `sdkconfig` the first time, so adding a line
   there and rebuilding gives a clean successful build that does not contain the
   option. Check the setting in `firmware/sdkconfig` after building, not in the
   file you edited; a real change relinks essentially the whole tree, so a
   twelve-step build is itself evidence that nothing took. Edit both.
10. **A network can hand out an address and no resolver.** DHCP option 6 is
   optional, and a phone tethering without an upstream may omit it. Nothing
   reports this as a name-lookup problem: `getaddrinfo()` returns 202 in about
   five milliseconds, so all three gateway slots are spent in well under a
   second and the panel just cycles "Reconnecting". NTP dies with it, since
   `pool.ntp.org` also needs resolving, which leaves the clock at 1970 and makes
   every `wss://` certificate read as not-yet-valid — two alarming symptoms from
   one cause. `CONFIG_LWIP_FALLBACK_DNS_SERVER_SUPPORT` covers it; the `dns0..2`
   lines logged at got-IP say what was actually configured.
11. **Kiki can only be woken by something that can be transcribed** (§5.3). A
   dead Whisper server, or one started with the wrong language model, now
   costs the wake word as well as the request — the panel button still works,
   and `KIKI_GATEWAY_WAKEWORD_ENABLED=1` restores the acoustic detector.
12. **The board's start gate is time-to-first-word** (§7.0). Jitter tolerance
   belongs in the gateway's send lead, which is free; anything that makes
   `prebuffer_ms` climb above 100 is buying smoothness with latency.
13. **A DeviceSession is per-connection; Kiki is not** (§5.4b). Anything built
   per socket is rebuilt every time a bad link hiccups, and anything that
   captures a session's bound method keeps calling into a dead socket.
14. **A mood is not a state.** `ui_set_expression()` used to write the mood name
   into the same variable `kiki_is_busy()` reads, so `<oled:curious>` mid-reply
   made the panel's one button stop offering Stop and send `push_to_talk`
   instead -- which stops nothing, and which the gateway discards because it is
   still `playing`. She talked straight through the press. It looked
   intermittent because it depended on whether that reply had reached a mood tag
   yet. `kiki_panel_state.hpp` now keeps the state and the drawn pose apart and
   `firmware/tests/test_panel_state.cpp` fails on the old behaviour.
15. **A service without a port breaks the whole manager** (§11). `stop_all()`
   and `/status` read `svc["port"]`, and `stop_all()` runs first, so one
   portless entry means no server starts at all — while the unit still reads
   `active (running)` and `/status` is the thing that crashes.
