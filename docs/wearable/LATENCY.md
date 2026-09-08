# Latency evidence

Measurements were made from the current Pi to the laptop inference services on
the same LAN, with the existing server manager and models unchanged.

| Stage | Observed |
|---|---:|
| Laptop HTTP health RTT, 20 calls | p50 10.7 ms, p95 30.6 ms |
| Whisper, checked-in 10 s 44.1 kHz test clip, 8 calls | p50 534.0 ms, p95 720.9 ms |
| llama.cpp realistic prompt to first token, 8 calls | p50 486.3 ms, p95 2084 ms |
| OmniVoice request to first PCM, 8 calls | p50 421.2 ms, p95 646.6 ms |
| ICMP, 20 packets | average 108.6 ms, max 508.9 ms, 5% loss |
| RNNoise, 10 ms frames, 1,000 calls | p50 0.040 ms, p95 0.053 ms |
| Silero, 32 ms frames, 1,000 calls | p50 0.090 ms, p95 0.139 ms |

The HTTP RTT is the relevant steady-connection signal; the ICMP result still
shows that this Wi-Fi path has a long jitter tail. The firmware queue prevents
I2S starvation, but no architecture can guarantee identical tail latency over a
lossy WLAN. Reserve the laptop's address, use a strong 2.4 GHz channel, keep the
device and laptop on the same AP, and measure p95 as well as median.

For a 600 ms endpoint and a 240 ms speculative threshold, the approximate
post-speech paths are:

```text
sequential  = 600 + STT + LLM-first-sentence + TTS-first-PCM + LAN
speculative = max(600, 240 + STT) + LLM-first-sentence + TTS-first-PCM + LAN
```

At the observed median STT time, speculation hides about 360 ms and leaves only
about 174 ms of STT beyond the endpoint. It does not change model output: both
paths call the same Whisper model, and resumed speech invalidates the early
request.

## End-to-end gateway test

The software ESP32 emulator streamed a checked-in 2.15 second clip as real-time
48 kHz/10 ms frames over Wi-Fi. The laptop ran the production RNNoise, Silero,
Whisper and OmniVoice services. A deterministic sentence replaced only the LLM
so that the rest of the new path could be measured while llama.cpp was down.

| Run | Speech-file end to first PCM | Gateway endpoint to first PCM | Whisper |
|---:|---:|---:|---:|
| 1 | 895 ms | 474 ms | 318 ms |
| 2 | 807 ms | 417 ms | 317 ms |
| 3 | 802 ms | 418 ms | 319 ms |

Median file-end-to-first-PCM was 807 ms and median gateway
endpoint-to-first-PCM was 418 ms. Every run used speculative STT. The difference
between file end and detected speech end explains why these two columns are not
directly additive.

This proves the capture protocol, continuous VAD, speculative STT and streaming
TTS path, but it is not a full model TTFW claim. On 2026-08-08, the laptop's
Whisper and OmniVoice services were healthy, but llama.cpp on port 8080 could
not load because a Windows/QEMU VM held roughly 10 GB and the 512 MB swap was
almost exhausted. The service had produced a 486 ms median first-token result
before that memory pressure appeared. The VM was subsequently stopped and the
complete local-model measurement is recorded below.

Hardware I2S, touch and AMOLED acceptance remains pending until the connected
board can be flashed. Exact p95 parity cannot be guaranteed on the measured lossy Wi-Fi;
the acceptance gate therefore compares both median and p95 against the current
Pi using the same spoken clips and laptop load.

## Live local-model acceptance after restoring RAM

After the Windows VM was stopped, llama.cpp, Whisper and OmniVoice all became
healthy. The gateway now reports `warming` and waits for the exact Kiki prefix
to be resident in llama.cpp before it reports `idle`. A persistent WebSocket
emulator then ran three consecutive turns, matching the real device lifecycle.

| Run | Speech-file end to first PCM | Gateway endpoint to first PCM | Whisper | LLM first token |
|---:|---:|---:|---:|---:|
| 1 | 1,271 ms | 857 ms | 317 ms | 234 ms |
| 2 | 1,205 ms | 802 ms | 321 ms | 208 ms |
| 3 | 1,366 ms | 950 ms | 322 ms | 241 ms |

llama.cpp reused 4,989–5,105 cached prompt tokens and freshly processed only
12–14 tokens per turn. Median detected-endpoint-to-first-PCM was 857 ms and
median file-end-to-first-PCM was 1,271 ms. This is the relevant software TTFW
for the persistent ESP32 architecture; reconnecting for every turn invalidates
the cache lifecycle and measured 4.6–6.6 seconds, so it is not a valid device
benchmark.

## 2026-08-12 — second pass, first hardware run

Firmware `be206d1` (built from a tree also containing `aab510c`), gateway
deployed from the same commits. Board on LAN as 192.168.1.13, laptop
192.168.1.10, llama.cpp/OmniVoice/Whisper all healthy, no VM running.

| Measurement | Result |
|---|---|
| Endpoint to first PCM (calibration replay, 1 sample) | **731 ms** |
| Microphone stream rate | 100.0-100.2 frames/s, audio_rate 1.00x |
| Playback underruns / dropped bytes / forced ends | 0 / 0 / 0 |
| Internal heap free after init | 175,795 B |
| Largest free DMA block after init | 126,976 B |
| PSRAM free after init | 8,115,780 B |

The 731 ms sits alongside the 723 ms measured on 2026-08-08 with the previous
200 ms send lead, i.e. **raising the lead to 600 ms did not cost
time-to-first-word**, which is the property the change was predicated on.
One sample each; this is a sanity check, not the 30-turn A/B in §9.

Not yet measured on hardware: a real spoken turn end-to-end, barge-in, music,
and the touch gestures.
