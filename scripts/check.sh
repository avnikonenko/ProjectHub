#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
if ! command -v node >/dev/null && [[ -x "$HOME/opt/miniforge3/bin/node" ]]; then
  export PATH="$HOME/opt/miniforge3/bin:$PATH"
fi
.venv/bin/python -m pytest
cd frontend
npm test
npm run build
