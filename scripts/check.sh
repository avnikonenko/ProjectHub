#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
if ! command -v node >/dev/null && [[ -x /home/user/opt/miniforge3/bin/node ]]; then
  export PATH="/home/user/opt/miniforge3/bin:$PATH"
fi
.venv/bin/python -m pytest
cd frontend
npm test
npm run build
