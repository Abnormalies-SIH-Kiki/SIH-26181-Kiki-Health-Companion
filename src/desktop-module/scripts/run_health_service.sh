#!/usr/bin/env bash
set -euo pipefail

cd /home/kiki/kiki2/KikiFast
exec /home/kiki/Kiki/kiki/bin/python -m core.health.service
