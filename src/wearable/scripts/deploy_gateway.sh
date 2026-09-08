#!/usr/bin/env bash
# Push the gateway from the Pi (the repo of record) to the laptop (where it runs).
#
# The laptop deployment is a git checkout that nobody commits to -- its working
# tree is whatever was last copied over -- and the venv installs the package in
# editable mode, so copying the files IS the deployment and a restart is all
# that is needed to pick them up.
#
# Always takes a timestamped backup first: that tree is not backed by anything
# else, and a bad copy would otherwise be unrecoverable.
#
#   ./scripts/deploy_gateway.sh              # copy, test, restart
#   ./scripts/deploy_gateway.sh --no-restart # copy and test only
#   ./scripts/deploy_gateway.sh --no-test    # skip pytest on the laptop
set -euo pipefail

HOST="${KIKI_LAPTOP:-vaibhav@vaibhav}"
REMOTE="${KIKI_LAPTOP_ROOT:-/home/vaibhav/KikiESP32}"
LOCAL="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RESTART=1
TEST=1
for arg in "$@"; do
  case "$arg" in
    --no-restart) RESTART=0 ;;
    --no-test) TEST=0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

STAMP="$(date +%Y%m%d-%H%M%S)"
echo "==> backing up the laptop's gateway to deploy-backups/gateway-$STAMP"
ssh "$HOST" "mkdir -p '$REMOTE/deploy-backups/gateway-$STAMP' && \
  cp -a '$REMOTE/gateway/kiki_gateway' '$REMOTE/deploy-backups/gateway-$STAMP/' && \
  cp -a '$REMOTE/gateway/tests' '$REMOTE/deploy-backups/gateway-$STAMP/' 2>/dev/null || true"

echo "==> copying kiki_gateway, tests and tools"
# firmware/main goes too. Several gateway tests parse the firmware headers to
# prove the two sides have not drifted -- the dance move table is a wire format,
# the motion states are a prompt vocabulary -- and without the sources those
# tests fail on the laptop for a reason that has nothing to do with the change
# being deployed. The laptop is also where the board is flashed from.
tar -C "$LOCAL/firmware" --exclude='__pycache__' -cf - main \
  | ssh "$HOST" "mkdir -p '$REMOTE/firmware' && tar -C '$REMOTE/firmware' -xf -"
# legacy_kiki is deliberately NOT copied: it is a gitignored snapshot of
# KikiFast and the laptop's copy is the live one, with its own knowledge base,
# journal and conversation history.
# The excludes have to precede the paths: tar applies them positionally and
# silently ignores the ones that come after, which ships __pycache__ instead.
tar -C "$LOCAL/gateway" --exclude='__pycache__' --exclude='*.pyc' \
  -cf - kiki_gateway tests tools pyproject.toml \
  | ssh "$HOST" "tar -C '$REMOTE/gateway' -xf -"

if [ "$TEST" = 1 ]; then
  echo "==> running the gateway test suite on the laptop"
  ssh "$HOST" "cd '$REMOTE/gateway' && '$REMOTE/.venv/bin/python' -m pytest -q tests 2>&1 | tail -25"
fi

if [ "$RESTART" = 1 ]; then
  echo "==> restarting the gateway"
  # Only the gateway. The manager's /restart reloads llama, Whisper and TTS as
  # well -- minutes of model loading, and a fresh Cloudflare hostname -- for a
  # change that only ever touches this one process. stop_all() kills strays by
  # pattern, so a later manager restart still cleans this up correctly.
  ssh "$HOST" "pkill -f '[k]iki-esp32-gateway' || true; sleep 5; \
    cd '$REMOTE' && setsid nohup ./scripts/run_gateway.sh \
      >> /home/vaibhav/kiki_servers/logs/gateway.log 2>&1 < /dev/null & \
    sleep 2; echo started"
  # Wait remotely, in one ssh. Polling over ssh spends more time on session
  # setup than on the check and made the whole deploy look hung.
  echo "==> waiting for :8765 (it imports the whole legacy runtime first)"
  ssh "$HOST" 'for i in $(seq 1 90); do
      if timeout 2 bash -c "echo > /dev/tcp/127.0.0.1/8765" 2>/dev/null; then
        echo "gateway is listening"; exit 0
      fi
      sleep 2
    done
    echo "gateway did not come back within 180s -- see kiki_servers/logs/gateway.log" >&2
    exit 1' 
fi
