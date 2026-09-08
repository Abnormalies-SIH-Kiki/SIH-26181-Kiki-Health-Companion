#!/usr/bin/env bash
# Ship the firmware in firmware/build to the board, wherever the board is.
#
#   ./scripts/push_ota.sh                 # ships firmware/build/kiki_esp32.bin
#   ./scripts/push_ota.sh /path/to.bin    # ships an image built elsewhere
#
# The second form is the usual one here: the firmware is built on the Pi and the
# gateway runs on the laptop, so the image arrives by scp and is not sitting in
# this checkout's build directory.
#
# The device downloads the image itself over HTTPS, so the image has to be on a
# public URL for the "board is not at home" case to work at all -- a LAN address
# is unreachable from a hotspot, and the gateway refuses any pending URL that is
# not https:// because a firmware image fetched over plaintext is a firmware
# image anyone on the path can replace. That is why this starts a throwaway
# Cloudflare tunnel rather than just serving on port 8137.
#
# What actually happens:
#   1. a static file server holds firmware/build/kiki_esp32.bin
#   2. a quick tunnel gives it an https:// name
#   3. the URL goes into pending_ota.txt, which the gateway hands to the board
#      on its next hello -- immediately if it is connected, otherwise whenever
#      it comes back
#   4. the board downloads, installs, reboots, and reconnects. If it cannot
#      reach the gateway within three minutes of that reboot it puts the old
#      image back by itself.
#
# Run this on the gateway host (the laptop), not the Pi: pending_ota.txt is read
# by the gateway process, and the tunnel has to reach the file server.
#
# The tunnel is left running so the download can be retried; stop it with
# --stop once the board reports the new build.
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
image="${1:-${project_dir}/firmware/build/kiki_esp32.bin}"
serve_dir="${KIKI_OTA_SERVE_DIR:-${project_dir}/.ota}"
pending="${KIKI_GATEWAY_PENDING_OTA_FILE:-${project_dir}/pending_ota.txt}"
log="${project_dir}/ota_tunnel.log"
port="${KIKI_OTA_PORT:-8137}"
# ~/bin is on the interactive PATH but not the one a non-interactive shell gets,
# so an ssh-driven run finds nothing while the same command works when typed.
cloudflared="${CLOUDFLARED:-}"
if [[ -z "$cloudflared" ]]; then
  if command -v cloudflared >/dev/null 2>&1; then
    cloudflared=cloudflared
  elif [[ -x "${HOME}/bin/cloudflared" ]]; then
    cloudflared="${HOME}/bin/cloudflared"
  else
    echo "cloudflared not found; set CLOUDFLARED=/path/to/cloudflared" >&2
    exit 1
  fi
fi

stop() {
  pkill -f "cloudflared tunnel --url http://127.0.0.1:${port}" || true
  pkill -f "http.server ${port}" || true
  echo "OTA file server and tunnel stopped."
}

if [[ "${1:-}" == "--stop" ]]; then
  stop
  exit 0
fi

# --lan: skip the tunnel entirely and serve on this machine's LAN address.
#
# The tunnel exists for the "board is not at home" case. When the board and the
# gateway are on the same network it is pure overhead -- and on this network it
# does not work at all: freshly-allocated *.trycloudflare.com names do not
# resolve, so the board cannot fetch the image by name however healthy the
# tunnel process looks. The gateway only accepts an http:// URL whose host is a
# literal private address, so this cannot be pointed at the open internet.
if [[ "${1:-}" == "--lan" ]]; then
  image="${2:-${project_dir}/firmware/build/kiki_esp32.bin}"
  [[ -f "$image" ]] || { echo "no image at ${image}" >&2; exit 1; }
  lan_ip="${KIKI_LAN_IP:-$(ip -4 route get 1.1.1.1 2>/dev/null | grep -oP 'src \K\S+' | head -1)}"
  [[ -n "$lan_ip" ]] || { echo "could not work out this machine's LAN address; set KIKI_LAN_IP" >&2; exit 1; }
  build_id="$(od -An -tx1 -j 176 -N 4 "$image" | tr -d ' \n')"
  mkdir -p "$serve_dir"
  cp "$image" "$serve_dir/kiki_esp32.bin"
  stop >/dev/null 2>&1 || true
  sleep 1
  # Bound to the LAN address, not 127.0.0.1: the board has to reach it.
  (cd "$serve_dir" && setsid nohup python3 -m http.server "$port" --bind "$lan_ip" \
    > /dev/null 2>&1 < /dev/null &)
  sleep 2
  url="http://${lan_ip}:${port}/kiki_esp32.bin"
  if ! curl -fsS -m 5 -o /dev/null "$url"; then
    echo "the file server is not answering on ${url}" >&2
    stop
    exit 1
  fi
  echo "$url" > "$pending"
  echo "build:   ${build_id}"
  echo "url:     ${url}  (LAN, plaintext)"
  echo "queued:  ${pending}"
  echo
  echo "The gateway hands this to the board on its next hello. Watch for:"
  echo "  grep -E 'kiki_ota|device firmware build' the gateway log"
  echo "and confirm it ends with: device firmware build ${build_id}"
  echo
  echo "Then: ${BASH_SOURCE[0]} --stop"
  exit 0
