# Kiki ESP32 migration handoff

Last updated: 2026-08-13 (Asia/Kolkata)  
Implementation repository: `/home/kiki/kiki2/KikiESP32`  
Laptop deployment: `/home/vaibhav/KikiESP32` on host `vaibhav`  
Original project (read-only reference): `/home/kiki/kiki2/KikiFast`  
Laptop inference manager (read-only reference): `/home/vaibhav/kiki_servers/server_manager.py`

## 1. Executive summary

Kiki was migrated to the Waveshare/Robu ESP32-S3 1.75-inch round AMOLED board
using **Route 1**: the ESP32 is a low-latency audio/display endpoint and the
laptop remains the inference and Python-runtime host. This is the only practical
route that retains the existing LLM, Whisper, OmniVoice, memory, tools and Kiki
personality at approximately the same quality. The models and Python runtime do
not fit in the board's 16 MB flash and 8 MB PSRAM.

The implementation is deliberately isolated. No existing files in `KikiFast`
were edited, and `/home/vaibhav/kiki_servers/server_manager.py` was not edited.
All new work lives in `KikiESP32`. A snapshot of the required legacy Kiki
runtime is under `gateway/legacy_kiki`; it is ignored by Git because it contains
runtime state, credentials and personal data.

The board was flashed and tested end to end on 2026-08-08. It connected to Wi-Fi,
streamed its microphone at real-time rate, received streaming TTS and music, and
played through the onboard speaker. Kiki's voice was subsequently made louder
with a clean digital gain/peak-limiter chain, and a live calibration script was
added. The last live calibration test delivered first PCM in 723 ms.

The implementation baseline before this handoff is `47c2e7a` (`Add live ESP32
voice calibration controls`). The local unit suite currently passes: **18
tests**.

> **Update 2026-08-13 (second pass), commits `049c069`..`facf30c` — 21 commits.**
> Playback integrity, the Clawd face, mirrored LCD rows, on-panel Wi-Fi
> provisioning, a touch control surface, the boot wizard, an `esp32` boot target
> on the laptop, and a run of bugs found only by using the board. See §4.7–§4.13
> and the session log in §13. The suite is now **59 tests**.
>
> Unlike the first pass, **this work has been on hardware**: flashed repeatedly,
> spoken through, and driven end to end. §13 says what is verified and what is
> still only compile-verified.
>
> **The KikiFast reference for this migration is commit
> `f1b8b957c28bd94d3d453e1cc256a0e689c5b55e`.** Feature parity is judged against
> that tree, minus the finished GDG demo work and the removed camera/face paths.

## 2. Non-negotiable constraints

Any continuing agent should preserve these rules unless the user explicitly
changes them:

1. Do not edit, delete, reset or overwrite anything in
   `/home/kiki/kiki2/KikiFast`.
2. Do not edit `/home/vaibhav/kiki_servers/server_manager.py` or the laptop's
   existing Kiki installation.
3. Make all source changes in `/home/kiki/kiki2/KikiESP32`, then test there.
4. Treat `gateway/legacy_kiki` as private mutable runtime data. Never commit it
   or expose its `.env`, tokens, memory files, conversations or credentials.
5. Do not put the Wi-Fi password, SSH password or gateway token in documentation
   or Git. Firmware secrets belong only in the ignored generated `sdkconfig`.
6. Preserve the persistent WebSocket and llama.cpp KV-cache lifecycle. Starting
   a new connection for each turn causes multi-second latency and is not a valid
   benchmark.
7. Measure speech-end-to-first-audible-PCM, not only server request time. Report
   median and p95; do not claim guaranteed parity over a lossy WLAN.

## 3. Final architecture

```text
User speech
  -> ESP32 ES7210 microphone / 48 kHz mono / 10 ms frames
  -> persistent uncompressed binary WebSocket over 2.4 GHz Wi-Fi
  -> laptop RNNoise
  -> 48-to-16 kHz FIR decimation
  -> openWakeWord + Silero VAD/endpointing
       240 ms apparent silence: speculative Whisper request
       600 ms silence: conservative endpoint/commit
  -> isolated Kiki prompt, memory, modes, tools and llama.cpp KV cache
  -> eager sentence stream
  -> OmniVoice 24 kHz PCM
  -> streaming 2x interpolation to 48 kHz
  -> digital gain + transparent soft peak limiting
  -> same WebSocket
  -> ESP32 playback ring buffer
  -> ES8311 codec and onboard speaker
```

### Responsibility split

| Component | ESP32 | Laptop |
|---|---|---|
| Microphone and speaker I/O | Yes | No |
| AMOLED UI and touch wake/cancel | Yes | No |
| Wi-Fi reconnect and WebSocket client | Yes | WebSocket server |
| Playback/microphone buffering | Yes | Stream pacing |
| RNNoise, wake word and Silero VAD | No | Yes |
| Whisper STT | No | Port 5555 |
| llama.cpp LLM | No | Port 8080 |
| OmniVoice TTS | No | Port 8082 |
| Existing service manager | No | Port 8079 |
| Kiki prompt, memory, modes and tools | No | Isolated snapshot |
| Music lookup/decoding, timers and voice selection | No | Yes; effects sent to ESP32 |
| Camera/face recognition/tracking | Removed | Disabled/stripped |

### Why VAD was not moved to the ESP32

Silero remains local in the latency sense: it runs on the laptop on the same LAN
and sees continuous 10 ms audio frames while the user is still speaking. Moving
to a different tiny ESP32 VAD would save roughly a LAN frame but could change
endpoint decisions and damage both latency and transcription quality. The
gateway starts Whisper speculatively after 240 ms of silence while retaining the
600 ms conservative endpoint. Resumed speech invalidates the speculative path.

## 4. What was implemented

### 4.1 ESP32 firmware

The ESP-IDF firmware is under `firmware/` and uses the board BSP.

- Configures the shared audio bus at 48 kHz.
- Captures the ES7210 microphone in four-slot TDM and selects one channel for
  mono transmission.
- Plays mono PCM as duplicated stereo through the ES8311 codec.
- Uses 480-sample/10 ms microphone frames.
- Uses a 64-frame (640 ms) bounded microphone network queue so Wi-Fi jitter does
  not block I2S capture.
- Uses a 128 KiB playback ring buffer.
- Disables Wi-Fi modem power saving (`WIFI_PS_NONE`).
- Reconnects Wi-Fi and the persistent WebSocket automatically.
- Uses a compact versioned binary PCM protocol with a 24-byte header, stream ID,
  sequence number and timestamp.
- Reports/handles state, transcript, response, first-PCM latency, expression,
  volume, playback end and cancellation events.
- Renders the full Clawd pixel-crab face locally (§4.8) and the mirrored 16x2
  LCD rows in large type.
- Provisions Wi-Fi on the panel and stores credentials in NVS (§4.9).
- Maps the Pi's IR gesture vocabulary onto touch zones, with a settings
  menu (§4.10).
- Keeps speaker volume controllable at runtime from the gateway.
- Has two 5 MB OTA partitions, though the OTA update mechanism itself has not
  yet been implemented.

Important files:

- `firmware/main/app_main.cpp`: task setup and capture/network queue.
- `firmware/main/audio_pipeline.cpp`: codecs, capture, playback and volume.
- `firmware/main/gateway_client.cpp`: WebSocket protocol and event handling.
- `firmware/main/kiki_ui.cpp`: screen layout, touch zones, LCD rows.
- `firmware/main/kiki_face.cpp`: the Clawd face, ported from oled_display.py.
- `firmware/main/kiki_setup.cpp`: on-panel Wi-Fi picker and keyboard.
- `firmware/main/kiki_settings.cpp`: runtime settings menu.
- `firmware/main/wifi_station.cpp`: Wi-Fi scan/connect/NVS credentials.
- `firmware/main/protocol.hpp`: binary protocol definition.
- `firmware/main/Kconfig.projbuild`: non-committed device configuration fields.

### 4.2 Laptop gateway

The Python gateway is under `gateway/kiki_gateway/`.

- Runs a persistent WebSocket server on `0.0.0.0:8765`.
- Validates protocol version and an optional device token.
- Runs streaming RNNoise, decimation, wake word and Silero endpointing.
- Starts partial/speculative ASR while endpointing is still in progress.
- Warms the exact Kiki prompt prefix into llama.cpp before reporting `idle`.
- Preserves the existing Kiki prompt, long-term knowledge summary, prior-session
  summary, modes, tool execution, background workers, idle mind, Web UI and
  WhatsApp startup where their dependencies and credentials are available.
- Streams each eager LLM sentence into OmniVoice instead of waiting for the full
  response.
- Streams TTS PCM back to the board with bounded 200 ms lead pacing.
- Detects microphone sequence gaps rather than silently accepting discontinuity.
- Moves device effects to a `DeviceToolBridge`: ESP32 volume, YouTube music,
  liked songs, playback controls, timers and runtime voice switching.
- Starts background/idle services after a 30-second boot delay so they stay off
  the connection and speech hot paths.
- Disables camera/vision/face/peeping configuration and removes
  `look_at_scene` from tool lists.
- Appends a hardware prompt telling Kiki never to claim it can see.

Important files:

- `gateway/kiki_gateway/session.py`: complete device session and speech path.
- `gateway/kiki_gateway/inference.py`: Whisper, OmniVoice, DSP and legacy Kiki
  adapter.
- `gateway/kiki_gateway/endpointer.py`: speculative/conservative endpoint state.
- `gateway/kiki_gateway/device_tools.py`: device-side tool replacements.
- `gateway/kiki_gateway/audio.py`: RNNoise, decimation and resampling.
- `gateway/kiki_gateway/config.py`: all environment-controlled defaults.
- `gateway/kiki_gateway/server.py`: WebSocket and calibration servers.

### 4.3 Preserved and removed features

Preserved or adapted:

- Kiki personality and local LLM quality.
- Whisper STT and OmniVoice TTS.
- Prompt history, long-term memory and last-session summary.
- Streaming responses and follow-up context.
- Tool calls and one follow-up tool round.
- Idle mind, workers, ambient listening, Web UI and WhatsApp/MCP startup.
- Music search/playback, liked/history state, pause/resume/next/previous/stop and
  repeat playback.
- Timers, runtime voice switching and speaker volume.
- Wake word plus touch wake/cancel.
- AMOLED state, transcript, response, expression and TTFW display.

Removed or intentionally unavailable:

- Camera capture and instant vision.
- Face recognition/enrolment and visual greetings.
- Person/face tracking and camera peeping.
- Camera hand gestures.
- Pi Bluetooth/ALSA speaker path.
- Pi-specific IR controls and direct neck/chassis hardware unless a separate
  controller is deliberately integrated later.
- Equal-quality standalone/offline answers when the laptop is unavailable.

### 4.4 OmniVoice tag handling

The original model sometimes emitted unsupported style tags, which OmniVoice
spoke literally. The gateway now removes unsupported bracketed tags and maps a
small set of common aliases. Only these tags are retained:

```text
[laughter] [sigh] [confirmation-en] [question-en] [question-ah]
[question-oh] [question-ei] [question-yi] [surprise-ah] [surprise-oh]
[surprise-wa] [surprise-yo] [dissatisfaction-hnn]
```

The hardware prompt also tells the LLM to use only this set.

### 4.5 Music stability

Initial continuous music playback could overrun the device because decoded PCM
was sent faster than real time. Media delivery is now paced to a maximum 200 ms
lead, and repeat-queue/autoplay support was added. This was live-tested through
the ESP32 speaker at full codec volume.

### 4.6 Louder Kiki voice and audio calibration

The first boost used a full-range `tanh` soft limiter at 2.0x. It was later
replaced with a more transparent chain:

- Default digital gain: **3.2x**.
- Samples below a -3 dBFS knee remain linear.
- Only peaks above the knee are smoothly compressed.
- Output ceiling: **-0.5 dBFS**.
- No lookahead or additional buffering, so the DSP adds no TTFW latency.
- Speaker/codec default: **100%**.

Measured on representative OmniVoice output:

| Signal | RMS | Peak |
|---|---:|---:|
| Raw OmniVoice | -21.65 dBFS | -3.82 dBFS |
| Previous 2x tanh boost | -15.98 dBFS | -1.00 dBFS |
| Current 3.2x transparent chain | -12.09 dBFS | -0.50 dBFS |

The current chain was about 3.9 dB louder in RMS than the prior boosted voice.
Its measured linear-passband processing error was approximately -100.2 dBFS.

An interactive runtime calibration interface now listens only on laptop
localhost port 8770. It can change gain, change ESP32 codec volume and replay a
fixed/custom Kiki phrase without restarting the gateway or reflashing the board.

Run it on the laptop:

```bash
cd /home/vaibhav/KikiESP32
./scripts/tune_kiki_audio.py
```

Or invoke it remotely from the Kiki machine:

```bash
ssh -t vaibhav@vaibhav \
  'cd /home/vaibhav/KikiESP32 && ./scripts/tune_kiki_audio.py'
```

Commands:

```text
p or Enter   replay the standard phrase
v 80         set codec/speaker volume to 80%
g 3.5        set digital gain to 3.5x
v+ / v-      speaker volume +/- 5%
g+ / g-      digital gain +/- 0.2x
s TEXT       speak custom text
q            quit
```

The allowed gain range is 0.0-6.0x, but 2.0-4.0x is the recommended quality
range. Calibration changes are runtime-only and revert to configuration defaults
when the gateway restarts. To make a selected value persistent, set
`KIKI_GATEWAY_TTS_GAIN` and `KIKI_GATEWAY_SPEAKER_VOLUME` in the untracked
`/home/vaibhav/KikiESP32/gateway.env`, then restart and re-test.

