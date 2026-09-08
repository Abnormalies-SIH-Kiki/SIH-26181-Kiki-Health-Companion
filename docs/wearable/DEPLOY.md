# Deployment

## Laptop

The deployed directory is `/home/vaibhav/KikiESP32`. The existing
`/home/vaibhav/kiki_servers/server_manager.py` and `/home/vaibhav/KikiFast` are
not edited.

```bash
cd /home/vaibhav/KikiESP32
./scripts/install_laptop.sh
./scripts/run_gateway.sh
```

Optional `gateway.env` values:

```bash
KIKI_GATEWAY_TOKEN=choose-a-device-token
KIKI_GATEWAY_PORT=8765
KIKI_GATEWAY_LEGACY_ROOT=/home/vaibhav/KikiESP32/gateway/legacy_kiki
# Optional overrides; these defaults make Kiki initiate a grounded question
# at a random point every 20-30 minutes while the panel is idle.
KIKI_GATEWAY_PROACTIVE_QUESTIONS_ENABLED=true
KIKI_GATEWAY_PROACTIVE_QUESTION_MIN_SECONDS=1200
KIKI_GATEWAY_PROACTIVE_QUESTION_MAX_SECONDS=1800
```

`KIKI_GATEWAY_LEGACY_ROOT` is optional. When it is unset, the gateway now uses
the bundled `gateway/legacy_kiki` runtime. Set it to an explicit empty string
only for a deliberately bare `DirectLlamaCore` deployment; that mode has no RPi
persona, Unified Idle Mind, workers, or proactive questions.

This uses the RPi mechanism, not a separate ESP prompt builder. The byte-identical
Unified Idle Mind reasons in the background with its dedicated cloud Gemini
model over recent conversation, ambient speech, long-term memory, workers, and
the thinking journal. It may persist one worthwhile next-turn note. Every 20–30
minutes the gateway calls the same `get_proactive_injection("")` selector used by
the RPi, appends its result as a system turn, and sends the normal persona/history
through the configured cloud model and ordinary streaming TTS path. If there is
no active note, Kiki stays quiet.

The ESP32 panel has no camera, so the selector receives an empty scene instead
of inventing vision. If Kiki is listening, speaking, processing a turn, or
playing music when the timer fires, the attempt does not interrupt the user.

The same bundled runtime owns the IMU movement-question bank in
`thinking_journal.json`. Unified Idle Mind may optionally add original questions for any
semantic movement state with `add_movement_questions`; its action selection is not forced.
When an idle, locally shown movement event arrives, the gateway randomly reserves one unused
question, speaks it directly, retains it in conversation history, and opens the normal
follow-up window. Asked text is permanently recorded and is never wrapped or repeated.

The existing inference manager must report LLM `:8080`, Whisper `:5555` and
OmniVoice `:8082` healthy before the gateway starts.

## Firmware configuration

Install ESP-IDF 5.5, then configure the values that are intentionally not stored
in source control:

```bash
cd /home/kiki/kiki2/KikiESP32/firmware
source ../.tools/esp-idf/export.sh
idf.py menuconfig
```

Under `Kiki ESP32`, set:

