# KikiFast

A low-latency voice companion robot ("Kiki") on a Raspberry Pi 5: wake word → speech
recognition → an LLM turn → streamed speech, with face/vision, a stationary neck, a
senior-care mode, and a layered memory system.

## Start here

**[`Codestructure.md`](Codestructure.md)** is the architecture reference. It is ~2700
lines, so read §0 (contents) first, find the section you need, and read that range —
don't read the file whole.

The four sections worth reading before changing anything:

| Section | Why |
|---|---|
| §1 Hardware / Service Topology | What runs where, and on which endpoint |
| §3 Runtime Flows | Boot, a normal speaking turn, and background cognition |
| **§4 The KV-Cache Contract** | The difference between a 1–2 s reply and a 40–60 s one |
| §8 Gotchas & invariants | The rules that were learned the expensive way |

`plan.md` holds the senior-care checklist and device lock gates. `to_do/README.md`
explains the freezer: parked code that is deliberately kept, not dead.

## Running it

```bash
source /home/kiki/Kiki/kiki/bin/activate
python main.py                 # or let kikifast.service run kiki_boot.py
```

`kiki_boot.py` is the real entry point on the robot — it connects the Bluetooth speaker,
offers the LCD/IR config wizard, brings up Wi-Fi if needed, waits for the LLM/TTS/STT/Hailo
services, then execs `main.py` (§3.0).

## Tests

```bash
/home/kiki/Kiki/kiki/bin/python -m pytest        # 1048 tests, ~27s, no external services
```

`tests_llamaserver/` is separate and needs the live llama.cpp box. See §5.20.

## Layout

`core/` is the application (speaking path, memory, workers, vision, senior care),
`tools_and_config/` holds the single `config.json` and the tool catalog, `robot/` and
`hotwords/` are the hardware surfaces, `webui/` is the :8090 dashboard, and `scripts/`
holds operator tools that are run by hand or at boot.

**Everything the robot writes lives in `state/`** (memory, journal, workers, care plan,
conversations, faces, speech archive) or `logs/` — the repo root holds no data files. Paths
come from `config.json`; the code fallbacks agree with it and derive the repo root from
`__file__`, so a copy of the tree elsewhere still resolves.

`to_do/` is the freezer: parked code kept deliberately, imported by nothing. Full tree in §2.