fi

[[ -f "$image" ]] || { echo "no image at ${image}; build it, or pass one as the first argument" >&2; exit 1; }

# The build ID is the first four bytes of the ELF SHA-256 at offset 0xB0 -- the
# same stamp the panel shows at boot and the board now sends in its hello. Print
# it so you can tell, afterwards, whether the board is running THIS image and
# not a similar-looking one. Guessing from the compile date is what went wrong
# before: esp_app_desc.c is not always recompiled, so its date can be hours
# stale while the code is current.
build_id="$(od -An -tx1 -j 176 -N 4 "$image" | tr -d ' \n')"

mkdir -p "$serve_dir"
cp "$image" "$serve_dir/kiki_esp32.bin"

stop >/dev/null 2>&1 || true
sleep 1
(cd "$serve_dir" && setsid nohup python3 -m http.server "$port" --bind 127.0.0.1 \
  > /dev/null 2>&1 < /dev/null &)
sleep 2

: > "$log"
setsid nohup "$cloudflared" tunnel --url "http://127.0.0.1:${port}" \
  >> "$log" 2>&1 < /dev/null &

for _ in $(seq 1 60); do
  url="$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$log" | head -1 || true)"
  if [[ -n "$url" ]]; then
    # Cloudflare prints the hostname when it *allocates* it, which is before it
    # is routable -- a request in that window comes back 530 (origin
    # unreachable). The board treats that as a failed download, and its three
    # retries are 8 s apart, so a slow registration can burn the whole update
    # against a tunnel that was simply not ready. Queue the URL only once it has
    # actually served the image.
    echo "waiting for ${url} to serve..."
    host="${url#https://}"
    # Resolve through PUBLIC resolvers, not just this machine's.
    #
    # A freshly allocated quick-tunnel name propagates unevenly: measured on
    # 2026-09-07, 8.8.8.8 answered immediately while 1.1.1.1 returned NXDOMAIN
    # and this laptop's systemd-resolved never resolved it at all -- yet the
    # tunnel was serving the image perfectly the whole time (HTTP 200, full
    # 2,055,168 bytes, pinned with --resolve). The old check used the system
    # resolver, so it declared a working tunnel dead and refused to queue.
    #
    # The board does its own DNS through its own DHCP server, so what matters
    # is whether the name resolves ANYWHERE, not whether it resolves here.
    ready=0
    addr=""
    for _ in $(seq 1 40); do
      for resolver in 8.8.8.8 1.1.1.1 9.9.9.9; do
        addr="$(nslookup "$host" "$resolver" 2>/dev/null \
                | awk '/^Address: /{print $2; exit}')"
        [[ -n "$addr" ]] && break
      done
      if [[ -n "$addr" ]] && curl -fsS -m 8 -o /dev/null -r 0-1023 \
           --resolve "${host}:443:${addr}" "${url}/kiki_esp32.bin" 2>/dev/null; then
        ready=1
        break
      fi
      sleep 3
    done
    if (( ! ready )); then
      echo "tunnel never served the image; not queueing an update that would fail" >&2
      stop
      exit 1
    fi
    if ! getent hosts "$host" >/dev/null 2>&1; then
      echo
      echo "NOTE: this machine's resolver still cannot see ${host}," >&2
      echo "      but it resolves publicly (${addr}) and the tunnel serves." >&2
      echo "      The board resolves through its own DHCP DNS. If the download" >&2
      echo "      fails with a DNS error, that resolver is the thing to fix," >&2
      echo "      or use --lan while the board is on this network." >&2
      echo
    fi
    echo "${url}/kiki_esp32.bin" > "$pending"
    echo "build:   ${build_id}"
    echo "url:     ${url}/kiki_esp32.bin"
    echo "queued:  ${pending}"
    echo
    echo "The gateway hands this to the board on its next hello. Watch for:"
    echo "  grep -E 'kiki_ota|device firmware build' gateway.log"
    echo "and confirm it ends with: device firmware build ${build_id}"
    echo
    echo "Then: ${BASH_SOURCE[0]} --stop"
    exit 0
  fi
  sleep 1
done

echo "cloudflared never reported a hostname; see ${log}" >&2
stop
exit 1