### 4.7 Playback integrity while Kiki speaks

Four separate defects could break audio mid-sentence. All are fixed in
`049c069`; each is worth knowing about because the symptom was identical.

1. **The jitter buffer was 200 ms deep.** The gateway paced sends to a 200 ms
   lead, so the board never held more than that and any Wi-Fi stall longer than
   200 ms emptied the ring and clicked. The lead is now
   `GatewayConfig.playback_lead_seconds`, default **0.6 s**. This costs nothing
   in TTFW: pacing only ever delays *later* frames, and the lead accumulates
   from zero because generation outruns playback.
2. **The microphone re-opened mid-sentence.** `is_playing` was "is the ring
   non-empty", so a network hiccup un-gated the microphone and Kiki heard her
   own speaker — self-wake and false endpoints. Playback is now a *session*
   spanning first queued byte to real drain, which survives an underrun. It also
   holds one 120 ms DMA tail before reporting drained, because
   `esp_codec_dev_write` returns when the DMA accepts samples, not when the
   speaker has produced them.
3. **Every chunk allocated.** `playback_task` runs at priority 19 and built a
   `std::vector` per received item. It now uses a static scratch buffer in fixed
   10 ms chunks, and writes defined silence rather than letting the DMA run dry.
4. **Raising the lead required stream gating first.** A cancel does not un-send
   the audio already in the laptop's socket buffer, so with 600 ms of lead a
   barge-in would be followed by stale PCM over the next reply. The device
   tracks a per-kind stream watermark; `audio_stop` advances it idempotently
   (a second stop must not skip the stream about to start), and `audio_end`
   now carries `kind` so a stale end marker cannot cut the next reply short.

Audio also moved to **core 1 alone**, and LVGL is pinned to core 0 with Wi-Fi.
The BSP leaves the LVGL task unpinned, which was harmless with a static UI and
is not once the face animates continuously.

The board reports `device_stats` every 5 s (buffer depth, underruns, underrun
ms, dropped bytes, forced ends, free heap). The gateway logs it at WARNING only
when a counter moves. **This is the acceptance instrument for §9 Priority 0**:
"the audio broke" is now a number.

### 4.8 The Clawd face and the mirrored LCD rows

The AMOLED showed a rounded rectangle with two bullet points for eyes. It now
runs the real pixel-crab from `core/oled_display.py`: all 35 states, their
per-state frame rates, and the `<oled:>` expression vocabulary with the Python's
guard that a tag may colour how Kiki looks while speaking but can never claim
she is listening, running a tool, or playing music.

The port keeps oled_display.py's **logical 128x64 coordinate system** rather
than re-laying-out for the round panel, so every literal coordinate transcribes
unchanged and the two faces cannot drift; a canvas scales those logical pixels
onto the face box. Frames are written straight into the canvas buffer rather
than through `lv_draw_*`, and the timer re-arms at the current state's rate, so
a calm state costs 6 redraws a second and only lively ones pay for 14.

Rendering is **local**, not streamed, because the face has to keep moving while
the gateway is warming, unreachable or unconfigured — exactly when a streamed
face would freeze.

The 16x2 LCD rows are **mirrored, not re-derived**. `lcd_display.py` keeps
running its worker and calling its commit observer with no panel attached, and
`oled_display.set_state` is a guarded flag flip, so `display_bridge.py` taps the
real ones. That is what carries the events `DeviceSession` cannot see at all — a
worker finishing, a summary being written, the idle mind waking, music
metadata — which would otherwise leave the face on `idle` throughout. The
session stays authoritative for turn states so the two sources cannot fight over
the face mid-sentence.

Both callbacks run on legacy worker threads, never the event loop, so every
forward goes through `run_coroutine_threadsafe`.

Layout is sized against the **inscribed disc**, not the 466x466 bounding box: at
distance d from centre the usable half-width is `sqrt(233² - d²)`.

### 4.9 On-panel Wi-Fi provisioning

Credentials now live in NVS, entered on the panel, with the Kconfig values as
fallback — changing AP no longer means reflashing.

KikiFast dictates the password through local Whisper, which cannot be ported:
the gateway, and therefore Whisper, is unreachable precisely when Wi-Fi is down.
The board uses a **6-column button matrix, not `lv_keyboard`**: the panel is
1.75 inches across, so a ten-key QWERTY row is about 3 mm per key. Six columns
gives ~4.6 mm keys at the cost of one extra page. The typed key stays visible
and correctable, is never logged, and is wiped from RAM once stored.

`wifi_station` gained scan / explicit connect / saved connect. Automatic
reconnect is now armed only after a **successful** connect: it previously fired
on every disconnect, so during provisioning a wrong password started an endless
retry that raced the next attempt and the panel could never report a clean
failure. An empty password selects `WIFI_AUTH_OPEN`, since demanding WPA2 made
every open network look like a bad password.

A provisioned network still gets one quiet attempt before any UI appears.

### 4.10 Touch control surface and settings

`ir_controls.py`'s vocabulary maps onto three touch zones over the whole panel,
rather than buttons stealing space from the face:

| Gesture | Meaning | Pi equivalent |
|---|---|---|
| Tap anywhere | wake, or cancel what is running | double-tap either sensor |
| Hold left/right | push-to-talk while held; release commits | single-sensor hold |
| Hold the centre | settings | both-sensor hold |

Two-finger gestures are avoided deliberately. Release needed a gateway change:
`StreamingEndpointer.force_commit` ends the utterance immediately instead of
costing another `endpoint_silence_ms`, and commits nothing when there was no
real speech.

Settings covers volume, change Wi-Fi and restart. Volume is applied on the codec
immediately *and* routed through the tool bridge, so panel and voice changes
land in the same place. Change Wi-Fi clears NVS and reboots into the picker
rather than tearing the station down under a live gateway socket.

### 4.11 The boot wizard

`startup_config.py` asks its questions on the Pi's own LCD before any service
starts. Here the answers live in `config.json` on the laptop, so the board cannot
ask them alone: the gateway sends the questions and their current values once the
session is up (`config_options`), the board walks them, and the choices come back
as `config_set` / `config_commit` and are saved atomically. Offered once per boot
and auto-declined after twenty seconds, like the Pi, so an unattended power-on
never stalls.

Three questions change meaning on this hardware and are adapted rather than
dropped:

* Bluetooth speaker volume becomes the board's codec volume.
* "Sync LCD+audio" becomes the caption delay. The Pi calibrates word timing by
  measuring speaker-to-microphone latency; the board mutes its microphone while
  speaking, so there is nothing to measure with and the knob is set by ear.
* Wi-Fi is omitted — the board owns the radio and already has its own picker.

Volume and gain apply as they are turned, so the wizard is not a promise. Only
the speaking brain still needs a gateway restart.

### 4.12 Boot target on the laptop (`server_manager.py`)

**This lifted non-negotiable constraint 2, at the user's explicit request.** The
file was backed up first (`server_manager.py.bak-*`).

`server_manager.py` already had a profile mechanism (`omni` / `gepard`) with a
persisted choice, so the new `esp32` profile rides on it: llama, tts and whisper
exactly as the laptop profile runs them, plus the gateway — the piece that used
to live on the Pi. The gateway is listed last because it talks to the other
three and warms llama's prefix as soon as the board connects.

```bash
curl -X POST "http://vaibhav:8079/start?target=esp32"    # models + gateway
curl -X POST "http://vaibhav:8079/start?target=laptop"   # models only
```

`?tts=` still selects a profile by name, unchanged.

It also **auto-starts on launch when the saved profile is `esp32`**. The manager
has always come up idle waiting for a `/start`, which the Pi sent because the Pi
was the thing that booted Kiki. There is no Pi here and the board cannot ask —
it can only connect to a gateway that already exists — so a laptop reboot left
the board showing "Reconnecting" indefinitely. The laptop profile keeps its
on-demand behaviour.

Adding the gateway as a managed service needed one fix: the manager checks
liveness by opening a socket and closing it, and `websockets` logged a full
traceback per poll. `server.py` filters exactly that shape, walking the
`__cause__` chain for the `EOFError` — the record's own exception is
`InvalidMessage`, and matching on that alone would hide real handshake failures.

### 4.13 Display text: Hinglish, captions, and who is speaking

The panel's font has no Devanagari glyphs, so Hindi renders as empty boxes. The
Pi already solved this for its HD44780 with `romanize_hindi_for_lcd`, which is
deliberately dependency-free (no model, no network, no transliteration package),
so `display_bridge.panel_text()` reuses it rather than duplicating it.

The same function turns voice tags into captions (`[laughter]` -> "Ha ha ha!")
and drops the ones the model invents. Those captions are held locally rather than
imported: raw `[sigh]` on screen is a visible defect that should not depend on an
optional import succeeding. Before this, the panel was literally showing
`[sigh] [deadpan] Well, the world is definitely not having a chill day`.

It is applied to Kiki's captions, your transcripts (partial and final) and
ambient capture — **display only**. A test asserts the TTS worker never calls it;
applying it to the synthesis path would stop Kiki speaking Hindi and stop her
sighing.

Colour distinguishes the speaker: Kiki in the primary, you in the accent, ambient
in the dim secondary. Ambient also expires after ten seconds, because capture
never stops and the state stays `idle` throughout, so no status row would ever
arrive to take the band back and the panel would sit on a random overheard
fragment forever.

## 5. Latency work and evidence

Latency was treated as the primary design constraint.

Implemented controls:

- Persistent WebSocket; no per-turn handshake.
- WebSocket compression disabled.
- 10 ms binary PCM frames.
- Wi-Fi power saving disabled.
- Continuous laptop-side VAD while the user speaks.
- Speculative Whisper at 240 ms silence.
- Conservative endpoint at 600 ms.
- Partial ASR/prefix prefill while speech is in progress.
- Exact llama.cpp prompt/KV rewarm before `idle`.
- Sentence-level eager TTS.
- First-PCM streaming rather than whole-file TTS.
- 48 kHz capture/playback throughout the board path.
- Bounded stream pacing and hardware queues.

Historical measurements are in `docs/LATENCY.md`. Key results:

- End-to-end gateway test with deterministic LLM: median detected endpoint to
  first PCM 418 ms; median input-file end to first PCM 807 ms.
- Persistent local-model three-turn test: median detected endpoint to first PCM
  857 ms; median input-file end to first PCM 1,271 ms.
- Reconnecting each turn measured 4.6-6.6 seconds and is specifically invalid.
- Live boosted-voice test on 2026-08-08: first PCM 943 ms.
- Live calibration-script test on 2026-08-08: first PCM 723 ms.
- Stable connected-board microphone telemetry was 100 frames/s and 1.00x audio
  rate when not playing output.

The earlier LAN test showed a long Wi-Fi tail (ICMP loss/jitter despite healthy
steady HTTP RTT). The architecture protects audio continuity, but exact p95
parity cannot be guaranteed without controlling RF conditions.

## 6. Repository history

The commits tell the implementation sequence:

| Commit | Purpose |
|---|---|
| `383b513` | Initial isolated ESP32 firmware and laptop gateway |
| `16ef0d4` | Warm LLM cache, persistent-session benchmark and turn gating |
| `f6ccc97` | Stabilize the ESP32 hardware audio path |
| `e89ba51` | Pace media playback and add repeat behavior |
| `2b6761f` | First digitally boosted Kiki speech path |
| `e7dd7cd` | Replace it with cleaner 3.2x gain and peak limiting |
| `47c2e7a` | Add live speaker-volume/digital-gain calibration controls |

The second pass (`049c069`..`facf30c`, 21 commits) is listed in §13.1 rather
than here, because the interesting part of it is not the sequence but which
failures were only visible on hardware — see §13.2.

## 7. How to run the system

### 7.1 First-time laptop setup

On the laptop:

```bash
cd /home/vaibhav/KikiESP32
./scripts/install_laptop.sh
```

This creates `.venv`, installs the gateway, the ONNX wake-word dependencies and
the dependencies required by the isolated legacy runtime.

### 7.2 Start the existing inference services

Use the existing laptop manager; do not modify it. The expected listeners are:

```text
8079  server manager/health API
8080  llama.cpp OpenAI-compatible chat endpoint
8082  OmniVoice speech endpoint
5555  Whisper inference endpoint
```

Confirm that llama.cpp, Whisper and OmniVoice are healthy before evaluating
gateway latency. Memory pressure from a VM previously prevented llama.cpp from
loading; stopping that VM restored the expected local-model path.

### 7.3 Start the gateway

```bash
cd /home/vaibhav/KikiESP32
nohup ./scripts/run_gateway.sh >>gateway.log 2>&1 </dev/null &
echo $! > gateway.pid
```

Expected listeners:

```text
0.0.0.0:8765    ESP32 WebSocket gateway
127.0.0.1:8770  local-only audio calibration control
```

Watch the log:

```bash
tail -f /home/vaibhav/KikiESP32/gateway.log
```

Healthy startup should show the gateway listener, calibration listener, a board
connection, and microphone telemetry near `100 frames/s` / `1.00x` while the
speaker is idle.

### 7.4 Build and flash firmware

On a machine with the board connected as `/dev/ttyACM0`:

```bash
cd /home/kiki/kiki2/KikiESP32/firmware
source ../.tools/esp-idf/export.sh
idf.py menuconfig
```

Under `Kiki ESP32`, set the 2.4 GHz SSID/password, laptop gateway URI, gateway
token and default speaker volume. Then:

