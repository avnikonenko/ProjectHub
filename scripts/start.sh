#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
[[ -x .venv/bin/python ]] || { echo 'Run bash scripts/setup.sh first.' >&2; exit 1; }
[[ -f frontend/dist/index.html ]] || { echo 'Frontend not built; run bash scripts/setup.sh.' >&2; exit 1; }
exec .venv/bin/python -m backend.app.cli start
