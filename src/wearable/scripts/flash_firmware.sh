#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: $0 /dev/ttyACM0" >&2
  exit 2
fi
project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
idf_dir="${IDF_PATH:-${project_dir}/.tools/esp-idf}"
source "${idf_dir}/export.sh"
cd "${project_dir}/firmware"
idf.py -p "$1" flash monitor
