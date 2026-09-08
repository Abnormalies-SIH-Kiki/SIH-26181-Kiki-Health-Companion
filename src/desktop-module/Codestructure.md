# KikiFast — Code Structure & Architecture Reference

KikiFast is a low-latency voice companion robot ("Kiki") running on a Raspberry Pi 5,
speaking through a local llama.cpp box (gemma-4-26B over Tailscale) or a cloud provider,
with a local omnivoice.cpp TTS server, local whisper.cpp STT, OpenWakeWord hotword
detection, a Hailo-based face/vision pipeline, a stationary neck (left/right/center only —
the wheeled chassis is parked in `to_do/`), and a layered memory system (knowledge base,
thinking journal, conversation summaries).

## How to use this document

**Before any change: commit and push the current state** (`git add .`, `git commit`,
`git push`). That is what makes a bad edit recoverable on a machine that is also the robot.

**Do not read this file whole.** It is ~2500 lines and a single read will truncate. Find
the section you need in §0, then `grep -n "^### 5.2c" Codestructure.md` for its start line
and `sed -n 'START,ENDp'` that range. The headings are the stable anchors.

**Section numbers are load-bearing.** `§4`, `§5.2a`, `§5.2c`, `§5.13` and `§5.23` are cited
from code comments and from `config.json`, so sections are never renumbered — only moved.
`§5.11` and `§5.18a` are deliberate gaps left by earlier merges; do not reuse them.

**Anchors are `symbol @line`.** Trust the symbol; the number is a jump hint that drifts.
`grep -n "def <symbol>"` is always authoritative.

**Running it:** `source /home/kiki/Kiki/kiki/bin/activate` before `main.py`.
**Testing it:** `/home/kiki/Kiki/kiki/bin/python -m pytest` — 1048 tests, all passing as of
2026-09-01. `tests_llamaserver/` is separate and needs the live box.

**Senior care** has its own canonical checklist, lock gates and status in `plan.md`. Read
and update that whenever working on senior-care features.

> **Unified Idle Mind (2026-07-26):** `core/brain/unified_idle_mind.py` is Kiki's only
> background cognition process. It chooses `no_action`, `reflect`, `light_research` or
> `deep_research`; ambient listening is capture-only; `recall_memory` searches knowledge,
> conversations and dated journal research; and one model-selected next-turn note is the
> only background prompt injection. The process is cloud-only and continues safely if
> foreground speech begins.

> Last reconciled against the code: **2026-09-01**.

> **Wearable health (2026-09-03):** `core/health/service.py` is the dedicated
> port-8091 ingest/summary/dashboard process. `core/health/wearable.py` owns
> deterministic wellness analytics and strips POOR optical estimates from both
> current context and legacy snapshots. `core/health/signal_quality.py` also
> treats the firmware label as untrusted input: estimator schema v2 plus either
> peak/ACF consensus or an explicit, well-supported paired-peak correction,
> perfusion, channel correlation and I2C integrity must independently pass.
> Wearable batches and latest state live in
> `care_plan.json`; `CarePlan.save()` uses a sibling `flock` plus changed-section
> merging so this service and Kiki cannot overwrite each other. Pi voice context
> continues to use the existing change-gated `CARE NOW` row—there is no second
> prompt injector. See `docs/WEARABLE_HEALTH.md`.

---

## 0. Contents

- **§1** — Hardware / Service Topology
- **§2** — File Tree
- **§3** — Runtime Flows
  - **§3.0** — System boot and offline Wi-Fi setup (`kiki_boot.py`)
  - **§3.1** — A normal speaking turn
  - **§3.2** — Startup sequence (`main.py main()` top half)
  - **§3.3** — Unified Idle Mind
  - **§3.4** — Ambient listening
  - **§3.5** — Background work vs. the speaking slot
  - **§3.6** — Always-listen capture boundary
- **§4** — The KV-Cache Contract (read this before touching message_history)
- **§5** — File-by-File Deep Dive
  - **§5.1** — `main.py` — Orchestrator
  - **§5.1a** — Three ways one turn lost a tool call (2026-07-29)
  - **§5.2** — `core/llm.py` — Speaking path
  - **§5.2a** — `core/vision/instant_vision.py` — Live-image path (Groq Qwen VLM)
  - **§5.2b** — Speaking provider toggle — `llm.speaking_provider` (`local` | `cerebras` | `groq` | `openrouter`)
  - **§5.2c** — `core/brain/action_agent.py` + `fast_cloud.py` — the `complex_query` action agent
  - **§5.2d** — `core/self_extend/whatsapp_contacts.py` — the address book
  - **§5.2e** — Inline image reading — and the three ways it hung the turn
  - **§5.2f** — Name matching and row size — why summaries were thin and sends were refused
  - **§5.2g** — What the agent knows besides the request
  - **§5.2h** — `core/brain/history_view.py` — the history, rendered for readers
  - **§5.2i** — Address-book staleness — a 4 ms hole in the cache signature
  - **§5.3** — `core/local_llm.py` — Single-slot coordinator
  - **§5.4** — `core/stt.py` — Local Whisper.cpp STT (client-side Silero VAD)
  - **§5.4a** — `core/brain/ambient_listening.py` — Always-listen distillation
  - **§5.4b** — `core/runtime_controls.py` — Spoken runtime settings
  - **§5.4c** — `core/noise_suppression.py` + `core/near_field_gate.py` — noise handling
  - **§5.5** — `core/tts.py` — TTS providers
  - **§5.5a** — `core/speech_recorder.py` — `record_enabled` speech archive
  - **§5.6** — `core/brain/unified_idle_mind.py` — Unified Idle Mind
  - **§5.7** — `core/brain/thinking_journal.py` — Dated research
  - **§5.8** — `core/brain/knowledge_base.py` — Long-term memory
  - **§5.8a** — `core/brain/memory_search.py` — Human-like cross-store recall
  - **§5.9** — `core/brain/summary_manager.py` — Summaries
  - **§5.10** — `core/brain/generate_llm_resp.py` — Brain/vision/summary router
  - **§5.12** — `core/brain/token_counter.py`
  - **§5.13** — `core/workers/` — Background agent system
  - **§5.14** — `core/vision/`
  - **§5.15** — `core/self_extend/`
  - **§5.15a** — Gmail MCP: two auth layers and the 2026-08 server swap
  - **§5.16** — `hotwords/hotword_recog.py`
  - **§5.17** — `robot/`
  - **§5.18** — `tools_and_config/`
  - **§5.18b** — `core/ir_controls.py` — IR-sensor + LCD control surface
  - **§5.18c** — `core/lcd_display.py` — 16x2 char LCD
  - **§5.18d** — `core/tts_sync.py` + `scripts/calibrate_lcd_sync.py` — calibrated LCD speech clock
  - **§5.18e** — `core/wifi_setup.py` — boot-time Wi-Fi provisioning
  - **§5.18f** — `core/oled_display.py` + `robot/oled_tags.py` — the face
  - **§5.18g** — `scripts/power_blackbox.py` — why the Pi died
  - **§5.18h** — `kiki-cpu-cap.service` — trimming peak SoC current
  - **§5.18i** — CLIP care-event cascade — senior activity detection
  - **§5.19** — `scripts/streaming_tts.py`
  - **§5.20** — Tests
  - **§5.21** — `kiki_control_client.py`
  - **§5.22** — `kiki_startup.sh`
  - **§5.23** — `core/observability.py` + `webui/server.py` — Dashboard
  - **§5.24** — `core/senior/` — Senior Citizen Mode (elderly-care addon)
  - **§5.25** — `core/health/environment.py` — weather and air quality
  - **§5.25a** — `core/health/care_snapshot.py` — the `CARE NOW` row
  - **§5.25b** — `core/health/companion_routines.py` — the Phase H daily experiences
  - **§5.26** — `core/brain/auto_recall.py` — noticing a topic was discussed before
- **§6** — `tools_and_config/config.json` Reference
- **§7** — Data Files
- **§8** — Important Things to Keep in Mind (gotchas & invariants)
- **§9** — Known Pending / Watch List

---

## 1. Hardware / Service Topology

| Component | Where | Endpoint |
|---|---|---|
| Main orchestrator (`main.py`) | Raspberry Pi 5 (this repo) | — |
| Foreground speaking LLM (gemma-4-26B-A4B, llama-server) | Vaibhav's laptop (RTX 4060) over Tailscale | `http://100.64.0.10:8080/v1` |
| TTS (omnivoice.cpp tts-server, 24 kHz PCM) | Same laptop | `http://100.64.0.10:8082` |
| STT (whisper.cpp server, HTTP POST + Silero VAD) | Same laptop (Tailscale) | `http://100.64.0.10:5555/inference` |
| Cloud LLM fallback | Vertex AI Gemini (via litellm) / google-genai / Groq | API keys in `.env` + an optional Vertex service-account key at `VERTEX_SERVICE_ACCOUNT_FILE` (project from `VERTEX_PROJECT`, location `VERTEX_LOCATION`) |
| Web search | Exa API | key hardcoded in `tools.py` (⚠) |
| Face recognition + neck/relay control (KikiController) | Hailo pipeline process | ZMQ `192.168.1.11:5555` (REQ commands) / `:5556` (SUB events) |
| Chassis motor server (collision-safe movement) | local process (`hailo_follower_webcam_only.py`) | ZMQ `127.0.0.1:5557` |
| Camera | local MJPEG stream | `http://localhost:5000/mjpeg` |
| WhatsApp bridge + MCP | local Go bridge + Python stdio MCP daemon | bridge REST `http://127.0.0.1:8080/api`; MCP owned by Kiki |
| Audio out | ALSA via `aplay` (TTS) and `mpv` (music/sfx); configured A2DP sink guarded by `core/audio_output.py` | — |

**The llama.cpp box is single-slot (`-np 1`)**: one request at a time runs fast. The entire
client architecture is built around protecting that slot for the *speaking* path and making
every background request instantly killable. See §4 — this is the most important section
in this document.

llama-server launch command (current, correct shape):
```
./build/bin/llama-server -m gemma-4-26B-A4B-it-UD-Q4_K_S.gguf -ngl 99 --n-cpu-moe 30 \
  -fa on -c 7000 -ctk q8_0 -ctv q8_0 -np 1 --ctx-checkpoints 6 --cache-prompt \
  --mmproj gemma-4-26B-it-mmproj.gguf --mmproj-offload \
  --model-draft gemma-4-26B-A4B-it-MTP-BF16.gguf --spec-type draft-mtp --spec-draft-n-max 1 \
  --host 0.0.0.0 --port 8080 -l 64980-100,27388-100,45518-100,15081-100
```
Critical: **no `--reasoning-budget` flag** (it would silence the per-request
`thinking_budget_tokens` field every client request sends), `-np 1` (single slot, full ctx),
comma-separated `-l` (multiple `-l` flags silently drop all but the last).

---

## 2. File Tree

> Regenerate the skeleton with `git ls-files`, not by hand. This tree was last
> reconciled against the repo on **2026-09-01**; anything it claims should exist.

```
KikiFast/
├── main.py                     # Orchestrator: wake→listen→think→speak loop + all background tasks (§5.1)
├── kiki_boot.py                # System boot: Bluetooth → config wizard/Wi-Fi → services → exec main.py (§3.0)
├── kiki_control_client.py      # Async ZMQ client for the Hailo face/neck controller (§5.21)
├── kiki_startup.sh             # Boot script: env (Pulse/X), launches the full process stack (§5.22)
├── max30102_read.py            # MAX30102 pulse-oximeter driver, imported by core/senior/heart_rate.py
├── pytest.ini                  # Pins collection to tests/ (a bare `pytest` used to walk to_do/)
├── requirements.txt            # Python dependencies
├── Codestructure.md            # ← this file
├── plan.md                     # Senior-care phased checklist + real-device lock gates
├── .env                        # API keys (Gemini, Groq, Cerebras, OpenRouter, Deepgram, Exa, …)
│
├── core/
│   ├── llm.py                  # SPEAKING path: SSE streaming → sentences, tool-call parsing, provider routing (§5.2)
│   ├── local_llm.py            # Single-slot coordinator: background gen, preempt, rewarm, cache hashing (§5.3)
│   ├── stt.py                  # Local whisper.cpp STT with mute/unmute + server-side Silero VAD (§5.4)
│   ├── noise_suppression.py    # RNNoise at capture time: kills steady noise, NOT voices (§5.4c)
│   ├── near_field_gate.py      # Crowd fix: adaptive noise floor + near-field level gate (§5.4c)
│   ├── tts.py                  # TTS streamers (local omnivoice / Groq / Inworld) + tag sanitizer (§5.5)
│   ├── audio_output.py         # Repairs Pulse auto_null fallback + pins aplay to the configured A2DP sink
│   ├── speech_recorder.py      # record_enabled: off-path wav archive of what Kiki actually said (§5.5a)
│   ├── tts_sync.py             # Calibration-profile word timing; no runtime Whisper dependency (§5.18d)
│   ├── media_manager.py        # Exact-video music state/likes/playlist controls + timer alarms (§8.15a)
│   ├── lcd_display.py          # 16x2 I2C char-LCD status/streaming display (§5.18c)
│   ├── oled_display.py         # 128x64 SSD1306 pixel-crab face: 35 states + <oled:> expression tags (§5.18f)
│   ├── oled_log_feed.py        # stdout/stderr tap → the resting face's background-activity ticker
│   ├── i2c_bus.py              # ONE process-wide lock for /dev/i2c-1, shared by the LCD and the OLED
│   ├── ir_controls.py          # Two IR sensors (GPIO22/17): left-wave interrupt + LCD settings menu (§5.18b)
│   ├── gesture_controls.py     # Thread-safe camera-gesture mute + activity-stop state
│   ├── startup_config.py       # Boot IR/LCD Wi-Fi/provider/volume/language/mode wizard (§3.0)
│   ├── wifi_setup.py           # Offline IR/LCD Wi-Fi picker + local Whisper password dictation (§5.18e)
│   ├── runtime_controls.py     # Spoken mode/follow-up state + deterministic command parsing (§5.4b)
│   ├── observability.py        # Recorder singleton: flat events + grouped SESSIONS for the Web UI (§5.23)
│   ├── agent_loop.py           # run_agent_loop: shared JSON-protocol multi-turn tool agent (+ session logging)
│   ├── cloud_budget.py         # Cost guard: rate limits + per-feature caps for paid cloud calls (§5.23)
│   ├── brain/
│   │   ├── unified_idle_mind.py  # One cloud background agent: decisions, tools, notes, scheduling (§5.6)
│   │   ├── action_agent.py       # The `complex_query` multi-step cloud agent (§5.2c)
│   │   ├── fast_cloud.py         # Lean Cerebras/Groq JSON client behind action_agent (§5.2c)
│   │   ├── generate_llm_resp.py  # Multi-provider router for NON-speaking work (§5.10)
│   │   ├── history_view.py       # One readable rendering of message_history for agents/logs (§5.2h)
│   │   ├── auto_recall.py        # Entity-anchored automatic memory recall (§5.26)
│   │   ├── ambient_listening.py  # Capture-only crash-safe ambient transcript buffer (§5.4a)
│   │   ├── thinking_journal.py   # Dated research/open-question persistence and dedupe (§5.7)
│   │   ├── knowledge_base.py     # Hierarchical long-term memory + context summary (§5.8)
│   │   ├── memory_search.py      # Ranked recall over KB + all conversations + journal (§5.8a)
│   │   ├── summary_manager.py    # Conversation summaries, past-summary cache (§5.9)
│   │   └── token_counter.py      # tiktoken-based context counting with per-message cache (§5.12)
│   ├── workers/                # Background agent system (§5.13)
│   │   ├── worker_engine.py    # Dataclasses: Worker, WorkerTrigger, WorkerCondition, enums
│   │   ├── worker_brain.py     # execute_worker (builds prompt → run_agent_loop), face/vision buffers
│   │   └── worker_manager.py   # Scheduler thread, persistence, lifecycle events, live-toggle reload
│   ├── health/
│   │   ├── environment.py      # Open-Meteo weather + air quality; CPCB AQI; freshness lifecycle (§5.25)
│   │   ├── care_snapshot.py    # The ephemeral `CARE NOW` row injected only when it changed (§5.25a)
│   │   └── companion_routines.py # Phase H daily experiences, seeded as care-plan data (§5.25b)
│   ├── senior/                 # Senior Citizen Mode (§5.24)
│   │   ├── care_plan.py        # Whole-day session briefs + real transcript + trusted health trends
│   │   ├── care_voice_agent.py # Persistent multimodal Cerebras care dialogue + fresh frame/turn
│   │   ├── senior_care_manager.py # Care schedules → timing-only workers → foreground voice queue
│   │   ├── exercise_cadence.py # Audible hold countdowns and round cues for guided sessions
│   │   ├── health_events.py    # Gates 04-05 of the CLIP care cascade: adjudicate, then decide to speak (§5.18i)
│   │   └── heart_rate.py       # Thread-safe structured MAX30102 controller; never generates speech
│   ├── vision/                 # §5.14
│   │   ├── camera.py           # capture_photo_b64 from the MJPEG stream
│   │   ├── vision_handler.py   # Periodic capture/QA logic, silent scene injection
│   │   └── instant_vision.py   # Bounded clean snapshots + live-image Groq Qwen path (§5.2a)
│   └── self_extend/            # §5.15
│       ├── skill_manager.py    # Local SKILL.md skills (list/create/summarize)
│       ├── smithery_cli.py     # subprocess wrapper around the `smithery` CLI
│       ├── mcp_manager.py      # MCP registry/config/codegen helpers
│       ├── mcp_data_access.py  # Compact model-facing reads for the Gmail and Notion MCPs (§5.15a)
│       ├── whatsapp_mcp.py     # Long-lived bundled WhatsApp MCP/bridge lifecycle + compact calls
│       ├── whatsapp_contacts.py # The address book: name → JID, read from the bridge store (§5.2d)
│       └── kiki_self_extend_agent.py # Autonomous JSON-loop agent for installing skills/MCPs
│
├── robot/                      # §5.17
│   ├── face_handler.py         # Async listener/router for face + hand-gesture ZMQ events
│   ├── neck.py                 # <neck:left|right|center> expressive-gesture tags on the speaking path
│   ├── oled_tags.py            # <oled:…> expression-tag parsing/stripping (§5.18f)
│   └── stranger_enroll.py      # Stranger auto-enrollment brain (Kiki side of the face server)
├── hotwords/
│   ├── hotword_recog.py        # OpenWakeWord recognizer + pause/quiet-resume logic (§5.16)
│   └── kiki.onnx               # The "kiki" wake-word model, beside its only loader
├── webui/server.py             # Flask dashboard on :8090 — Controls/Sessions/Live Feed/Context (§5.23)
├── tools_and_config/
│   ├── config.json             # THE config: all tunables, prompts, personalities (§6)
│   ├── config_loader.py        # Loads .env + config.json once at import
│   ├── logger.py               # stdout/stderr tee → logs/kiki.log with rotation
│   ├── tools.py                # All tool implementations + OpenAI schemas + dispatch (§5.18)
│   └── vertex-service-account.json  # Vertex AI service-account key (never committed; optional, see §8.12)
├── scripts/                    # Operator tools — run by hand or at boot, never imported by main.py
│   ├── calibrate_lcd_sync.py   # LCD/audio word-timing calibration; run at boot by startup_config (§5.18d)
│   ├── streaming_tts.py        # Standalone gapless TTS lib + FILLERS list + filler wav generator (§5.19)
│   ├── bench_action_agent.py   # Provider/model benchmark behind the §5.2c latency table
│   ├── calibrate_auto_recall.py # Tune automatic memory recall against your own questions (§5.26)
│   └── power_blackbox.py       # Flight recorder for the Pi's power and thermal state (§5.18g)
├── sound_effects/
│   ├── sound_effects.py        # ThinkingSoundPlayer: one random filler wav while the LLM thinks
│   ├── soundeffects/fillers/   # Generated filler wavs (from scripts/streaming_tts.py)
│   └── audioeffects/           # Wake-word audio stingers (currently unused on the wake path)
├── state/                      # ALL runtime data the robot writes — see §7
│   ├── knowledge_base.json     # Long-term memory
│   ├── thinking_journal.json   # Dated Unified Idle Mind research + open questions
│   ├── idle_mind_state.json    # Scheduler, anti-repeat policy, next-turn note, WhatsApp cursor
│   ├── workers.json            # Persisted background workers
│   ├── care_plan.json          # Senior-care schedule, transcript and vitals history
│   ├── liked_songs.json        # Music likes/playlist
│   ├── ambient_listen_buffer.json # Capture-only ambient transcript buffer
│   ├── conversation_summary.txt   # Last session summary
│   ├── conversations/          # Per-session timestamped summaries + cached_past_summary.txt
│   ├── faces/                  # Enrolled/guest face thumbnails written by the face server
│   └── speeches/               # record_enabled wav archive (§5.5a) — unbounded, see §9
├── logs/                       # kiki.log (rotated at 5 MB), events.jsonl, whatsapp-*.log
├── tests/                      # 1048 tests. `pytest` or `pytest tests/` — see §5.20
├── tests_llamaserver/          # E2E tests against the LIVE box; not run by the ordinary suite (§5.20)
├── docs/                       # SIH engineering handoff + senior-care feature summary
├── skills/                     # Installed SKILL.md skills (injected into system context)
├── faces/                      # Enrolled/guest face thumbnails written by the face server
├── whatsapp-mcp/               # Bundled Go WhatsApp bridge + 12-tool Python FastMCP server
├── whisper.cpp/                # Checked-in whisper build used ONLY by offline Wi-Fi dictation (§3.0)
└── to_do/                      # PARKED code — see to_do/README.md. Nothing here is imported.
```

**Everything the robot writes lives under `state/` or `logs/`** — the repo root holds no
data files at all. Paths come from `config.json` (`knowledge_base.file_path`,
`idle_mind.state_file`/`journal_file`, `workers.persistence_file`,
`senior_mode.care_plan_file`, `agent.summary_file_path`/`conversations_folder_path`,
`always_listen_config.buffer_file`, `record_dir`, `face_events.auto_enroll.faces_dir`);
each module's fallback default now points at the same place, derived from `__file__` rather
than a hardcoded `/home/kiki/kiki2/KikiFast`, so a copy of the repo elsewhere still works.

**Module sizes** (`git ls-files '*.py' | xargs wc -l`, 2026-09-01) — useful for
judging where the weight is, and expected to drift:

| Module | Lines |
|---|---|
| `tools_and_config/tools.py` | 3506 |
| `main.py` | 3194 |
| `core/llm.py` | 2198 |
| `core/oled_display.py` | 1685 |
| `core/tts.py` | 1484 |
| `core/brain/unified_idle_mind.py` | 1374 |
| `core/senior/care_plan.py` | 1250 |
| `core/senior/care_voice_agent.py` | 1396 |
| `core/stt.py` | 948 |
| everything else | < 900 each |

---

## 3. Runtime Flows

### 3.0 System boot and offline Wi-Fi setup (`kiki_boot.py`)

`kikifast.service` runs `kiki_boot.py` in the Kiki venv. Before any laptop or Hailo
service is touched, the boot orchestrator checks `auto_start`; when false, it exits and
starts nothing. Otherwise, before Wi-Fi setup or any boot sound, it powers on Bluetooth,
connects the paired `bluetooth_speaker` (Clavier One; configured MAC first with paired-name
discovery as fallback), waits for its A2DP PulseAudio sink, and selects that sink as default.
It retries for `connect_timeout_seconds`; by default a powered-off speaker is logged but does
not prevent Kiki from booting (`required: false`).

Name discovery is deliberately loose — exact match, then substring, then best word overlap —
because a speaker can broadcast a name the config never learned (`Clavier One` in
`config.json` vs `Clavier Fusion` over the air). A loose match is logged.

When every ordinary reconnect fails, the orchestrator treats the **bond itself** as the
suspect and re-pairs (`_repair_bluetooth_pairing`). BlueZ can hold a link key the speaker
has forgotten — after a factory reset, or after it was paired to a phone. The ACL then still
completes (`Connected: yes`) while the speaker refuses the audio profile with
`br-connection-refused`, so no A2DP sink is ever created and a plain `connect` retries
forever. Recovery is `disconnect` → `remove` → **scan again** → `pair` → `trust` → `connect`,
followed by a second connect loop capped at 20s. The order is load-bearing in both
directions:

- The scan **after** `remove` is mandatory. `remove` drops the device from BlueZ entirely,
  and `pair` answers `Device … not available` until a fresh inquiry rediscovers it.
