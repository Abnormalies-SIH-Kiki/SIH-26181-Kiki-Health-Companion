#!/usr/bin/env bash
# Flash a prebuilt Kiki firmware image from a machine that has the board but no
# ESP-IDF. The images are built on the development machine and copied here
# alongside this script; only esptool is needed to write them.
#
#   ./flash_prebuilt.sh [PORT]        # default /dev/ttyACM0
#
# NVS is deliberately NOT erased. Wi-Fi credentials provisioned on the panel
# live there, and erasing would silently send the board back to the setup
# screen after every flash. Use --erase-nvs when that is what you actually want.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT="${1:-/dev/ttyACM0}"
PYTHON="${KIKI_PYTHON:-/home/vaibhav/KikiESP32/.venv/bin/python}"
ERASE_NVS=0
for arg in "$@"; do
    [ "$arg" = "--erase-nvs" ] && ERASE_NVS=1
done

for image in bootloader.bin partition-table.bin ota_data_initial.bin kiki_esp32.bin; do
    if [ ! -f "$HERE/$image" ]; then
        echo "missing $image next to this script" >&2
        exit 1
    fi
done

if [ ! -e "$PORT" ]; then
    echo "no board at $PORT" >&2
    exit 1
fi

if [ "$ERASE_NVS" = "1" ]; then
    echo "erasing the NVS partition (saved Wi-Fi will be forgotten)"
    "$PYTHON" -m esptool --chip esp32s3 --port "$PORT" erase-region 0x9000 0x6000
fi

echo "flashing $PORT"
"$PYTHON" -m esptool --chip esp32s3 --port "$PORT" --baud 921600 \
    --before default-reset --after hard-reset \
    write-flash --flash-mode dio --flash-size 16MB --flash-freq 80m \
    0x0     "$HERE/bootloader.bin" \
    0x8000  "$HERE/partition-table.bin" \
    0xf000  "$HERE/ota_data_initial.bin" \
    0x20000 "$HERE/kiki_esp32.bin"

echo
echo "flashed. watch it boot with:"
echo "  $PYTHON -m serial.tools.miniterm --raw $PORT 115200"
