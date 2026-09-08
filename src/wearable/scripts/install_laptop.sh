#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python_bin="${PYTHON_BIN:-/usr/bin/python3.12}"

"${python_bin}" -m venv "${project_dir}/.venv"
"${project_dir}/.venv/bin/pip" install --upgrade pip setuptools wheel
"${project_dir}/.venv/bin/pip" install -e "${project_dir}/gateway"
# openWakeWord is no longer the wake path -- the hotword is matched in Whisper's
# transcript (codestructure §5.3) -- but it is still installed, because it is the
# only detector that works when Whisper is down and one env var brings it back.
# It declares tflite-runtime even though this gateway exclusively uses its ONNX
# path, and Python 3.12 has no matching tflite wheel on every Linux build.
"${project_dir}/.venv/bin/pip" install --no-deps openwakeword==0.6.0
"${project_dir}/.venv/bin/python" -c \
  'from openwakeword.utils import download_models; download_models(["__features_only__"])'
# mcp is pinned below 2.0 deliberately. The bundled WhatsApp MCP server is
# written against FastMCP 1.x (`from mcp.server.fastmcp import FastMCP`), and
# 2.0 removed that module -- so an unpinned install resolves to 2.x and the
# daemon dies on import. The failure is invisible from the outside: the client
# reports only "unhandled errors in a TaskGroup", the Go bridge is healthy, and
# Kiki answers that WhatsApp "had a startup error". The Pi runs 1.25.0.
"${project_dir}/.venv/bin/pip" install \
  python-dotenv deepgram-sdk litellm google-genai openai groq tiktoken exa-py \
  pillow pyzmq pyserial 'mcp<2'

echo "Laptop environment ready: ${project_dir}/.venv"