- The bond is only deleted once the speaker has proven it is switched on. Presence comes
  from `_bluetooth_connect`, which reads the failure text: everything except a page timeout
  or "not available" means something answered. RSSI is only a backstop, because a bonded
  BR/EDR speaker stops answering inquiry and would otherwise look absent every time.
  A speaker that is merely powered off keeps its pairing — rebuilding one needs somebody
  standing next to it holding the pairing button.

A successful re-pair at a new address updates `bluetooth_speaker.mac` in memory and writes
it back to `config.json` (`_persist_speaker_mac`), so the next boot finds the speaker first.
Set `bluetooth_speaker.repair_pairing: false` to disable the whole recovery, or
`repair_scan_seconds` (default 12) to change the inquiry window.

After Bluetooth audio is ready, `core/startup_config.py` briefly takes ownership of
GPIO22/17 and offers `Edit config?` on the LCD. Tap LEFT/RIGHT to navigate and hold either
sensor to select; each recognized action plays the short `startup_config_beep.wav` feedback
sound. Selecting No continues immediately, and no input auto-selects No after 20 seconds
so unattended boots cannot stall. Selecting Yes walks through Wi-Fi
(`Keep current`/`Change WiFi`), speaking brain (`local`/`cerebras`), Bluetooth volume
(tap LEFT +10%, tap RIGHT -10%, hold to confirm), language (`English`/`Hindi`), and every
configured `assistant_modes` startup mode, then atomically saves `config.json` and continues
boot. The confirmed volume is applied live and saved as
`bluetooth_speaker.volume_percent` for future startups. English does not alter prompts;
Hindi saves `assistant_modes.language_on_startup = "hindi"` and appends
`Always output hindi devnagri.` to the effective system prompt for every mode, including
after runtime mode switches.
After the startup mode, the wizard asks `Sync LCD+audio?` — the LCD/audio word-timing
calibration (§5.18d). It is the last question because every earlier choice can invalidate
the saved profile (a different speaker, TTS server, or mode voice), and it preselects
**Yes** whenever `startup_config.calibration_status()` finds the stored profile unusable,
so the ordinary "my speaker changed" case is a single hold. The answer is **not** written
to `config.json`: it is a one-boot request returned alongside the config by
`offer_startup_config()`, because the calibration cannot run here — the TTS and Whisper
servers it needs are only started later. The boot orchestrator therefore defers it to step
10, after all four services are live: it stops the looping boot sound first (the script
measures real speaker→microphone latency, so any other audio would corrupt it), runs
`scripts/calibrate_lcd_sync.py` in the venv via `run_lcd_sync_calibration()`, and streams the
script's progress lines onto the LCD (`Calibrating.../<voice>`, `Speaker delay`,
`Checking sync`). main.py is not running yet, so nothing else holds the microphone.
Calibration is never a boot gate: a missing script, a crash, a validation failure, or the
900s timeout (`tts.display_sync.calibration_timeout_seconds`) all log, show
`Calib failed / using old sync`, and continue to `main.py` with the previous profile
intact.

The Wi-Fi selector remembers the exact connection active on entry and restores it when a
change is canceled or fails. The feedback beep is scoped to this startup wizard (including
its Wi-Fi subflow); normal runtime IR gestures remain silent.

After the wizard, the orchestrator checks for a connected Wi-Fi device and gives
NetworkManager `wifi_setup.connection_grace_seconds` to reconnect a saved network
automatically. If still offline, `core/wifi_setup.py` takes temporary ownership of
GPIO22/17 and the LCD:

```
nmcli scan → strongest SSID on LCD
  tap LEFT / RIGHT → next / previous SSID
  hold either      → select
    saved/open network → connect directly
    secured network    → password screen
      hold LEFT + speak characters + release → local whisper-cli → parse → nmcli --ask
      tap RIGHT                             → backspace
      hold RIGHT                            → clear
      hold BOTH                             → return to SSID list
```

`wifi_setup.preferred_ssids` is probed with directed NetworkManager scans and remains
selectable at the end of the LCD list when absent from ordinary beacon results. This
supports idle mobile hotspots and hidden SSIDs such as `Chalja`; their password connection
uses NetworkManager's `hidden yes` mode. A failed attempt still restores the exact Wi-Fi
profile that was active before the startup selector opened. A missing hotspot returns to
the network list with `Hotspot not found` instead of presenting the failure as a bad
password or continuously polling NetworkManager.

The laptop Whisper server cannot be reached before Wi-Fi exists, so this path records
16 kHz PCM from the configured STT device and runs the checked-in
`whisper.cpp/build/bin/whisper-cli` with `models/ggml-base.en.bin`. Dictation accepts forms
such as `k i k i`, `capital k i k i`, spoken `clear`, and spoken `backspace`. Whisper may
join spelled letters into a word; the password parser splits that word back into
characters. The LCD shows the entered password directly through `lcd.write_raw` (so special
characters are not stripped), using both rows and retaining the newest 32 characters if it
exceeds the panel. Logs never contain the transcript/password, and `nmcli --ask` receives
the password over stdin rather than argv.

Once connected, the GPIO lines and microphone are released, the WhatsApp Go bridge is
started at low priority without waiting, then the existing critical sequence continues:
start the looping boot sound → POST the laptop manager `/restart` → restart
`hailo_follower.service` → wait for llama/TTS/Whisper/Hailo → stop sound → exec `main.py`.
Bridge compilation/reconnect overlaps the laptop model-load window and is never a boot gate.
The `--force` settings-menu restart uses this same Wi-Fi gate while ignoring `auto_start`.

### 3.1 A normal speaking turn
```
"kiki" → HotwordRecognizer (hotwords/hotword_recog.py, own thread)
  → main.py hotword handler:  stt.unmute() FIRST (zero added latency)
                              → set_neck_active(True) (daemon thread)
                              → local_llm.preempt_background()  (non-blocking; kills any bg prefill)
                              → idle_mgr.interrupt()/mark_activity()  (marks conversation HOT)
user speech → STTEngine (Deepgram Flux) → ("final", text) events → stt_queue
"endpoint" event → main loop:
  mute mic, pause hotword recognizer, start ThinkingSoundPlayer (one filler wav)
  append context (time every 5 min, pending vision, one Unified Idle Mind note)
  append {"role":"user", ...}
  stream_response(message_history)            # core/llm.py
    └─ _stream_local(): note_user_activity + note_speaking(True) + preempt, then SSE POST
       sentences yielded as they complete → TTSStreamer.add_sentence()
       <tool_call>{...}</tool_call> in stream → parsed → tool runs in bg thread
         (canned TOOL_FILLER spoken if nothing said yet) → result appended as system note
         → followup_llm_and_tts() streams the answer onto the SAME TTS streamer
  tts_streamer.finish() blocks until audio drains → recognizer.request_resume()
  append {"role":"assistant", clean_response}
  register_history(message_history)           # ← registers + rewarms EXACT prefix (see §4)
  fire after_response workers, maybe vision update, token count, maybe auto-summarize
  unmute after post_speech_unmute_delay_seconds (0.35s); 15s of silence → mute (hotword mode)
```

Clear settings utterances are routed before generation: “switch to funny mode” calls
`switch_mode`, volume requests call `adjust_volume`, and follow-up requests call
`set_followups`. A mode switch applies its configured TTS voice immediately, replaces the
first system message only after the acknowledgement finishes, then explicitly re-warms the
new prompt. Disabling follow-ups closes the query window after every answer and forces true
hotword-only listening even when `always_listen` is configured.

### 3.2 Startup sequence (`main.py main()` top half)
1. `setup_logging()` before any other import — every print everywhere is tee'd to the log.
2. Eager module imports (~2s) — each module pre-initializes at import time.
3. Build context: KB summary + latest conversation + skills summary → `message_history[0..1]`.
4. `load_past_summary_bg()` task — loads `conversations/cached_past_summary.txt` with
   `prefer_cache=True` (instant, even if stale) and splices into `message_history[1]`.
5. `refresh_past_summary_cache_bg()` task — 3 min later regenerates the cache **for the
   next boot only** (never touches the live context).
6. STT engine + hotword recognizer + worker manager start; `fire_event("startup")` workers run.
7. `warmup_when_context_ready()` task: awaits the summary splice (≤180s), bakes pending
   Unified Idle Mind next-turn note into the prefix, then runs the ONE startup warmup
   (`llm_warmup(message_history)`) — full prefill on the box (~15–60s, in background).
8. Unified Idle Mind monitor, non-blocking WhatsApp MCP daemon, face listener,
   periodic-question loop, peeping loop, STT thread start.
9. Main `while True` event loop over `stt_queue`.

### 3.3 Unified Idle Mind
```
main.py post-turn → idle_mgr.note_turn(user, assistant)
15s monitor tick → meaningful conversation lull OR model-selected next-check time
                  OR debounced new WhatsApp messages
  → build one cloud prompt from new turns, ambient snapshot, KB, recent journal,
    open questions, current note, recent intents, and untrusted WhatsApp updates
  → model chooses no_action | reflect | light_research | deep_research
  → shared agent loop executes bounded, validated tools
  → deliberate writes: knowledge, dated research, open questions, one next-turn note
  → model chooses the next check (30–360 minutes) or waits for a new conversation
```

The agent never uses the local speaking slot. User speech does not cancel its cloud
request; resource-conflicting actions queue until the conversation is no longer hot.
Physical movement and tracking are blocked. Exact and near-duplicate tool intents are
rejected using the last twelve completed sessions. Light research permits two
investigative calls, deep research permits six, and action/persistence budgets prevent
runaway loops.

WhatsApp is polled locally through the long-lived MCP session; polling itself uses no
cloud model. New incoming messages are debounced and passed to the next Unified Idle Mind
session as untrusted private content. The model may deliberately save durable personal
facts with `update_knowledge` and select one time-sensitive event with
`set_next_turn_note`. A note is **typed sourced context**, not loose prose: it persists
`source` + `reason` + creation time, and its injected row preserves that provenance as
`VERIFIED CURRENT CONTEXT`. For a fresh (default <=180 min), read-only status question,
`core/llm.py::_direct_sourced_context_reply()` speaks the already voice-ready note directly;
the small speaking model is not asked to rediscover or reclassify a fact the cloud idle mind
already selected. The gate requires that exact note ID to exist in this conversation, rejects
stale/internal-instruction notes and explicit source mismatches, and never intercepts actions or
explicit refreshes. A legacy/unknown note therefore cannot answer a WhatsApp-specific question;
that falls through to the live agent. The usual
post-turn overlap check marks it used, so speculative turns remain side-effect-free until adopted.
It must never autonomously reply: sends require a recent explicit
request from Vaibhav or a configured worker. WhatsApp sends/media actions requested by
the idle mind are treated as resource-conflicting and queue while conversation is hot.

### 3.4 Ambient listening

`AmbientListeningManager` only stores finalized passive STT sentences in
`ambient_listen_buffer.json`. It has no timer, cloud model, journal writer, knowledge
writer, or prompt injector. Unified Idle Mind snapshots up to 24 buffered snippets and
consumes those IDs only after a successful session.

### 3.5 Background work vs. the speaking slot

Unified Idle Mind is cloud-only. Shared summary/vision routes may use
`local_llm.generate_background()`, which is refused while conversation is hot,
preemptible by socket shutdown, and followed by speaking-prefix rewarm. The single local
slot therefore remains owned by foreground speech.

### 3.6 Always-listen capture boundary

When `always_listen: true`, STT remains in ambient capture mode while Kiki is idle.
The wake word or IR input switches STT to query mode immediately; no cloud flush occurs
on that path. Kiki's own TTS is excluded because microphone capture and hotword inference
remain paused while output audio is active.
---

## 4. The KV-Cache Contract (read this before touching message_history)

gemma-4 is an **SWA model** (sliding-window attention, n_swa=1024). On the single slot,
any byte-level divergence between the cached prompt and a new request forces re-prefill
from the divergence point — and if no context checkpoint covers that point, **full**
re-processing (~40–60s). Four hard rules keep turns at ~1–2s TTFT:

