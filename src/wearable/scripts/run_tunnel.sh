#!/usr/bin/env bash
# Publish the gateway on the public internet through a Cloudflare tunnel, and
# record the URL where the gateway can read it.
#
# Why Cloudflare and not Tailscale Funnel: measured from this network, Funnel's
# ingress is 143 ms away (Dubai) and gave a 447 ms round trip. Cloudflare's edge
# is 6.5 ms away and gave 20.6 ms -- 22x better, and only ~14 ms worse than
# being on the LAN. Tailscale is still the right tool for SSH to the laptop;
# it is the wrong one for carrying live audio out of this house.
#
#   ./run_tunnel.sh              # quick tunnel, ephemeral hostname
#   ./run_tunnel.sh mytunnel     # a named tunnel you have already configured
#
# A quick tunnel needs no account and no domain, but Cloudflare issues a new
# hostname every restart. That is fine here: the gateway reads the URL from the
# file below on every device connection and hands it to the board, which keeps
# it in NVS. A changed hostname costs a reconnect, not a reflash.
#
# For a hostname that never changes, put a domain in a Cloudflare account and
# run a named tunnel instead -- that is the only part a quick tunnel cannot do.
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
uri_file="${KIKI_PUBLIC_URI_FILE:-${project_dir}/public_uri.txt}"
log="${project_dir}/cloudflared.log"
port="${KIKI_GATEWAY_PORT:-8765}"

# `--supervised` keeps this script in the foreground for the lifetime of the
# tunnel, because server_manager.py tracks the process it launched: a script
# that forks and exits reads as a dead service and gets restarted forever. Run
# by hand it still detaches and returns the prompt.
#
# Parsed before anything else and shifted away, or the named-tunnel branch below
# takes it for a tunnel name and tries to run a tunnel called "--supervised".
supervised=0
if [[ "${1:-}" == "--supervised" ]]; then
  supervised=1
  shift
fi

# ~/bin is on an interactive PATH and not on the one a service or an ssh command
# gets, so a bare `cloudflared` works when typed and fails when supervised.
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

# A named tunnel keeps its hostname forever, so there is no URL to publish and
# nothing to learn -- it just runs.
if [[ $# -ge 1 ]]; then
  exec "$cloudflared" tunnel run "$1"
fi

pkill -f "[c]loudflared tunnel --url" || true
# The old hostname is worse than none: it is dead the moment the previous
# tunnel died, and the supervisor's health check is the presence of this file.
rm -f "$uri_file"
: > "$log"
if (( supervised )); then
  "$cloudflared" tunnel --url "http://127.0.0.1:${port}" >> "$log" 2>&1 &
else
  setsid nohup "$cloudflared" tunnel --url "http://127.0.0.1:${port}" \
    >> "$log" 2>&1 < /dev/null &
fi
cloudflared_pid=$!

# The hostname is only printed once the tunnel is registered, so wait for it
# rather than racing the gateway to the file.
for _ in $(seq 1 60); do
  url="$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$log" | head -1 || true)"
  if [[ -n "$url" ]]; then
    # The gateway hands this to the device, which speaks WebSocket, not HTTP.
    printf 'wss://%s/\n' "${url#https://}" > "$uri_file"
    echo "public gateway: $(cat "$uri_file")"
    echo "written to: ${uri_file}"
    # Supervised: hand our own lifetime to cloudflared, so the supervisor sees
    # the tunnel go down when it actually does.
    (( supervised )) && wait "$cloudflared_pid"
    exit 0
  fi
  sleep 1
done

echo "cloudflared did not report a hostname; see ${log}" >&2
exit 1