```bash
cd /home/kiki/kiki2/KikiESP32
./scripts/build_firmware.sh
./scripts/flash_firmware.sh /dev/ttyACM0
```

Never commit the generated `sdkconfig`; it contains network credentials.

### 7.5 Run tests

```bash
cd /home/kiki/kiki2/KikiESP32
.venv/bin/pytest -q gateway/tests
```

Current expected result: `18 passed`.

Run the software device benchmark with a known speech WAV:

```bash
.venv/bin/python gateway/benchmarks/live_pipeline.py \
  --uri ws://vaibhav:8765 \
  --wav /path/to/test-speech.wav \
  --iterations 5
```

Do not use personal speech assets from `KikiFast` in commits.

## 8. Deployment status and caveats

Last fully verified live state (2026-08-08):

- Laptop inference manager, llama.cpp, OmniVoice and Whisper were healthy.
- Gateway listened on 8765 and calibration control on 8770.
- ESP32 connected over Wi-Fi.
- Board microphone streamed at 1.00x real time while idle.
- Kiki TTS and continuous/repeated music played through the speaker.
- Codec volume was 100% and TTS digital gain was 3.2x.
- The interactive calibration script changed volume/gain live and replayed Kiki.

On 2026-08-12, a read-only deployment recheck was attempted, but Tailscale SSH
required interactive re-authentication. Therefore **current remote process and
board status is not verified as of this handoff**. Source tests were rerun locally
on 2026-08-12 and all 18 passed.

The remote `/home/vaibhav/KikiESP32` deployment was copied with `scp` and was not
a Git repository at the time of deployment. The authoritative tracked source is
the local `/home/kiki/kiki2/KikiESP32` Git repository. Do not edit remote files
first and then forget to port them back; make and commit source changes locally,
test, then deploy explicit files or use a deliberate packaging/sync process.

## 9. Remaining work

### Priority 0: finish exercising the board

§4.7–§4.13 have all been flashed and run. What has actually been *observed*
working is listed in §13; the rest of the matrix has not been touched and is the
first thing to do:

1. **Barge-in.** Cancel mid-reply repeatedly and confirm no stale audio plays
   over the next answer. This is what the per-kind stream watermark exists for
   and it is the change most likely to still hold an off-by-one; nothing has
   deliberately exercised it.
2. **Music.** A full song, playlist next/previous, repeat, and cancelling
   mid-track. The media pump shares the raised send lead and the same watermark.
3. **Audio under load.** Watch `device_stats` across a long reply and a whole
   song: `underruns` and `dropped_bytes` must stay at 0. If underruns appear,
   raise `KIKI_GATEWAY_PLAYBACK_LEAD_SECONDS` before touching anything else.
4. **Timers**, and the remaining non-camera tools.
5. **Wi-Fi provisioning from an erased NVS.** "Change Wi-Fi" now reaches the
   picker, but a first-boot provision with no stored credentials at all has not
   been run since the keyboard was written. Check the ~4.6 mm keys are hittable
   and that a wrong password reports cleanly instead of looping.
6. **The face at rest.** All 35 states exist but only a handful have been seen.
   Trigger the background ones (music, summarizing, idle mind, workers) and
   confirm they arrive from the legacy bridge and are not clipped by the bezel.

Then re-establish the pre-existing live baseline:

7. Complete Tailscale SSH authentication and perform a read-only health check.
8. Confirm ports 8079, 8080, 8082, 5555, 8765 and 8770 are listening.
9. Confirm `gateway.pid` refers to the live gateway process and inspect
   `gateway.log` for exceptions, audio gaps and reconnect loops.
10. Confirm the board reconnects and reports approximately 100 mic frames/s and
    1.00x audio rate while idle.
11. Replay the calibration phrase at 100% / 3.2x and confirm subjective quality.
12. Run at least five real spoken turns and record endpoint-to-first-PCM median,
    p95, Whisper time, LLM first token and any dropped sequence numbers.

Acceptance: all local services healthy, no clean-LAN audio gaps, no reconnect
loop, five successful real turns, and no literal unsupported tags.

### Priority 1: production supervision and recovery

The gateway is currently launched with `nohup` and a PID file. Create a user
`systemd` unit (or equivalent supervisor) that:

- waits for network readiness and the inference services;
- starts from `/home/vaibhav/KikiESP32` with the untracked `gateway.env`;
- restarts on failure with bounded backoff;
- writes structured logs with rotation;
- stops media/child processes cleanly;
- does not modify or become coupled to `server_manager.py`.

Add a localhost health endpoint or control action that reports gateway uptime,
connected device count, inference health, current gain/volume, last mic frame,
audio gaps and last TTFW. This will make field diagnosis much easier.

### Priority 1: formal Pi-versus-ESP32 latency acceptance

The software and live tests are promising, but a controlled A/B comparison
against the prior Pi path is still required to substantiate “same latency.” Use
the same WAVs, model processes, prompt cache state, AP/channel and laptop load.
Measure at least 30 turns per path and report median, p90 and p95 for:

- physical/detected speech end to final STT;
- speech end to first LLM token;
- speech end to first TTS PCM;
- speech end to first audible speaker output;
- false endpoint/restart rate;
- packet/sequence gaps.

Acceptance should be agreed numerically. A suggested initial gate is median
within 100 ms of the Pi and no material p95/endpoint-quality regression, but the
user should approve the threshold.

### Priority 1: audio-quality calibration

The current DSP is transparent below its knee, but the small speaker and
enclosure define the real acoustic limit. Remaining work:

- Use the calibration script to choose a final subjective setting.
- Record the board speaker with a fixed microphone position at several gain and
  codec-volume combinations.
- Measure acoustic loudness, harmonic distortion, rattling and intelligibility.
- Compare 3.0x/100%, 3.2x/100%, 3.5x/95% and 4.0x/90%.
- Persist the chosen values in the untracked `gateway.env`.
- Consider a speech-specific high-pass/EQ only after measurement; do not add an
  arbitrary bass boost that wastes the tiny speaker's headroom.
- If more loudness is needed without peak coloration, evaluate a streaming
  compressor with persistent envelope state and a very short lookahead. Account
  for and measure any added first-word latency.

### Priority 1: echo cancellation and barge-in decision

The firmware currently does not transmit microphone audio while playback is
active. This prevents Kiki from hearing her own speaker but means voice barge-in
is unavailable; touch cancellation still works. Decide whether barge-in is a
required retained feature. If yes, implement and test acoustic echo cancellation
or a robust duplex strategy before enabling microphone streaming during output.
Do not simply turn the mic back on during playback, because that can create
self-wake, false endpoints and feedback.

### Priority 2: complete feature regression audit

The legacy runtime was adapted broadly, but not every historical tool has been
hardware-tested on the ESP32 route. Build a feature matrix and test:

- normal and follow-up conversation;
- memory persistence and summary updates;
- mode and voice switching;
- all non-camera tool calls;
- web/search tools and credential-dependent providers;
- WhatsApp/MCP startup and messaging;
- idle-mind and scheduled workers;
- ambient listening behavior;
- timers and alarm sound;
- liked songs, history, playlist next/previous, repeat and long-duration music;
- cancellation during TTS, tools and music;
- laptop/board/AP reboot and reconnect behavior.

Some legacy tools may still assume Pi GPIO, Bluetooth, a face controller or
other unavailable hardware. Each should be routed to an ESP32/control endpoint,
explicitly disabled, or made to fail clearly. Never let Kiki falsely claim that
an unavailable physical action succeeded.

### Priority 2: memory and state ownership

The isolated `gateway/legacy_kiki` snapshot can diverge from the original
KikiFast runtime because it writes its own memories, summaries, liked-song state
and worker data. Define one intentional policy:

- ESP32 Kiki becomes the authoritative runtime; or
- selected state files are synchronized with explicit conflict handling; or
- the two installations remain deliberately separate.

Before any sync, enumerate private/mutable files and back them up. Never replace
the full legacy snapshot blindly, and never copy credentials into Git.

### Priority 2: security hardening

- Ensure `KIKI_GATEWAY_TOKEN` is non-empty and matches the firmware token.
- Keep calibration control bound to `127.0.0.1`; do not expose 8770 to the LAN.
- Consider WSS or a trusted VPN/VLAN if PCM and transcripts cross an untrusted
  network.
- Rotate any credentials that may have appeared in terminal history.
- Confirm logs do not retain secrets or unnecessary transcript content.
- Add request/rate limits and explicit device identity if multiple boards are
  introduced.

### Priority 2: firmware robustness and OTA

- Implement the OTA update flow for the existing dual application partitions.
- Add firmware version reporting and compatibility checks in the hello message.
- Add watchdog/recovery tests for Wi-Fi loss, gateway restart and corrupt frames.
- Persist useful crash/coredump diagnostics without leaking credentials.
- Run multi-hour microphone and music soak tests while watching heap, queue
  drops, WebSocket reconnects and codec errors.
- Validate power supply, thermal behavior and speaker safety at sustained 100%.

### Priority 3: UI and product polish

- Improve the round AMOLED layout and expression animation.
- Add visible offline/reconnecting/inference-down states.
- Show speaker volume and possibly a safe gain indicator.
- Add a settings/calibration screen accessible by touch.
- Add Wi-Fi provisioning so changing AP does not require reflashing.
- Add battery/power status if the final hardware exposes it.
- Decide whether transcripts should be hidden/redacted for privacy.

### Priority 3: tests and documentation cleanup

- Add integration tests for the localhost audio-control protocol.
- Add a mock OmniVoice streaming test across irregular chunk boundaries.
- Add end-to-end cancellation, reconnect, sequence-gap and playback-drain tests.
- Add firmware-side host tests where practical.
- Update `docs/DEPLOY.md`, which still contains an earlier statement that board
  acceptance was pending; the board was later flashed and live-tested.
- Keep `docs/LATENCY.md` append-only for measured runs, including date, firmware
  commit, RF conditions, model state and sample count.
- Package deployment so the remote copy can be reproduced from a commit without
  copying ignored private data accidentally.

## 10. Recommended continuation sequence for another agent

1. Read this file, `README.md`, `docs/ARCHITECTURE.md`, `docs/DEPLOY.md` and
   `docs/LATENCY.md`.
2. Run `git status --short` in `/home/kiki/kiki2/KikiESP32`. Preserve any user
   changes; do not reset them.
3. Run `.venv/bin/pytest -q gateway/tests` before changing anything.
4. Re-authenticate Tailscale SSH if requested, then perform only the read-only
   Priority 0 checks.
5. Verify the manager and model services without editing their code.
6. Confirm the exact board device/IP and current firmware version from logs;
   do not assume the historical address is still assigned.
7. Establish a fresh five-turn latency/audio baseline.
8. Choose one remaining-work item with the user. Do not combine security,
   persistence, OTA and DSP redesign into one unreviewable change.
9. Make changes only in the local `KikiESP32` repository using small commits.
10. Run unit tests, emulator tests and a targeted live hardware test.
11. Deploy explicit tracked files to `/home/vaibhav/KikiESP32`; do not overwrite
    the ignored legacy state or secrets.
12. Restart only the gateway unless firmware or inference services genuinely
    need restarting. Restore normal listening mode after any startup-speech test.
13. Record measured results, commit hash, deployed files and rollback steps in
    this handoff or the relevant docs.

## 11. Troubleshooting shortcuts

### ESP32 does not connect

- Confirm it is on 2.4 GHz Wi-Fi and the laptop address in firmware is current.
- Confirm port 8765 listens on the laptop LAN interface.
- Confirm firmware and gateway tokens match.
- Inspect `gateway.log` and the ESP32 serial monitor.
- Check Tailscale/LAN routing separately; the board normally uses the local LAN.

### Gateway says warming for a long time

- Check llama.cpp on 8080 and available laptop RAM/VRAM.
- Ensure no VM has reclaimed the memory needed by the model.
- Do not bypass `ensure_ready` to make the UI look ready; that would move cache
  warmup into the first user turn and regress TTFW.

### Kiki hears nothing

- In idle logs, expect about 100 mic frames/s and 1.00x audio rate.
- Check for `audio_gap`, queue-full or codec-read warnings.
- Microphone transmission intentionally pauses during speaker playback.

### Kiki speaks literal tags

- Confirm the deployed `inference.py` contains `sanitize_for_omnivoice` and the
  supported-tag set.
- Inspect the LLM sentence and sanitized TTS request separately.
- Add aliases only when they map unambiguously to a supported OmniVoice tag.

### Speech is quiet or distorted

- Run `scripts/tune_kiki_audio.py` on the laptop.
- Start from 100% speaker / 3.2x digital gain.
- Reduce digital gain first if peaks sound crushed; reduce codec volume if the
  physical speaker/enclosure rattles.
- Do not remove the -0.5 dBFS ceiling just to make the numeric gain larger.

### Music stutters or overruns

- Confirm the deployed media pump contains real-time pacing with a 200 ms lead.
- Check Wi-Fi gaps and the playback ring buffer before increasing its size.
- Update `yt-dlp` if YouTube URL resolution fails, but test liked/history behavior
  after an update.

## 12. Final status

The Route 1 migration is functionally operational and demonstrated end to end:
the ESP32 handles the physical conversation interface, while the laptop retains
the models and Kiki runtime needed for quality. The latency-critical streaming
architecture, speculative STT, warm KV cache, streamed TTS, music playback,
supported OmniVoice tags, high-volume speech DSP and live calibration controls
are implemented.