1. **Never mutate an existing message in `message_history` mid-session.** Appending new
   messages extends the cached prefix; editing `message_history[1]` invalidates everything
   after it. (The old code rewrote msg[1] every 5 minutes — that was the "context
   invalidated every 4-5 turns" bug.) Workers context is now append-only-on-change
   (the `_last_workers_context` guard now lives on `UnifiedIdleMindManager`,
   `core/brain/unified_idle_mind.py` @719, and is applied by `maybe_inject_time()`
   @860 — main.py only calls that one entry point).
2. **Store the assistant reply VERBATIM, and rewarm from that same history.** The box's
   KV cache holds the reply *exactly as generated* — `<neck:…>`, `<oled:…>` and `<tool_call>…</tool_call>`
   tags included. So `message_history` must store the reply verbatim (tags and all), or the
   resent history diverges from the cache right where the first tag was and forces a
   re-prefill. main.py captures the raw `done` text (`raw_first` / `raw_followup`) and
   appends THAT; the tag-stripping for TTS/logging happens on a separate `clean_response`
   copy. For a **tool turn** the order in history must mirror the slot:
   `assistant(first_gen with <tool_call>)` → `system(result note)` → `assistant(follow-up)`
   — the pre-tool assistant message is appended *before* the result note (omitting it
   diverged the cache for both the follow-up request and the next turn). If the follow-up
   itself emits a `<tool_call>` (§5.1a) the same three rows repeat, with the follow-up
   generation now playing the "first_gen" role. `register_history()`
   derives the prefix from `message_history` itself and is called *after* these appends; do
   not re-introduce prefix registration inside the streaming path.
3. **One warmup at startup, after the context is final.** The past summary loads from
   cache instantly (`prefer_cache=True`) and talking points are baked in pre-warmup
   precisely so the warmup sees the final prefix. Competing warmups invalidate each other.
4. **Background prompts evict the speaking prefix.** That's why every background request
   auto-rewarms afterwards, why rewarms are hash-deduped (`_warm_prefix_hash`), and why
   the conversation-hot window reroutes background work to the cloud.

Verified end-to-end by `tests_llamaserver/test_prefill_e2e.py` (cold 24s → warm 1.26s;
preempt 0 ms, mid-prefill abort; mutation demo). Run it whenever you touch this machinery.

---

## 5. File-by-File Deep Dive

### 5.1 `main.py` — Orchestrator

> Anchors are `symbol @line`, re-derived 2026-09-01. **Trust the symbol, not the
> number** — the line is a hint for jumping, and `grep -n "def <symbol>" main.py`
> is always authoritative. (The previous version of this table was anchored to a
> 1063-line `main.py`; the file is now 3194 lines and every number in it was
> wrong by roughly 3×.)

**Module level**

| Symbol / span | What it does |
|---|---|
| lines 1–23 | Module docstring: the 11-step turn flow. |
| `setup_logging()` @40 | Installed **before all other imports** so every module's prints land in `logs/kiki.log`. |
| lines 26–110 | Eager imports of every subsystem — each pre-initializes at import for latency (§8.10); timing print at the end. |
| `active_tts_streamer` @104 | Global handle so the hotword thread can abort playback mid-sentence. |
| `TOOL_FILLERS` @110 | Canned spoken lines for tool calls that would otherwise be silent. |
| `tool_exec_timeout` @139 | Longest configured wait among the tools about to run. |
| `tool_result_note` @149 | Builds the system note that turns a tool result into the spoken answer. Splits on `runtime_controls.mode_has_own_character()` — see §5.1a case 3. |
| `deterministic_care_plan_failure_reply` @216 | A care-plan failure gets a fixed spoken line rather than a model guess. |
| `is_direct_care_complex_call` @239 | True when a care `complex_query` summary is already voice-ready, so the turn skips `followup_llm_and_tts` (§5.2c, *Care result fast path*). |
| `is_successful_care_session_handoff` @261 | Distinguishes a real handoff to the care voice agent from a failed one. |
| `is_speakable_reply` @286 | Rejects text that must never reach TTS (empty, JSON, internal markers). |
| `direct_complex_reply` @313 | Extracts the voice-ready string from a `complex_query` result. |
| `queue_voice_ready_text` @333 | Pushes already-final text straight onto the active `TTSStreamer`. |
| `drain_stale_input_events` @355 | Empties `event_queue` of anything captured while Kiki was speaking. |
| `should_continue_care_without_listening` @377 | Keeps a guided care session advancing when the mic stays closed. |
| `build_summary_input` @402 | Assembles the history slice the summarizer sees. |
| `pick_tool_fillers` @427 | Chooses which canned filler matches the tools about to run. |
| `set_neck_active` @461 | Fire-and-forget neck-tracking power toggle via KikiController in a daemon thread — must never block the wake path (as `set_motor_relay` it used to block ~5 s when the controller was down). Renamed from `set_motor_relay` in the neck-only refactor. |
| `SpeculativeTurn` @491 | Speculative pre-generation (`_run` @517, `abort` @553). Gated on `speaking_is_local()` — never runs on a cloud provider (§5.2b). |
| `stt_stream_worker` @572 | Bridges the blocking STT generator (its own thread) into the asyncio `event_queue`. |

**`main()` @585 — setup**

| Anchor | What it does |
|---|---|
| `get_tts_system_prompt_note()` @594 | Voice-tag restriction appended to the system prompt **before** warmup, so the cached prefix matches. |
| @614–670 | STT/async state, then peeping (@648) and vision-injection (@662) config. |
| @672–730 | Context build: KB summary, latest conversation file, skills (@707) → `additional_context` @697 → `message_history = [sys_prompt, sys_context]` @727. msg[0] content is a list-of-parts (`{"type":"text",...}`); msg[1] is a plain string. |
| `sync_mode_prompt` @742 | Applies a pending mode change to msg[0] and re-warms (`register_history` @795). |
| @798 | Session-start time anchor, baked into the prefix (append-only, KV-safe). |
| `load_past_summary_bg` @821 | Loads the past-conversations summary with `prefer_cache=True` (0.002 s from cache) and splices it into `message_history[1]` **before** warmup awaits it. |
| `refresh_past_summary_cache_bg` @856 | Sleeps 180 s, then regenerates the cache with `force_refresh=True` for the NEXT startup; retries (box may be hot); never touches the live context. |
| @879–895 | `ThinkingSoundPlayer` init, `stt.mute()`, `HotwordRecognizer` @888 (device_index from `stt.device_index`). |
| `return_to_background_listening` @919 | The single path back to idle: mute, resume hotword, drop the neck relay. |
| `mute_stt` @934 / `reset_mute_timer` @956 / `cancel_mute_timer` @965 | 15-second silence timer → back to hotword-only mode. |
| `open_listen_window` @973 / `extend_listen_window` @979 | The 15 s timer is reset by the STT `interim` heartbeat so a long sentence isn't cut off mid-thought — but in a crowded room that heartbeat never stops, so the timer was reset forever and Kiki listened indefinitely. `open_listen_window()` stamps a fresh budget on real progress (wake word, IR hold release, committed `final`, follow-up unmute); `extend_listen_window()` is what `interim` calls and stops resetting the timer past `stt.max_listen_window_s` (45 s; `0`/absent disables the cap), letting the ordinary silence timer close the window. |
| `is_idle_mind_active` @1002 | Late-bound checker (idle_mgr is created further down) used by the vision and peeping loops. |
| `hotword_thread_func` @1006 | THE wake path. Order is load-bearing: **unmute STT first**, then the neck relay, then `preempt_background()` @1035 (non-blocking), then idle interrupt/mark_activity. Also handles `stop_music`/`stop_it` → `pkill mpv` + `active_tts_streamer.abort()`. |
| @1062–1084 | Vision/autonomy state; `VisionHandler` @1063; `_vision_history_inject` @1066. |
| @1085–1158 | WorkerManager + scheduler (@1085), senior-care→workers bridge (@1088), environment poller (@1127), auto-recall index build (@1140). |
| `fire_event("startup")` @1160 | Startup workers run and may inject context. |
| `warmup_when_context_ready` @1170 | Awaits the past-summary task (≤180 s) → bakes the pending next-turn note and the time anchor into the prefix (`maybe_inject_time` @1194) → the ONE `llm_warmup(message_history)` in an executor. |
| @1206–1245 | `UnifiedIdleMindManager` @1207 + monitor; `_start_whatsapp_off_path` @1216 (non-blocking); Web UI on :8090 @1228. |
| @1247–1340 | IR controls: `ir_talk_hold_start` @1254, `ir_talk_hold_end` @1297, `ir_enter_settings` @1310, `recognizer.pause()` @1319 / `request_resume()` @1325, `ir_return_to_idle` @1330. |
| `handle_hand_gesture` @1382 | The controller SUB task routes debounced `hand_gesture` events straight here: `mute` toggles LCD-only output, `open_palm` aborts current output/generation/activity (`stop_audio_processes` @1386), `peace` uses the established idle cleanup path, `thumbs_up` ends listening (clears any IR hold, then `stt.commit_now()` → immediate `final`+`endpoint`). No gesture polling or inference runs in the speaking loop. |
| @1507–1585 | `face_event_listener` task @1507; CLIP senior-care activity events @1517 (`_care_inject` @1529, `_care_speak` @1532, `_care_scene` @1554) and the `autonomous_vision` event @1539. |
| `periodic_question_loop` @1591 | On its interval, vision asks for a source-grounded proactive prompt. Only a meaningful live scene and the one active next-turn note qualify; otherwise no autonomous event is queued. |
| `peeping_loop` @1621 | Every `peeping.interval_seconds` (1200): unmute 10 s, collect ambient speech, inject as a `[Peeping…]` system message. Skips during Unified Idle Mind / active conversation. |
| @1673–1690 | STT thread start; `collected_sentences` @1673; `_prewarm_tts_cache_bg` @1688. |
| `trigger_background_summary` @1699 | `token_counter.count_tokens` @1724 vs `agent.token_limit` @1711 (6000). Over limit → `summarize_task` @1721: summarize on the local box (abortable), save, then rebuild history as `[sys_prompt, summary]` **mutating in place** (`message_history[:] = new_history` @1800 — other modules hold references, §8.5) and `register_history` to pre-warm the new short prefix. |

**`main()` — the turn loop @1833**

| Anchor | What it does |
|---|---|
| @1833 | `while True:` over `event_queue` with a 0.5 s timeout. `"final"` → collect (or route to the peep buffer); idle interrupt/mark_activity; reset the mute timer. Always-listen idle events enter the capture-only ambient buffer instead; wake/IR switches STT directly to query mode. |
| @1901 | Between-turn `idle_mgr.maybe_inject_time(rewarm=False)`. |
| `endpoint`/`autonomous_vision`/`face_wake` branch | Cancel mute timer → `preempt_background()` → mute mic → `recognizer.pause()` (so Kiki can't hear itself) → `sfx.start()` @2054 (one filler wav). |
| Context injection @2056 | Time anchor + changed workers context. **The single source of truth is `idle_mgr.maybe_inject_time()`** (`core/brain/unified_idle_mind.py` @860) — the idle thread pre-injects and re-warms this between turns, so on most turns this call is a no-op via the shared `_last_time_injected` gate. Append-only; see §4 rule 1. `CARE NOW` follows, injected only when it changed (§5.25a). |
| TTS setup @2342 | `stop_sfx_on_first_play` thread waits on `first_play_event` @2343 to kill the filler sound exactly when real speech begins. |
| `llm_and_tts` @2413 | Runs `stream_response` in an executor: `"sentence"` → strip movement/neck/oled tags → `add_sentence(clean)`; `"tool_calls"` → start `run_tools_bg` @2456 immediately and speak a canned filler if nothing was said yet; `"done"` → strip `<tool_call>` XML into `clean_response`, keeping the verbatim `raw_first` @2400 for history. |
| `followup_llm_and_tts` @2499 | Streams the post-tool answer onto the SAME streamer, keeping `raw_followup` verbatim. |
| Tool rounds @2652 | `while pending_tool_calls:` (@2407) — wait for `tool_result_ready` (@2410, abort-aware and bounded), append the **verbatim generation that requested the tool** (with `<tool_call>`) as an assistant message, then the result as a `tool_result_note()` @2715 system note, then `followup_llm_and_tts()` streams the answer onto the same streamer (filler + answer play back-to-back). The pre-tool assistant append keeps history aligned with the box's KV cache (§4 rule 2). A `<tool_call>` in the follow-up starts another round, bounded by `llm.tool_calling.max_followup_tool_rounds` — see §5.1a. |
| Drain + resume | `tts_streamer.finish()` blocks until playback drains; `finally:` always calls `recognizer.request_resume()` (which resumes only once the room is acoustically quiet). |
| Post-turn @2897 | Append the assistant message **verbatim** (`raw_first`/`raw_followup`, tags included — §4 rule 2) while keeping the tag-stripped `clean_response` for logging/notes → apply any pending mode prompt change → `register_history(message_history)` → execute movements in a thread → `idle_mgr.note_turn(user, assistant)` @2897 → maybe vision update → `fire_event("after_response")` @2972. |
| Follow-up @3003 | `should_skip_followup()` (music) or runtime follow-ups disabled → straight back to hotword mode; otherwise wait `post_speech_unmute_delay_seconds` (0.35 s), drain stale queue events, unmute, arm the 15 s timer. |
| `finally:` @3108 | Cancel tasks, `idle_mgr.stop()`, preempt, `fire_event("shutdown")` @3137, stop the scheduler, generate + save the session summary (Ctrl+C again skips to a raw save), `stt.stop()`. |

### 5.1a Three ways one turn lost a tool call (2026-07-29)

One live session produced all three, and each had a different owner. They are
grouped here because the visible symptom was identical every time: Kiki says
something that sounds like the action happened, and nothing happened.

**1. The schema gate rejected a call the handler would have run.**
`tools.validate_tool_arguments` type-checked with bare `isinstance`, so
`adjust_volume({"action":"set","amount":"60"})` — a JSON *string* — was refused
with `amount must be integer`, even though `adjust_volume` opens with
`int(amount)`. The rejection returns *before* `get_recorder().span(...)` in
`execute_tool`, so a refused call leaves **no tool event in `events.jsonl`** at
all while `kiki.log` still prints `[Tool] Executing` — that asymmetry is how you
identify one. The gate now **coerces scalars in place** (`_coerce_scalar`) for
lossless string→int/number/bool conversions only: `"60"` and `"60.0"` pass,
`"sixty"` and `"60%"` still fail, and **enums are never coerced** because a
category outside the allowed set is a real mistake whose error text is what
teaches the model the right value.

*Why the model quoted it:* `_get_tools_instruction` printed signatures without
types and its only format example was `{"param_name": "value"}` — every value
the model had ever been shown there was a quoted string. Non-string params now
carry a short type (`amount?:int`) and the example includes one unquoted number.

**2. The model's own retry was parsed, logged and dropped.**
The follow-up generation (the one that turns a tool result into speech) can emit
a `<tool_call>` — here it re-sent `adjust_volume` with a correct unquoted `60`.
`followup_llm_and_tts` handled only `sentence`/`done`, so the fix was discarded:
Kiki said *"Wait, did I mess that up? Let me try that again properly"* and the
volume never moved. The tool block in main.py is now a `while` loop over tool
**rounds**, bounded by `llm.tool_calling.max_followup_tool_rounds` (1). Each
round appends the requesting generation verbatim before its result note, so the
history order still mirrors the box's KV cache (§4 rule 2). The bridge connector
is round-0 only (a second one mid-answer sounds like a stutter). Raising the
bound to chain tools is the wrong instinct — that is `complex_query`'s job
(§5.2c) — and every extra round holds the turn open, which holds the wake word
closed.

**3. `tool_result_note` taught the next turn to answer without calling.**
`704acd8` softened this note to *"Answer in YOUR OWN VOICE, fully in character"*
to stop service-desk register flattening a 253-char roleplay prompt. That is
right for a character mode and wrong for `default`, whose own 7.9k prompt
already carries the voice: the note is stored in history, so the prose answer it
produced became the nearest precedent, and two identical repeat requests were
answered *"Playing Maafi again"* with no tool call either time. The note now
splits on `runtime_controls.mode_has_own_character()` — the in-character wording
for modes that declare their own `system_prompt`, the directive wording for
`default` — and **both** forms end by saying a repeat request needs its own call.
The `complex_query` form is unchanged.

Diagnostic note: `⚠️ Tool execution timed out after 15.0s` used to print for an
IR/gesture abort too (`_wait_tool_or_abort` returns `False` for both), so a 1.7s
open-palm barge-in read as a slow tool. The two exits now log differently.

Regression tests: `tests/test_tool_call_recovery.py`.

### 5.2 `core/llm.py` — Speaking path

- **Import-time** (1–102): loads `.env`, reads the Vertex service-account key json (`_VERTEX_SA_FILE` → `vertex_credentials_json`; auth matches `apiusage.py`),
  caches `llm` config, **lazy litellm** (`_get_completion` @33 — importing litellm eagerly cost
  seconds on the Pi). Constants: `_USE_LOCAL`, `_LOCAL_URL`, `_MAIN_TOOL_NAMES` (the curated
  tool subset exposed to the fast model), `_SENTENCE_RE` (split after `.!?`), `_THINK_RE` +
  `_scrub_reasoning` @92 (defensive scrub of leaked reasoning markers before TTS),
  `_CLAUSE_RE` + `_FIRST_FLUSH_MIN_CHARS=18` (eager first-clause flush so TTS starts sooner).
- `_extract_sentences` @105 — splits buffer into complete sentences + remainder.
  Hindi Devanagari (`।॥`) terminators flush immediately at the current end of the
  stream, so Hindi replies do not accumulate into one large TTS request; Latin (`.!?`)
  retains its whitespace guard to avoid splitting streamed decimals/abbreviations.
- `_get_tools_instruction` @119 — builds the `<tool_call>{json}</tool_call>` protocol text from
  `_MAIN_TOOLS` schemas; injected into the FIRST system message by the normalizer (stable
  across turns → cache-safe).
- `_normalize_messages_for_local` @150 — **the canonical normalization**: flattens
  list-of-parts content to plain strings, drops `tool` role messages and empty content,
  injects the tools instruction. Byte-for-byte stability of its output across turns is what
  makes `--cache-prompt` hit. Anything that builds a prefix for the box must go through it.
- `warmup` @197 — registers the normalized prefix + synchronous `rewarm()`; called once at
  startup by `warmup_when_context_ready`.
- `_stream_local` @228 — speaking entry: `note_user_activity()` (marks hot) →
  `note_speaking(True)` → `preempt_background()` → delegates to `_stream_local_inner`;
  `finally:` only `note_speaking(False)`. **Deliberately does NOT register the prefix** (§4 rule 2).
- `register_history` @257 — public; normalize live history → `update_speaking_prefix` →
  `schedule_rewarm`. Called by main.py after the assistant append and after summarization.
- `_stream_local_inner` @278 — raw SSE loop: posts with `cache_prompt: true` and
  `thinking_budget_tokens: 0` (REQUIRED — without it gemma thinks on every voice turn since
  the server runs without `--reasoning-budget`). Char-by-char scanner detects
  `<tool_call>...</tool_call>` mid-stream → yields `("tool_calls", ...)`; otherwise eager
  first-clause flush then sentence extraction → `("sentence", s)`; ends with `("done", full)`.
  Raises `_LocalUnavailable` only if the connection fails before any token (clean fallback).
- `execute_tool_calls` @417 — runs parsed calls via `tools.execute_tool`, caps each result at
  1500 chars (keeps the follow-up prefill small).
- `stream_response` @455 — top-level: local path first; `_LocalUnavailable` → cloud fallback
  via litellm (`_FALLBACK_MODEL`), with a content-timeout fallback chain, native tool_calls
  handling and recursive follow-up. The cloud path is the emergency path only.

### 5.2a `core/vision/instant_vision.py` — Live-image path (Groq Qwen VLM)

The local speaking box is **blind** on the speaking path (image parts are dropped by
`_normalize_messages_for_local`). So a genuinely visual question — "does my shirt look
good?", "look at this phone, should I buy it?", "describe what you see" — is routed to
Groq's multimodal `qwen/qwen3.6-27b`: a fresh camera frame is captured and the answer is
**streamed** to TTS, box untouched. Config: `llm.instant_vision`.

**Two triggers, one Groq path:**
1. **Regex fast-path** (`is_instant_image_query`) — a high-precision regex on the user
   utterance. When it matches, `stream_response` routes to Groq BEFORE touching the box →
   lowest latency (no box round-trip). Keyed on visual verbs / "what do you see" /
   appearance judgements / "should I buy THIS" / "read this".
2. **`look_at_scene` tool call** (the smart-model safety net for regex misses) — the tool
   is in the speaking model's catalog (`llm.main_tools`, schema+no-op handler in `tools.py`);
   when the local model recognises a vision question mid-stream it emits
   `<tool_call>{"name":"look_at_scene"}</tool_call>`. `_stream_local_inner` intercepts it
   (sets `vision_switch["on"]`, ends the local stream with NO "done"), and `stream_response`
   hands the turn to Groq — **transparently**, so `main.py`/`llm_and_tts` just see sentences +
   done like any other turn (the still-playing thinking sound covers the box→Groq gap).

- **8K TPM cap**: qwen3.6-27b is rate-limited to 8K tokens/min, so `build_capped_messages`
  keeps the whole request under it — personality system prompt + current question (with the
  image attached as a `data:image/jpeg;base64` url) are always kept; older history/summary is
  added newest-first only while it fits `max_context_tokens` (5000); the reply is bounded by
  `max_completion_tokens` (900). Text tokens are counted via `token_counter`.
- **Key source**: `GROQ_API_KEY_LIST` in `.env` (JSON array, tried in order → a bad/throttled
  key rolls to the next; falls back to single `GROQ_API_KEY`). Same pool `generate_llm_resp` uses.
  The keys are **separate orgs with independent 8K TPM budgets**, so rotation genuinely
  multiplies the budget — rotating is the recovery, never waiting.
- **`max_retries=0` is load-bearing** (measured). The Groq SDK defaults to **2 retries and
  SLEEPS for the server's `retry-after` on a 429** — observed 11s, 18s, 40s. That sleep, on the
  same already-exhausted key, was a real **50s time-to-first-word** in production. Failing fast
  + rotating gives 1-2s under the same conditions, and ~3s to exhaust every key and fall back to
  the local box. 401 keys are remembered in `_DEAD_KEYS` and skipped for the session; a 20s
  request timeout prevents a stalled socket from hanging the turn.
- **Token budget reality** (measured, not estimated): the **image costs a flat 786 prompt tokens
  at any resolution** — Qwen normalizes it, so downscaling saves upload time but *zero* tokens.
  The **text context is what exhausts the 8K/min cap**, which is why `max_context_tokens` is
  small (1800) here: the current question + personality is enough to answer "what do you see".
- **Reasoning**: this model rejects `reasoning_effort` of `low`/`high` (only `none`/`default`);
  `none` disables thinking for lowest latency. `<think>…</think>` spans are still stripped from
  the stream defensively (`_think_filter`, tag-split-safe across SSE deltas). `_create_stream`
  retries without optional kwargs on a 400, but re-raises auth/rate errors so key rotation fires.
- **Wiring** (`core/llm.py`): the regex fast-path fires BEFORE the local path; the tool-call
  path fires from WITHIN the local stream (`vision_switch`). Both only on the PRIMARY turn
  (`verify_prefill and not use_fallback and not local_only`). On any pre-first-token failure
  (no frame / all keys down) the regex path raises `_InstantVisionUnavailable` and falls through
  to the (blind) local path; the tool-call path speaks a short "can't see clearly" line.
  `abort_event` (IR barge-in) cuts the Groq stream mid-flight.
- **Speculative turns**: a spec pre-gen runs blind on the box (`local_only`). If the model emits
  `look_at_scene` there, `stream_response` yields a `("vision_requested", None)` event (it does
  NOT fire the camera/Groq speculatively); `SpeculativeTurn` records `result["vision"]=True` and
  main.py **refuses to adopt** it (also refuses when the regex flags the utterance) — the real
  turn re-runs and routes to Groq.
- **KV-cache correctness**: a Groq turn never touches the box, so `last_turn_used_instant_vision()`
  tells main.py to do a **real** rewarm afterwards (`after_speaking=False`) instead of the usual
  `--prefill-after-response` shortcut (which would mark a stale prefix "warm"). The image is never
  stored in `message_history` — only the text reply — and for a tool-call turn the model's
  lead-in + `<tool_call>` text is NOT stored either (only the Groq answer), so the real rewarm
  re-prefills the correct prefix and the next local turn stays a cache hit.

### 5.2b Speaking provider toggle — `llm.speaking_provider` (`local` | `cerebras` | `groq` | `openrouter`)

**`cerebras` — the current default.** `gemma-4-31b` on Cerebras' OpenAI-compatible endpoint
DIRECTLY (`_stream_cerebras_speaking`), streamed with raw `requests` SSE over the shared
keep-alive session — the same lean path as `_stream_local_inner`, no SDK layer. **Measured
0.56–0.89s to first token**, whole reply in one burst. Going direct is what made this viable:
the same model *via OpenRouter* was stuck behind a shared pool returning 429 every ~60s.

**Image-cost guard (`llm.cerebras_speaking`)**: images are NEVER sent through the ordinary
speaking function —
`_normalize_messages_for_local` drops image parts, and periodic scene context arrives as
Gemini-written TEXT. Pictures leave only on an explicit `look_at_scene` tool call, served by
`instant_vision` on Groq. Each response prints `prompt/completion/image` token counts, so
`image=0` is verifiable per turn rather than assumed. Combined with speculative turns being
off for cloud providers, **one ordinary spoken turn == exactly one billed request.** The separate
foreground care agent is the deliberate exception: when its event enables continuous vision,
it sends one fresh JPEG to the same Cerebras/Gemma request that creates that care reply.


All cloud providers share ONE protocol implementation, `_scan_cloud_deltas` — it turns a raw
content-delta iterator into the same `sentence`/`tool_calls`/`done` events the local path emits,
including the `<tool_call>` scanner, the `look_at_scene` vision switch, and the eager
first-clause flush. A provider function only has to produce deltas.

**`openrouter`** — `google/gemma-4-31b-it` pinned to **Cerebras** (`_stream_openrouter_speaking`).
Measured **0.91–1.74s to first token at 260–1700 tok/s** (the reply effectively lands at once),
with no TPM squeeze (131K ctx) so the conversation is sent uncapped. **Blocked on BYOK**: the
shared Cerebras pool (`is_byok:false`) returns 429 with `retry_after 59s` after ~one request —
about 1 turn/min, unusable for conversation. Adding a personal Cerebras key at
`openrouter.ai/settings/integrations` removes that ceiling and makes this the fastest option.
`allow_fallbacks:false` is deliberate — failing fast to the warm box (~1.5s) beats silently
landing on SiliconFlow/Novita (measured 7–8s TTFT). Key: `OPENROUTER_API_KEY` in `.env`.


One config value picks the brain that generates spoken replies:
- **`local`** (default) — the llama.cpp box (`_stream_local`): KV-cache warmed, speculative
  turns, ~1-2s warm TTFT, unlimited context.
- **`groq`** — `_stream_groq_speaking` streams every reply from Groq's Qwen instead. It reuses
  `_normalize_messages_for_local` (so the SAME `<tool_call>` protocol + tools instruction),
  caps context for the model's TPM budget (`groq_speaking.max_context_tokens`), and emits the
  SAME `("sentence"|"tool_calls"|"done")` events + `vision_switch` handling — so main.py's tool
  execution, follow-ups, history, and **speculative turns** all work unchanged. No box ⇒ no
  KV cache to manage on the hot path, and latency is pure network+generation — **measured
  0.75-1.3s to first sentence** at conversational pacing, including turns that hit a 429.

**What makes Groq mode actually fast (all three are load-bearing):**
1. **Key-pool capacity.** 8K TPM is *per key*, and each key in `GROQ_API_KEY_LIST` is a separate
   org, so 4 valid keys = 32K TPM ≈ 5.5 turns/min at this repo's ~5800-token context. Rotation
   is round-robin with per-key 429 cooldowns (`_ordered_keys`), so the first key tried almost
   always has budget instead of re-probing a drained one.
2. **Speculative turns are gated on `speaking_is_local()`** — they run on the box ONLY, never on
   any cloud provider. A spec turn is a full duplicate generation: free on the box's own slot,
   but on Groq/OpenRouter it doubles spend against a per-minute budget, and the discarded spec
   is exactly what pushes the REAL turn into a 429. Same gate disables `prefill_partial`.
3. **The box is kept warm as a HOT STANDBY.** `warmup`/`register_history`/the instant-vision
   pre-warm all still run under Groq (the box is idle then, so it costs nothing, and
   `--cache-prompt` makes each post-turn rewarm incremental). This is not cosmetic: when every
   key is in cooldown we fall back to the box, and an *unwarmed* box paid a **45s cold prefill**
   (~5900 tokens at ~13 tok/s) in testing. main.py must pass `after_speaking=False` for these
   turns (`groq_turn`) — the box never generated the reply, so there is no server-side prefill
   to defer to.

**Remaining caveat**: a general chat model **over-calls tools** vs the tuned local gemma

### 5.2c `core/brain/action_agent.py` + `fast_cloud.py` — the `complex_query` action agent

The speaking model emits **one** tool call per turn, so "check the messages at burgito
and set a reminder if there's an event tomorrow" was structurally impossible. This is the
same two-trigger shape that makes `look_at_scene` seamless (§5.2a), applied to multi-step
work: a specialised cloud handler behind a normal tool call, returning into the ordinary
tool-follow-up machinery. `main.py` needs no knowledge that it ran.

**The 12 WhatsApp tools were REMOVED from `llm.main_tools`** (and senior's override); they
remain in `tools.TOOLS` for agents. The speaking tools instruction went 2186 → 2044 chars.

**Two triggers, one path** (both land on the `complex_query` tool):
1. **Code-level router** — `_should_route_complex_query` / `_auto_complex_query_tool_event`
   in `core/llm.py`, modelled on `_auto_memory_tool_event`. Fires in **0.00s** (no box
   round-trip). This is **load-bearing**: with WhatsApp gone from the catalog, a missed
   model decision leaves no tool at all, and the failure mode is a confident *"sure, I sent
   it!"* for a message that never left. Negatives (`play`, `song`, `what did we discuss`,
   `switch mode`, …) win outright — a false positive would cost every normal turn seconds.
2. **Model-emitted** — `complex_query` is in the catalog for everything the regex misses.

**Clarifications are resolved across one conversational boundary.** A user naturally says
"what's going on?" and then "I mean in my WhatsApp" after Kiki asks what they mean. The action
verb and service surface live in different utterances, so testing only the final string misses
both. `_resolved_complex_request()` joins at most two recent verbatim user fragments only when
the current turn is an explicit clarification; the resulting request (both original phrasings)
is passed to the agent. It never concatenates arbitrary history. A fresh sourced next-turn note
gets first refusal for read-only status questions; without one (or when stale), the same sequence
deterministically becomes `complex_query`.

**Provider split (`action_agent` config).** Cerebras gets the **FULL** context; Groq gets a
**compacted** one. Measured on the Pi with `scripts/bench_action_agent.py`, 4-turn loop:

| route | loop | notes |
|---|---|---|
| **cerebras `gemma-4-31b`** (default) | **3.07s** | one clean JSON object per turn, 0 rate limits, reports `cached_tokens` |
| cerebras `gpt-oss-120b` | 2.88s | fabricates follow-on turns + its own tool results, ~3× output tokens |
| groq `openai/gpt-oss-120b` | 5.83s | **429 on turn 4 of ONE query** |
| groq `qwen/qwen3.6-27b` (fallback) | 5.92s | 429 on turn 4 |

**Groq's 8000 TPM is per key** (confirmed via `x-ratelimit-limit-tokens`; a token bucket
refilling in ~24s, not a daily cap) — one complex query is roughly one whole key's minute,
and the pool is **shared with `instant_vision`/`look_at_scene`**. That, not latency, is why
Cerebras is the default. End-to-end live runs land at **2.9–6.9s**.

**Four guards exist because live testing produced these exact failures** — all four are
about never letting a non-action sound like a completed one:
- `fast_cloud.first_json_object` — `gpt-oss` emits its tool call, then *fabricates the
  result*, then continues. Only the first balanced object is kept.
- `action_agent._placeholder_arg` — the model batched `list_messages(chat_jid=
  "<PLACEHOLDER_JID_FROM_FIRST_CALL>")` in the same turn as the `list_chats` meant to
  supply it; the empty result became "there are no new messages". Refused, not executed.
- `min_tool_calls=1` — a run that touches no tool has invented its answer (observed: a
  detailed WhatsApp conversation about taco night and guacamole that did not exist).
- `_is_meaningful` — a `"..."` summary is not a success.

**The nudge has to name a tool the task actually has.** `min_tool_calls` used to reject a
premature completion with one fixed sentence recommending `search_web`, appended verbatim
every turn. Live failure 2026-08-28 23:35:35: "Did I drink water recently?" routed to care,
the agent already had the answer from an injected `care_event`, called no tool, and got the
same unusable nudge six times — it re-emitted byte-identical JSON each turn until the turn
budget died and Kiki spoke *"That care action did not complete"*, discarding an answer it
had. `run_agent_loop` now takes `verification_tools` (the agent passes its care or general
menu) and `_force_tool_call` escalates: it names those tools, and on a verbatim repeat it
says so and demands one specific call. A nudge the model cannot act on is not a guard, it
is a deadlock.

`allow_unverified_finish` then stops exhaustion from destroying a good answer: on the FINAL
turn only, a completion with no tool call is returned with `unverified: True` instead of
failing. It is opt-in and the agent enables it **only** for care requests that are
plainly questions (`is_read_only_care_request`) with no active session — a stale answer to
a question is recoverable, whereas a write reported without the write having happened is
the taco-night failure again. Writes, guided session turns and `CARE_PLAN_DELEGATION` keep
the hard failure.

`is_care_request` was also narrowed for the same reason. Bare everyday nouns (`water`,
`walk`, `lunch`, `sleep`, `routine`) now only mean care alongside a first-person reference,
because matching them anywhere sent ordinary conversation down the care agent's
direct-to-TTS path: "play a walking song" became a care request, and so did "what did they
say about lunch?" — a WhatsApp question, i.e. exactly the request the fabrication guard
exists for. Care-specific words (`medicine`, `caregiver`, `heart rate`, `दवाई`, `नब्ज़`, …)
still match on their own.

On failure or deadline the returned string explicitly forbids a false success. Ordinary
agent work retains the 22-second internal deadline; a MAX30102 session receives a bounded
75-second measurement deadline. Both sit below the speaking path's 85-second
`complex_query` wait, so the agent reports its own outcome rather than being truncated by
main.py. `run_agent_loop`'s existing `progress_fn` drives the LCD/OLED line. Cloud-only —
the local speaking slot is never used for care reasoning.

**Care result fast path:** a care `complex_query` final summary is already the exact
voice-ready response. `main.py is_direct_care_complex_call()` therefore sends it directly
to the existing `TTSStreamer` and skips `followup_llm_and_tts`. The synthetic assistant row
did not come from llama.cpp, so `synthetic_tool_followup=true` forces a real prefix rewarm;
marking it warm would violate §4. Ordinary tool results still use the local follow-up LLM.

**Care planning is not runtime scripting.** Older plans may still contain normalized
`actions` records because the original agent used an action DSL and live failures showed
that models vary key names (`action`/`data` versus `type`/`instruction`). Those normalizers
remain for migration and WebUI compatibility, but new plans provide one substantive
`session_brief`. The runtime never advances an action index. This removes the more serious
failure mode: the scheduler executing several scripted assistant steps and accidentally
creating/answering the participant side of the conversation. Schedule normalization stays
strict (`daily`, `once`, `recurring`) and actionable validation errors still let the complex
agent repair format mistakes before claiming that an event was saved.

**Related fixes made for this feature:**
- `whatsapp.py list_chats` selected `messages.content` without its JOIN when
  `include_last_message=False`, so it silently returned **zero chats**.
- `search_contacts` excludes groups in SQL (`AND jid NOT LIKE '%@g.us'`), so a group name
  could **never** resolve. `whatsapp_mcp._resolve_recipient` now also sweeps `list_chats`
  and fuzzy-scores (`_name_score`): "burrito time" → the real group "burgito time" (0.92).
  An exact match always wins; a genuine tie returns `matches` so Kiki asks instead of
  messaging the wrong person. `tools.list_chats` retries fuzzily when the LIKE finds
  nothing, which saves the agent a wasted turn.

### 5.2d `core/self_extend/whatsapp_contacts.py` — the address book

**`messages.db` has no names.** Measured on the device: **292 direct chats, 0 with a human
name** — every DM is a bare identifier like `20000000000051`. So "send a message to
Namita" could never resolve, and a chat summary read out as a list of phone numbers.

The Go bridge's **own** store (`whatsapp-bridge/store/whatsapp.db`) held the answer all
along, unused until now: `whatsmeow_contacts` (~2400 rows of full/first/push/business
name against a phone JID — the real WhatsApp address book) and `whatsmeow_lid_map`
(~1200 rows mapping the opaque `@lid` message senders back to phone numbers). Chaining
them turns `20000000000031` into "Nikhil" — verified 8/8 on live group senders.

This module owns that chain in **both** directions, read-only, cached against the store's
(mtime, size) because the bridge syncs contacts continuously:
- `resolve_name(q)` → who to SEND to. Fed into `_candidate_pool` **first**.
- `display_name(id)` → who to READ OUT. `whatsapp_mcp._label_people` walks every MCP
  payload adding `sender_name`, and replaces a numeric DM chat name with the contact.
- `jid_variants(jid)` → **the phone-vs-`@lid` split**: a direct chat is *addressed* by
  phone but *filed* under its `@lid`. "Studio Website" resolves to
  `15550000021@s.whatsapp.net` (0 message rows) while its 60 real messages live under
  `20000000000021@lid` — querying only the first made the agent state "there are no
  messages" as fact. `tools.list_messages`/`get_chat` retry the alternate form before
  believing an empty result.

Contacts are stored twice (once per identifier form) so `_load` canonicalizes via the lid
map — otherwise one person looks like two and Kiki asks a needless clarifying question.

**`whatsapp.contacts` in config.json** is the manual override: `{"name": "number"}` for
nicknames WhatsApp doesn't know ("mom") or to settle a name several contacts share (three
people called Nikhil). Config entries beat the address book outright in
`_resolve_recipient` — that is the documented way to disambiguate. Keys starting with `_`
are treated as comments, not people. Restart to apply.

