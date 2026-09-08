#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -f "${project_dir}/gateway.env" ]]; then
  set -a
  source "${project_dir}/gateway.env"
  set +a
fi

export KIKI_GATEWAY_HOST="${KIKI_GATEWAY_HOST:-0.0.0.0}"
export KIKI_GATEWAY_PORT="${KIKI_GATEWAY_PORT:-8765}"
export KIKI_GATEWAY_LEGACY_ROOT="${KIKI_GATEWAY_LEGACY_ROOT:-${project_dir}/gateway/legacy_kiki}"
exec "${project_dir}/.venv/bin/kiki-esp32-gateway"