The next phase is not another architectural rewrite. It is controlled
productionization: re-verify the current deployment, complete formal Pi/ESP32
A/B latency testing, select and persist the final acoustic settings, add service
supervision, audit every retained tool, decide barge-in behavior, and harden
security/recovery/OTA.

## 13. Session log — 2026-08-12/13

Reference tree for parity: **KikiFast commit
`f1b8b957c28bd94d3d453e1cc256a0e689c5b55e`**, minus the finished GDG demo work
and the removed camera/face paths. 21 commits, `049c069`..`facf30c`. Suite:
**59 tests** locally, 56 on the laptop (the gap is `test_audio_control.py`,
which was never in that deployment).

### 13.1 What was built

| Area | Commit | Section |
|---|---|---|
| Playback integrity while speaking | `049c069` | §4.7 |
| Clawd face + mirrored LCD rows | `12593f8` | §4.8 |
| On-panel Wi-Fi provisioning | `6bc235a` | §4.9 |
| Touch surface + settings menu | `ebfbaa3` | §4.10 |
| Boot wizard | `0e765e1` | §4.11 |
| `esp32` boot target, auto-start | `0e765e1`, `b20d7cd` | §4.12 |
| Hinglish panel text + captions | `fbeb198`, `f7721ce` | §4.13 |
| Colour theme, stop button, caption lock | `7d9dd7a` | — |
| Caption timing (`display_sync_offset_ms`) | `636abb7` | — |

### 13.2 Bugs the hardware found that nothing else would have

These are the ones worth knowing about, because each was invisible to the tests
and to code review:

1. **Dead screen (`be206d1`).** The board has ~166 KiB of internal heap (§4.5a;
   "~297 KiB" here and elsewhere was wrong). The BSP
   gives LVGL two 466x50 RGB565 draw buffers *in PSRAM*, so every flush needs a
   46,600-byte internal DMA bounce buffer — against a
   `SPIRAM_MALLOC_RESERVE_INTERNAL` of 32 KiB. It could never have worked; the
   old static UI simply dirtied regions small enough that nobody noticed. Fixed
   by moving the 128 KiB playback ring to PSRAM (it never needed to be
   DMA-capable) and raising the reserve to 128 KiB. Boot now prints both pools.
2. **The display bridge was silent (`e1fa8fa`).** The tap on `lcd_display` was
   correct and nothing ever called `update_status` — on the Pi that is main.py's
   job, and main.py is not running here. The pipe was built and never connected.
3. **Kiki had no clock (`d5781f5`).** `main.py` calls
   `idle_mgr.maybe_inject_time()` every turn; the gateway never did. Observed in
   the log: asked what was happening today, she ran
   `search_web("current time and world news today")` — she was looking up the
   clock because she genuinely did not have one.
4. **Modes and Hindi did nothing (`9f965fd`).**
   `runtime_controls.get_active_system_prompt()` is what selects a character
   mode's prompt and appends the Hindi instruction; `llm.system_prompt` is only
   the *default* that feeds into it. The gateway read the raw key, bypassing
   both layers.
5. **The wizard wrote settings nobody read (`facf30c`).** `config_loader` caches
   `config.json` at import, so writing the file changes nothing in a running
   process. Switching *to* Hindi only appeared to work because the gateway
   happened to be restarted afterwards. **The reload is not free**: it
   deep-merges the file back over the live dict, and two reloads without
   protection put `set_person_real_name` back in the catalog. The hardware
   overrides are now an idempotent function registered as a reload listener.
6. **WhatsApp could never have worked (`23acc8a`).** llama.cpp and the WhatsApp
   bridge both want `:8080`, and Route 1 puts them on the same host for the
   first time. It fails silently rather than cleanly: the "is the bridge
   running?" check probes whether 8080 is open, llama-server answers, so Kiki
   concludes it is up and never starts it — and the MCP server then sends its
   REST calls to llama-server. Bridge moved to 8099.
7. **`set_person_real_name` (`aab510c`).** Still in the catalog, and its handler
   catches the Hailo ZMQ failure, renames the person in the knowledge base
   anyway, and reports success — so Kiki would claim to have learned a face she
   has no camera to see. Stripped alongside `look_at_scene`.

### 13.3 Verified on hardware

- Flash, boot, Wi-Fi connect, gateway connect, display bridge attach.
- Face renders and animates; canvas 396x198 at 3.09 px per logical pixel.
- Microphone 100.0–100.2 frames/s at 1.00x.
- TTS playback with **0 underruns, 0 drops**; TTFW **731 ms** against the 723 ms
  measured before the send lead was raised to 600 ms — i.e. the extra jitter
  tolerance is free at the front of a reply (`docs/LATENCY.md`).
- Largest free DMA block after init: 126,976 B (needed 46,600).
- Abrupt disconnect and automatic reconnect, logged as an event not a traceback.
- Hindi speech end to end, including the clock: *"[casual] पर इतनी रात को? एक
  बजकर तेइस मिनट हो रहे हैं..."*
- `search_web` full round trip (Exa 1.26 s / 2.10 s, follow-up spoken).
- `?target=esp32` brings all four services healthy; manager auto-start verified
  by restarting it.

### 13.4 Not verified — start here

- **Barge-in / cancel mid-reply.** The stream watermark has never been
  deliberately exercised.
- **Music**: long playback, playlist controls, repeat, cancel mid-track.
- **Timers** and the remaining non-camera tools.
- **Wi-Fi provisioning from a fully erased NVS** since the keyboard was written.
- **Most of the 35 face states**, particularly the background ones.
- **The 5 s silent gap on tool turns.** `search_web` works, but there is no
  filler sound during the gap — the Pi covers it with `ThinkingSoundPlayer` and
  this route has nothing. It reads as "she never answered". Porting the thinking
  sound is the obvious fix and is not done.

### 13.5 Environment state on the laptop

- Go (amd64) installed at `~/go-toolchain`; the bundled tarball was linux-arm64,
  from the Pi. The WhatsApp bridge binary is built.
- ~~**WhatsApp still needs a QR scan.**~~ Superseded — see §14.2. The QR was
  scanned and the bridge has been logged in and syncing since; what was
  actually broken was the MCP server's import. Original note: The session returned
  `401: logged out from another device`. `whatsapp.db` was backed up first.
  Run the bridge in an interactive terminal to see the QR — the gateway launches
  it with stdin closed, so it can never show you one:
  `cd ~/KikiESP32/gateway/legacy_kiki/whatsapp-mcp/whatsapp-bridge && ./whatsapp-bridge`
- Flask installed; Web UI at `http://vaibhav:8090`.
- **npm/Smithery is not installed**, so `read_gmail` fails.
- `scripts/fix_whatsapp_port.sh` re-applies the 8099 move after a re-deploy,
  because `legacy_kiki` is untracked.
- `scripts/flash_prebuilt.sh` + `prebuilt/` flash the board without ESP-IDF.
  It does **not** erase NVS, so provisioned Wi-Fi survives a flash.

### 13.6 Open decisions

- **Memory and state ownership** (§9 Priority 2) is now urgent rather than
  theoretical: the laptop snapshot has its own knowledge base, conversations and
  liked songs, diverging from the Pi since 2026-08-07, and scanning the WhatsApp
  QR links the laptop's bridge as a separate device.
- **Barge-in during playback** still requires echo cancellation; the firmware
  does not transmit microphone audio while the speaker is active.
- The gateway suite does not import `server.py`, which is how a syntax error in
  it was deployed during this session before being caught. Worth closing.

## 14. Session — 2026-08-13, "play nice music and she hung"

Two reports, two unrelated causes. Commits `a7fbc27`, `1d7c87c`, `ab9e9ef`.
Suite: **68 tests** locally, 65 on the laptop (still the `test_audio_control.py`
gap). Both fixes are gateway-side; **no reflash was needed**.

### 14.1 The hang was the microphone gate, not a deadlock

Nothing was stuck. `play_music` ran perfectly: "nice music" resolved to a
5466-second YouTube mix and ffmpeg streamed it happily for the ten minutes
until it was killed by hand. The firmware does not transmit microphone audio
while its speaker is running (§9 Priority 1), and `activate_query` only
announced `listening` — so hold-to-talk put the panel in a listening state that
no audio could ever reach, no endpoint could fire, and the turn stayed open.
Ears animating, because the face renders locally. It would have cleared itself
in 91 minutes.

Waking now **ducks**: the pump parks, the board is told to drop what it holds,
and the mic is back within a buffer. The turn ends and the song resumes where
it stopped — ffmpeg is never killed, so asking the time does not cost the
track. `duck()`/`unduck()` live on `DeviceToolBridge`; `activate_query`,
`respond`'s `finally`, and the woken-but-silent expiry drive them.

Three things are load-bearing, all with tests in `test_media_ducking.py`:

1. **Resuming needs a new stream id.** `audio_stop` runs the board's
   `abandon_in_flight_audio`, which advances the media watermark past the
   current stream, so re-sending under the old id is silence for the rest of
   the song.
2. **The restart is a latched flag, not "the pump noticed the gate shut".**
   Between sends the pump sleeps a whole pacing interval, so a short duck can
   open and close without it ever observing the closed gate — and it then
   carries on under the blacklisted id. Written the obvious way first; the test
   caught it before the hardware could.
3. **The pacing clock is rebased on resume**, or the pump believes it owes the
   listener the entire length of the conversation and sends it at socket speed,
   overrunning the 128 KiB ring.

Two related state bugs fixed alongside, both of which are why the panel gave no
clue: `playback_drained` reported `idle` when a reply finished on top of a
song (mislabelling the panel *and* un-gating the wake word while the speaker
ran), and the display bridge let any background state — the idle mind waking —
overwrite `music`. The board picks its one button's meaning from the state
name, so that quietly turned "Stop" back into "Hold to talk".

### 14.2 WhatsApp: `install_laptop.sh` asked for `mcp` unpinned

`whatsapp-mcp-server/main.py` opens with `from mcp.server.fastmcp import
FastMCP`. **mcp 2.0 removed that module**, the laptop resolved 2.0.0, and the
daemon died on import every time it was spawned — since before the 2026-08-12
session, which is why it looked like the unscanned QR (that had already been
fixed; the bridge was logged in and syncing throughout).

Nothing pointed at the cause. The Go bridge was healthy on 8099, and the client
reported only `MCP client stopped: unhandled errors in a TaskGroup (1
sub-exception)`, which swallows the sub-exception; the action agent turned that
into Kiki saying WhatsApp "is having some startup error". Running `main.py` by
hand is what surfaced the `ModuleNotFoundError`. Pinned `mcp<2` (laptop now on
1.29.0; the Pi runs 1.25.0).

### 14.3 Verified live after the fixes

- Gateway, MCP daemon and bridge all running; 8765/8770/8080/8082/5555/8099 up.
- Board reconnected at **100.0 mic frames/s, 1.00x**; a spoken turn answered.
- WhatsApp MCP **ready (12 tools)**, `list_chats` returning live chats, and a
  `CallToolRequest` served from the running gateway.
- Zero tracebacks since restart.

### 14.4 Two things to know before the next restart

- **The gateway must be launched with `setsid`.** `nohup ... &` over SSH died
  ~50 s later. `pkill -f kiki-esp32-gateway` also matches the SSH command line
  that contains the string and kills its own shell — use `pkill -f
  "[k]iki-esp32-gateway"`.
- It is therefore running **detached, not as a `server_manager` child** (the
  manager holds a zombie for it; its TCP health check still passes). A
  `/restart?target=esp32` puts that right, at the cost of an llama reload. This
  is the same gap §9 Priority 1 already asks to close with a systemd unit.
- The legacy Web UI cannot bind **8090 — VS Code holds it** on the laptop.


### 14.5 Second round, same afternoon — a different hang

"play farq hai by suzonn" hung Kiki again within minutes of §14.1 going live.
Not a regression of that fix, and not the same failure: this time the mic was
streaming into the gateway at a healthy 100 frames/s and the gateway was
throwing every frame away.

`playing` gates **both** the VAD and the wake word in `handle_audio`, and the
only thing that clears it is the board reporting `playback_drained` — which the
board only sends for a playback session it actually opened. A turn that sends
no PCM at all therefore left it stuck True forever. **Deaf until the process
restarted**, and nothing in the log said so; `mic stream` kept printing at
100.0/s throughout, which is exactly what a healthy session looks like.

A *failed* `play_music` is precisely that shape, and it is worse than it
sounds: the media tools deliberately skip the spoken follow-up because the song
is the reply, so when the lookup fails there is no reply **and** no song. The
request that failed also cost you the microphone. Fixed on both sides —
`respond()` clears `playing` itself when the turn produced no audio, and the
follow-up is now skipped only when the media *actually started*, asked of the
device bridge rather than inferred from the tool's own prose. A failed music
request gets spoken now instead of answered with silence.

Worth internalising for anything else in this area: **`playing` is a lock, and
the only key is held by the board.** Any path that sets it must be able to
answer "what clears this if no audio is ever sent?"

### 14.6 Web UI, and Stop's second job

The Web UI has never worked on this laptop. **VS Code's forwarding holds 8090
and 8091**, so Flask died on "Address already in use" every boot — while
looking healthy, because `start_webui` prints the dashboard URL *before* it
binds and Flask binds on a thread the surrounding `try/except` cannot see. The
port is now `KIKI_GATEWAY_WEBUI_PORT` (default 8090), the gateway probes it
first and names the failure, and the laptop's untracked `gateway.env` sets
**8092**. Live at **http://vaibhav:8092** (verified 200 from the Pi).