Ranking prefers a real **chat** over an address-book-only entry at equal score: most of
those ~2400 names have never been messaged, so an existing conversation is the likelier
target than a namesake in the contact list.

### 5.2e Inline image reading — and the three ways it hung the turn

An image message arrives as `content: ""` with `media_type: "image"` — indistinguishable
from a blank message — so the agent skipped pictures entirely (measured: 18 images in a
chat, 0 reads). `tools._describe_images_inline` now describes the newest few up front
through the **same free Groq qwen VLM `look_at_scene` uses**, so a summary includes what
the pictures say without the agent spending extra turns. Config: `whatsapp.describe_images`
/ `describe_images_limit` (3) / `describe_images_timeout` (5s).

Getting this bounded took three separate fixes, each worth keeping:

1. **Slice before doing per-image work.** The cap was applied *after* checking every
   image row, so an active group did unbounded work *outside* the timeout.
2. **Never fetch cold media inline.** Media that isn't downloaded goes over the network
   and serializes behind the single MCP session lock. A background prefetch thread looked
   free but was worse — it holds that same lock, so the agent's own next call queued
   behind it (a summary went 3.8s → 25.4s). Cold images are simply marked with the exact
   `read_whatsapp_image` call needed; on-disk presence is a cheap stat via the `filename`
   column, no MCP round trip.
3. **Give image work its own threads** (`_IMAGE_POOL`). On a timeout the workers keep
   running until their Groq call returns; on the shared default executor those stragglers
   occupied the same pool the agent uses for its next tool call, so a 5s image cap still
   produced a **108s turn**. Isolating them made the timeout real: 108s → 10.6s.

On timeout the chat is summarised **without** the pictures rather than the turn hanging.

Separately, `execute_tool` used `with ThreadPoolExecutor()`, whose `__exit__` calls
`shutdown(wait=True)` — so a timed-out tool still blocked until its worker finished. It
now shuts down with `wait=False`, or the 30s cap is not a cap at all.

