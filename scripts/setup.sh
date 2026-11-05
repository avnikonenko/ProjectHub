#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
if ! command -v node >/dev/null && [[ -x /home/user/opt/miniforge3/bin/node ]]; then
  export PATH="/home/user/opt/miniforge3/bin:$PATH"
fi
command -v node >/dev/null || { echo 'Install Node.js 22.18+ and npm, then rerun setup.' >&2; exit 1; }
command -v npm >/dev/null || { echo 'npm is required.' >&2; exit 1; }
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[test]'
cd frontend
if [[ -f package-lock.json ]]; then
  npm ci
else
  echo 'No dependency lockfile yet: resolving packages and generating package-lock.json.' >&2
  npm install
fi
npm run build