Stop now has two jobs, as requested: **tap = cancel and keep listening; hold =
cancel and go back to idle** (`sleep`). The hold is a superset of the tap
rather than a separate action decided at release — the cancel goes out on first
touch so barge-in stays instant, and the long-press adds `sleep` on top.

**The firmware half of Stop needs a flash.** `firmware/` builds clean
(`kiki_esp32.bin`, 69% of the app partition free) but the board is on Wi-Fi,
not USB, so it could not be written. Plug it into the Pi and run
`./scripts/flash_firmware.sh /dev/ttyACM0`. Until then the gateway's `sleep`
handler is inert and Stop behaves as before.

### 14.7 Music that finds the song, and a transport row (flashed)

`_play_query` ran exactly one `ytsearch1` and gave up, which is the whole
reason "play farq hai by suzonn" produced silence: speech recognition mangles
names it has never heard and YouTube returns *nothing* for the mangled form.
The ladder is now: the raw query, the query with spoken filler stripped, then
a cloud model asked what the user actually meant ("farq hai by suzonn" ->
"Suzonn - Farq") plus two alternates. All under a 24 s deadline so it always
returns inside the tool call's 30 s bound.

Searches are **flat** — titles and page URLs only. Extraction is per-track and
lazy in `_play_entries`, which is what makes both several retry attempts and a
real queue affordable in one call. Individual results are skipped when they
will not resolve; a search hit is not a promise, and age-gated or
region-blocked videos look fine until yt-dlp tries them.

Those results become the **queue**, so next/previous finally have somewhere to
go — a spoken "play X" used to produce a queue of one, and "next song" always
answered "that playlist position does not exist".

**Transport row** (previous / play-pause / next) sits over the caption area,
visible only while a song is loaded, routed through the same `control_music`
tool the voice commands use so the two cannot drift. Sized against the
inscribed disc: three 96 px buttons and two 24 px gaps is 336 px, inside the
~395 px available at y=356.

**Pause now shares the ducking gate.** `SIGSTOP` on ffmpeg was the existing
mechanism and it is wrong for exactly the reason ducking could not simply stop
sending: the pacing clock keeps running, so resuming dumps the whole pause into
the 128 KiB ring at socket speed — and the board has blacklisted the stream id
anyway. Pause and duck are two independent reasons on one gate, because ending
a conversation must not un-pause a song the user paused.

