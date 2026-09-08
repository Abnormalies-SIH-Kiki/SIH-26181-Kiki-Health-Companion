#!/usr/bin/env bash
# Move the WhatsApp bridge off port 8080 in the isolated legacy snapshot.
#
# On the Pi, llama.cpp ran on the laptop and the WhatsApp bridge ran on the Pi,
# so both could own :8080. Route 1 puts them on the same host for the first
# time and they collide. The failure is not a clean "address in use" either:
#
#   launch_whatsapp_bridge_background() decides whether the bridge is already
#   running by probing whether :8080 is open. llama-server answers, so Kiki
#   concludes the bridge is up and never starts it -- and the MCP server then
#   issues its REST calls against llama-server, which is where the
#   "unhandled errors in a TaskGroup" came from.
#
# The port is hardcoded in the Go bridge and in the MCP server, so this patches
# both plus the config used for the liveness probe, then rebuilds the binary.
#
# The snapshot is untracked private runtime data, which is why this is a script
# in the repo rather than an edit: it has to be reproducible after a re-deploy.
set -euo pipefail

ROOT="${KIKI_LEGACY_ROOT:-/home/vaibhav/KikiESP32/gateway/legacy_kiki}"
PORT="${KIKI_WHATSAPP_PORT:-8099}"
GO_BIN="${GO_BIN:-/home/vaibhav/go-toolchain/go/bin/go}"

BRIDGE_DIR="$ROOT/whatsapp-mcp/whatsapp-bridge"
MCP_SERVER="$ROOT/whatsapp-mcp/whatsapp-mcp-server/whatsapp.py"
CONFIG="$ROOT/tools_and_config/config.json"

for path in "$BRIDGE_DIR/main.go" "$MCP_SERVER" "$CONFIG"; do
    [ -f "$path" ] || { echo "missing $path" >&2; exit 1; }
done

echo "moving the WhatsApp bridge to port $PORT"

sed -i -E "s/startRESTServer\(client, messageStore, [0-9]+\)/startRESTServer(client, messageStore, $PORT)/" \
    "$BRIDGE_DIR/main.go"
sed -i -E "s#WHATSAPP_API_BASE_URL = \"http://localhost:[0-9]+/api\"#WHATSAPP_API_BASE_URL = \"http://localhost:$PORT/api\"#" \
    "$MCP_SERVER"

python3 - "$CONFIG" "$PORT" <<'PY'
import json, sys
path, port = sys.argv[1], int(sys.argv[2])
with open(path) as handle:
    config = json.load(handle)
config.setdefault("whatsapp", {})["bridge_port"] = port
# The default log path still points at the Pi's tree; keep the bridge's log
# inside the snapshot so it is findable on this host.
config["whatsapp"]["bridge_log_file"] = ""
with open(path, "w") as handle:
    json.dump(config, handle, indent=2)
    handle.write("\n")
print(f"config: whatsapp.bridge_port = {port}")
PY

grep -n "startRESTServer(client" "$BRIDGE_DIR/main.go"
grep -n "WHATSAPP_API_BASE_URL" "$MCP_SERVER"

echo "rebuilding the bridge"
cd "$BRIDGE_DIR"
CGO_ENABLED=1 "$GO_BIN" build -o whatsapp-bridge .
echo "done: $(ls -l whatsapp-bridge | awk '{print $5}') bytes"