- a 2.4 GHz Wi-Fi SSID and password;
- gateway URI `ws://192.168.1.10:8765` (or the laptop's reserved LAN address);
- the same token as `KIKI_GATEWAY_TOKEN`;
- speaker volume.

Build and flash after the board appears as a serial device:

```bash
../scripts/build_firmware.sh
../scripts/flash_firmware.sh /dev/ttyACM0
```

The partition table has two 5 MB OTA application slots, persistent storage, NVS
and a coredump partition. Wi-Fi credentials are in the generated `sdkconfig`,
which is ignored by git.

## Acceptance test

Use the software device emulator before flashing:

```bash
cd /home/kiki/kiki2/KikiESP32
.venv/bin/python gateway/benchmarks/live_pipeline.py \
  --uri ws://vaibhav:8765 \
  --wav /home/kiki/kiki2/KikiFast/kiki_speeches/speech_76.wav \
  --iterations 5
```

After flashing, repeat while watching the serial log. Reject a build if it has
audio gaps during clean LAN conditions, fails to invalidate a false speculative
endpoint, or materially regresses median speech-end-to-first-PCM against the
emulator. Hardware acceptance cannot be completed until the board is attached.

## Updating the firmware over the air

The board fetches its own image over HTTPS, so the URL has to be reachable from
wherever the board is -- a LAN address is useless the moment it leaves the
house, and the gateway rejects any pending URL that is not `https://`.
`scripts/push_ota.sh` handles that: it serves the image, gives it a throwaway
public name, and queues it.

Run it on the laptop, not the Pi -- it writes the file the gateway reads:

```bash
scp firmware/build/kiki_esp32.bin vaibhav:~/kikifw/
ssh vaibhav
cd ~/KikiESP32 && set -a && . ./gateway.env && set +a
./scripts/push_ota.sh ~/kikifw/kiki_esp32.bin     # prints the build ID
grep -E 'kiki_ota|device firmware build' gateway.log
./scripts/push_ota.sh --stop
```

The update is delivered on the board's next hello and re-offered every 10 s
while it stays connected, so it does not sit waiting for a disconnection. The
device retries the download three times before giving up, and reports
`ota_result` either way. After installing it reboots, and if it cannot reach the
gateway within three minutes it puts the previous image back by itself.

**Confirm with the build ID, never the compile date.** The board reports the
first four bytes of its ELF SHA-256 in `hello`, the gateway logs it as
`device firmware build <id>`, and the panel shows the same string at boot.
`esp_app_desc.c` is not always recompiled, so the date it carries can be hours
stale while the code around it is current -- which is exactly how a correct
flash came to look like a failed one.

## Recovering from a full erase

A full chip erase takes the bootloader and partition table with it, so the board
will not boot and cannot be reached over the air. It needs USB and all four
images, not just the application:

```bash
pip3 install --user esptool
esptool --chip esp32s3 --port /dev/ttyACM0 --baud 921600 \
  --before default-reset --after hard-reset write-flash \
  --flash-mode dio --flash-size 16MB --flash-freq 80m \
  0x0     bootloader/bootloader.bin \
  0x8000  partition_table/partition-table.bin \
  0xf000  ota_data_initial.bin \
  0x20000 kiki_esp32.bin
```

`0xf000` is the one people leave out, and leaving it out is worse than it
sounds: `ota_data` decides which of the two application slots boots. A
successful OTA repoints it at the other slot, so after any OTA a flash written
to `0x20000` alone lands in the partition that is *not* running, and the board
comes up on the old image looking like the flash silently failed. Writing
`ota_data_initial.bin` puts the choice back to `ota_0`.

Avoid the erase in the first place where possible: it also wipes NVS, which
holds the Wi-Fi credentials, the learned public gateway address and the
brightness setting. Re-provisioning needs the on-panel setup screen, which is
hard to use if the display is the thing being debugged.

## When the board keeps reconnecting

Two things caused this, and they look identical from the gateway log.

**Check which network it is actually on first.** A device session from the
laptop's own subnet is not proof of a healthy link:

- `192.168.1.5` — the board is associated with the house AP directly. Good.
- `192.168.1.2` — that is a *hotspot's* LAN address, and the board is behind its
  NAT. The board had an open AP named `Kiki` saved in NVS from a trip; it is in
  range at home, it wins over the house network on every boot, and it drops the
  connection every few minutes. `kiki_wifi: connecting to "<ssid>"` on the serial
  console says which one it chose, and `disconnected from <ssid>, reason <n>`
  says why it left.

Clear a bad saved network by erasing NVS; the build-in credentials take over:

```bash
esptool --chip esp32s3 --port /dev/ttyACM0 erase-region 0x9000 0x6000
```

Nothing important is lost — the learned public gateway URL is handed back on the
next `hello_ack`, and brightness returns to its default.

**The other cause was a send timeout.** `esp_websocket_client` treats a short
write as fatal and aborts the connection, using the timeout the *caller* passed
rather than `network_timeout_ms`. Event sends passed 100 ms, so any moment the
socket was not writable for a tenth of a second killed the session. Both senders
now share `kSendTimeout` (5 s) in `gateway_client.cpp`. If you are tempted to
shorten it to "drop a frame under congestion", read the comment there first: it
does the opposite.

**A connection drop must not race an active TLS write.** Do not enable
`CONFIG_ESP_WS_CLIENT_SEPARATE_TX_LOCK`. With that component option enabled,
the WebSocket receive/reconnect task can call `esp_transport_close()` while
`kiki_net_tx` is in `mbedtls_ssl_write()`. The saved crash then decodes as a
`LoadProhibited` in `ssl_check_ctr_renegotiate()` with a null `ssl->in_ctr`.
Kiki deliberately uses the component's default single recursive lock so reads,
writes and transport teardown are serialized; `gateway_client.cpp` contains a
compile-time guard to prevent an unsafe build.

Diagnosing either one needs **unfiltered serial**, because the remote log dies
with the connection — the explanation is the one thing that never reaches the
gateway. The gateway also reports when its own event loop stalls
(`event loop blocked for N s`), which is what fills the board's send buffer.