**Flashed 2026-08-13 over SSH** (board is on the *laptop's* `/dev/ttyACM0`, not
the Pi's): `scp` the four images to the laptop and run its own venv esptool.
Hash verified, board reconnected at 100.0 frames/s. NVS untouched.

### 14.8 Idle mind, journal and knowledge base — what is actually true

Paths on the laptop, all under `~/KikiESP32/gateway/legacy_kiki/`:
`thinking_journal.json`, `knowledge_base.json`, `idle_mind_state.json`,
`ambient_listen_buffer.json`, `conversation_summary.txt`, `conversations/`.

Checked live rather than assumed:

* **Idle mind: working.** Cycling every few minutes, 13 sessions, calling
  `update_knowledge`, `set_next_turn_note`, `list_messages`, `search_web`.
* **Knowledge base: working.** Written the same day; 51 people, 182 facts.
* **Thinking journal: stale since 2026-08-08, but NOT broken.** The write path
  was tested end to end against a *copy* (132 -> 133 entries). It is stale
  because the model picked `reflect` in 11 of 13 sessions, and both
  `light_research` runs downgraded to `reflect` after their `search_web` was
  refused as a near-duplicate intent. Nothing to fix; worth knowing before
  someone goes looking for a bug.

Two genuine gaps *were* found, both invisible because the reading half works —
Kiki loads a summary at boot and sounds like she remembers:

* **Session summaries were never written.** The gateway only ever called
  `load_saved_summary`. `conversations/` stopped on 2026-08-07, the day the
  board took over, and every boot since re-read the same file. `close()` now
  writes one, with main.py's raw-transcript fallback, and skips sessions with
  fewer than two spoken turns — a fresh core is built per WebSocket session, so
  this runs on every reconnect.
* **The context was never compacted.** main.py counts tokens every turn and
  rebuilds the history past `agent.token_limit` (6000); the gateway had no
  token counting at all. History only grew, against a llama started `-c 8000`
  and already observed at ~5300. `after_response` now runs the same check, with
  the same in-place mutation, next-turn-note re-arm and prefix rewarm.

The Pi itself is **not running Kiki** (`kikifast.service` is `failed`), so its
own files are frozen at 2026-08-11 by simple disuse, not by a fault.

### 14.9 Laptop deployment is now a git repo

`/home/vaibhav/KikiESP32` had never been versioned (§8). It is now, with
`legacy_kiki/`, `gateway.env`, logs and the venvs excluded — private runtime
data and credentials. Rollback point: **`b20fd1d`**, "Deployed state before the
Pi-parity work". 97 files tracked. Commit there before each deploy.

### 14.10 Gmail and Notion: blocked on the user, on BOTH machines

Both go through the Smithery CLI (`core/self_extend/mcp_data_access.py` ->
`smithery_cli`), which uses a login session, not `SMITHERY_API_KEY`.

Installed on the laptop this session: Node 22.14 at `~/node-toolchain`,
`@smithery/cli` 4.11.1 at `~/.npm-global` (both home-dir installs — there is no
passwordless sudo on that machine, same pattern as the Go toolchain in §13.5).

**The connections themselves are not authorised, and this is true on the Pi
too** — so this was never a working feature waiting to be ported:

| server | `mcp list` | actual `tool list` |
|---|---|---|
| gmail | `auth_required` | requires authentication |
| gmail-server | `error` | — |
| notion | `connected` | **`Authorization required`** |

Notion reporting "connected" while every tool call fails is the trap here;
`mcp list` is not evidence. Both need an interactive browser OAuth that cannot
be done from a shell:

* https://connect.smithery.ai/moth-M4K2/gmail/setup
* https://connect.smithery.ai/moth-M4K2/notion/setup

Copying `~/.config/smithery` from the Pi to the laptop gets the CLI as far as
`403 Missing required permission: connections:read in namespace moth-M4K2`, so
the laptop additionally needs its own `smithery auth login`. Do that first,
then re-check with `smithery tool list notion`.

### 14.11 The reconnect loop — mostly solved, partly still open

**Found and fixed: `ping_timeout=10` on the websockets server.** The log was
full of `sent 1011 (internal error) keepalive ping timeout` on sessions that
were streaming 100 mic frames a second at the moment they were killed. A pong
is answered by the board's websocket task, which shares a core with audio and
LVGL and can be busy for most of a second, so an ordinary burst read as a dead
peer. Raised to 60 s; **zero keepalive drops** in the three minutes after.

Each false positive was expensive: the whole legacy runtime is rebuilt and
llama re-warmed on the next connect. One of them landed *mid-warm*, and
`warming` is the one state the board cannot leave on its own — it is waiting
to be told — so the panel sat on "Warming up model" while the gateway was
perfectly healthy and answering `hello_ack`. Warming is now bounded
(`warm_timeout_seconds`, 120 s) and non-fatal: a timeout or an exception logs
and goes idle anyway, paying the prefill on the first turn.

**The board did not recover on its own** from that wedged state — it never
reattempted the connection and needed a hard reset over USB
(`esptool --before default-reset --after hard-reset chip-id`). That is a
firmware robustness gap worth its own look (§9 Priority 2 already asks for
watchdog/recovery tests for exactly this).

### 14.12 Still open: the remaining board-side drop

The board drops and re-establishes its WebSocket every 2–5 minutes. Serial
shows the sequence: `microphone network queue full` -> `transport_poll_write(0)`
-> `esp_transport_write() returned 0` -> reconnect. No panic, no reboot — this
is not the "restart" reported by the user (that was the wizard, §14.9 above).

It predates this session's work. Two things to look at: `gateway_client_send_mic`
blocks up to **1000 ms** in `esp_websocket_client_send_bin`, which at 100
frames/s can only ever end in the 64-frame (640 ms) queue overflowing; and the
mic task, the telemetry task and the LVGL event callbacks all call into the
same websocket client. Measure before changing — RF conditions are also a
candidate (AP is `HomeNet_5GHz`, RSSI −51).

### 14.13 The disappearing audio — stream watermarks outlived their session

Reported as: Kiki listens, the caption appears on the panel, **no sound comes
out**, and it starts happening "after some time". Nothing was broken in the
TTS path — the board was discarding her speech on arrival.

`g_min_tts_stream` / `g_min_media_stream` are file-scope globals in
`gateway_client.cpp` and were never reset on reconnect. The gateway builds a
**fresh `DeviceSession` per connection**, with `tts_stream_id` back at 0 and
`media_stream_id` back at 1000. So every `audio_stop` in the *previous*
session — a duck, a pause, a barge-in, a music replace — left a floor via
`abandon_in_flight_audio()`, and the new session's ids started out below it.
`handle_binary` drops anything under the floor without a word.

It appeared to heal, which is what made it confusing: once enough turns pushed
`tts_stream_id` back above the floor, sound returned — until the next
`audio_stop` raised it again. With the board reconnecting every few minutes
this was inevitable rather than rare.

Both watermarks now reset on `WEBSOCKET_EVENT_CONNECTED`. Nothing can be in
flight on a socket that has just opened, so there is nothing there for a
watermark to suppress. **This is the one to remember when adding any new
stream kind: an id space that restarts per session cannot be policed by a
watermark that does not.**

### 14.14 Music gain, and the mic send timeout

**Music has never been gain-staged.** `amplify_pcm_s16` is applied only on the
TTS path, so `tts_gain` (3.2x) is speech-only and the media pump sent ffmpeg's
output untouched. That is the right answer acoustically — OmniVoice is quiet
(−21.65 dBFS RMS raw) and a YouTube track is already mastered near the ceiling,
where 3.2x would spend the whole song on the limiter — but it was accidental,
not chosen. Now `media_gain` / `KIKI_GATEWAY_MEDIA_GAIN`, default **1.0**,
which `amplify_pcm_s16` treats as a true bypass (returns the buffer unchanged).
Raise it only if the voice/music balance needs it, and expect the ceiling to
bite quickly.

**`gateway_client_send_mic` and the reconnect loop — one wrong fix, then the
right one.** Frames are 10 ms and arrive 100/s against a 64-frame (640 ms)
queue, so a 1000 ms send can never be absorbed; the queue overflowed and that
backlog preceded every `transport_poll_write(0)` in the serial log. The
obvious response — shorten the timeout to 200 ms so a congested send "drops a
frame" — **is wrong, and made the board reconnect far more often.** From
`managed_components/espressif__esp_websocket_client/esp_websocket_client.c`:

```c
if (wlen < 0 || (wlen == 0 && need_write != 0)) { ...
    esp_websocket_client_abort_connection(...)
```

A send that hits its timeout returns exactly 0, so **every expired write tears
the socket down**. Shortening the timeout only multiplied the aborts.

Congestion is now shed in `microphone_network_task` *before* the send: above 32
queued frames (320 ms, half the queue) the backlog is discarded. There a
dropped frame is genuinely just a dropped frame, sequence numbers still advance
so the gateway reports an honest `audio_gap`, and every send that does happen
is prompt. The timeout is back to 1000 ms as a backstop for a dead link, which
is the only thing it was ever suited to be.

**Do not treat a write timeout as flow control on this client.** That is the
lesson; the same trap is waiting in `gateway_client_send_event`, which sends
from LVGL callbacks with a 100 ms timeout.

### 14.15 The reconnect loop is not (only) software — read this first

The load-shedding fix in §14.14 **did not stop the drops**. Measured after it
was flashed: drops at 20:13:59, 20:16:02, 20:19:09 — the same 2–3 minute
cadence as before. Then the board went away entirely, and the reason is worth
more than any of the software theories:

* `/dev/ttyACM0` **disappeared** and has not come back.
* `ping 192.168.1.5` — 100% loss. Not on the network at all.
* The gateway's last mic frame was 20:17:47; last drop 20:19:09; no reconnect.

The board lost USB *and* Wi-Fi at the same moment. That is a power/reset
event, not a protocol one. The kernel log shows the pattern over hours:

```
usb 1-1: new full-speed USB device number 18   <- chip boots, enumerates
usb 1-1: USB disconnect, device number 18      <- 11 s later
usb 1-1: new full-speed USB device number 19
usb 1-1: USB disconnect, device number 19
...
```

A **new device number each time is a full re-enumeration**, i.e. the chip
reset — not a software reconnect. Several of those are explained by flashes
and serial captures, which reset the board deliberately. The final one is not.

**Do not keep tuning the WebSocket for this.** An ESP32-S3 driving an AMOLED
panel and a speaker amp draws hard in bursts (Wi-Fi TX + display + audio
together), and a thin or data-only USB cable, or an unpowered hub port, sags
enough to brown it out. That would produce exactly this: periodic resets, Wi-Fi
drops with no panic and no error in the serial log, and USB re-enumerations.

**The decisive next measurement** is the reset reason in the ROM boot banner,
which is printed before any application log:

```
rst:0x1  (POWERON)          -- power was actually removed/sagged
rst:0xf  (BROWNOUT)         -- the brownout detector fired: supply is marginal
rst:0xc  (RTC_SW_CPU_RST)   -- software reset, look at the firmware again
rst:0x7/0x8 (TG*WDT)        -- watchdog, look at the firmware again
```

Capture it with the serial reader used in this session and **do not filter the
first ten lines** — that is where the answer is. Until that byte is known,
every further protocol change is a guess.

What the software fixes in §14.13–14.14 are still worth: the stream-watermark
reset is a real bug fix regardless (Kiki's speech was being discarded after
every reconnect, and resets make reconnects frequent), and shedding before the
send is correct in its own right. But neither is the cause of the resets.

### 14.16 The battery never charged because nothing configured the AXP2101

The board has an **AXP2101 PMIC** (I2C `0x34`, confirmed by a bus scan) handling
charging and the battery power path. **Nothing in the ESP-BSP component
(v3.0.1) or in this firmware has ever talked to it** — zero references to
`axp`/`pmu`/`batt` anywhere in the tree — so it has been running in power-on
defaults since the board was new.

Read off the chip, before any change:

```
i2c scan: 0x18 0x20 0x34 0x40 0x51 0x5a 0x6b
axp2101 30: 03 ...     <- ADC_CHANNEL_CTRL: bit0 VBAT on, bit1 TS-pin on
axp2101 00: 20 ...     <- VBUS good
axp2101 01: 15 ...     <- charger status 0b101 = NOT CHARGING
axp2101 62: 08         <- constant-charge current ~200 mA
axp2101 64: 03         <- charge target 4.2 V
```

`0x30` bit 1 is the answer. It enables measurement of the TS
(battery-temperature) pin, **and this board has no thermistor fitted** — so the
charger reads an out-of-range temperature and refuses to charge. That is why
the BAT pads measure 0.3–0.8 V instead of 4.2 V, and why they did so from the
day the board was bought. Waveshare's own code for this exact board calls
`disableTSPinMeasure()` before anything else for precisely this reason.

`probe_pmic()` in `app_main.cpp` now dumps `0x00`–`0x7F` at boot and clears
that one bit. **It is the only register written.** The AXP2101 also sets this
board's rail voltages and a wrong write there can over-volt the ESP32; `0x30`
is an ADC enable and cannot. Charge current and target voltage are left exactly
as the board came (~200 mA into 4.2 V — a safe rate for any cell from 500 mAh
up, and 0.67C on a 300 mAh, which is why the 300 mAh cell is the wrong one).

**The PMIC keeps its registers across an ESP32 reset** — it stays powered from
VBUS — so the second boot logged "TS measurement already off" and `0x30` read
`01`. That is the write from the first boot persisting, not a no-op.

Still shows `charger=not-charging`, which is correct with **no battery
attached**: the MX1.25 connector on this unit is physically broken. Confirming
that charging actually starts needs a cell making solid contact — that repair
is the remaining blocker, and no software change substitutes for it.

### 14.17 Charging confirmed working — it was the TS pin all along

Verified on hardware. With the TS fix in and battery detection temporarily
disabled so the charger would drive an empty BAT pin, the telemetry caught the
exact moment a 4.1 V cell was touched to the pads:

```
t=110s  vbat=4543mV  vsys=4671mV  charger=not-charging      (no battery)
t=115s  vbat=4226mV  vsys=4438mV  charger=constant-voltage  <- contact
t=130s  vbat=4185mV  vsys=4398mV  charger=constant-voltage
t=175s  vbat=4556mV  vsys=4677mV  charger=not-charging      (removed)
```

VBAT pulled from the unloaded 4.54 V to **4185 mV** — the cell's real voltage —
and the charger engaged. It went straight to constant-voltage rather than
constant-current because a 4.1 V cell is already ~90% full; there is no bulk
phase to observe. Before the fix this same register read `not-charging` with
VBAT floating at 1963 mV.

`kPmicForceChargeForTest` is back to **false**: battery detection is what stops
the charger servicing a pin with no battery on it. The TS fix (`0x30` bit 1) is
the actual repair and stays unconditionally.

**The wires got hot during this test.** Nothing in the data shows sustained
current — CV into a nearly-full cell taper is well under the 200 mA limit, and
telemetry samples every 5 s so a sub-5-second short is invisible to it. The
MX1.25 pads are 1.25 mm apart; hand-held bare wire tips bridging them shorts
the cell through the wires. **Do not probe these pads by hand.** Solder flying
leads with strain relief.

Remaining: this unit's MX1.25 connector is physically broken, so a soldered
connection is required before battery operation is usable. Once a cell is
permanently attached, `log_pmic_state()` already reports VBAT/VBUS/VSYS and
charger state every 5 s, and battery percentage (AXP2101 `0xA4`, fuel gauge)
would be the natural next addition to `device_stats`.

### 14.18 `KIKI_LOW_POWER` — a build for running off a cell

New Kconfig switch (`CONFIG_KIKI_LOW_POWER`, off by default). When on:

* Wi-Fi uses `WIFI_PS_MIN_MODEM` instead of `WIFI_PS_NONE` — the radio dozes
  between DTIM beacons rather than listening continuously. Much the largest
  saving available, and it costs wake latency, which is exactly why
  `WIFI_PS_NONE` is the default everywhere else (§4.1).
* The AMOLED runs at `CONFIG_KIKI_LOW_POWER_BRIGHTNESS` (default 30%).

**It does not reduce the boot surge, and that is the thing that actually
decides whether a battery works.** Display init and Wi-Fi init both draw hard
inside the first second; a cell that cannot source roughly an amp browns out
regardless of any firmware setting. That is the cell's internal resistance, not
software. The 300 mAh cell already failed this repeatedly ("white flash, then
black") and will keep failing — capacity is not the issue, peak current is.

Note that with USB removed there is no serial console, so on-battery behaviour
has to be judged from the gateway log (does the board appear and stream mic
frames?) rather than from `log_pmic_state()`.

Turn it off again for normal use — the latency work in §5 assumes the radio is
awake.

### 14.19 Battery gauge on the panel, and a brightness control

The 300 mAh cell **does** hold the board up — soldered to the pads, with the
`KIKI_LOW_POWER` build of §14.18. §14.16–14.18's caution about peak current
stands as a general point, but this particular cell cleared it.

So the PMIC stopped being a bench instrument and became a feature.
`probe_pmic()` and friends moved out of `app_main.cpp` into **`kiki_power.cpp`**
behind a small struct:

```cpp
struct PowerState {
    bool pmic, present, usb, charging;
    int percent;            // -1 when there is no cell to measure
    uint16_t vbat_mv, vbus_mv, vsys_mv;
    const char *charger;    // "constant-current", "done", ...
};
```

Read off the chip, all four verified on hardware with a cell attached:

| Source | Register | Reading with a charging cell |
|---|---|---|
| VBUS good | `0x00` bit 5 | `0x00 = 0x38` → set |
| Battery present | `0x00` bit 3 | set (the empty-board dump in §14.16 read `0x20`, i.e. neither) |
| Charger phase | `0x01` bits 2:0 | `0x32` → `2` = constant-current |
| Charge level | `0xA4` | `0x54` = **84%**, and climbing to 87% over the next few minutes |

`0xA4` is the AXP2101's own fuel gauge and it works, so no calibration table is
needed. A resting-voltage curve is kept only as a fallback for the window after
a cell is attached where the gauge has not converged — and it is *only* a
fallback, because under load the cell sags and the curve reads low. At
vbat=4110 mV the curve says ~93% while the gauge says 84%; the gauge is right,
because a charging cell's terminal voltage is elevated above its resting one.

The probe now dumps to `0xB0` rather than `0x80` so the gauge registers are on
the record too.

**On the panel** — top row, right of centre, opposite the latency readout:

* Charging → `LV_SYMBOL_CHARGE` + percent, in the teal accent.
* On USB, not charging (full) → battery icon + percent, still teal. Not
  draining, so not a warning.
* On battery → battery icon at one of five levels + percent, in secondary
  grey, turning red below 15%.
* No cell attached → the USB plug alone. A battery outline with nothing behind
  it would be inventing a measurement.

**In the gateway log** — `device_stats` carries `battery_percent`, `battery_mv`,
`on_usb`, `charging` and `charger`, and `Session._report_battery` logs the
source every time it changes plus one line per 10% as a cell runs down (warning
at ≤20%). This is deliberately the *only* way to watch a battery run down:
with USB unplugged there is no serial console, so the log is all there is.

```
power: now on usb (86%, 4148mV, charger=constant-current)
```

**Brightness** is now a settings item (±10%, `LV_SYMBOL_EYE_OPEN`), applied
immediately and saved to NVS under `kiki_ui/bright`. The panel is the largest
single load on the board, so this is a power control as much as a comfort one.

A saved value **wins over `CONFIG_KIKI_LOW_POWER_BRIGHTNESS`**: the Kconfig
default is what a low-power build starts at, not a ceiling it enforces. An
explicit choice made by whoever is holding the device outranks a build-time
default.

### 14.20 Charge current has to track the cell (now a Kconfig value)

The cell changed to a **1050 mAh Li-ion**, and charging was reported as slow.
It was not faulty — it was doing exactly what it was configured to do. The
AXP2101's power-on default is 200 mA (§14.16), and **200 mA into 1050 mAh is
0.19C**: a trickle, six-plus hours from empty.

The PMIC cannot detect capacity. Nothing in the charge path knows what cell is
attached, so nothing but a human will ever catch a mismatch. That makes charge
current the one setting that must follow the hardware, which is why it is now
`CONFIG_KIKI_CHARGE_CURRENT_MA` (default **500 mA**, 0.48C for this cell)
rather than a number in the source.

`set_charge_current()` rounds **down** to a step the chip can express (25 mA
steps to 200 mA, then 100 mA), so a configured value is a ceiling. It is
re-asserted on every boot: the PMIC keeps its registers across an ESP32 reset,
but a full power loss returns `0x62` to the 200 mA default.

Rough guide — aim for 0.5C, with 1C the usual maximum rating:

| Cell | 0.5C | Set |
|---|---|---|
| 300 mAh | 150 mA | 150 |
| 800 mAh | 400 mA | 400 |
| 1050 mAh | 525 mA | 500 |

Verified on hardware. `0x62` read `08` (200 mA) before and `0b` (500 mA) after,
and the gauge roughly doubled its rate of climb — 1%/min at 200 mA against
2%/min at 500 mA.

**Do not read a current off those gauge percentages.** 2%/min of 1050 mAh would
be 1.26 A, which is impossible under a 1000 mA input limit with the board
running. `0xA4` is a model-based estimate, not a coulomb count, and it is
nonlinear in exactly this region. The register readback is the evidence that
500 mA is set; the gauge only shows the direction.

Two things bound how fast this can ever get:

* **The VBUS input limit (`0x16 = 0x04`, 1000 mA) covers the system load as
  well as the charger.** The board draws several hundred mA running, so beyond
  ~500 mA the input limit throttles charging anyway. Raising `0x62` further
  mostly buys nothing while the board is on.
* **A laptop USB port supplies less than a charger.** VBUS was measured
  sagging from 4976 mV at 200 mA to ~4830 mV at 500 mA on the same port. Still
  well above the VINDPM threshold, so it is not throttling — but that headroom
  is finite, and a proper charger or power bank is the way to get the full
  rate.

Above ~4.1 V the charger enters constant-voltage and tapers by design. That
last stretch is slow no matter what `0x62` says, and it is not a fault.

### 14.21 Software shutdown

**Settings → Shut down**, confirmed by a second tap (the prompt lapses after
5 s, and touching any other item cancels it). Two taps rather than one because
the panel is among the things that go off — there is no undo from the screen.

`power_shutdown()` sets **`0x10` bit 0**, the AXP2101's soft power-off, which
cuts every rail: ESP32, AMOLED, codecs, all of it. Draw falls to the PMIC's own
standby, tens of microamps. Same safe register class as the TS fix — the
control domain, not the rail voltages at `0x80+` that can over-volt the ESP32.

This is a real off. `esp_deep_sleep_start()` is not: it drops the ESP32 to
~10 µA while the panel, codecs and rails stay powered, and the panel is the
largest load on the board. Wrong tool here.

The sequence runs on its own task, not the LVGL callback: it sends a
`shutdown` event to the gateway, waits 600 ms for it to flush, then writes the
register. The gateway logs `device is powering off deliberately`. That line
exists because every other way this socket dies looks identical in the log, and
§14.15's phantom-reconnect hunt is not worth repeating. If the PMIC refuses the
write, Kiki keeps running and the menu item says `Shutdown failed` rather than
going quiet.

**Coming back on:**

* **PWR button.** Waveshare's hardware reference exposes its state through the
  TCA9554 at `EXIO4` (HIGH = pressed); the AXP2101 handles the press itself.
* **USB.** Confirmed from the board's own `PWRON_STATUS` (`0x20 = 0x04`,
  "VBUS insert") — that is how it powered up every boot this session.

One caveat worth knowing before testing on USB: **VBUS *insert* is the wake
event, not VBUS being present.** Shutting down with the cable already plugged
in may need an unplug/replug to come back. The button is the clean way.

Not fired remotely on purpose — a shutdown that needed a physical replug to
undo is not something to trigger from an SSH session.

**Long-press PWR is still a restart, not a power-off.** Read off the chip:

```
axp2101 22: 06   → bit 1 set: long-press shutdown already ENABLED
axp2101 25: 18   → bit 5 clear: long press RESTARTS instead of powering off
```

So the hardware path is half-configured out of the box. Setting `0x25` bit 5
(XPowersLib's `setLongPressPowerOFF()`) would complete it. Deliberately not
done yet: `0x25` is the PWROK sequence control, a mistake there affects how the
board powers *on*, and it is worth confirming against the datasheet rather than
against a recalled library constant.

### 14.22 Reaching the gateway from anywhere

**Tailscale does not run on the ESP32 and will not.** There is no ESP-IDF port:
the client is a Go program that needs an OS-level network stack to attach to,
and the limit is not the 8 MB PSRAM. Tailscale *uses* WireGuard, but the
coordination, NAT traversal, DERP relays and MagicDNS are the product, and that
is the part that cannot be cross-compiled onto this target.

It does not need to. The firmware already speaks TLS — `gateway_client.cpp`
attaches `esp_crt_bundle_attach` for a `wss://` URI, and
`CONFIG_MBEDTLS_CERTIFICATE_BUNDLE_DEFAULT_FULL` is on — so the board can reach
any public HTTPS endpoint with no firmware change. Tailscale runs on the
laptop, where it already was, and **Funnel** publishes the gateway at
`wss://vaibhav.taila7920.ts.net/`.

**Two addresses, LAN first.** `CONFIG_KIKI_GATEWAY_URI_FALLBACK` holds the
public URL and is only used when the LAN one fails six consecutive attempts
(~6 s). The LAN address is always preferred and always tried first, because
audio streams continuously at 100 frames a second and routing that through a
public ingress and back adds latency to the entire voice loop for nothing when
the laptop is one hop away. Pointing the board permanently at the Funnel URL
would have made every day at home slower to buy the occasional day away.

The switch runs on its own task. Stopping the websocket client from inside its
own event callback deadlocks — stop waits for that task to finish.

Known limit: the switch is failure-driven, so coming home does not immediately
return to the LAN path. A reboot or any Wi-Fi drop does. Switching back while
the fallback is still working would mean deliberately dropping a live
connection to test the other address, which is worse.

#### Authentication was off, and had to be fixed first

```
firmware/sdkconfig   CONFIG_KIKI_GATEWAY_TOKEN=""
gateway.env          (no KIKI_GATEWAY_TOKEN at all)
session.py:109       if self.config.auth_token and hello.get("token") != ...
```

Both sides empty, and the gateway only checks a token when one is configured —
so the handshake accepted anyone. Harmless on a LAN. **Not harmless on a public
URL**, where that endpoint carries audio, inference, robot control and tool
access including WhatsApp.

A 32-byte random token now sits in `gateway.env` (chmod 600) and in
`CONFIG_KIKI_GATEWAY_TOKEN`. Verified by probing the live gateway rather than
by reading the code:

```
bad token : REJECTED code=1008 reason='unauthorized'
no token  : REJECTED code=1008 reason='unauthorized'
real token: ACCEPTED -> {"v":1,"type":"hello_ack",...}
```

A probe must send `{"v":1,...}`; `decode_event` rejects a missing protocol
version with a 1011 before auth is ever reached, which is what the first
attempt at this test hit.

#### Remaining step (browser, cannot be done over SSH)

Funnel is not yet enabled on the tailnet:

```
Funnel is not enabled on your tailnet.
To enable, visit:
    https://login.tailscale.com/f/funnel?node=nSFPtsuUSV11CNTRL
```

After that, `tailscale funnel --bg 8765` on the laptop publishes it, and the
board's fallback URI starts working with no reflash. Until then the fallback
simply fails to resolve and the LAN path carries everything, which is the
correct degradation.

**Funnel makes the gateway reachable by anyone on the internet who learns the
hostname.** The token is the whole front door. Do not clear it.

### 14.23 Constant rebooting: coredump proved a WebSocket/TLS teardown race

The silent reboot at 22:42 was not a brownout. The 64 KiB coredump partition
was read from `0xFF0000` and decoded against the exact running build
`68694e72`. It recorded `LoadProhibited` in task `kiki_net_tx`, while sending a
344-byte remote microphone frame:

```text
ssl_check_ctr_renegotiate -> mbedtls_ssl_write -> esp_transport_write
-> esp_websocket_client_send_bin -> gateway_client_send_mic
excvaddr=0x0, ssl->in_ctr=NULL
```

`CONFIG_ESP_WS_CLIENT_SEPARATE_TX_LOCK=y` was the cause. A sender held the
component's TX lock, but its receive/reconnect task could independently hold
the client lock and call `esp_transport_close()`. That cleared the shared
mbedTLS context underneath `mbedtls_ssl_write()`. Remote links expose the race
frequently because tunnel stalls and reconnects make teardown overlap the
continuous 100-frame/s microphone stream.

The firmware now keeps the component's separate-TX option disabled, restoring
one recursive lock around receive, send, and transport teardown. A compile-time
guard in `gateway_client.cpp` rejects any future build that re-enables the
unsafe option. This is separate from the LVGL one-shot timer bug: the grace
callback must also continue to clear its pointer without calling
`lv_timer_delete()`, because LVGL deletes a repeat-count-one timer itself.

### 14.24 Pi-parity expression tags, now timed and coloured on the AMOLED

The ESP32 already had the Pi's 19 crab poses and accepted `expression` control
events, but the feature did not actually obey the Pi timing contract. The
gateway emitted `<oled:name>` as soon as a sentence was generated, while the
board was still in `thinking`. `ui_set_expression()` correctly refuses to let
a model-selected mood overwrite a system state, so the first/only tag was
usually discarded. Even when a later tag landed, it could lead the spoken
sentence by the gateway's buffered audio.

`session.py` now carries the last valid tag beside its cleaned sentence in the
TTS queue. On the first real PCM chunk it establishes the `speaking` base state,
then schedules the expression and caption together for that sentence's audible
window (queued audio lead plus `display_sync_offset_ms`). The event order is:

```text
thinking -> first PCM -> speaking -> <oled:...> expression -> caption
```

Unknown tags are stripped from TTS but never sent to the face. The accepted
vocabulary exactly matches `KikiFast core.oled_display.EXPRESSION_STATES`:
`love`, `shy`, `giggle`, `wink`, `excited`, `curious`, `proud`, `sulk`,
`surprised`, `sleepy`, `idea`, `mischief`, `scared`, `awe`, `happy`, `sad`,
`dizzy`, `confused`, and `sleeping`.

The adapter must also teach that vocabulary itself. It does not execute Pi
`main.py`, so relying on `main.py`'s `get_oled_tag_prompt_note()` silently left
the active ESP32 prompt without any face instruction. The hardware-independent
`gateway/kiki_gateway/expressions.py` now owns the static sorted prompt suffix
and registry; `LegacyKikiCore` appends it after the active persona/language
prompt for every mode without importing or starting the Pi OLED manager.

The AMOLED is no longer treating colour as a single global coat of paint.
Every one of those moods has a coordinated three-colour palette in
`kiki_theme.hpp`: a chromatic near-black screen ground, a readable crab-shell
colour, and a related prop/accent. The choices follow one visual grammar:
warm coral/amber for playful and high-energy moods, teal/green for curiosity
and disorientation, violet for pride/mischief/wonder, and cool desaturated
blue/slate for vulnerable or low-energy moods. Controls and caption text keep
their stable semantic colours, so mood never makes Stop ambiguous or speech
hard to read. Shell contrast against its mood ground is at least 4.8:1 even at
the low-power brightness setting.

The whole round screen background changes with the face; returning to a system
state restores Kiki's normal warm near-black. `test_mascot_palette.py` prevents
the Pi vocabulary, firmware pose registry, and palette registry from drifting,
and checks palette contrast/prompt coverage. `test_playback_pacing.py` proves
`speaking` arrives before the expression and the expression before its caption.
Gateway suite: **84 passed**. Firmware builds successfully with 66% of the app
partition free.

Deployed on hardware on 2026-08-14: firmware build `6b2cc2a8` and laptop
gateway commits `54a0fbe` + `ac7bde0`. The board reconnected over the remote
link at ~100 microphone frames/s and completed real post-update speech turns
(measured first PCM 1.2–1.5 s) without a reboot. The temporary OTA tunnel and
pending marker were removed after the build ID was confirmed live.

### 14.25 Tap-to-listen, true push-to-talk, and a touch-reactive body

The centre button now has two release semantics without clipping the first
syllable. `PRESSED` still sends `push_to_talk` immediately, but release before
400 ms sends `listen_open`: the gateway converts the provisional hold into the
same open, silence-ended listening window as a hotword. Holding for at least
400 ms sends `commit_now` on release. The gateway tracks
`push_to_talk_active` and calls `StreamingEndpointer.feed(...,
defer_commit=True)`, so neither the 240 ms speculative path nor the 600 ms VAD
endpoint can process a held utterance before the finger lifts. `force_commit()`
then processes it immediately with no extra endpoint wait and the panel moves
to `thinking` during final Whisper. A silent hold returns to idle instead of
leaving an apparently released button listening forever.

The grace timer for a drifting finger remains one-shot and must retain the
§14.23 ownership rule: its callback clears the pointer but never deletes the
timer LVGL is already deleting.

Short taps on visible crab geometry are no longer generic wake/cancel gestures.
They are hit-tested locally and play a matching one-shot animation plus a short
procedurally synthesized effect:

| Region | Reaction | Sound character |
|---|---|---|
| Left eye | recoils, one X eye, tear, `!` | descending cartoon yelp |
| Right eye | alternate one-eye recoil | two startled chirps |
| Shell/face | elastic boop, then puzzled | rubbery rising boop |
| Head | accepts a pat, then looks proud | three soft pixel-bells |
| Left claw | reaches out to pinch the finger | double claw-snip |
| Right claw | produces an absurd tiny ray gun | descending laser/fizz |
| Legs | trips, falls sideways, sees stars | three tumbling impacts |

These reactions are board-local: no LLM call, no TTS request, no conversation
history entry, and they still work with the gateway offline. Their palettes
borrow the existing emotional colour grammar and the full background changes
with the pose. A one-shot timer restores whichever system state is current;
any real gateway state change cancels the flourish immediately. If speech or
music is already playing, the animation still responds but the cue is
suppressed rather than mixed over intelligible audio. While a cue plays the
microphone uplink is gated so Kiki cannot hear its own effect.

Taps outside the visible body retain their old behavior (wake when idle,
cancel when busy), and a long background press still opens Settings. Gateway
suite after this change: **87 passed**. Firmware builds with 66% of the app
partition free.

Deployed on hardware on 2026-08-15: firmware build `3a3cc978` and gateway
commit `a3a5e29`. The first OTA attempt reached Cloudflare before its quick
tunnel hostname had propagated and correctly abandoned the update after three
HTTP 530 responses, leaving the old image running. The same verified image was
then served from a warmed URL; the panel downloaded through 100%, rebooted into
`3a3cc978`, and resumed at about 100 microphone frames/s. The temporary OTA
server, tunnel, and pending marker were removed afterwards.

The physical interaction trace recorded a 130 ms button tap becoming
`listen_open`, a 3043 ms hold producing exactly one `commit_now` endpoint on
release, and a 1395 ms silent hold returning idle with zero actions. Live body
hits exercised the right-eye ouch, head pat, both claw reactions, and leg
tumble; the complete seven-region reaction/effect registry remains covered by
the source-level test. No Guru Meditation, panic, brownout, reset, or reboot
signature appeared during the interaction pass.

### 14.26 Source-grounded proactive questions on the ESP32 route

The original Pi runtime starts periodic vision QA from `main.py`; the ESP32
gateway does not execute `main.py`, and its hardware override deliberately
disables camera/face paths because this panel has no camera. Merely leaving the
Pi configuration enabled therefore did not make autonomous questions run.

The gateway adapter now preserves the Pi architecture instead of maintaining a
second source collector. `gateway/legacy_kiki/core/brain/unified_idle_mind.py`
is byte-for-byte identical to KikiFast's implementation. Its dedicated Vertex
Gemini cloud route reasons over recent conversation, passive ambient speech,
long-term knowledge, workers and the thinking journal, then persists at most one
chosen next-turn note. At a random point every 20–30 minutes the gateway calls
the same `get_proactive_injection(scene_context)` method as the Pi. It passes an
empty scene because no camera exists, so only the idle mind's worthwhile note
can trigger speech; without one, Kiki stays quiet.

As in RPi `main.py`, the returned prompt is appended to retained history as a
`system` row, with no fabricated `user` message, and is handled by the ordinary
response/event/TTS path. The ESP adapter explicitly selects the configured
Vertex/Gemini cloud response route for this autonomous turn. Kiki's question is
retained as the following assistant row so Vaibhav's answer has the correct
antecedent. The session avoids interrupting listening, speech, push-to-talk,
another turn, or music. A spoken question sets `awake` and opens the same
15-second reply window as an ordinary response, so Vaibhav can answer without
repeating the hotword.

The production defaults are configurable with
`KIKI_GATEWAY_PROACTIVE_QUESTIONS_ENABLED`,
`KIKI_GATEWAY_PROACTIVE_QUESTION_MIN_SECONDS`, and
`KIKI_GATEWAY_PROACTIVE_QUESTION_MAX_SECONDS`; busy attempts retry after 60
seconds. Focused tests cover the exact idle-mind selector call, empty ESP scene,
system-turn history shape, forced cloud route, idle gating and reply-window
activation. Gateway suite: **93 passed** after this architecture correction.

### 14.27 Legacy-core startup default and disabled cleanup worker

A live retry exposed a separate deployment failure: manually restarted gateway
processes did not have `KIKI_GATEWAY_LEGACY_ROOT` in their environment. They
therefore constructed `DirectLlamaCore`, which has no RPi persona/history,
Unified Idle Mind, workers, or proactive scheduler. This is why no 5-second
test question appeared and no new `proactive questions enabled` line was logged.
`GatewayConfig.from_env()` now defaults to the repository's bundled
`gateway/legacy_kiki` directory when the variable is absent; an explicitly empty
variable still opts into the bare core.

The only persisted worker, `Knowledge Base Cleanup`, was inspected. It checks
Kiki's and Vaibhav's memory note lists at startup and removes only duplicates or
clearly redundant entries once either list exceeds 50. Its status was already
`completed`, and `Worker.is_active()` only permits `pending` or `failed`, so it
was not blocking startup. At the user's request its runtime status was changed
to `cancelled` anyway. Matching local and laptop backups are named
`workers-before-disable-20260815.json` under their respective backup folders.
Gateway suite after the legacy-root regression test: **94 passed**.

Live acceptance used temporary 5-second scheduler bounds with the existing real
idle-mind note. At 01:11:34 the gateway logged the scheduler, at 01:11:41 the
turn entered `vertex_ai/gemini-3-flash-preview`, first PCM reached firmware
`4e550c58`, and at 01:12:14 the gateway logged `proactive question spoken`.
The note was then marked used. The gateway was immediately restarted without
test overrides; at 01:13:08 it selected a 1330-second first attempt (within the
production 1200–1800-second range), with the legacy display bridge and Unified
Idle Mind active. The cancelled cleanup worker loaded but did not fire.

### 14.28 The reconnects were never the Wi-Fi — one mutex owned the whole socket

Reported as four things: the talk button hanging, listening arriving late,
speech stuttering, and random reconnects with "Warming up model" again. All at
college on a 1–10 Mbps AP, or on a phone hotspot. None of it at home.

**The first useful thing was to stop believing the premise.** The same gateway
log that showed drops at college showed them at home too:

```
device dropped the connection (rcvd=None sent=None uptime=15.4s)
device dropped the connection (... sent=1011 keepalive ping timeout ...)
device link health: rssi -23 dBm (ok), wifi drops 0 (last reason 0), ws drops 3
device firmware build 914f5969 ... link=lan
```

Sessions of 15–275 seconds, on the LAN, at −23 dBm, with **zero Wi-Fi drops**.
A bad link was not causing this; it was making an existing defect fire
constantly. Which also meant it could be reproduced and fixed on the bench.

#### What it actually was (§6.5 of codestructure.md)

`esp_websocket_client` is built here with a single lock —
`CONFIG_ESP_WS_CLIENT_SEPARATE_TX_LOCK` is off on purpose, a separate TX lock
races mbedTLS teardown and reboots the board. So every send takes the same mutex
the client's own task needs to read the socket and to answer a PING. One send
stuck on a congested uplink (up to `kSendTimeout`, 5 s) therefore stopped the
board reading its own audio (**stutter**), stopped the keepalive (**`1011`
reconnect**), and blocked the LVGL touch callback that called `send_event()`
straight through (**"tap or hold" hung**, then the event was dropped on
timeout). Inbound had the mirror fault: `handle_json`/`handle_binary` ran inside
the websocket callback, still holding the lock, where they take the display lock
and used to block 250 ms on a full playback ring.

`kiki_log.cpp` had already written the mechanism down in a comment and
rate-limited itself around it. The fix generalises it: **the socket has exactly
one owner.** Producers enqueue and return; `kiki_net_tx` drains urgent control,
then ordinary control, then bulk, then one microphone frame, so a button press
overtakes a backlog of audio. The callback copies inbound messages into a PSRAM
ring and `kiki_net_rx` parses them somewhere it is allowed to be slow.

The uplink governor is reversible now rather than latching for the boot, and
thrifty (VAD-gated) sending is the default on a remote link instead of something
the board switches on after it has already lost a session.

#### The second half: Kiki was being rebuilt per WebSocket

`DeviceSession` is per-connection, and it used to build a whole
`LegacyKikiCore` with it. The board reconnects by itself, so each drop threw
away the conversation, re-warmed llama (**"Warming up model"** — that message,
exactly), spent a cloud summarization call on a fragment, and stopped and
restarted the workers and Unified Idle Mind. `get_shared_core()` now builds the
runtime once per process. See §5.4b for the three details that matter, in
particular that attach is a **stack**: sessions genuinely overlap when the board
reconnects before its old socket is reaped, and a naive detach silently unbinds
the live one.

#### The third half: the jitter buffer was time-to-first-word

The board's prebuffer was both the cushion that absorbs jitter and the gate that
decides when the first word comes out, and it grew +60 ms after every stuttered
reply. Measured on the harness: six turns on an **unshaped LAN link** walked it
from 100 ms to 300 ms. Cushion and gate are now separate — the gate is pinned at
its 100 ms floor, the cushion is the gateway's send lead (free, because pacing
only ever delays frames after the first), and a stuttered reply sends
`request_lead` rather than paying for it locally. The playback ring went
128 KiB → 768 KiB because it has to hold the lead; it did not before, and the
surplus was discarded on arrival — not a gap, **missing words**.

#### The bench that made all of this measurable

`gateway/tools/slowlink.py` shapes one TCP flow (rate per direction, RTT,
jitter, retransmission stalls, scheduled outages) with no root and no effect on
ssh or the tunnel. `gateway/tools/fake_device.py` speaks the real protocol and
replays the firmware's jitter buffer **from arrival timestamps**, so it can tell
a real underrun from its own scheduling. It reports time-to-first-word three
ways — perceived, transport, and gateway-reported — because the gateway's own
number is blind to the network and to the start gate, and can look perfect while
a person waits.

Two harness bugs worth remembering, because both produced convincing nonsense:
a turn started over the tail of the previous reply attributed that reply's audio
to it (negative TTFW), and the gateway discards every microphone frame while
`playing` is set, so a turn started too early is not endpointed until the
previous reply drains.

The board also stopped being nearly out of internal RAM. The microphone queue is
64 slots of just over a kilobyte and was allocated internally, in direct
competition with the display's DMA buffers; it and the new tx/rx buffers all
live in PSRAM now. Steady state went from **19 KiB internal free / 7-12 KiB
largest DMA block** to **84 KiB / 31 KiB** -- the old numbers were close enough
to the `ESP_ERR_NO_MEM` display floods of codestructure §4.6 to matter by
themselves.

#### Measured, same stimulus, before = 5 turns/row, after = 6 turns/row

| | before | after |
|---|---|---|
| audible gaps, LAN unshaped | 3.56 % of speech time | **0 %** |
| audible gaps, 5 / 2 / 1 Mbps | 3.21 / 3.21 / 2.89 % | **0 / 0 / 0 %** |
| audio discarded on arrival | 0.6–1.5 s of words per row | **0 bytes, every row** |
| board start gate after a row | 240–340 ms | **100 ms (the floor), every row** |
| control round trip, 1 Mbps | 102–189 ms | 100–281 ms (unchanged; it was never the bottleneck here — the emulator has no LVGL task to block) |

And the row that answers "it drops all the time on my hotspot" directly --
1 Mbps, 120 ms RTT, 60 ms jitter, and the link **severed for 8 s every minute**
(the shaper aborts the sockets rather than closing them politely, or neither end
notices):

| | |
|---|---|
| link losses over six turns | **40** (41 sessions) |
| turns answered | **6 / 6** |
| TTFW p50 / p95 | 1820 / 2500 ms -- the same as the row with no outages at all |
| audio discarded on arrival | **0 bytes** |
| audible gaps | 1 (0.48 % of speech time) |
| "Warming up model" | **0**, across all six reconnects |
| prefix re-warms, session summaries | **0** and **0** |

Before, each of those reconnects would have rebuilt the runtime, re-warmed
llama, spent a cloud summarization call and shown "Warming up model" on the
panel -- which is the whole of the reported "random reconnects and warming up
model again".

And on the **real board**, which is where the mutex actually mattered: firmware
`914f5969` was dropping every 15–275 s. Firmware `cabf1948` connected at
20:47:56 and had not dropped once by 21:16, with the same AP and the same
gateway. The emulator cannot show this at all — Python's websockets has no
shared TX/RX lock — which is exactly why the board run is not optional.

Gateway suite: **267 passed**. Firmware builds with 64 % of the app partition
free; `firmware/tests/test_tx_priority.cpp` pins the send classes on the host.
`scripts/deploy_gateway.sh` now does the laptop copy, the test run and the
gateway-only restart, with a timestamped backup first.

**Still open, and the next thing to do:** the wire is still raw PCM — 256 kbps
up continuously and the same down. Everything above makes that survivable;
Opus at ~24 kbps would make it irrelevant, and is also what would let the send
lead be deep on a 1 Mbps link without the cushion taking seconds of wire time to
fill. Frames are 32 ms, which is not a legal Opus frame size, so the tx path has
to re-frame to 20 ms and the gateway re-accumulate to the 512-sample chunks the
endpointer and the barge-in check expect. Bench the encoder on the S3 first:
the AEC already uses 23 ms of a 32 ms budget on core 1, so encoding belongs on
core 0 in `kiki_net_tx`.


### 14.29 A mood tag was disabling the Stop button

Reported as: tapping Stop while Kiki is speaking puts the panel back into
listening but she carries on talking.

The gateway was innocent, and proving that first is what made the rest quick.
`fake_device.py --stop-after N` taps Stop mid-reply exactly as the panel does:

```
[stop] cancel_turn sent (state=speaking, 0.60s already buffered on the board)
[stop] state -> listening after 21 ms | audio sent AFTER cancel: 0 B | board buffer now 0.00s
```

Twice out of two. So when she does keep talking, either the press never leaves
the board or the board ignores it. `cancel_turn` turned out to be the one
control event with **no log line at all** -- `push_to_talk` and `commit_now`
both have one, precisely because "the button does nothing" has several invisible
causes -- so that was added first, and the live capture then read:

```
09:15:30,785  first PCM                                   <- reply starts
09:15:34,123  cancel_turn from device                     <- first tap arrives
09:15:34,262  [device] playback aborted; drained=54492 B  <- silent 139 ms later
...
09:15:53,665  push_to_talk (awake=True playing=True)      <- second tap, mid-reply
09:15:53,814  short talk tap -> open listening
```

The first tap worked perfectly. The second sent **`push_to_talk`**, not
`cancel_turn` -- so nothing stopped, and the gateway discarded the microphone
anyway because it was still `playing`.

`talk_event_cb` chooses between the two on `kiki_is_busy()`, which reads
`g_state_name`. And `ui_set_expression()` ended with:

```c
std::snprintf(g_state_name, sizeof(g_state_name), "%s", name);   // "speaking" -> "curious"
```

One variable was doing two jobs: the authoritative system state *and* the face
currently drawn. The moment a reply emitted `<oled:curious>` the state stopped
being "speaking", `kiki_is_busy()` went false, and the button quietly went back
to meaning "talk to me". The reply in the capture above is full of
`<oled:curious>` and `<oled:idea>`, which is why the first tap worked and the
second did not -- and why it has always looked intermittent rather than broken.
It shipped with the expression feature on 2026-08-14.

`kiki_panel_state.hpp` now holds the pair, ESP-IDF-free, and `kiki_ui.cpp` uses
it rather than a copy of the rule -- a test guarding a parallel implementation
would have been worse than none. `firmware/tests/test_panel_state.cpp` asserts
that a mood leaves `busy()` true, and it was checked against a deliberately
reintroduced version of the old line: 4 failures, exit 1.

Firmware `2af142ab`.

### 14.30 On a genuinely weak link the problem was bytes, not locks

§14.28 fixed the socket ownership and it was right, but it was measured through
a bandwidth shaper on a −23 dBm LAN. The real board at college is not there.
Taken to a weak open AP it reported:

```
device link health: rssi -92 dBm (weak), wifi drops 67 (last reason 201)
turn endpoint  stt_ms=377            <- heard correctly
first PCM      ttfw_ms=1264          <- answer generated and sending
reply stuttered (21 underruns)       <- board ran dry, over and over
device ... dropped (1011 keepalive ping timeout uptime=80.0s)
```

−55 dBm (the house network) to −92 dBm is roughly ten thousand times less
received power, and no amount of shaping on a strong signal reproduces it.
Reason 201 is `NO_AP_FOUND`: the AP was not disconnecting the board, it was
vanishing.

**"Thinking, then never speaks" was not a bug in the thinking path.** The reply
was generated and sent; 256 kbps of PCM could not arrive faster than it plays,
so there was nothing to play. And the same queued audio sat ahead of the
keepalive PING on the same TCP connection, which is why every session died at
80-100 s. See §7.0a for the fix (Opus, kind 7) and why it is a latency fix as
much as a bandwidth one.

Three things found alongside it:

- **Two live sessions served one board.** The board abandons a stalled socket in
  seconds and reconnects; the gateway kept the corpse for another 60-402 s
  (observed 80, 82, 100, 140, 191, 402). Both were attached to the shared core.
  A second connection *is* proof the first is dead, and better proof than a ping
  timeout -- `server.py` now evicts on connect.
- **`request_lead` was a positive feedback loop.** A stuttering board asks for
  more send lead and the gateway granted it (0.8 → 1.5 → 2.2 s). On a saturated
  link that is *more* bytes queued ahead of the PING: stutter, ask for lead,
  stall harder. Now refused when the last reply took longer to send than to
  play, which `_tts_worker` measures and logs as `reply downlink ... goodput=`.
- **`aec_max_process_us` reached 283,000** against a 32 ms frame budget, and
  went straight back to 23,152 on a strong signal. It is a symptom of the Wi-Fi
  driver thrashing at the noise floor, not an independent fault.

**Do not repeat the mistake that led here:** a shaper can model bandwidth, RTT,
loss and outages, but not a radio at the edge of its sensitivity. When the
report is "it fails in the field", get RSSI from the board before believing any
bench result.