**Answer length**: the agent returned 242 chars because four things capped it — the agent
prompt, `summary_max_chars`, `execute_tool_calls`' 1500-char result cap, and (largest)
main.py telling the model to "answer briefly in one or two spoken sentences". That last
instruction now switches on the tool: `main.tool_result_note` tells the model to relay a
`complex_query` result **completely**, since the agent already wrote a finished spoken
reply rather than raw data to distil.
- New tools: `read_whatsapp_image` (downloads media → `instant_vision.describe_image_file`,
  the same free Groq VLM; ~1.5s) and `record_voice_note` (taps the STT `mic_reader` ring
  buffer via `STTEngine.record_clip` — a second PyAudio stream would fail with "Device or
  resource busy"; works while muted, which is the normal mid-turn state).

### 5.2f Name matching and row size — why summaries were thin and sends were refused

Reported live (2026-07-26): "summarize chats by namitha" and "send a message to namita"
both still failed. Four separate causes, all now fixed and regression-tested.

**1. Mid-word containment.** Both scorers boosted a plain substring match to ~0.9. But
`"amit"` is literally inside `"namitha"`, so the contact **Amit** scored 0.90 against the
real **Namita**'s 0.92 — inside `_FUZZY_MARGIN` (0.08), so `_resolve_recipient` declared it
ambiguous and refused to send. A length-ratio guard cannot separate these: `amit/namitha`
is 0.57 and the legitimate `burgito/burgito time` is 0.58. **Word boundaries can** —
`whatsapp_contacts.contained_at_word_boundary` requires the shorter name's tokens to be a
contiguous run of the longer's. Shared by `whatsapp_contacts._score` and
`whatsapp_mcp._name_score` so the rule cannot drift between the read and send paths.

**2. `chat_name` was never labelled.** `_label_people` de-numbered a chat row's `name` but
not a *message* row's `chat_name`. Every message in Namita's chat therefore read
`"93638927347889"`, the agent concluded it had fetched the wrong chat, and burned a whole
extra turn re-fetching byte-identical messages under the `@lid`.

**3. Fat rows, not a short model.** The 308-char summary was not the model being lazy: the
agent asked for 100 messages and `max_tool_result_chars` handed back 1500 characters —
**five rows** — because each MCP row spends ~300 chars repeating `chat_jid`, `sender` and a
32-char `id`. `tools._compact_message_rows` cuts a row to `{time, from, text}` ≈ 60 chars
(`id`/`chat_jid` survive only on media rows, where `read_whatsapp_image` needs them), and
the budget rose to 4500. **308 → 999 chars of genuinely specific summary.**

**4. A wasted turn on every send.** The agent prompt said to resolve recipients with
`search_contacts` first — but `send_message`/`send_file`/`send_audio_message` already pass
`resolve_recipient=True` and do the fuzzy address-book resolution themselves. The lookup
turn was pure latency, and its result was *worse* than the resolver's. **11.5s → 7.6s.**

Measured after: `namitha`→Namita, `studio graty website`→Studio Website, `burrito time`→the
burgito group, `bharat`→Bharat Pandey; `studio` alone stays ambiguous (three real Studios),
which is correct. Summaries 9.3s/999 chars and 12.9s/685 chars (the latter reads 2 images).

### 5.2g What the agent knows besides the request

The code router synthesises its tool call from the user's **words alone**, so until
2026-07-27 the agent received `"summarize my chat with him"` with no referent whatsoever
and no idea who Kiki or Vaibhav are. It now gets two things, both assembled in
`action_agent._background()`:

- **`llm.conversation_snapshot(turns, chars)`** — recent spoken turns as `Vaibhav:`/`Kiki:`
  lines, trimmed **from the front** so the newest turns (the ones a pronoun points at)
  always survive. Fed by `llm._note_conversation(messages)`, called once per primary turn.
- **`llm.persona_brief(chars)`** — the *identity opening* of `llm.system_prompt`, clipped at
  a sentence boundary. Not the whole 7.9k prompt: the rest is behavioural rules for
  free-form conversation that would only distract a model whose job this turn is calling
  tools correctly. The summary is re-voiced by the speaking model, which still has all of it.

**Why a module snapshot and not a tool argument** — this is the load-bearing detail. A tool
call's `arguments` become assistant text that `register_history` writes into the warm
speaking prefix. Putting the conversation in there would rewrite that prefix on **every**
routed turn and force a full re-prefill, breaking the §4 cache contract. Reading it
out-of-band costs the speaking path one list comprehension and zero prompt bytes.
`test_context_never_enters_the_tool_call_arguments` guards this.

Verified: with "studio website has been messaging me all week" in history, **"summarize my
chat with him"** picks Studio Website out of three Studios — 9.4s, 974 chars.

### 5.2h `core/brain/history_view.py` — the history, rendered for readers

Traced from one question: *"if I asked a while ago to play music, when will 'send the
music link' work?"* Answer: **never.** `play_music` returns `"Now playing X - <url>"`, and
main.py files every tool result under `role: "system"`. Kiki only *speaks* the follow-up
line, which has no URL. Both readers of the history dropped that role, so the link existed
only in the one row neither of them read — and vanished for good at the next compaction.

One tool turn writes **four** rows:

```
user      "play some music"
assistant '<tool_call>{"name":"play_music",...}</tool_call>'     ← protocol, not speech
system    'Here is the result ... :\nNow playing X - https://...'  ← the link lives HERE
assistant "Playing X for you."
```

`history_view` renders that once, correctly, and is **shared by both consumers** so they
cannot drift apart again:

- `render()` → typed records: `user`, `kiki`, `tool_call`, `tool_result`, `memory`,
  `time`, `context`. The `<tool_call>` tag becomes `[Kiki used play_music(song="…")]` —
  previously it was passed through verbatim and read as something Kiki said out loud.
- `as_text(max_chars)` → trimmed **from the front**; the newest records are what "it",
  "him" and "that link" point at.
- `harvest_artifacts()` → URLs, file paths and jids with a little surrounding text
  (`https://… (Now playing Blinding Lights -)`). The durable half: even when a long result
  is clipped, the link a follow-up needs survives.

**Budget**: `action_agent.history_chars` = 28000 (~7000 tokens), deliberately matched to
`agent.token_limit` — the ceiling the *local* model runs against. The Pi's box model was
seeing the entire `message_history`, tool results and all, while the cloud agent got 1800
chars of user/assistant text: **the 3B-class local model was ~14× better informed than the
agent built to act on what it heard.** `max_prompt_chars` rose 22000 → 60000 so history and
tool results stop competing (what `_compact_conversation` evicted was the results).

**Summariser** (`main.build_summary_input`): the same fix. It kept user/assistant rows plus
`[TIME]` anchors and `continue`d past everything else, so nothing Kiki learned by *calling*
a tool was ever written into long-term memory — it survived verbatim until the token limit
tripped, then disappeared. That is why "a while ago" failed as a **cliff, not a fade**. It
now carries `[TOOL RESULT]` rows and the previous `[EARLIER MEMORY]` block forward (without
that, each summary covered only what happened since the last one and memory reset at every
compaction instead of accumulating).

Verified: with the music turn buried under 8 later exchanges, **"send the music link to
… on whatsapp"** → one `send_message` call with the correct URL, **3.3s**.

### 5.2i Address-book staleness — a 4 ms hole in the cache signature

`whatsapp_contacts` cached against `(st_mtime, st_size)`. **Measured on this Pi**: inode
timestamps advance in **4 ms** steps (kernel jiffies, CONFIG_HZ=250), and a small INSERT
into the 3 MB store reuses a free page so `st_size` never moves. A bridge contact-sync
landing inside that tick is invisible to the signature — and stays invisible, because
nothing later changes it either. A contact added at that instant would **never** be
findable: exactly the "send a message to someone I just added" failure. Reproduced 2 runs
in 6. Fixed with `_CACHE_TTL_SECONDS = 30`; a full reload is ~0.05s for 2434 contacts, so
the sweep costs nothing and bounds staleness. The reload log only fires when the entry
count actually changes, or the TTL would print every 30s forever.

### 5.3 `core/local_llm.py` — Single-slot coordinator

- **Header** (1–60): module docstring documenting the abort/rewarm design; config constants:
  `BASE/URL`, parsed `_HOST/_PORT/_PATH` for the raw-socket path, `_REWARM_MAX_TOKENS=1`
  (prefill-only — generated tokens during a rewarm would themselves diverge the cache),
  `REASONING_OVERRIDE_FIELDS` (`chat_template_kwargs.enable_thinking`), thinking-block regexes,
  shared keep-alive `SESSION`.
- **Coordination state** (70–105): `_bg_lock` (serializes background tasks), `_bg_abort`,
  `_bg_active`, `_bg_current["conn"]` — **the in-flight `http.client` connection**, whose
  socket exists from the moment of POST (unlike a `requests.Response`, which only exists
  after headers = after the whole prefill). `_PROTECT_S` + `_last_user_activity` implement
  the conversation-hot window.
- `note_user_activity` @106 / `conversation_hot` @111.
- `preempt_background` @115 — sets abort, then **socket `shutdown(SHUT_RDWR)` in a throwaway
  daemon thread** (never blocks the caller — a blocking close once delayed mic unmute 7.6s).
  llama-server frees the slot on disconnect even mid-prefill.
- `_warm_prefix_hash` + `_prefix_hash` @150 — md5 of the normalized prefix currently warm in
  the box's KV cache; lets rewarms be skipped when redundant. Cleared by: any non-rewarm
  background request, `note_speaking(True)` (generated reply tokens diverge the cache).
- `update_speaking_prefix` @159 / `note_speaking` @166.
- `rewarm` @179 — skip if hash matches, else `generate_background(..., is_rewarm=True,
  rewarm_hash=h)` with max_tokens=1; hash recorded on success (`_last_bg_failed` False).
- `schedule_rewarm`/`_schedule_rewarm` @202 — fire-and-forget, coalesced
  (`_rewarm_scheduled`), skipped while speaking or when no prefix registered.
- `strip_thinking*` @228 — removes closed AND unclosed inline thinking blocks.
- `post_stream` @244 — requests-based streaming POST (used by tests/spec paths; speaking has
  its own in llm.py). Always sends `thinking_budget_tokens` (0 default).
- `iter_sse` @271 (requests) / `_iter_sse_httpclient` @293 (readline-based for http.client).
- `generate_background` @311 — THE background entry point. Order of operations inside:
  1. refuse if `conversation_hot()` and not a rewarm (→ caller falls back to cloud);
  2. build messages (prompt string or full list; optional `image_b64` → mmproj vision part);
  3. under `_bg_lock`: re-check `rewarm_hash` (two queued rewarms race the pre-lock check);
     clear abort; mark active; reset `_last_bg_failed`;
  4. open `http.client.HTTPConnection`, store in `_bg_current` **before** posting,
     invalidate `_warm_prefix_hash` for non-rewarms, POST, `getresponse()` (blocks during
     prefill — abortable because the socket is already exposed);
  5. SSE loop collecting `delta.content` and `delta.reasoning_content` separately, with a
     client-side thinking cap (fallback if the server budget isn't enforced);
  6. inline-thinking extraction; **salvage**: if the model spent all tokens thinking and
     produced no answer, return the thinking text for distillation instead of None;
  7. `finally` (inner): record warm hash on rewarm success, close conn;
     `finally` (outer, **outside the lock**): `_schedule_rewarm()` for non-rewarms.

### 5.4 `core/stt.py` — Local Whisper.cpp STT (client-side Silero VAD)

Optimized pipeline ported from the standalone `whisper_tts.py` benchmark: per-frame
**client-side** Silero VAD endpointing + **speculative finalization** drops end→text
from ~2s to ~200–300ms. Replaces the old "re-transcribe a growing rolling buffer every
0.4s + RMS-silence endpoint" design. **Same event API** (drop-in for main.py / vision /
face handlers).

- `STTEngine.__init__` — config: device 2, **16 kHz mono, fixed 512-sample (32 ms)
  frames** (the window Silero requires), whisper server at `stt.whisper_url`. New
  endpointer knobs (all in `_ep_cfg`, defaults shown): `vad_threshold` 0.5,
  `endpoint_ms` 200 (trailing silence that commits), `spec_silence_ms` 80 (silence after
  which the speculative ASR fires), `min_speech_ms` (reuses `vad_min_speech_ms`, default
  150), `preroll_ms` 250, `max_utterance_seconds` 20. `_muted` event controls behavior.
- `_AsrClient` — small `ThreadPoolExecutor` (2 workers) + keep-alive `Session`; one
  whisper request per utterance with short-clip params (`vad:false` — client already did
  VAD, `single_segment`, `no_timestamps`, `audio_ctx` sized to the clip via
  `_compute_audio_ctx`). Read timeout scales with clip length (`request_timeout` floor →
  `max_request_timeout` cap) so a 20s monologue doesn't spuriously time out.
- `_Endpointer` — per-frame Silero state machine with a pre-roll ring buffer (so the
  first phoneme isn't clipped). On trailing silence ≥ `spec_silence_ms` it fires the
  final ASR **in the background** and keeps listening; at ≥ `endpoint_ms` it commits using
  that in-flight result (critical path = max(endpoint_ms, asr_time)). Speech resuming
  discards stale speculation; utterances < `min_speech_ms` are dropped as noise (no
  event); a pause-less monologue is force-committed at `max_utterance_seconds`.
  A frame counts as voiced only if Silero says speech **and** `NearFieldGate` says
  near-field (see 5.4c) — or a push-to-talk hold is active, which bypasses the gate.
- `commit_now()` — explicit "I'm done talking" (thumbs-up gesture): clears the hold and
  sets the force-commit flag so the VAD worker ends the utterance on its next iteration
  rather than waiting for trailing silence that a noisy room may never produce.
- `mute`/`unmute` — instant flag flips (no reconnect). On unmute a **flush** is requested
  FIRST (discards buffered frames + resets the endpointer) so TTS-era audio isn't echoed
  back. While muted the mic reader keeps draining ALSA but frames are dropped — zero-latency unmute.
- `stream()` — generator; starts two threads: **mic_reader** (continuous 512-frame reads
  → frame queue) and **vad_worker** (runs the endpointer, fires speculative ASR, pushes
  events). Emits `("interim","…")` on speech onset and ~every 1s while speaking (keeps
  the LCD live + resets main.py's 15s mute timer on long utterances), then `("final", text)`
  (never None) + `("endpoint", None)` at commit. A slow/dead server never blocks capture.
- `stop()` — closes stream/PyAudio + ASR pool/session.
- `set_capture_mode("ambient"|"query")` — resets the current VAD boundary without draining
  newly queued frames. Query events retain the existing names; passive commits emit
  `ambient_final`/`ambient_endpoint`. The mode is stamped at speech onset so a slow ambient
  ASR completion cannot race a wake word and become the user's query. Rolling partial ASR
  is disabled in ambient mode (it only exists to prefill an imminent speaking request).

### 5.4a `core/brain/ambient_listening.py` — Always-listen distillation

- Buffers timestamped finalized ambient transcripts in `ambient_listen_buffer.json` using
  atomic replacement; failed/denied cloud calls retain the batch for retry.
- A randomized scheduler flushes every `batch_min_minutes`–`batch_max_minutes`; wake-word
  and IR query activation request an immediate flush via the event loop without blocking.
- The cloud returns original transcript indices, not rewritten speech. Only indexed coherent
  snippets can produce a context summary, `focus=ambient_listening` journal entry, and
  conservative `knowledge_updates` (speaker identity must never be guessed).
- Live context is queued after distillation and drained by `main.py` only between turns,
  preserving the append-only KV-cache contract. Calls use the `ambient_listen` cloud-budget
  category and `purpose=summary`, which hard-pins them to cloud rather than the local slot.

### 5.4b `core/runtime_controls.py` — Spoken runtime settings

- Owns thread-safe, in-memory active mode, mode revision, and persistent follow-up state.
- Resolves `assistant_modes.modes.<name>.system_prompt`; the default mode's null prompt
  inherits the existing `llm.system_prompt`. `switch_mode` applies the mode's `voice` and
  increments the revision consumed by `main.py` at a cache-safe boundary.
- Mode-name resolution returns the exact config key using normalized equality first,
  contained-string/token matching second, and typo-tolerant similarity last. Close fuzzy
  ties are rejected, preventing an arbitrary switch between similarly named modes.
- `parse_spoken_control` conservatively recognizes explicit mode, volume, and follow-up
  commands. `core/llm.py` converts them to the normal tool-call event protocol before model
  generation, so the same execution/history/follow-up machinery handles them.

### 5.4c `core/noise_suppression.py` + `core/near_field_gate.py` — noise handling

Two different problems, two different mechanisms. **They are not interchangeable**, which is
the key thing to remember before tuning either one.

**`noise_suppression.py` — RNNoise (steady noise).** A ctypes wrapper over the
`librnnoise.so` bundled with `pyrnnoise` (the package itself is never imported — it drags in
a file/plotting stack the mic path doesn't need). The mic is opened at RNNoise's native
**48 kHz / 480-sample (10 ms)** frames, denoised, then `StreamingDecimator3` resamples to the
16 kHz / 512-sample frames Silero needs via a causal 63-tap anti-aliased filter (<0.7 ms group
delay). All work happens *at capture time*, so nothing is added after endpoint and
time-to-first-word is untouched. Measured on this Pi: **~2.0 ms per 10 ms frame (~21 % of one
core)**. A real-time guard bypasses suppression permanently for the session after
`slow_frame_limit` frames exceed `max_process_ms` (5 ms), so an overloaded denoiser degrades
to raw audio rather than adding latency. If the mic can't do 48 kHz, capture falls back to
16 kHz with suppression off. RNNoise state is reset at each mute/unmute boundary so Kiki's own
TTS never pollutes the noise history. Removes fans, AC, traffic, motors. **Does not remove
background speech** — it is trained to preserve voices.

**`near_field_gate.py` — NearFieldGate (crowds).** The crowd fix. Silero answers "is this
speech?", not "is this speech addressed to Kiki", so in a crowded room every frame reads as
voiced, `silence_ms` never accumulates, no endpoint ever fires, and the 1 s interim heartbeat
keeps main.py's listen window open forever. What actually separates the user from the crowd is
**level**: the person talking to Kiki is near-field and sits well above the room's babble.
`NearFieldGate` tracks the noise floor with an asymmetric envelope follower (falls fast at
`floor_fall_per_s_db`, rises slowly at `floor_rise_per_s_db`, and only *non-speech* frames may
push it up, so the user's own voice cannot gate them out mid-sentence), then requires speech to
stand `open_margin_db` above it. Thresholds are hysteretic — `close_margin_db` to keep
counting — so quiet trailing syllables aren't clipped. Crucially the gate **stays entirely
inert until the floor itself rises above `engage_floor_dbfs`** (-50 dBFS), so at home it never
engages and endpointing behaves exactly as before. A push-to-talk hold bypasses the gate's
verdict (the floor keeps tracking): a hand on the IR sensor is unambiguous intent, and gating
a quiet user out there would capture nothing at all.

Covered by `tests/test_noise_suppression.py`, `tests/test_near_field_gate.py`, and
`tests/test_endpointer_crowd.py` (drives the real `_Endpointer` with a stubbed VAD to prove
babble alone never commits, a near-field speaker still endpoints *while the room stays noisy*,
and a quiet room is unaffected).

### 5.5 `core/tts.py` — TTS providers

- Provider chosen by `tts.provider` ("local" in production). Import-time config caching.
- `SUPPORTED_TAGS`/`TAG_MAP` + `sanitize_for_local_tts` @102 — the local voice model only
  understands 13 bracket tags; everything else (motion tags `<...>`, emojis, markdown chars)
  is stripped or remapped ([laugh]→[laughter], [gasp]→[surprise-ah], ...). Keep in sync with
  omnivoice `voice_api.py` and `scripts/streaming_tts.py`.
- `get_tts_system_prompt_note` @118 — appends the tag restriction to the system prompt
  (local provider only). Called by main.py BEFORE warmup (cache safety).
- `get_local_voice` / `set_local_voice` / `list_local_voices` select preloaded omnivoice
  references at runtime. Switching clears the text-only PCM cache so audio from a previous
  voice cannot leak into the new mode.
- `_trim_edge_silence` @138 — strips model-baked silence at PCM edges (keeps 50 ms).
- `GroqTTSStreamer` @159 — sentence queue → Groq API → temp wav files → `mpv` playback.
- `InworldTTSStreamer` @250 — bidirectional websocket, OGG_OPUS chunks piped into a
  long-lived `mpv` stdin.
- `LocalTTSStreamer` @404 — **production path**: synth worker posts each sanitized sentence
  to `POST /v1/audio/speech` (`response_format: pcm`) on a keep-alive session; trims edges;
  prepends a 120 ms gap **only from the 2nd sentence on** (appending it after the last
  sentence used to delay mic unmute); playback worker holds a single long-lived `aplay`
  pipe and writes the first audible PCM immediately. Because omnivoice buffers each response
  body behind a full synthesis pass, short Hindi continuations are opportunistically joined
  into a >=48-character request while prior audio has safe playback headroom. Sentence 1 is
  never joined or delayed, preserving TTFW; English request boundaries are unchanged.
  `abort()` (the "stop it" hotword) kills the aplay pipe directly. `first_play_event` is the
  signal main.py uses to stop the thinking sound; a `finally` safety always sets it.
- `LCDOnlyStreamer` — TTS-compatible, audio-free output used while the camera mute toggle is
  active. It preserves speculative hold/release semantics and reveals the reply on the 16x2
  LCD at a readable pace; it never opens an audio process or contacts the TTS server.
- `TTSStreamer()` @563 — factory. Its unmuted provider path is unchanged; one in-memory mute
  flag selects `LCDOnlyStreamer` before any TTS work. `speak_sentence` @573 is the simple
  blocking helper used by workers.

### 5.5a `core/speech_recorder.py` — `record_enabled` speech archive

With `record_enabled: true` in config.json, every spoken reply is also saved as a wav in
`record_dir` (default `kiki_speeches/`, gitignored), named after the **first two words** of
the reply (`Hey_there.wav`; a `_2`, `_3` … suffix is added on collision). One file per
assistant response, not per sentence — a tool turn's filler + answer land in the same file
because they share one streamer.

It records the **exact PCM handed to the sink**, tapped inside
`LocalTTSStreamer._play_worker` immediately *after* `self._write_pcm(pcm)` (§5.5). Three
properties keep it off the latency path, and all three are load-bearing:

- **Capture is a `list.append` of a bytes object the playback thread already holds** — no
  copy, no encode, no I/O, no lock. It sits after the aplay write, so time-to-first-word is
  byte-for-byte the old path.
- **`close()` only puts the buffer on a queue.** It runs first in the play worker's
  `finally`, *before* the aplay drain, so it never adds to `finish()` — which main.py blocks
  on before unmuting the mic. The wav encode and the disk write happen on one daemon writer
  thread (`speech-recorder`).
- **`new_recording()` returns `None` when disabled**, so with the flag off the streamer's
  only cost is an `is not None` check per PCM chunk.

Because the tap is at the sink, an aborted reply ("stop it" / open-palm) saves exactly the
audio that was actually spoken, and a `_MAX_BYTES` cap (~10 min) bounds the buffer. Only the
**local** provider is recorded — the production path; Groq/Inworld play through `mpv` and are
not captured, and `LCDOnlyStreamer` (camera mute) produces no audio to record. Filenames keep
their own script: combining marks are preserved explicitly because `\w` drops them and would
mangle Devanagari (नमस्ते → नमसत).

### 5.6 `core/brain/unified_idle_mind.py` — Unified Idle Mind

- `UnifiedIdleMindManager` owns scheduling, conversation buffering, ambient snapshots,
  prompt construction, tool policy, queued actions, and one next-turn note.
- Model/provider/fallback/thinking level come from `idle_mind` in config.
- `_SessionPolicy` blocks physical actions, repeated intents, and excess research,
  persistence, or proactive actions. The live-data tools `read_gmail`,
  `read_gmail_message`, `read_gmail_thread`, `search_notion`, and `read_notion`
  are deliberately exempt
  from cross-session duplicate blocking because inbox/workspace contents may change;
  the shared agent loop still suppresses an exact duplicate inside one session.
- Routine Gmail/Notion reads use those five compact tools directly. Raw
  `self_extend_tool_call` requests for `Gmail_ListEmails`, `Gmail_SearchEmailsByQuery`,
  `Gmail_GetEmail`, `Gmail_GetThread`, `notion-search`, or `notion-fetch` are redirected
  by policy so full HTML, MIME headers, MCP envelopes, and oversized workspace results
  cannot enter agent context. The pre-2026-08 Gmail names (`fetch_emails`,
  `fetch_message_by_message_id`, `fetch_message_by_thread_id`) stay in the redirect map
  so a model working from a stale prompt is still caught. **Keys in
  `_GENERIC_MCP_READ_REPLACEMENTS` must be lowercase** — the guard lowercases the
  connection/tool pair before looking it up.
- `_run_session` uses the shared `run_agent_loop`, records a complete observability
  session, consumes ambient snippets on success, and persists the next check.
- `set_next_turn_note` requires voice-ready `text` and accepts a compact evidence `source`
  (`whatsapp`, `email`, `notion`, `web`, `conversation`, `ambient`, `vision`, `care`,
  `calendar`, `memory`, `mixed`, `unknown`). Legacy notes infer only strong provenance;
  ambiguous sources stay `unknown`. Injection includes `source` and a bounded `reason` instead
  of discarding both. An unused note's `injected` bit is re-armed for a new process because it
  describes an in-memory history, not durable delivery.
- `sourced_note_reply` is the narrow foreground consumer: it requires the matching injected
  note ID, freshness, a read-only current-status/source/topic match, and voice-ready prose.
  It returns the note itself without a local/cloud model call. Controls, explicit memory
  requests, mutations, fresh inbox checks, stale notes and unrelated conversation continue
  through their existing routes.
- Foreground activity calls `interrupt`, but this deliberately does not cancel cloud
  work. Conflicting tools and prompt mutation wait until speech ends.
- Proactive context accepts a meaningful live scene and the single active next-turn note;
  there is no multi-point or automatic journal surfacing queue.

### 5.7 `core/brain/thinking_journal.py` — Dated research

- Schema v2 stores `entries` and `open_questions` only.
- `save_background_research` validates and deduplicates model-selected writes.
- `recent_summaries` supplies anti-repetition context to Unified Idle Mind.
- `recall_memory` searches full topic/summary/details and labels matches as dated
  background research.
- Open questions persist curiosity between sessions and can be resolved by word overlap.

### 5.8 `core/brain/knowledge_base.py` — Long-term memory

- `KnowledgeBase` over `knowledge_base.json`: categories `people` (incl. self "Kiki"),
  `environments`, `learnings`, `experiences`, `facts`, `personality`, `metadata`.
- People CRUD: `add_person`, `add_person_attribute` (list attrs append-dedup; scalar attrs
  overwrite; **setting `current_ongoing` also stamps `current_ongoing_updated`**),
  `add_note_to_person`, `set_current_ongoing`, etc.
- `get_summary(max_lines=50)` @~407 — the context injection: Kiki self-section (mood + last
  3 notes), people (appearance/character/interests/routine[:3]/last 2 notes),
  **`current_ongoing` aging**: ≤14 days → "Currently:", 15–60 days → "A while back (N days
  ago, probably finished):", >60 days → dropped, undated → "At some point (undated, may be
  old):". This stops months-old "currently working on X" from surfacing as live context.
- Module singleton: `get_knowledge_base()`, `get_knowledge_summary()`, `save_knowledge_base()`.

### 5.8a `core/brain/memory_search.py` — Human-like cross-store recall

- Backs the `recall_memory` speaking tool and searches **granular records** from the full
  knowledge base (including experience outcomes/details and archives), every timestamped
  conversation file, the current summary, and thinking-journal entries.
- Query scaffolding ("search my memories for...") is removed; remaining terms use exact
  phrase/token scoring, a small autobiographical concept map (humor, study, sleep, music,
  etc.), conservative stemming, and typo-tolerant fuzzy matching. Rare terms, title hits,
  full-query coverage, source value, and recency influence rank.
- Large people dictionaries are split into individual facts/notes before ranking, preventing
  the speaking path's 1500-character tool-result cap from hiding later matches. Similar
  records are deduplicated and selection applies source diversity.
- A filesystem-signature cache keeps repeat calls cheap but automatically rebuilds when any
  memory file changes. If no meaningful term matches, deterministic salience + diversity
  returns an explicitly labelled approximate assortment instead of "nothing found".
- Output is capped below the speaking tool limit and tells the follow-up model to synthesize
  dated evidence while treating fallback memories as leads rather than exact matches.
- The fast-model tool catalog stays deliberately terse. `core/llm.py` gives `recall_memory`
  one mandatory rule plus one example, and renders compact parameter signatures instead of
  verbose JSON schemas. A conservative code-level router emits the same normal tool event for
  unmistakable autobiographical prompts ("what did we discuss", "search your memory", "funny
  memories", etc.), so a missed decision by the small model cannot become a hallucinated answer.

### 5.9 `core/brain/summary_manager.py` — Summaries

- Single-file summary (`conversation_summary.txt`): `load_saved_summary`/`save_summary`.
- Timestamped per-session files in `conversations/` (newest-first list);
  `save_summary_to_conversations_folder`, `load_latest_conversation` (raw injection of the
  very last session).
- `generate_past_conversations_summary(n, prefer_cache, force_refresh)` @135 — combines the
  N previous session files into one LLM-written memory, cached in
  `conversations/cached_past_summary.txt`. **`prefer_cache=True`** (startup) returns the
  cache even if stale — every shutdown writes a new conversation file, so the strict mtime
  check missed on every boot and a ~60s box-blocking regen ran at the worst possible time.
  **`force_refresh=True`** (the mid-session background refresher) regenerates uncached and
  writes the cache for the next boot. Generation runs on the local box via
  `generate_background` (refused while hot → retry later).

### 5.10 `core/brain/generate_llm_resp.py` — Brain/vision/summary router

`generate(content, b64_image, thinking_level, websearch, purpose)`: routing by
`use_local_llm` flags (currently `summary: true`, `vision: false`, `reasoning: false`) —
local primary goes through `local_llm.generate_background` (so it's preemptible and
hot-window-aware); then Gemini key rotation (`GEMINI_KEY_LIST`, gemini-3-flash-preview →
gemini-2.5-flash, optional Google-Search grounding, image support), then Groq
(`GROQ_API_KEY_LIST`, gpt-oss-120b, text-only), then local as last resort. This is the
SLOW/quality path — completely separate from the speaking pipeline.

### 5.12 `core/brain/token_counter.py`

`count_tokens(messages, model)` with tiktoken (char/4 fallback), per-message hash cache
(2048 entries, cleared when full) because history is append-mostly and re-tokenizing every
message every turn wasted Pi CPU. Handles both dict and object message shapes.

### 5.13 `core/workers/` — Background agent system

**`worker_engine.py` (173)** — dataclasses only. `Worker` (id/name/task_description/
trigger/conditions/status/retries), `WorkerTrigger` (scheduled_time ISO | event name |
recurring interval), `WorkerCondition` (person_seen / time_range / custom),
`VALID_EVENTS = {startup, shutdown, sleep, wake, after_response, face_detected}`.
`mark_failed` keeps status `pending` until `max_retries` is hit.

**`worker_brain.py` (520)** — the shared agent loop:
- `FaceHistoryBuffer` @30 / `VisionContextHistory` @86 — thread-safe rolling buffers filled
  by face_handler/vision_handler, read by worker conditions and prompts.
- `check_conditions` @132 — person_seen (within N min), time_range (hour window).
- Context budget constants @~175: `MAX_PROMPT_CHARS=20000`, `MAX_TOOL_RESULT_CHARS=3000`.
- `_truncate_middle` — keeps head (70%) + tail of oversized tool results.
- `_compact_conversation` — **smart context compression** for the 7k-token box: the task
  prompt (conversation[0], with all tool/JSON instructions the small model needs) is kept
  VERBATIM, the last 2 entries kept whole, older middle entries squeezed to fit the budget.
- `run_agent_loop(prompt, llm_fn, max_turns, label, stop_event, min_tool_calls,
  max_tool_calls, max_calls_per_turn, max_prompt_chars, max_tool_result_chars,
  continue_guidance_fn, verification_tools, allow_unverified_finish)` @~230 — the engine shared
  by workers and Unified Idle Mind. JSON protocol: model replies either `{"tool_calls":[{tool,args}]}` or
  `{"status":"completed","summary",...,"speak":bool,"speak_text"}` or `{"status":"failed",...}`.
  Invalid JSON → the parse error is fed back for self-correction; `min_tool_calls` enforcement
  counts TOTAL executed calls (`total_tool_calls`, not unique tool names — 5 search_web calls
  count as 5). All four `min_tool_calls` rejection sites go through `_force_tool_call`, which
  names `verification_tools`, detects a verbatim repeat, and honours `allow_unverified_finish`
  on the last turn (see §5.2c — a nudge naming the wrong tool deadlocked the care agent). **Hard tool-call budget** (stops runaway research loops — on cloud the model can
  batch a 20-call `tool_calls` array AND keep doing so turn after turn, so `max_turns` alone
  doesn't bound executed calls; one idle cycle fired 50+ searches): `max_calls_per_turn`
  (default 5) caps how many calls one turn executes (rest deferred to the next turn);
  `max_tool_calls` (0 = unlimited; idle 6 / deep 12 / reflection 6 / worker 12 from config) is a
  TOTAL ceiling — once spent the loop refuses further calls and forces a final-JSON wrap-up
  (2 nudges via `_MAX_FORCED_WRAPS`, then bails with what's gathered).
  `continue_guidance_fn(total_tool_calls, tools_used)` optionally replaces the
  generic "Continue with your task" tail after each tool round (deep-research coaching), but the
  budget wrap-up message overrides it once the ceiling is hit;
  `_compact_conversation` clamps the RECENT entries too when head+recent+middle-floor bust
  the budget (head always verbatim); `stop_event` checked between turns and
  before each tool call (Unified Idle Mind interruption); `execute_python_code` runs via a temp
  file in a subprocess. Returns `(success, result, speak_text, final_json, tools_used)`.
- `execute_worker(worker)` @~395 — builds the worker prompt (task + time + tools + recent
  vision/face context + retry note) and runs the loop.

**`worker_manager.py` (510)** — `WorkerManager` singleton (`get_worker_manager`):
persistence to `workers.json`, CRUD (`create_worker` validates triggers, caps at 20 active),
scheduler thread (30s tick → time + recurring triggers), `fire_event(name)` (event-triggered
workers; face_detected filtered by person condition), `_execute_worker_background` (runs on
the main loop; recurring/event workers reset to pending instead of completed; result
injected into chat history as a system message; optional spoken result via `_speak_text` →
TTSStreamer + assistant-message append).

### 5.14 `core/vision/`

**`camera.py`** — `capture_photo_b64()`: one frame from `http://localhost:5000/mjpeg` via
OpenCV → JPEG → base64. Debug frame save gated behind `KIKI_DEBUG_SAVE_FRAME` env var.

**`vision_handler.py` (≈128)** — `VisionHandler`: two timers — the question timer
(`agent.question_*_interval_seconds`, currently 1000–3000s, drives both in-conversation question
injections and the periodic loop) and the slower vision-QA timer
(`vision_injection.qa_interval_*`, 30–60 min). `run_vision_update(force_trigger, force_qa)`:
skips while Unified Idle Mind; captures a photo; analyzes on the local box
(`generate_background`, preemptible) **with cloud fallback when the box refuses/fails**;
records into the shared vision history; then either queues an `("autonomous_vision", ctx)`
event (spoken proactive comment, QA path) or silently injects the text. Silent injection
is now **eager + pre-warmed**: `history_inject_fn` (set by main.py) appends the context to
`message_history` immediately and calls `register_history` so the background rewarm bakes
it into the warm prefix — the next turn's prefill is a cache hit instead of paying those
tokens on the speaking path. Before a spoken QA event is queued,
`proactive_prompt_fn` requires a fresh grounded source and suppresses low-value
empty/quiet-scene interruptions. Refused while a turn is mid-flight (`turn_active`) or during
summarization → falls back to the old `pending_vision_context` (injected at next turn).
Known people list comes from the Hailo train directory.

### 5.15 `core/self_extend/`

- **`skill_manager.py` (128)** — local SKILL.md skills under `skills/`: list (name + 400-char
  preview), create (dir + SKILL.md + extra files), `get_skills_summary()` injected into the
  system context at startup.
- **`smithery_cli.py` (221)** — subprocess wrapper over the authenticated `smithery` CLI:
  mcp search/add/list/remove, tool find/list/call, skill search/view, and
  `install_skill_to_kiki` (full SKILL.md content → local skills dir).
- **`mcp_data_access.py`** — compact model-facing access over the connected `gmail`
  and `notion` Smithery services. Gmail list reads force metadata-only mode and return
  bounded snippets; individual messages decode the preferred text/plain MIME part and
  discard raw headers/HTML. Notion search sets server-side page/highlight limits and
  `read_notion` bounds selected enhanced-Markdown content. These helpers unwrap duplicate
  Smithery envelopes before returning results. The Instagram connection was removed.
- **`whatsapp_mcp.py`** — owns one long-lived stdio session to the bundled 12-tool
  WhatsApp FastMCP server on a daemon event-loop thread. It starts/checks the Go bridge
  without waiting, resolves unique contact names for sends, bounds returned message
  content, and exposes startup/poll helpers without importing the MCP SDK on Kiki's
  foreground import path.
- **`mcp_manager.py` (396)** — MCP registry client, Claude-Desktop-config management,
  FastMCP server code generation (`MCPServerCreator`).
- **`kiki_self_extend_agent.py` (449)** — autonomous JSON-loop agent (same
  thought/tool/done protocol) over `generate_llm_resp.generate`; goal-driven skill/MCP
  installation. Triggered via the `self_extend_run_task` tool or (when enabled) Unified Idle Mind.

### 5.15a Gmail MCP: two auth layers and the 2026-08 server swap

Smithery replaced the server behind the `gmail` slug with an **Arcade-backed** one. Same
`connectionUrl` (`https://server.smithery.ai/gmail`), completely different surface: 30
`Gmail_*` tools instead of `fetch_emails` / `fetch_message_by_message_id` /
`fetch_message_by_thread_id`. An existing connection keeps talking to whatever version it
was created against, so **the break lands when the connection is recreated**, not when the
server changes — re-authenticating replaces the connection and silently swaps the tool set.

**Authorization is two independent layers, and they report separately:**

| layer | checked with | failure looks like |
|---|---|---|
| Smithery connection | `smithery mcp get gmail` | `status.state: auth_required` + `connect.smithery.ai/...` URL |
| Arcade → Google grant | `smithery tool call gmail Gmail_WhoAmI '{}'` | returns an `accounts.google.com` OAuth URL instead of the profile |

Layer 1 can read `connected` while layer 2 is still unauthorized — that mismatch is what
makes this confusing to diagnose. **`Gmail_WhoAmI` is the real health check**: it returns
`my_email_address` only when both layers are good.

To (re)authorize both scopes at once, ask the server for a combined link rather than
walking into consent twice:

```
smithery tool call gmail System_ManageAuthorization \
  '{"action":"authorize","tools":["Gmail_ListEmails","Gmail_SearchEmailsByQuery",
    "Gmail_GetEmail","Gmail_GetThread","Gmail_SendEmail","Gmail_WhoAmI"]}'
```

Naming `Gmail_SendEmail` is what pulls `gmail.send` into the grant; a read-only
authorization leaves `send_care_email` broken. Re-run with `"action":"status"` to confirm.

**Response shapes differ between the read tools** (`mcp_data_access._compact_email`
normalises all of them):

- `Gmail_ListEmails` / `Gmail_GetEmail` / `Gmail_GetThread` → `id`, `from_`
- `Gmail_SearchEmailsByQuery` → `message_id`, `sender`
- Bodies arrive pre-parsed as `body` (plain) and `html_body`. There is **no** base64 MIME
  `payload` tree any more; the old `_part_text()` walker is gone.
- Paging moved into a `pagination` object (`total_estimate`, `next_page_token`).
- `Gmail_ListEmails` takes `n_emails`, `Gmail_SearchEmailsByQuery` takes `max_results`;
  passing `limit` to either is a hard `TOOL_RUNTIME_BAD_INPUT_VALUE`.

`read_gmail` picks the search tool when given a query and the list tool otherwise, and
passes `exclude_automated: False` to preserve the old server's unfiltered behaviour. Flip
it to `True` to drop promotions/social/updates and no-reply senders.

Smithery reports tool and auth failures **inside a normal envelope with exit code 0**
(`isError: true`), so `_decode_smithery` checks `isError` first and surfaces the text; that
is why an expired grant used to read as `{"error":"error: unknown error"}`.

### 5.16 `hotwords/hotword_recog.py`

OpenWakeWord on `kiki.onnx`, threshold 0.5, 4.5s detection cooldown. The key mechanism is
**pause/resume around Kiki's own speech**: `pause()` while the bot talks (frames are still
drained so the buffer never overflows, but no inference — the bot can't wake itself);
`request_resume()` arms quiet-detection: the loop resumes only after 3 consecutive frames
under RMS 500 (~240 ms of silence) or a 2.5s hard cap, then `owwModel.reset()` clears
activations accumulated from the bot's own audio and the cooldown is shortened to 0.3s so
the user isn't locked out. `listen()` yields `"heyy"` (main.py's expected wake token).

### 5.17 `robot/`

- **`face_handler.py` (99)** — resilient async loop: connect to KikiController, consume
  face and `hand_gesture` events. Hand controls bypass face rate limits and are routed to
  main.py immediately (the controller connection remains active for gestures even when
  conversational face injection is disabled). Face events record into FaceHistoryBuffer; fire `face_detected`
  workers; rate-limit injections to 2 per 5 min; known face during Unified Idle Mind →
  `interrupt` + `("face_wake", name)` event (forces a spoken vision QA); inject
  `[System: '<name>' has just appeared...]` into history (append-only).
- **`movement.py` (113)** — `<turn(90)>`, `<forward(50)>`, `<move(angle,dist)>`-style tag
  regexes; `extract_movement_tags` (→ dicts), `strip_movement_tags` (applied to each
  sentence before TTS; the stripped text is the spoken/`clean_response` copy, while the
  copy STORED in history stays verbatim with its tags — see §4 rule 2),
  `execute_movements` (legacy direct-GPIO path via `motor_control`; the `move`/`dance`
  TOOLS use the ZMQ motor server instead).
- **`motor_control.py` (380)** — low-level gpiod + SoftPWM mecanum driver (pins, trims,
  serial). Primarily used by the separate motor-server process; tools.py deliberately never
  touches GPIO directly ("Device or resource busy" elimination).

### 5.18 `tools_and_config/`

- **`config_loader.py` (73)** — loads `.env` + `config.json` ONCE at import into module
  global `CONFIG`; the `get_*_config()` accessors return live references (mutations would be
  shared — treat as read-only). **A config change requires a restart.**
- **`logger.py` (111)** — `_Tee` wraps stdout/stderr: every write mirrored to
  `logs/kiki.log` with a per-line `HH:MM:SS.mmm` prefix; size rotation at `max_bytes`
  (5 MB, one `.1` backup). `debug(tag, msg)` prints only when `logging.debug` is true
  (Unified Idle Mind prompts/responses, stream stats).
- **`tools.py` (1678)** — all tools. Layout:
  - utilities @25–170: `should_skip_followup` (one-shot flag set by play_music/dance so
    main.py skips the follow-up listen), `set_neck_active` (sync variant),
    `KikiMotorClient` (stateless per-call ZMQ REQ to the motor server @5557 with
    `VALID_MOTOR_ACTIONS` — the full mecanum vocabulary), Exa client lazy-init
    (**⚠ hardcoded API key @149**), shared KikiController getter.
  - tool impls @174–1052 (all async): `search_web` (Exa, 3 results, highlights, IN locale,
    time ranges), `execute_shell_command` (10s timeout), `get_current_time`,
    `recall_memory` (→ journal.search), `switch_voice`, `switch_mode`, `set_followups`,
    `adjust_volume` (PulseAudio default sink), stateful YouTube music tools (`play_music`,
    exact-video likes, liked-song playlist, last song, pause/resume/next/previous) backed by
    `core/media_manager.py`, `set_timer` (validated in-process countdown + mpv alarm),
    `update_knowledge` (full KB CRUD grammar over categories/actions/attributes),
    `remember_me` (face training via controller), `track_person`, `follow_me`,
    `move(steps)` (threaded step sequencer via motor server; per-step interval/duration/
    speed clamps), `dance(song, steps)` (music + choreography interleaved; waits for mpv
    audio, **hardcoded 20s pre-choreo sleep @657**, pause steps, cleanup killpg),
    worker tools (`schedule_worker`/`cancel_worker`/`list_workers`), `execute_python_code`
    (subprocess with the `/home/kiki/Kiki/kiki/bin/python` venv), five compact
    Gmail/Notion read tools, and `self_extend_*` wrappers over
    skill_manager/smithery_cli/mcp_manager/agent.
  - `TOOLS` @1058 — OpenAI function schemas for every tool (the `dance` schema embeds an
    entire choreography style guide; `move` documents the turn-rate constant).
  - dispatch @1575: `_ASYNC_TOOL_HANDLERS` map; `execute_tool` (sync — used by the
    speaking-path loop; runs the coroutine in a throwaway thread+loop when already inside
    an event loop, 30s cap); `execute_tool_async`; `get_tool_descriptions` (name→desc) and
    `get_detailed_tool_descriptions` (full text for agent prompts).

### 5.18b `core/ir_controls.py` — IR-sensor + LCD control surface

Two active-LOW IR proximity sensors on `/dev/gpiochip4` (hand over sensor ⇒
`Value.INACTIVE`): **LEFT = GPIO 22** (Kiki's left), **RIGHT = GPIO 17** (Kiki's right).
`IRControls` polls both at ~33 Hz on a daemon thread and degrades to disabled if
`gpiod`/the lines are unavailable (runs off-robot fine).

- **NORMAL mode** — holding either sensor is push-to-talk: it immediately interrupts
  music/speech/generation, opens STT, and commits on release. Double-tapping the same
  sensor within `DOUBLE_TAP_GAP_S` fires `on_double_tap`; `main.py` aborts active audio/
  generation, discards the gesture's pending STT events, closes the query window, and
  returns Kiki to idle. *Both sensors held* `BOTH_HOLD_S` ⇒ open settings.
- **Release debounce is asymmetric.** A hold asserts on the *first* present sample so
  push-to-talk feels instant, but cancelling a pending release needs
  `HOLD_REASSERT_SAMPLES` (2, ≈60 ms) consecutive present reads. These sensors chatter at
  the edge of their cone and pick up ambient IR, and a single spurious read used to reset
  the release window on every poll — the hold then never ended and Kiki kept listening
  after the hand was gone. `HOLD_MAX_S` (25 s) is the backstop for a line stuck asserted:
  it commits the turn and refuses to re-arm until the sensors genuinely read clear.
  `clear_hold()` drops a hold without committing (used by the thumbs-up path).
- **SETTINGS mode** (modal; main.py mutes STT + pauses the hotword recognizer via
  `on_enter_settings`, restores via `on_exit_settings`): tap RIGHT ⇒ selection left, tap
  LEFT ⇒ selection right; long-hover (≥`LONG_HOVER_S`) either sensor ⇒ select. Menu =
  `["BT Volume", "Restart", "Exit"]`. **BT Volume** sub-screen: tap LEFT `+VOL_STEP` / tap
  RIGHT `-VOL_STEP` applied live via `pactl set-sink-volume @DEFAULT_SINK@ N%` (the BT
  speaker is the default bluez sink), long-hover confirms. **Restart** re-execs the process
  (`os.execv(sys.executable, [sys.executable]+sys.argv)` — restarts main.py in the same
  venv). Auto-exits after `IDLE_EXIT_S` of no gesture.
- **Tap vs long-hover** is per-sensor press timing; `_settings_armed` ignores input until
  both sensors clear once after entry/select so the resting hands don't auto-navigate.
  A same-sensor double-tap in settings exits the menu and returns to idle as well.
- Wired in `main.py` right after `idle_mgr` (closures `ir_talk_hold_start`/
  `ir_talk_hold_end`/`ir_return_to_idle`/settings callbacks), `ir_controls.stop()` in
  the shutdown `finally`.

Camera gestures use the same immediate, event-driven control principles. With
`hailo_follower_webcam_only.py --hands`, the hand worker reuses its already-smoothed
classification result and publishes `hand_gesture` over the existing controller PUB socket.
Holding a control pose emits once; that same pose must be absent continuously for 0.8s before
it can fire again. Poses listed in `CONTROL_GESTURE_HOLD` get a stricter false-positive gate:
their smoothed *and* current raw label must both agree at a minimum confidence for a dwell
time (with a 0.15s dropout grace) before firing — `mute` needs >=92% for 1.5s, `thumbs_up`
>=85% for 0.4s. The control mapping is:

- `mute`: toggle persistent output mute. A reply uses `LCDOnlyStreamer`, so model generation
  and prompt caching stay unchanged while TTS synthesis/playback are skipped entirely.
- `open_palm`: invalidate in-flight activities, stop music/filler/TTS, abort current model
  generation, and turn off active neck tracking. A music URL resolution racing the gesture
  checks the stop generation before it is allowed to launch `mpv`.
- `peace`: perform the same terminal cleanup as IR double-tap and return to hotword/idle.
- `thumbs_up`: **end of listening** — "I'm done, answer now." Clears any IR hold via
  `ir_controls.clear_hold()` and calls `stt.commit_now()`, which drops the push-to-talk
  hold and sets the force-commit flag so the endpointer emits `final` + `endpoint`
  immediately instead of waiting out the silence window. This is the manual escape hatch
  for rooms so noisy that the VAD never sees enough trailing silence on its own.

### 5.18c `core/lcd_display.py` — 16x2 char LCD

`LCDDisplayManager` singleton (`lcd_manager`) over an I2C PCF8574 16x2 LCD (RPLCD; emulated
print-only when the lib/panel is absent). Async write worker thread coalesces a backlog to
the latest frame; `write`/`clear`/`update_status(action,details)` (maps states→layouts),
`display_stream`/`wrap_text_16x2` (sliding 2-line scroll), `_clean_text` (strips
markdown/bracket/XML tags). Speech frames carry a playback-session id, so barge-in or turn
completion invalidates queued words before they can overwrite the next status. Physical
writes overwrite both padded rows without clearing the panel, avoiding flicker and reducing
I2C latency; commit timestamps are exposed only for calibration/tests. Used across main.py,
tts.py, main.py, and ir_controls.py.

### 5.18d `core/tts_sync.py` + `scripts/calibrate_lcd_sync.py` — calibrated LCD speech clock

Local TTS keeps its existing first-PCM streaming path: **LCD synchronization never buffers
audio and never delays the current ~0.8s time-to-first-word**. On the first audible PCM chunk,
`LocalTTSStreamer` creates a word schedule from the already-known TTS text and a preloaded
per-voice/per-script calibration profile. Every PCM chunk advances the master audio clock;
if synthesis or a tool-result follow-up starves the `aplay` pipe, future word times are
re-anchored to the new audible window instead of accumulating drift. Expression tags consume
calibrated time but are not printed. Cached fillers, tool bridges, normal responses, and
follow-up responses all use this one path.

Devanagari speech cues are converted to ASCII Hinglish only in the independent LCD worker,
after PCM has already entered `aplay`. The dependency-free Unicode converter makes no API or
network calls and never changes the text sent to TTS, the timing schedule, or the playback
critical path; mixed Latin/Devanagari replies retain their existing Latin text.

Whisper is **calibration-only**. Running `scripts/calibrate_lcd_sync.py` manually synthesizes
known English, Hindi and mixed-language phrases for every server voice, requests token
timestamps from the existing whisper.cpp server, fits the small timing profile, and refuses
to replace the profile unless the configured 150ms validation bound passes. Neither
`core/tts.py` nor `core/tts_sync.py` imports/calls Whisper during a conversation. A missing,
stale-speaker, or missing-voice profile fails closed: audio still streams normally while the
LCD shows `Speaking... / Calibration req` rather than knowingly mistimed words.
Run it with Kiki stopped so it can briefly own the microphone and speaker:
`source /home/kiki/Kiki/kiki/bin/activate && python scripts/calibrate_lcd_sync.py`.
The startup wizard's `Sync LCD+audio?` step (§3.0) runs this same script unattended at the
end of boot — it is still the only thing that writes the profile, and it is still never
imported or called by main.py.
It speaks the short English latency phrase three times, measures real Bluetooth + LCD write
latency, and writes `tools_and_config/lcd_sync_calibration.json`. Hindi/mixed Whisper text is
treated as non-authoritative: mismatches are skipped and the Devanagari fallback is fitted
from deterministic source-PCM duration instead of forcing an incorrect transcript match.

### 5.18e `core/wifi_setup.py` — boot-time Wi-Fi provisioning

Standalone synchronous boot UI using the same active-LOW GPIO22/17 sensors, but not the
runtime `IRControls`/`STTEngine` objects (they do not exist yet). `IRWifiSetup` scans and
connects through passwordless-sudo `nmcli` calls (the system service has no active desktop
PolicyKit session), retains each AP BSSID for reliable activation, sorts/de-duplicates
SSIDs by signal, and releases its gpiod line request in a `finally` block.
`LocalPasswordDictation` captures
only while LEFT is held and runs local whisper.cpp after release. Pure helpers
`parse_nmcli_networks` and `apply_password_dictation` contain the escaped-SSID and spoken
character/edit parsing logic and are covered by `tests/test_wifi_setup.py`.

### 5.18f `core/oled_display.py` + `robot/oled_tags.py` — the face

A single 128x64 SSD1306 at 0x3c sharing bus 1 (and `I2C_LOCK`) with the char LCD. There
are no bitmap assets: every frame is procedurally drawn PIL geometry around a 15x16
logical-pixel crab sprite (`_crab`), rendered directly at native x4 scale for crisp 1-bit
edges. `OLEDDisplayManager` is a singleton with a
daemon render thread; `set_state` is a lock-guarded flag flip, so **no caller ever blocks
on an animation**. Each state is one `_draw_<name>` method resolved by `getattr`, plus a
`VALID_STATES` and `_STATE_FPS` entry — that is the whole contract for adding one.

Two layers drive it:

1. **System state** — `speaking`, `listening`, `thinking`, `tool`, `music`, `workers`,
   `idle_mind`, `face`, … pushed by the runtime, and by `lcd_display.update_status`, whose
   keyword mapper fans every LCD status string out to the OLED.
2. **Expression tags** — the speaking model emits `<oled:name>` inline and Kiki's face
   changes to match what it is saying. `EXPRESSION_STATES` (19 moods) is the single source
   of truth for tag validity *and* for the prompt vocabulary
   (`get_oled_tag_prompt_note()`), so what the model is taught can never drift from what
   can be drawn.

**Priority.** `set_expression` only overrides `_EXPRESSION_OK_FROM` (`speaking`, `tool`,
another expression) and only accepts `EXPRESSION_STATES`, so a reply can colour its face
but can never claim to be listening or running a tool, and a late tag cannot stomp a face
card or worker progress. The one inversion: `set_state("speaking")` will *not* replace a
held expression, because `speaking` is the baseline face for a turn and main.py's
first-play callback would otherwise race the player and win. The turn's closing
`set_state("idle")` releases the hold.

**Timing (why this is not the neck-tag dispatch).** Neck gestures are collected for the
whole turn and fired after it. That is wrong for a face: the LLM streams several sentences
ahead of the voice, so firing on parse puts the expression ahead of the words. Instead the
tag rides in-band through `add_sentence`, is read off the raw text in
`LocalTTSStreamer._synth_worker` (before `sanitize_for_local_tts` strips all `<…>`),
travels on the `("meta", speakable, gap, voice, oled)` marker, and is applied by
`_play_worker` on the sentence's **first audible PCM chunk — after `_write_pcm`**. TTFW is
therefore untouched. Cloud/LCD-only streamers have no such marker and fire at queue time
via `_fire_oled_tag`, which also strips neck tags (previously only the local path did, so
cloud voices read them aloud).

**Latency rule.** A tag must never open a reply or a sentence — tokens spent before the
first word delay the voice directly. The prompt note says so, and `core/llm.py`
`_has_spoken_word` makes it a safety net rather than a dependency on model behaviour: a
flushed fragment containing only silent tags no longer clears `first_sentence_pending`, so
a leading tag can't disarm the eager first-sentence flush. Measured on the cloud scanner,
a leading tag cost 46 chars-to-first-audible before the fix and 21 after; a mid-sentence
or trailing tag costs exactly the baseline 6, i.e. nothing.

**KV-cache.** Identical contract to the neck tags (§4 rule 2): `message_history` stores
the reply VERBATIM with `<oled:…>` in it; only `clean_response` and the TTS text are
stripped. The prompt note is a *static* suffix appended once next to
`get_tts_system_prompt_note()` — it joins the warmed prefix, so turns still prefill nothing
but the user's new message. It is generated from a `sorted()` set precisely so it stays
byte-stable across restarts.

Covered by `tests/test_oled_tags.py` (parsing, registry consistency, priority, TTFW).

### 5.18g `scripts/power_blackbox.py` — why the Pi died

A 1 Hz flight recorder for power and thermal state, run by
`kiki-power-blackbox.service` (installed in `/etc/systemd/system/`, ordered
`Before=kikifast.service` so it is already recording when Kiki pulls up the Hailo
NPU, camera and TTS servers — the current spike a marginal supply dies on).
Samples land in `/var/log/kiki-power-blackbox.csv`; read them back with:

```bash
./scripts/power_blackbox.py --report          # verdict per boot session
sudo systemctl disable --now kiki-power-blackbox.service   # stop recording
```

Three failure modes look identical from a terminal after the fact, so the
recorder is built to separate them:

| Signature | Cause |
|---|---|
| No `#STOP`, samples evenly spaced, EXT5V sagging or under-voltage bit set | **Brownout** — supply/cable cannot hold 5 V |
| No `#STOP`, `late_s` growing beforehand, next boot's `rsts`/`wd_bootstatus` differ from baseline | **Watchdog reset** — PID 1 stalled past the 60s hardware timeout (`RuntimeWatchdogSec=1m`); a software hang, not power |
| `#STOP` present, `temp_c` ≥ 80 with throttle bits | **Thermal** — a thermal poweroff is still orderly |
| `#STOP` present, voltage and temperature normal | **Software / OOM** |

Two design points are load-bearing:

- **The `#STOP` marker.** systemd sends SIGTERM on every orderly shutdown, so
  its *absence* proves the board was cut off rather than shut down. Every sample
  is `fsync`ed for the same reason: the last line before a hard cut is the whole
  point, and a buffered write would lose exactly that line.
- **`late_s` and the reset registers.** A brownout and a watchdog reset are
  indistinguishable from userspace — both just stop. But the watchdog can only
  fire after PID 1 stalls for 60s, and that stall shows up first as the sampler
  falling behind schedule. Each `#BOOT` marker also records `vcgencmd get_rsts`
  and `/sys/class/watchdog/watchdog0/bootstatus` verbatim (not decoded — the
  RSTS layout varies by model); a session that ended `CLEAN` supplies the
  known-good baseline the next boot is compared against.

Journald on Raspberry Pi OS ships `Storage=volatile`
(`/usr/lib/systemd/journald.conf.d/40-rpi-volatile-storage.conf`) to spare the SD
card, which throws away the log of the boot that died. `/etc/systemd/journald.conf.d/99-kiki-persistent.conf`
overrides it to `persistent` with a 400 MB cap. Delete that file and restart
`systemd-journald` to go back.

### 5.18h `kiki-cpu-cap.service` — trimming peak SoC current

A systemd oneshot that caps all four cores at **2200 MHz** (they share
`cpufreq/policy0`), applied `Before=kikifast.service` so the cap is already in
force during the heaviest part of startup.

The number is not arbitrary. Measured on this board under a 4-core busy load:

| Cap | VDD_CORE | Power | vs 2400 |
|---|---|---|---|
| 2400 MHz | 5.315 A | 4.63 W | — |
| **2200 MHz** | 4.451 A | **3.76 W** | **−19% power for −8% clock** |
| 2000 MHz | 4.158 A | 3.39 W | −27% power for −17% clock |
| 1800 MHz | 3.692 A | 2.92 W | −37% power for −25% clock |

2400 MHz is a boost bin — the firmware raises core voltage to reach it, so the
last 8% of clock costs a fifth of the SoC's power. Capping one step below is by
far the best power-per-clock trade available, and the saving compounds because
the voltage drops with it (0.846 V capped vs 0.870 V uncapped).

Tuning is one number in `ExecStart`, then `systemctl restart kiki-cpu-cap`.
`systemctl stop` restores `cpuinfo_max_freq`, so the change is reversible with
no reboot and nothing to undo. For a cap that applies from the very first
instant of firmware boot instead, `arm_freq=2200` in `/boot/firmware/config.txt`
does the same thing but needs a reboot to change.

This exists because the Pi hard-cuts under combined load (§5.18g): the failure
captured so far was at the Hailo + webcam bring-up, and it also happens mid-
conversation with the MCP server, webcam and neck stepper active. Frequency
capping is the only lever that reduces *actual* draw without giving up a
peripheral — `usb_max_current_enable` only caps what the Pi will supply, and
with a single webcam on USB there is little there to reclaim.

### 5.18i CLIP care-event cascade — senior activity detection

Zero false positives is a property of the pipeline, not of CLIP. CLIP only
*nominates*; five gates in sequence decide, each cheaper than the next, so the
expensive judgement runs on a few candidates an hour rather than 30 frames a
second.

| Gate | Where | Rejects |
|---|---|---|
| 01 prompt geometry | `~/Kiki/navigation/health_texts.json` → `clip_prompts.json` | semantic neighbours (phone-to-ear scoring as drinking) |
| 02 margin + persistence | `care_gate.py` | single-frame spikes, motion blur, flicker |
| 03 plausibility | `care_gate.py` | too-brief runs, repeats inside the refractory |
| 04 VLM adjudication | `core/senior/health_events.py` | the confident-but-wrong CLIP match |
| 05 care relevance | `core/senior/health_events.py` | true but pointless interruptions |

**Write prompts for pixels, not meaning.** CLIP cannot see *water* — only a
vessel travelling to a mouth. `"person raising a cup or bottle to their mouth"`
works where `"person drinking water"` cannot; naming the drink is gate 04's job.
Six positives sit against **eleven negative distractors**, and the negatives do
most of the work: the matcher discards any crop whose best match is one of them.

Scores are a **softmax over all 17 prompts at temperature 100**, so a similarity
is a probability summing to 1 across the set (uniform = 0.059, a clear winner
lands 0.5+). That is why thresholds look low next to a raw cosine. The callback
recomputes the distribution itself rather than using `matcher.match()`, which
returns only the single best entry per person even with `report_all=True` — the
margin test and the debug panel both need the runner-up and the losing
negatives.

**Tuning workflow, no calibration pass.** `~/Kiki/navigation/care_events.json`
is **hot-reloaded** on mtime, so thresholds change while the service runs. The
MJPEG stream at `:5000` carries a debug panel showing, per track: the winning
event, an evidence bar (`[####------] 4/5`), dwell, margin, the current blocking
reason, and **the raw top-3 including negatives**. That last line is the
diagnosis — a negative sitting just under the positive means the prompt set
needs a distractor, not a lower threshold. Colour: green fired, amber building,
red blocked by a distractor (which it names).

Rebuild embeddings after editing prompts:

```bash
~/Kiki/navigation/scripts/build_clip_prompts.sh    # RN50x4, 640-d, offline
```

The variant is load-bearing: the HEF emits 640-d image embeddings and the
callback refuses to match a prompt file of any other width. `RN50x4.pt` is
cached in `~/.cache/clip`, so generation needs no network; runtime matching is
pure numpy and never imports torch.

Confirmed events reach the model the same way face events do: a natural-language
`[System: Kiki just saw this happen — ...]` line via `hot_inject`. It states the
observation and stops — whether it is worth mentioning is the model's call, not
something the injection preempts. Behind the
`care_events` context source (so a roleplay mode suppresses it) and a rolling
`max_injections_per_5min` cap. The cap is not optional — `unsteady` alone fired
nine times in ten minutes during testing, and an uncapped injection would push
the real conversation out of the window. Spoken events are *not* also injected:
their `autonomous_vision` prompt already carries the description into history.

A rejected candidate still donates its scene description. Gate 04 pays for a
VLM look at the room on **every** candidate and most are rejected, so that
description used to be discarded. It now goes through `_vision_history_inject`
— the same injector `core/vision/vision_handler.py` uses — so it lands as
`[WHAT KIKI SEES]: ...` with the existing turn-active guard, prefix rewarm and
`vision` mode gate, rather than becoming a second kind of scene note. It also
records into `get_vision_history()` so workers see it. Confirmed events are
excluded (their care line already carries the description) and so is
`NO PERSON`, and it runs on its own `max_scene_injections_per_5min` budget so a
burst of rejections cannot crowd out real care observations.

Speaking is a real turn, not a note. `_care_speak` in `main.py` queues
`("autonomous_vision", prompt)` onto `stt_queue` — the same route the periodic
spoken question uses, which appends the text and opens a turn immediately. An
earlier version `hot_inject`ed an instruction instead; that only surfaces when
the user next speaks, so a confirmed `heat_distress` sat silent while the WebUI
reported it as `spoken`. Telemetry that overstates what happened is worse than a
missing feature, especially in a pipeline whose whole purpose is not to claim
things it has not verified.

**Gate 04 judges the frame the event fired on**, shipped with the event as
`image_b64` (960px, q88, ~55 KB — the same mechanism `unknown_face_enrolled`
already uses). Capturing a fresh frame instead looked at the room **1-3 seconds
later** (ZMQ hop + HTTP fetch + inter-frame sleep), by which time the cup was
back on the table and the person had walked out of shot. Three true detections
died that way in one session, one at similarity **0.86**. With the real frame in
hand there is nothing to corroborate against — a later capture is a different
moment, not a second opinion — so the two-frame agreement rule now applies only
to the fallback path, where we are capturing live anyway and blur is the risk it
was written for.

**There is no person check in gate 04.** CLIP only produces a crop when its own
detector already found someone, so re-testing here was never verification — it
was a second, worse detector looking from further away in time. The prompt no
longer mentions a person, `NO PERSON` is not a verdict, and absence of
*evidence* is what rejects.

Gate 04 never asks a leading question — "is she drinking?" invites a yes. It
asks what the person is doing, open-ended, then tests whether that free answer
*entails* the activity. `UNCLEAR` is first-class and counts as a rejection, two
frames a few seconds apart must independently agree, and any vision failure
(offline, rate-limited) **fails closed**. A confirmed event is *logged*, not
spoken: `senior_mode.health_events.speak_for` is the short allow-list of
activities permitted to interrupt, under a per-window budget.

Enabling this took `hailo_follower.service` from `--vision-mode none` to
`clip` — 2 models to 4. Measured cost: rails peak 6.3 W (from ~4.2 W), SoC
70 °C (from 58 °C), EXT5V steady at 5.08–5.22 V with no cut. It fits inside the
headroom the CPU cap (§5.18h) bought, but it is not free — see §5.18g if the
board starts dropping again.

**The event set** (11 positives, 17 negatives). Beyond the original
`drinking` / `eating` / `medicine` / `sleeping` / `heat_distress`:
`exercising` (closes the loop on `care_plan.add_exercise` — a reminder becomes
confirmed adherence), `wearing_mask` (the hook into the PS's air-quality
alerts), `walking_aid`, `head_in_hands`, `wrapped_in_blanket`, and
`slumped_forward`.

`slumped_forward` is the hard one: working hunched over a laptop is visually
near-identical, and a wrong "collapsed" reading is the most alarming false
positive in the set. It gets three defences — a `leaning over a laptop or a
desk to work` negative, a raised margin (0.13) so it must win clearly, and a
`not` list in `_ENTAILMENT` that rejects the verdict outright if the vision
model's own words mention a laptop, desk, keyboard or book.

**`unsteady` was removed**, not tuned. `person holding a wall or furniture
while standing` fired **9 times in 10 minutes** on a healthy person because it
matched almost any standing pose near furniture — CLIP cannot separate
*steadying yourself* from *being near a wall*. Its prompt is now a negative, so
those crops are absorbed instead of firing, and `walking_aid` carries mobility
with a far more distinctive silhouette. A detector that cries wolf that often
is worse than no detector.

Growing the set from 17 to 28 prompts spreads the softmax mass and lowers every
winning score, so the default threshold came down 0.28 → 0.22. Expect this
whenever prompts are added; `margin` is the real protector, not `threshold`.

**Dropped from v1 deliberately:** `fall` and `chest_discomfort`. Both needed
pose or a vitals signal; a CLIP-only fall detector that misfires trains the user
to ignore it, which is worse than not having one. `coughing` is audio-shaped,
not visual, and belongs on the always-listen path.

### 5.19 `scripts/streaming_tts.py`

Self-contained gapless streaming-TTS library (mirrors `core/tts.py` LocalTTSStreamer
mechanics: sentence splitter with eager first split, synth-ahead worker, prebuffer-gated
single aplay pipe, edge-silence trim) + the **`FILLERS` list** (72 Kiki-personality filler
lines, grouped by style: robot-body humor, teasing Vaibhav, mock drama, TARS deadpan,
swagger, mock exasperation, curious hums, warm beats, quirky one-offs — all TTS-safe, no
contractions, only `SUPPORTED_TAGS`) and `generate_fillers(tts_url)`
(`--generate-fillers`) which synthesizes each filler to
`sound_effects/soundeffects/fillers/filler_N.wav` — the directory `ThinkingSoundPlayer`
plays from. CLI: `--demo`, stdin mode, `--wav` sink.

### 5.20 Tests

**`tests/` — the ordinary suite.** 1101 tests, ~27 s, no external services and no live
box; `pytest` or `pytest tests/` both work (`pytest.ini` pins collection to `tests/`, so a
bare run no longer walks the parked code in `to_do/`). `tests/conftest.py` points every
test at a throwaway `care_plan.json`, because several modules read the real one through a
process-wide singleton and the suite's answer to "is this a regression?" otherwise depended
on what the robot happened to be doing — read its docstring before adding a fixture.

Roughly grouped, so you can find the file that owns a behaviour:

| Area | Files |
|---|---|
| Senior care | `test_care_*` (9), `test_senior_care_plan_tools`, `test_guided_exercise_cadence`, `test_session_cues`, `test_engagement_routing`, `test_health_events`, `test_heart_rate_care`, `test_companion_routines` |
| Speaking path + agents | `test_action_agent`, `test_agent_loop_budget`, `test_agent_loop_completion_claim`, `test_tool_call_recovery`, `test_eager_split`, `test_mode_capabilities`, `test_sourced_context_routing`, `test_fast_cloud_vision`, `test_instant_vision_capture` |
| Memory + idle mind | `test_unified_idle_mind`, `test_auto_recall`, `test_memory_search`, `test_memory_tool_routing`, `test_history_view`, `test_ambient_listening` |
| WhatsApp / MCP | `test_whatsapp_contacts`, `test_whatsapp_mcp`, `test_whatsapp_recipient_resolution`, `test_mcp_data_access` |
| Audio + I/O | `test_endpointer_crowd`, `test_near_field_gate`, `test_noise_suppression`, `test_audio_output`, `test_media_manager`, `test_tts_lcd_sync`, `test_oled_tags`, `test_gesture_controls`, `test_ir_hold_release` |
| Boot + config | `test_startup_config`, `test_kiki_boot_bluetooth`, `test_wifi_setup` |
| Workers + infra | `test_worker_*` (3), `test_runtime_controls`, `test_environment`, `test_care_snapshot`, `test_cloud_budget_exempt`, `test_turn_observability`, `test_search_web_timeout`, `test_zmq_no_leak` |
| Dashboard | `test_webui_speak` — the "Speak" director cue, browser → Flask → foreground queue → injected `system` message (§5.23) |

`test_care_gate.py` self-skips unless `/home/kiki/Kiki/navigation/care_gate.py` exists —
the CLIP gate lives in the un-versioned Hailo tree (§5.18i).

Most of these files pin a *specific production failure* rather than a unit — `test_zmq_no_leak`,
`test_worker_retry_storm`, `test_tool_call_recovery` and `test_agent_loop_completion_claim`
each exist because of an incident described elsewhere in this document. Deleting one throws
away the only guard against that failure recurring.

**`tests_llamaserver/` — live-box tests.** Not collected by the ordinary suite; they need
the llama.cpp box up and they take minutes. Run them after touching any cache machinery.

`tests_llamaserver/test_prefill_e2e.py`

Three live tests against the box (run after touching any cache machinery):
1. cold turn → rewarm (history+assistant, max_tokens=1) → next turn must be ≫ faster
   (verified: 24s → 1.26s TTFT);
2. mutating an earlier system message → demonstrates re-prefill cost (why §4 rule 1 exists);
3. `generate_background` aborted mid-prefill via `preempt_background()` — preempt must
   return in ~0 ms and the request must die promptly.

### 5.21 `kiki_control_client.py`

`KikiController`: async ZMQ REQ (commands: neck_movement, mode, target person, train_person,
full_body_movement, state queries) + SUB (`listen_events` async iterator: face_detected /
face_lost / training_complete) against the Hailo pipeline at `controller.host` (192.168.1.11).
`quick_command()` one-shot helper (used for the motor relay).

### 5.22 `kiki_startup.sh`

Boot script: exports Pulse/DBus/X env (audio from systemd context), traps for graceful
shutdown of all child PIDs, launches the process stack (Hailo pipeline, motor server,
camera stream, main.py) each in its own venv.

### 5.23 `core/observability.py` + `webui/server.py` — Dashboard

`Recorder` keeps non-blocking flat events plus grouped sessions for speaking turns,
Unified Idle Mind, workers, tools, vision, and summarization. Unified Idle Mind opens one
`idle_mind` session and `run_agent_loop` records every model turn and tool result under
that session.

The Flask dashboard on `:8090` exposes Controls, Sessions, Live Feed, Context, and the
full config editor. Curated controls cover Unified Idle Mind scheduling/tool budgets,
active cloud limits, workers, summaries, vision, and routing.

`CloudBudget` enforces global limits plus active categories
(`idle_mind`, `vision`, `summary`, `reasoning`, and `face_enroll`). A cap of zero
means unlimited. Unified Idle Mind reads its dedicated provider/model/fallback/thinking
settings at process startup; restart after changing those values.

**The header "Speak" box — a director cue, not a question** (2026-09-08). Type what
Kiki should *do* ("ask Vaibhav to drink water", "give today's WhatsApp updates and the
news"); Kiki performs it out loud in character and then the ordinary follow-up window
opens so the room can answer. `POST /api/query` → `_query_handler` →
`make_webui_speak_handler(loop, stt_queue)` (main.py), which only does
`loop.call_soon_threadsafe` and returns — Flask's thread never touches the speaking
path. The cue lands on the SAME foreground queue as microphone events as
`("webui_instruction", text)`, so pressing Speak mid-sentence queues behind the current
turn instead of talking over it.

- `TURN_OPENING_EVENTS` (main.py) is the list of events that open a full turn. An event
  name missing from it is dropped by the loop **without a word** — the button would
  report success and Kiki would stay silent. `_PRESERVED_INPUT_EVENTS` is the matching
  list for the end-of-turn drain, which exists to delete late ASR events and would
  otherwise eat a cue queued during the previous turn.
- `open_directed_speech_turn()` injects `WEBUI_SPEECH_CUE` as a **`system`** message and
  appends no `user` message — same shape as `care_session_start` and
  `autonomous_vision`. A fake `user` turn is a lie the KV cache then carries for the
  rest of the conversation: Kiki would "remember" being asked, and the summarizer would
  write it into long-term memory as the person's request.
- The cue text names the two failure modes this prompt shape always has — reciting the
  instruction verbatim (see §5.2 on the box quoting prompt examples) and prefacing with
  "you asked me to tell you". `guided_care_turn` is forced False so a live care session
  cannot swallow the operator's cue.
- Everything after the injection is the ordinary lifecycle, so tools work: a dry run of
  "tell the whatsapp updates and news for the day" routes to `complex_query`, and the
  tool-round loop speaks the result. Tests: `tests/test_webui_speak.py`.

### 5.24 `core/senior/` — Senior Citizen Mode (elderly-care addon)

Strictly **additive** addon; active only while a mode declaring the `care` capability is selected
(`senior`, `health_sih` — see the mode-capabilities bullet below). Reuses the
existing speaking path, workers scheduler, face recognition, memory search, Unified Idle Mind and ambient
listening — it adds a caregiver **care plan** and family **email** on top.

- **`care_plan.py`** — `CarePlan` over `care_plan.json` (gitignored; path from `senior_mode.care_plan_file`).
  Atomic tmp+`os.replace` saves, module singleton (`get_care_plan_store`). The canonical daily-plan unit is
  `routine_events`: `{id,title,category,schedule,session_brief,continuous_vision,source,evidence,adaptation}`.
  `session_brief` is a rich natural-language handoff containing intended outcome and known context—not
  fixed dialogue or an executable action queue. At session start, `active_session` freezes the complete
  care-plan snapshot. It then records only real microphone speech, delivered assistant speech, tool use,
  and grounded visual evidence in `transcript`; there is no action index and no generated user response.
  The care model handles deviations naturally without rewriting future occurrences unless explicitly asked.
  Legacy `actions`, `reminders`, and `exercises` remain readable; a due legacy item is adapted into the same
  conversational foreground session rather than sent through WorkerBrain. `senior`, contacts,
  approved content and the rolling `care_log` (≤500) remain compatible. Schedule shape:
  `{"kind":"recurring","value":<sec>}` | `{"kind":"daily","value":"HH:MM"}` | `{"kind":"once","value":"<ISO>"}`.
- **`senior_care_manager.py`** — `SeniorCareManager` bridges the care plan onto `WorkerManager`.
  `activate()` materializes one worker per enabled routine/reminder/exercise + a daily-summary worker;
  `deactivate()` deletes every `senior:*` worker (via `WorkerManager.remove_workers_by_prefix`, not
  `cancel_worker` — cancelling left the rows in `workers.json`, so each care-plan edit accumulated
  copies until five real events had become twenty-four workers); `sync_workers()` rebuilds after a
  voice edit. `care_plan.json` itself is never touched, so the plan survives any number of mode switches.
  Daily schedules are recurring 86400s workers whose `last_fired_at` is back-dated so the first fire
  lands at HH:MM (§workers scheduler uses elapsed-since-last-fired). All routine/reminder/exercise workers
  are timing-only: they open persisted state and invoke a callback that places `care_session_start` on
  `main.py`'s normal foreground queue. They never call WorkerBrain or TTS. The daily-summary worker remains
  background work: it reads `care_log` and calls `send_care_email`. `schedule_receipt()` proves the exact
  item has an active worker and reports its next trigger in Asia/Kolkata.
- **A session cannot outlive its process, or run forever** (2026-08-31). Observed hijack: an
  engagement session opened at 19:35, was still `active` when Kiki restarted at 19:53, and then
  captured *every* following utterance — the care agent answered "what's seven times eight" — because
  `guided_care_turn` is simply "is a session active". The idle timeout could not save it: each stolen
  turn refreshed `updated_at`, resetting the very clock that would have killed it. That timeout only
  ever sees a session going **silent**, and this one was the opposite. Two additions:
  `end_orphaned_session()` runs **once at boot** (before anything can route a turn) and ends any
  session whose last turn predates this process — a startup fact, not a threshold, which is why it is
  deliberately not part of the read-triggered sweep; and `senior_mode.care_agent.max_session_minutes`
  (default 45) is a wall-clock cap measured from `started_at`, so a busy session still dies of old age.
- **Stop words must survive the STT's transliteration.** The matcher was Devanagari-only, and Whisper
  transcribes unpredictably: in one run "हम्म" and "पता नहीं" arrived in Devanagari while **"बस" arrived
  as "bus"** — which the care model then read as Hindi *bas* ("just") and answered *"तो बस शुरू करते
  हैं!"*, turning a stop into a go-ahead. `_STANDALONE_STOP` now carries romanised forms (`bus`, `bas`,
  `ruko`, `chup`, `khatam`, …), matched standalone-only because "bus" is also an English noun, plus
  multi-word romanised phrases in the Latin regex. English was widened too: "stop listening", "shut
  up", "be quiet" — all three phrasings tried in that run had failed to register.
- **Session completion is deterministic** (2026-08-29). Ending a session used to depend ENTIRELY on
  the care model emitting `session: "complete"`, while the same prompt tells it "usually it is
  `continue`" — so the observed 19:07 neck session ran eight turns and stayed `active`, holding the
  care lock against every other due routine and swallowing unrelated conversation until the 20-minute
  idle timeout. Three deterministic closers now sit underneath the model's wording:
  `user_asked_to_stop()` (a clear spoken stop in English or Hindi overrides `continue`), a hard
  `senior_mode.care_agent.max_session_turns` ceiling (default 40, with the model asked to land the
  ending itself 5 turns earlier via `_wrap_up_notice`), and the pre-existing idle timeout. A forced
  end also clears `expect_reply`, so main.py does not hold the microphone open for a finished
  conversation, and it applies even when the care turn itself failed — a broken agent must not trap
  someone in a session they asked to leave. `user_asked_to_stop` matches single words only as a
  COMPLETE utterance ("stop", "बस") and requires more evidence inside a sentence, because "I do not
  want to stop" is the opposite instruction and "बसंत" contains "बस". "no"/"नहीं" are deliberately
  never stops — they are the ordinary answer to "any pain?".
- **A finished session drops its frozen plan copy.** `care_context` is a snapshot of the WHOLE care
  plan, kept only so a stateless API can be re-sent an identical prefix mid-session; once the session
  is over it is dead weight. Measured on the live plan, one finished session was 18 kB of a 43 kB
  file and nothing ever removed it (clearing it took the file to 23 kB). `_close_session()` strips it
  and appends a compact record to `session_history` (bounded 60) with status, end reason, turn count
  and the last exchange — which is what the evening reflection reads via `session_history_since()`.
  `_migrate` self-heals a plan written before this. `active_session` deliberately keeps holding the
  finished record rather than becoming None: "how did the last session end" is a real question, and
  `start_care_session` already treats any non-active status as unblocked.
- **`care_voice_agent.py`** — owns one live microphone turn of the persistent session. It resends the frozen
  care-plan snapshot plus the real transcript to the configured Cerebras `gemma-4-31b`, exposes only relevant
  care tools, returns one exact voice-ready response, and separately marks the overall session as continue /
  complete / cancelled / declined. It never simulates the next participant turn and has no exercise,
  medicine, or wellbeing dialogue templates. If important context is missing, the model asks naturally;
  care-plan formulation likewise reasons about context sufficiency rather than running a fixed questionnaire.
- **Continuous care vision:** when the event/session switch is on, each care turn first pulls four bounded
  unannotated snapshots from Hailo `http://127.0.0.1:5001/clean`, selects the sharpest, and sends that frame
  as a base64 JPEG content part in the **same Cerebras Chat Completions request** as the frozen care plan,
  real transcript, and current speech. `gemma-4-31b` therefore sees the pixels and authors the next voice turn
  in one model pass; there is no preliminary Gemini description and no second conversational LLM. The model's
  brief grounded observation is persisted with the real turn, and image text is explicitly treated as untrusted.
  Snapshot pulls replace OpenCV MJPEG reads, which measured two 30-second stalls during a pipeline restart.
  Ending/cancelling ends the session-scoped override; voice can also set it on/off for the current session.
  Live 2026-08-29 two-turn test: distinct fresh JPEGs on both turns; 5.49 s cold end-to-end / 1.27 s warm,
  with the Cerebras portions taking 1.25 s / 0.91 s and no Gemini/Vertex request.
- **The verdict must be written before the praise** (2026-09-02). A seven-turn neck session
  confirmed six asanas nobody performed: every `visual_observation` read "…held the position
  steadily across both frames", which is the *instruction just given* plus the prompt's own
  description of the hold frames, not anything in the pixels. The JSON contract had `summary`
  first and `visual_observation` last, so the model committed to praise and then wrote an
  observation that agreed with it — and `_enforce_visual_reply_contract`, which only fires when
  the observation *admits* a failure, could never fire. Four changes: (1) `visual_observation` and
  a new structured `instruction_followed` (`yes|no|unclear|not_applicable`) are emitted **before**
  `summary`, so grounding precedes judgement; (2) any value but `"yes"` deterministically forces
  `reply_reason="incorrect_form"`, `hold_seconds=0` and a retry line, alongside the existing
  `_VISUAL_RETRY_RE` prose net; (3) the hold-frame status text no longer hands the model a
  ready-made verdict to recite ("whether the position was reached, held steady, and released" is
  gone — it now asks for a description of each frame); (4) a vision-enabled physical routine with
  no usable frame stops advancing, says it cannot see, and opens the microphone, rather than
  driving itself blind. `_reject_repeat_images` treats byte-identical JPEGs on two consecutive
  turns of the same session as a frozen camera — a live sensor never returns the same file twice,
  and this needs nothing from the frame server, unlike the `X-Frame-Age-Ms` guard. Both overrides
  are skipped on the opening turn (nothing has been instructed yet) and when the model is ending
  the session (a closing line is not an advance). The verdict is appended to the persisted
  `visual_observation`, so later turns read a transcript of judgements rather than a run of
  interchangeable confirmations to pattern-match.
- **A mismatch is corrected, not interviewed** (2026-09-02). Stopping the routine on the FIRST
  wrong tilt turned an exercise into a question-and-answer session — and worse, `main.py` opened
  the listening window on a turn where nothing had been queued to TTS at all, so the person was
  asked to answer a question they never heard. Both are closed. The stop policy now lives in
  `_enforce_visual_reply_contract`: an empty frame (`person_in_frame: "no"`, a new model field) or
  no usable frame at all stops immediately — there is nobody to correct — while a form mismatch is
  spoken aloud and the SAME movement is run again with the microphone still muted and a fresh
  `hold_seconds` (the model's, or `guided_exercise.retry_hold_seconds`, default 8). Only
  `guided_exercise.violations_before_listening` mismatches **in a row** (default 2) stop the
  routine and ask. The streak is read back out of the transcript's `[instruction_followed: …]`
  tags by `_violation_streak`, so it survives a restart and belongs to the session it was recorded
  in; a `"yes"` turn resets it. The model's OWN `reply_reason="incorrect_form"` goes through the
  same policy — its wording is kept on the free correction when it is not a question — while
  `safety`, `aborted` and `choice` still stop on the first occurrence, because pain is not a
  streak. In `main.py` the guided-care transition now checks `care_turn_spoke` (the return value
  of `queue_voice_ready_text`, previously discarded) **before** either the continue-muted or the
  listen branch: a turn that said nothing returns to idle instead of timing a hold for an
  instruction nobody gave or opening a microphone for an unasked question.
- **MAX30102 heart rate:** root `max30102_read.py` retains its CLI and also exposes structured
  `prepare_heart_rate`/`capture_heart_rate` functions. `core/senior/heart_rate.py` serializes
  access and exposes prepare/capture/cancel/status phases without any dialogue. The complex
  agent dynamically guides sensor-clear, placement, stillness, retry and result steps through
  `heart_rate_measurement`; only GOOD/FAIR quality BPM is atomically added to
  `health_measurements` and seven-day personal trends. Poor attempts go only to `care_log`.
  The MAX30102 SpO2 estimate remains uncalibrated and is never presented as a health reading.
- **Foreground ownership / anti-feedback loop:** due `senior:routine_event:*`, `senior:reminder:*`, and
  `senior:exercise:*` workers bypass generic `WorkerBrain` and queue `main.py`. The existing turn lifecycle
  sets `turn_active`, mutes STT, pauses wake-word recognition, gets one care-model reply, queues it directly
  to TTS, waits for playback, and only then reopens the microphone. Thus the scheduler contributes no LLM
  pass and Kiki cannot transcribe its own care prompt as the person's answer.
- **Agent ownership**: senior foreground care requests route to `complex_query`; direct `get_care_plan` and
  `update_care_plan` are intentionally absent from the speaking model's tool list. The care-specific complex
  agent reads first, corrects machine-format failures, writes an idempotent routine event, and verifies its
  runtime worker before promising success. Unified Idle Mind may call `complex_query` only for care-plan
  formulation/adaptation, only with concrete repeated-routine evidence; it cannot write the plan directly.
- **Tools** (in `tools.py`, private to the complex care agent): `update_care_plan(section,action,data)`,
  `get_care_plan(section)`, and `get_care_schedule_status(item_id)`. Public emergency/email tools remain
  `alert_family(reason,
  urgency)` (emails all alert contacts on distress/emergency + logs it), `send_care_email(to,subject,body)`
  (used by the daily-summary worker). Email goes through a **Gmail MCP**: `send_care_email` reads
  `senior_mode.email.{connection,tool,arg_map}` and calls `smithery_cli.tool_call` (same path as
  `self_extend_tool_call`). Configured 2026-08-28 as `connection: "gmail"`,
  `tool: "Gmail_SendEmail"`, `arg_map.to: "recipient"` (that server names the recipient
  field `recipient`, not `to`). Sending needs the `gmail.send` scope on the Arcade grant,
  which the read-only setup link does NOT include — see §5.15a.
- **Per-mode `main_tools`**: `core/llm.py _effective_main_tools()` honors an optional
  `assistant_modes.modes.<mode>.main_tools` override (senior adds `complex_query` and emergency tools), else the global
  `llm.main_tools`. Cache-safe — a mode switch already replaces msg[0] and re-warms (§4).
- **Mode capabilities, not mode names** (2026-08-29): a mode declares
  `assistant_modes.modes.<mode>.capabilities: ["care", "environment", "companion"]`, and
  `core/runtime_controls.py::mode_has_capability()` is what every gate tests. Five call sites used
  to compare `get_active_mode() == "senior"` — `main.py` startup activation and the `sync_mode_prompt`
  hook, plus three in `core/llm.py` (the `complex_query` hint, the "only complex_query touches the
  care plan" rule, and `care_scope` in `_should_route_complex_query`). Nothing under `core/senior/`
  ever read the active mode, so those five gates were the *entire* coupling between the care stack
  and one mode name. Naming the capability lets a second mode inherit the whole stack with no
  duplicate wiring. Unlike `context_enabled()`, `mode_has_capability()` fails **closed**: a
  capability starts subsystems that speak on a schedule and email families, so an unreadable config
  must leave them off rather than switch them on in a mode that never asked.
- **`health_sih` mode** — the SIH health-companion mode: `capabilities: ["care", "environment",
  "companion"]` and the same `main_tools` as `senior`. That list is already a superset of the global
  `llm.main_tools` (it adds `alert_family` + `start_care_session`), so the mode keeps every ordinary
  Kiki capability — music, memory, vision, and WhatsApp/Gmail through `complex_query` — while gaining
  the full care surface. Its system prompt keeps Kiki's own personality and answers in whatever
  language it is spoken to, where `senior` replaces the persona with a Hindi-locked caregiver. Voice
  resolution accepts "health", "health mode", "health companion", and "health sih".
  The care capability must **extend**, never replace, `complex_query`'s ordinary service contract:
  its compact hint continues to name WhatsApp/email/Notion/files/audio alongside care-plan work.
  `recall_memory` is for past conversation/knowledge, never current external-service activity or
  a `VERIFIED CURRENT CONTEXT` row.
- **Wiring** (`main.py`): the manager singleton and foreground callback are registered before
  `worker_manager.start_scheduler()` (so even an immediately-due event cannot fall through to worker speech),
  activates if the startup mode has the `care` capability, and `sync_mode_prompt` (the cache-safe mode
  boundary) activates/deactivates on mode change. Boot straight into it via
  `assistant_modes.active_on_startup: "senior"` / `"health_sih"`, or say "switch to senior citizen
  mode" / "switch to health mode".

---

### 5.25 `core/health/environment.py` — weather and air quality

The half of the SIH statement (heat waves, pollution events, early warning) that previously had
no code at all. Two free unauthenticated Open-Meteo endpoints — forecast (temperature, apparent
temperature, humidity) and air quality (PM2.5, PM10, US AQI) — polled by **one daemon thread**,
never from a voice turn. Readers get a deep copy, so a caller formatting a prompt cannot watch
the dict mutate mid-render. Active only under the `environment` capability (`health_sih`).

- **Freshness is a lifecycle, not a flag**: `fresh` → `stale` at 30 min → `unavailable` at 2 h
  (`environment.stale_after_seconds` / `unavailable_after_seconds`). An `unavailable` snapshot
  carries **no values at all** — returning the numbers next to an `available: False` flag is how
  a two-hour-old AQI eventually gets spoken as the current one. A failed poll does **not** reset
  the timestamp, so a permanently-down feed ages out instead of looking permanently fresh.
- **Partial results count.** Air quality alone is worth storing on a day the forecast endpoint is
  down, and vice versa; only a total outage leaves the previous reading in place to keep ageing.
- **AQI is computed on India's CPCB scale**, not taken from the feed. Open-Meteo returns US and
  European AQI, and neither is what a Delhi advisory, a news bulletin, or a doctor means by "AQI".
  `cpcb_aqi()` takes the **max** PM2.5/PM10 sub-index (CPCB never averages them) and reports which
  pollutant drove it. Two deliberate details: the published integer bands (0–30, 31–60, …) are used
  as **continuous** intervals, or a concentration of 120.6 would fall down the gap between 120 and
  121 and be dropped; and a concentration above the scale reports the 500 ceiling rather than
  extrapolating a number no Indian source would print. **Caveat that must reach the user:** the
  official CPCB AQI uses 24-hour averages over up to eight pollutants, while this uses the current
  hourly PM. It is an estimate on the CPCB scale and must never be spoken with station authority.
- **Heat is banded on apparent temperature**, not the raw reading: 38 °C dry and 38 °C at 80%
  humidity are not the same event for someone with a heart condition, and only the apparent figure
  knows the difference. Bands: `none` <32, `caution` <38, `high` <45, `very high` <54, `extreme`.
- **`compact_line()` is silent by default** — it returns `""` on an ordinary day. It only speaks up
  for a non-`none` heat band or an AQI worse than `satisfactory`, and appends its age when stale.
  Every character it returns is re-prefilled on every turn (§4), and a companion that announces
  pleasant weather each turn is one the person stops listening to.
- Wired in `main.py` next to the care bridge: started at boot and toggled by `sync_mode_prompt` on
  the same cache-safe capability boundary.

### 5.25a `core/health/care_snapshot.py` — the `CARE NOW` row

One short system row assembled from the environment provider, the care schedule, and trusted
vitals — the health-companion equivalent of the time anchor, under the same constraint: every
character lives in the warm KV prefix and is re-prefilled on every later turn (§4). Measured live
at 68 characters.

- **Silence is the default.** Only noteworthy facts earn a place: a non-`none` heat band, an AQI
  worse than `satisfactory`, a care item due within 90 minutes, a trusted reading under 12 h old,
  or an active session. An ordinary turn produces `""` and injects nothing.
- **Injected on CHANGE, never on a timer.** `CareNowInjector` appends only when the rendered line
  differs from the last one, and not before `environment.care_now_cooldown_seconds` (default 900)
  has passed — so an unchanged AQI never re-enters the prompt, and a reading oscillating across a
  band edge cannot inject every turn. This is the deterministic layer plan.md asks for under the
  AI's wording.
- **Append-only.** "Ephemeral" means *not repeated*, not *retracted*: removing the row would
  invalidate the warm prefix and cost a full reprefill on the next voice turn.
- **Every part fails soft and independently.** A broken care plan must not cost the person the
  air-quality warning, and vice versa; a total failure is silent, never an exception on a voice turn.
- **`care_log` is finally read.** The CLIP care cascade has written `observation` rows since it was
  built (`drinking: …`, `heat_distress: …`) and nothing ever read them back — on the live plan
  `drinking 4, heat_distress 2, wrapped_in_blanket 1, exercising 1` were sitting unused.
  `CarePlan.activity_counts()` aggregates them and `_activity_part` renders `SEEN TODAY drinking x3`.
  Two rules stop it becoming a fixation: **counts, never the observations** (eight near-identical
  descriptions of someone holding a glass tell a person nothing and cost real prefix), and **top three
  only**. Repetition is handled by the change-gate above. The Idle Mind copy carries an explicit
  "background only, do not make this the subject of a thought" instruction — given a running tally, a
  background mind biased toward finding something to say will reliably decide the something is the
  tally, every cycle, forever.
- Injected in `main.py` beside `idle_mgr.maybe_inject_time()`, under the `environment` capability.
  Unified Idle Mind gets the same snapshot freshly built (`_care_now_line`) plus the active mode and
  its capabilities (`_active_mode_line`) — it previously had no mode awareness at all, so its
  proactive thinking was identical whether or not Kiki was looking after someone's health.

### 5.25b `core/health/companion_routines.py` — the Phase H daily experiences

The morning briefing, evening reflection, and lifestyle follow-ups are **care-plan data, not code
paths**. Per the architecture decision in `plan.md`, a care event stores a rich goal/context
`session_brief` and the care agent decides how to conduct it — so these are ordinary
`routine_events` run by the same `care_voice_agent` as everything else. There is no new scheduler,
no new speech route, and deliberately no script, question list, or branching in any brief; the
module contains the *writing*, and `tests/test_companion_routines.py` asserts that no brief ever
grows a quoted line (the live box recites prompt examples verbatim — §8).

Five routines ship: `morning_briefing` (07:30), `hydration_checkin` (11:30), `movement_checkin`
(17:00), `evening_reflection` (20:30), `sleep_winddown` (22:00), all retimable under `companion.times`.

- **Seeded only under the `companion` capability**, in `main.py` *before*
  `senior_care_mgr.activate()`, so a freshly seeded briefing is materialized in the same activation.
- **Idempotent on `companion_key`**, a first-class field on the routine event (not `adaptation`,
  whose schema is a controlled allow-list that drops unknown keys). Identity survives the person
  renaming or retiming the event, so their edited copy is never duplicated underneath.
- **Only ever ADDS.** A routine that is disabled, retimed, or rewritten stays that way — an
  assistant that silently restores what you turned off is worse than one that never offered it.
  Deletion is not remembered; `companion.disabled` is the setting that is.
- The briefs that discuss heat or air quality point explicitly at `CURRENT OUTSIDE CONDITIONS`,
  the block `care_voice_agent._environment_brief()` adds to every care prompt. That block states
  the absence of a reading **explicitly** rather than omitting it: a silent gap invites the model to
  fill it from training data, which is exactly how a fabricated AQI would reach someone deciding
  whether it is safe to walk. The CPCB estimate caveat travels with it.
- The evening brief insists on **confirmed** outcomes from the care log and `session_history` —
  never inferring that a medicine was taken because it was scheduled.

**The engagement session** (`Something to think about`, 16:00) is the one with no activity at all in
its brief, deliberately. It hands the care agent the *tools for finding material* —
`conversation_topics` (the people/subjects/experiences this person actually has history with),
`recall_memory` for the detail of whichever it picks, `look_at_scene` for the room, `search_web` for
today — and asks it to invent something out of that. Directions are suggested (push for the details
of a half-told story, disagree with an opinion and make them defend it, a guessing round from their
own history, naming against the clock); a menu is not. It is explicitly forbidden from calling it a
game, a quiz or an activity, or announcing it at all: adults do not agree to play games, they get
interested in things.

**Starting it follows the `complex_query` shape: model first, code as backstop.** The speaking model
can pick `start_care_session` itself — its hint and schema now cover wanting company, not just
exercise — and `core/llm.py::_auto_engagement_tool_event` routes it in code when the model misses,
gated on `is_engagement_request()` ("I'm bored" / "मन नहीं लग रहा", excluding "bored **of** something",
which is a complaint about that thing rather than a request), the `companion` capability, and no
session already running.

Crucially it is routed **as a synthetic tool call, not by starting the session directly**. The first
version called `start_care_session_now()` straight from `main.py` on a keyword match, which skipped
`is_successful_care_session_handoff` — the guard that suppresses the follow-up local generation on
that exact tool. The result was the speaking model producing an ordinary sympathetic reply and
beginning to conduct the session, while the care agent opened the same session underneath it. Any
future "start a session from code" path must go through the tool route for the same reason.

**A conversation session never mutes the microphone.** Continuing without listening is an *exercise*
behaviour — mid-routine the person is moving and should not have to talk to keep it going — but
`guided_exercise.enabled` is a global config flag, so it applied to every care session. Observed live
on 2026-08-31: "I'm getting bored" opened an engagement session, Kiki asked a real question about the
person's own project, logged `Form accepted — microphone stays muted`, was handed
`[NO REPLY - CONTINUE THE ROUTINE YOURSELF]`, and repeated the identical question. Two fixes, both
keyed on the session's own category rather than a global flag:

- `_is_physical_session()` — only `category: "exercise"` (or a legacy exercise record) auto-continues.
  Everything else sets `expect_reply=True` and listens. An unknown session stays physical so the
  fallback cannot silently switch off, but an event with **no** category counts as a conversation:
  listening when you could have continued costs a pause, muting when you should have listened costs
  the session.
- `_session_guidance()` splits the prompt. The `LEADING AN EXERCISE` block — *"Default to NOT
  listening"*, *"you are the instructor, not an interviewer"* — is served only to physical routines;
  it was the root cause, since it is what told the model to set `reply_reason: "none"` mid-question.
  Conversations get `LEADING A CONVERSATION` instead: ask one thing, listen, build on the answer, and
  never re-ask a question they have already heard. `hold_seconds` still works in a conversation — the
  beeps play and Kiki then *listens*, which is a timed thinking round rather than a skipped answer.

**Audio cues** (`exercise_cadence.play_cue`, §5.24a) give a round shape. The care-agent JSON gained
an optional `cue` field — `start`, `correct`, `wrong`, `timeup`, `applause` — validated against
`CUE_NAMES` and played by `main.py` right after the spoken turn, before the hold. Non-blocking: a cue
is under half a second and marks a moment that already happened, so waiting for it would only add
latency. **Nothing in the set is a buzzer** — a harsh error tone aimed at an older person getting an
answer wrong is humiliating, and once is enough to stop them ever playing again; `wrong` is a soft
descending pair, closer to a thoughtful "hmm" than a rejection. The brief asks for cues sparingly,
since a sound on every turn stops meaning anything.

---

### 5.26 `core/brain/auto_recall.py` — noticing a topic was discussed before

Ask Kiki an ordinary question about something discussed for months and she answered from nothing.
The pre-existing router (`core/llm.py::_should_auto_recall_memory`) only fires on explicit cues —
*"we discussed"*, *"past conversation"*. A normal question carries no cue, so nothing searched, and
the speaking model cannot ask for what it does not know exists.

**A relevance threshold was measured and does not work.** On the live 3437-record corpus the scores
of questions that ARE in memory and questions that are not overlap almost completely:

| query | top score | in memory? |
|---|---|---|
| `the openclaw release` | 53.5 | yes |
| `can you move forward` | 43.0 | **no** |
| `play some music` | 39.0 | **no** |
| `did we talk about NSUT` | 19.7 | yes |

Distinctiveness (top hit vs. the rest) and term rarity were both tested and separate no better —
`what is the capital of France` has the single rarest matched term of anything tried. The cause is
structural, not a tuning failure: TF-IDF over a large personal corpus finds lexical overlap for any
English sentence, so the score measures *"these words occur somewhere"*, never *"we discussed this"*.

**What works is entity anchoring** against the knowledge base's own keys (people, facts, learnings,
recorded experience `event` names) — the things a person asks about again are the things that got
stored as something. Measured: **11/12 real recalls caught, 1/14 controls fired**, and that one
(`what's the weather` → a stored `Delhi_Weather`) is removed by a corpus-rarity gate on single-token
matches, since weather now has a live provider and has no business being answered from a July memory.

- **A turn with no anchor never runs the 250 ms search.** Anchoring is a dict scan over ~340 keys
  (measured 0.2 ms), which is the only reason this can sit on the speaking path at all. `start()` is
  called the moment the utterance exists and `maybe_inject()` after the context block, so a genuine
  recall overlaps that work rather than adding to it. A search that misses its timeout is abandoned.
- **`_token_idf` returns `None`, not a number, when rarity is unknowable.** On an unbuilt search
  index `MemorySearcher._idf` computes `log(4) = 1.386` for *every* term (corpus size and document
  frequency are both zero). Read as a rarity score it silently rejected every single-token anchor for
  the half second after boot — found during calibration, when `what about Moksha` recalled nothing
  with no error anywhere. Unknown rarity falls back to the length heuristic instead.
- Both indexes are warmed in a boot thread (memory index alone is 0.54 s cold).
- Injection is capped (~340 chars), deduped against the last 8 injections, and skipped when the text
  already appears in the recent history — the conversation summary and startup knowledge block
  already carry much of this, and restating it costs warm prefix on every later turn.
- `scripts/calibrate_auto_recall.py` tunes `min_anchor_idf` against your own questions
  (`--sweep` prints the precision/recall curve). Shipped default 5.5; on the sample corpus the safe
  band is 5.0–6.0, with a false fire appearing at 4.5 and a miss at 6.5.

---

## 6. `tools_and_config/config.json` Reference

| Block | Key points |
|---|---|
| `bluetooth_speaker` | Startup speaker connection (§3.0): `name`/`mac`, `connect_timeout_seconds`, `retry_interval_seconds`, `required` (abort boot when offline), `volume_percent`. `repair_pairing` (default true) allows the unpair→pair→connect recovery, and `repair_scan_seconds` (default 12) is its inquiry window. A re-pair at a new address rewrites `mac` here. |
| `llm` | Foreground speaking provider/model, local endpoint, tools, prompt, and cache controls. |
| `idle_mind` | The only background cognition configuration: provider/model/fallback/thinking level, state/journal paths, scheduling limits, and tool budgets. |
| `action_agent` | The `complex_query` multi-step agent (§5.2c): provider (`cerebras` default / `groq` fallback), per-provider model and context caps, turn/tool budgets, and the wall-clock deadline. Cloud-only; never uses the local slot. |
| `senior_mode.care_agent` | Foreground guided-care limits plus the direct-image and JPEG controls. Conversation and enabled per-turn vision reuse one latency-critical Cerebras/Gemma `action_agent` request. |
| `environment` | Home coordinates, poll interval, and the stale/unavailable thresholds for weather + air quality (§5.25). Polled only under the `environment` capability. Open-Meteo needs no key, so this costs nothing against `cloud_limits`. |
| `always_listen_config` | Capture-only buffer path and transcript size limits. |
| `cloud_limits` | Global and active-category caps; `idle_mind` has its own row. Zero means unlimited. |
| `knowledge_base` | Durable-memory path and startup context limits. |
| `auto_recall` | Automatic memory recall (§5.26): `min_anchor_idf` (corpus-rarity bar for a single-token anchor), injection cap, search timeout. |
| `agent` | Conversation summaries, token threshold, time anchors, and proactive-vision intervals. |
| `prompts` | Speaking, vision, greeting, summarization, and context wrappers. Unified Idle Mind builds its own protocol prompt in code. |
| `workers` | Independent scheduled/event background workers and their agent-loop limits. |
| `use_local_llm` | Shared non-speaking vision/summary/reasoning router; not used by Unified Idle Mind. |
| `vision_injection`, `peeping` | Scene capture and periodic local ambient capture behavior. |
| `self_extend` | Skill and MCP directories plus startup skill injection. |
| `companion` | Phase H daily routines: `seed_default_routines`, per-key `times`, `disabled`. Seeded only under the `companion` capability. |
| `whatsapp` | Async bridge/MCP lifecycle, timeouts/logs, and local idle-message polling/debounce/lookback limits. `contacts` is the manual name→number override map (§5.2d); the real address book is read automatically from the bridge store. |
| `logging` | Tee file, rotation, and debug verbosity. |

## 7. Data Files

| File | Writer | Reader |
|---|---|---|
| `state/knowledge_base.json` | Explicit memory tools and Unified Idle Mind | Startup context and `recall_memory` |
| `state/thinking_journal.json` | `save_background_research` and open-question tools | Unified prompt and `recall_memory` |
| `state/idle_mind_state.json` | Unified Idle Mind | Scheduler, anti-repeat policy, queued actions, one next-turn note, WhatsApp high-water cursor |
| `state/ambient_listen_buffer.json` | AmbientListeningManager | Unified Idle Mind snapshot/consume |
| `state/workers.json` | WorkerManager | WorkerManager |
| `state/care_plan.json` | Care agent, senior_care_manager | Care sessions, `CARE NOW`, health trends |
| `state/liked_songs.json` | `media_manager` like/unlike | `play_music` playlist controls |
| `state/conversation_summary.txt`, `state/conversations/*.txt` | Summary manager | Startup memory and `recall_memory` |
| `logs/kiki.log` (+`.1`) | Logger tee | Humans and diagnostics |
| `logs/whatsapp-bridge.log`, `logs/whatsapp-mcp.log` | WhatsApp bridge/MCP subprocesses | Humans and diagnostics |

**All of it lives under `state/`** (or `logs/`), not the repo root. Each path comes from
`config.json`, and every module's fallback default agrees with it — so a missing config key
cannot silently create a second copy at the root, which is how the root accumulated nine
loose JSON files in the first place. The fallbacks derive the repo root from `__file__`
rather than hardcoding `/home/kiki/kiki2/KikiFast`, so a copy of the tree elsewhere (the
`legacy_kiki` snapshot) resolves correctly.

**Some of these ARE tracked, and you should know which.** Tracked today:
`knowledge_base.json`, `workers.json`, `idle_mind_state.json`, `conversation_summary.txt`,
`liked_songs.json`, and nine historical files under `state/conversations/`. Untracked: `thinking_journal.json`,
`care_plan.json`, `ambient_listen_buffer.json`, `logs/`, `state/speeches/`, and the
other 349 conversation transcripts. The recurring `checkpoint live state before X` commits are
the deliberate backup workflow for the tracked half.

Note that `.gitignore` lists several of the tracked files. **Those entries are inert** —
git never applies an ignore rule to a path it already tracks. They are kept (and labelled
as such in `.gitignore`) so that a *newly created* file under one of those names starts
out untracked rather than being swept in by the next `git add .`. `state/conversations/` is
the one that matters most: 358 transcripts sit on disk against 9 tracked, so un-ignoring it
would commit the other 349 on the next `git add .`.

**The risk that workflow accepts.** Machine-written files drift away from whatever git
last committed, which is exactly the class that turned a brownout into unrecoverable
object-DB loss. On 2026-07-30 an under-voltage brownout left commit `013fb88` written but
nine of its blobs never flushed. Every *source* file was recoverable by re-hashing the
worktree copy (`git hash-object -w <path>`), because source on disk still matched what the
commit recorded. The tracked `__pycache__/main.cpython-313.pyc` had been regenerated in
the meantime, so its blob was gone for good and the commit had to be rebuilt without it.
A tracked file the robot rewrites continuously has the same exposure: if its blob is the
one lost, no copy on disk can reproduce it. That is the trade — checkpointing live state
is worth it, and bytecode (the thing that actually bit) is now ignored everywhere.

**Repo durability settings** (these live in `.git/config` + `~/.gitconfig`, so a fresh
clone on a new machine must re-apply them):

```
git config core.fsync all          # fsync loose objects, packs, index AND refs
git config core.fsyncMethod fsync  # real fsync, not writeout-only
git config transfer.fsckObjects true
```

The default is `core.fsync=committed`, which omits the index and refs. `all` costs ~5 ms
per object write on this SD card — immaterial. Note this hardens git's own write ordering
but cannot compensate for an SD card that lies about flushing; the durable fix for the
brownouts is the power supply, not git.

## 8. Important Things to Keep in Mind (gotchas & invariants)

1. **All four KV-cache rules in §4.** They are the difference between 1–2s and 40–60s replies.
2. **Never block the hotword thread.** Everything in the wake handler after `stt.unmute()`
   must be non-blocking (daemon threads / `run_coroutine_threadsafe`). A blocking call there
   eats the user's first words.
3. **`preempt_background()` is non-blocking and safe to call anytime** — but only kills
   *background* requests. The speaking request itself is never preempted.
4. **`thinking_budget_tokens: 0` must be sent on every speaking/background request**, and
   the server must run WITHOUT `--reasoning-budget`, or gemma thinks aloud on voice turns /
   per-request budgets are ignored.
5. **`message_history` is shared by reference** with WorkerManager and UnifiedIdleMindManager.
   Replace its contents with `message_history[:] = new` (in-place), never rebind the name.
6. **gemma's empty-response trap**: with reasoning on, it can burn the whole token budget in
   the thought channel. `generate_background` salvages the thinking text; distill steps must
   run `reasoning=False`; budgets are additive (answer + thinking).
7. **TTS tags**: the local voice model knows exactly 13 bracket tags (`SUPPORTED_TAGS` in
   `core/tts.py` — keep `scripts/streaming_tts.py` and the system-prompt note in sync).
   Movement tags use `<angle/dist>` syntax and must be stripped before TTS *and* before the
   history append.
8. **The model sees only the curated `llm.main_tools` set on the speaking path**; the full
   catalog is only for workers and Unified Idle Mind agent loops. Tool results are capped
   (1500 chars speaking / 3000 chars agent loops) and agent conversations are compacted to
   `max_prompt_chars` — the box has 7k ctx total.
9. **The hot-conversation window (180s)** makes `generate_background` return `None`. Every
   caller must treat `None` as "fall back to cloud or skip", never as an error/empty answer.
10. **Most latency-critical config is cached at import** — restart after editing modes/prompts.
    Spoken mode, voice, volume, and follow-up controls are runtime state and apply immediately.
11. **Anti-repetition is enforced in code, not prompts**: journal duplicate gate + banned
    topics + KB `current_ongoing` aging. Fresh Gmail/Notion reads are the exception:
    identical reads may run in later sessions so new mail/page edits are visible, while
    exact duplicate calls within one agent session remain suppressed.
12. **Secrets**: no key literal remains in source — verified 2026-09-01 by scanning every
    `.py` for a string of 16+ chars assigned to an `api_key`/`token` name. `core/tts.py`
    reads `os.getenv("INWORLD_API_KEY", "")` and the Exa key comes from `EXA_API_KEY`.
    This published copy is a working-tree snapshot with no git history, and it ships
    `.env.example` and `tools_and_config/config.example.json` with every value blanked.
    Neither the real `.env` nor the Vertex service-account JSON is present.
13. **STT mute = silence frames, not disconnect.** Don't "optimize" by closing the socket;
    the zero-latency unmute depends on it. The watchdog reconnects on 5s of Deepgram silence.
14. **Filler audio**: `ThinkingSoundPlayer` plays ONE random wav from
    `sound_effects/soundeffects/fillers/` and is stopped by `first_play_event` from the TTS
    streamer. Regenerate fillers with `python3 scripts/streaming_tts.py --generate-fillers`.
15. **`play_music`/`dance` set `should_skip_followup`** — after them the mic goes straight
    back to hotword mode (no follow-up listening over the music).
15a. **Every player process must be pinned to the speaker's sink.** mpv (music and timer
    alarms) inherits PulseAudio's default sink, and that default is `auto_null` whenever the
    A2DP sink dropped and came back. Speech pins its own sink in `core/tts.py`, so unpinned
    music is the *only* thing that disappears — it plays into the dummy output at full
    length with no error anywhere. `core/media_manager.py::_playback_environment()` calls
    `ensure_bluetooth_sink(reconnect=False)` and passes `PULSE_SINK` to every player it
    spawns. `reconnect=False` is deliberate: profile activation repairs auto_null in
    milliseconds, while a full BlueZ reconnect would stall *"play some music"* for a whole
    `bluetoothctl connect` timeout with the speaker off. Reconnecting belongs to boot and to
    `core/tts.py`. With no speaker configured or connected the environment is unpinned, so
    music still plays rather than failing closed.
15b. **yt-dlp rots, and it fails as silence, not as an error.** A stale build resolves the
    search fine — right title, right watch URL, `play_music` answers *"Now playing …"* — but
    the signed googlevideo URL it hands back answers **HTTP 403**, so mpv dies within a
    second. Two causes, both live: the build being months old, and modern yt-dlp enabling
    only **deno** by default while this box has **node**. Without a JS runtime signature
    deciphering fails and every URL 403s. `_yt_dlp_js_flags()` probes `--help` once and
    passes `--js-runtimes` for each installed runtime (older builds that lack the option get
    nothing). When a song "doesn't play", check `curl -o /dev/null -w '%{http_code}'` on the
    resolved stream URL *before* suspecting audio: 403 means upgrade yt-dlp
    (`/home/kiki/Kiki/kiki/bin/pip install -U yt-dlp`), 206 means the problem is the sink.
    Note `_yt_dlp()` prefers `yt_dlp_path` (venv) over `$PATH` — `/usr/bin/yt-dlp` is a
    distro package and is usually the stale one.
16. **Periodic spoken questions** require `run_vision_update(force_trigger=True,
    force_qa=True)` AND a timer reset at the call site — the bare call returns immediately
    with `traditional_context_enabled: false`.
17. **An agent must never let a non-action sound like a completed one.** The whole
    `complex_query` guard stack (§5.2c: first-JSON truncation, placeholder-argument
    refusal, `min_tool_calls`, meaningful-summary check, explicit "do not claim it
    succeeded" failure text) exists because every one of those failures was observed
    live — a model describing WhatsApp messages that did not exist, or reporting a send
    that never happened. Latency regressions are recoverable; a confident lie about
    Vaibhav's messages is not. Keep the guards when touching that path.
18. **WhatsApp group names only exist in `list_chats`.** `search_contacts` filters
    `@g.us` out in SQL, so any group lookup that goes only through contacts silently
    finds nothing (§5.2c).
19. **`messages.db` knows no names, and files DMs under `@lid`.** Person names come from
    the bridge's `whatsmeow_contacts`; sender ids come from `whatsmeow_lid_map` (§5.2d).
    A direct chat is *addressed* by phone but *stored* under its `@lid`, so any lookup by
    the resolved phone JID can come back empty for a conversation that plainly exists —
    always go through `jid_variants` before reporting "no messages".
20. **Always-listen context must enter history only between turns.** A cloud flush may finish
    during a reply; keep its result queued until `turn_active` and summarization are false,
    then append and re-warm once. Never let the wake thread wait for cloud processing.

---

## 9. Known Pending / Watch List

- **`state/speeches/` grows without bound** — 289 MB across 613 wavs as of 2026-09-01,
  written by `core/speech_recorder.py` whenever `record_enabled` is on. Untracked, so it
  costs disk rather than repo size, but nothing prunes it.
- The **relationship engine was removed** at some point but left `relationship_state.json`
  (55 KB) and `relationship_migration_report.json` (27 KB) behind with zero readers. Both
  are parked in `to_do/debris/`. If that feature is revived, the data is there.
- **`logs/events.jsonl` is not rotated** (4.7 MB) while `logs/kiki.log` is (5 MB cap). The
  observability recorder appends for the life of the install.
- Generation speed on the box occasionally drops to ~7 tok/s (draft-MTP acceptance dips +
  106 MiB checkpoint writes mid-generation) → TTS underruns. Server-side tuning question.
- The chassis stack (`movement.py`, `motor_control.py`, `chassis_tools.py`, and `dance()`'s
  hardcoded 20 s wait after music start) is **parked in `to_do/`**, not live — Kiki is a
  stationary neck-only unit. `to_do/README.md` has the reintegration notes. If wheels come
  back, the legacy direct-GPIO `execute_movements` path still needs consolidating with the
  ZMQ motor server that the `move` tool used.
