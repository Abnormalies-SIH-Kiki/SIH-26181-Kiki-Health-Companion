# Route 1 architecture

The ESP32 is a real-time audio/display endpoint. The laptop remains the compute
and state authority. This is the only route that preserves the current model
quality: the board has 8 MB PSRAM and 16 MB flash, while Kiki's LLM, Whisper,
OmniVoice and Python tool runtime are orders of magnitude larger. The board's
466 x 466 AMOLED, capacitive touch, ES7210 microphone input and ES8311 speaker
output are used directly.

```text
microphones -> ESP32 I2S (48 kHz, 10 ms frames)
            -> persistent WebSocket over 2.4 GHz Wi-Fi
            -> laptop RNNoise -> 48/16 kHz FIR -> Silero VAD
                    | 240 ms silence: speculative Whisper
                    | 600 ms silence: conservative commit
                    v
               existing Kiki prompt + memory + tools + llama.cpp KV cache
                    v
               sentence stream -> OmniVoice PCM -> 24/48 kHz resampler
                    v
               same WebSocket -> ESP32 jitter buffer -> ES8311 speaker
```

## Why VAD stays on the laptop

Silero is still local: it moved from the Pi to a machine on the same LAN, not to
an internet service. Audio is sent continuously in 10 ms frames, so VAD sees it
while the user is speaking. The conservative 600 ms endpoint is retained for
transcription quality, but Whisper starts after 240 ms of apparent silence. If
speech resumes, that speculative result is cancelled. In the common case most
of STT is therefore hidden inside the remaining endpoint wait.

Running a different tiny VAD on the ESP32 would save roughly one LAN frame but
would change endpoint decisions and can cost hundreds of milliseconds through
false endpoints. The continuous-stream design preserves the production
RNNoise/Silero behavior and makes network setup happen at boot rather than on a
turn's critical path.

## Latency controls

- Wi-Fi modem power saving is disabled.
- The WebSocket is persistent, has compression disabled, and carries binary PCM.
- Capture and playback both use 48 kHz so the board never reconfigures its shared
  audio clocks during a turn.
- A 640 ms bounded microphone queue isolates I2S from Wi-Fi jitter; gaps are
  reported rather than silently shifting audio.
- RNNoise, FIR decimation, wake-word inference and Silero run incrementally.
- Partial ASR warms the llama.cpp prefix while the user is still speaking.
- TTS begins on the first eager sentence and streams PCM; it never waits for the
  full answer.
- The laptop's existing `--prefill-after-response` and KV-cache contract remain
  in use.
- Background workers, idle mind, WhatsApp and web UI initialize outside the
  connection and speech hot paths.

## Feature placement

| Feature | Placement |
|---|---|
| AMOLED face/expression tags, state, transcript, touch wake/cancel | ESP32 |
| I2S capture/playback, volume, bounded jitter queues | ESP32 |
| RNNoise, near-field gate, Silero, wake word | Laptop gateway |
| Whisper, Gemma/llama.cpp, OmniVoice | Existing laptop servers |
| Kiki prompt, memory, modes, tools and KV history | Isolated laptop legacy snapshot |
| Idle mind, ambient buffer, workers, WhatsApp/MCP, web UI | Laptop |
| Music lookup/decoding, liked songs, timers, named voices | Laptop, PCM/effects delivered to ESP32 |

## Deliberately unavailable

Camera-dependent behavior is removed: live vision, face recognition/enrolment,
person tracking, visual greetings and image-based peeping. The copied prompt is
overridden so Kiki does not claim it can see. Pi-only Bluetooth/ALSA output is
also replaced by the board speaker. Physical neck and IR-hand gestures require
their separate controller hardware; on this board, touch supplies wake and
cancel. Nothing else needs to be reduced for flash or RAM because the Python
runtime and models do not run on the microcontroller.

## Failure behavior

If the laptop or LAN is unavailable, the ESP32 remains an offline UI/audio
endpoint and reconnects. It cannot provide an equal-quality offline answer: an
ESP32-scale fallback model would violate the quality requirement. A future
route can add a visibly degraded emergency command set, but it must not be
presented as equivalent Kiki. The isolated runtime's cloud speaking fallback is
disabled, so a failed laptop llama.cpp service fails visibly instead of silently
changing model quality or sending the conversation to a hosted model. Web,
messaging and other explicitly network-backed tools retain their normal APIs.
