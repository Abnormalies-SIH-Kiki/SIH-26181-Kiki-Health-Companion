#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
idf_dir="${IDF_PATH:-${project_dir}/.tools/esp-idf}"
source "${idf_dir}/export.sh"
cd "${project_dir}/firmware"
# esp_app_desc.c is not reliably rebuilt, which freezes the compile date
# reported by esp_app_get_description() while the code moves on. A stale stamp
# on the panel is worse than no stamp: it says a correct flash did not happen.
rm -f build/esp-idf/esp_app_format/CMakeFiles/__idf_esp_app_format.dir/esp_app_desc.c.obj
idf.py build
