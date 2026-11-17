#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
if ! command -v node >/dev/null && [[ -x "$HOME/opt/miniforge3/bin/node" ]]; then
  export PATH="$HOME/opt/miniforge3/bin:$PATH"
fi
command -v node >/dev/null || { echo 'Install Node.js 22.18+ and npm, then rerun setup.' >&2; exit 1; }
command -v npm >/dev/null || { echo 'npm is required.' >&2; exit 1; }
command -v python3 >/dev/null || { echo 'Install Python 3.11+ and venv support, then rerun setup.' >&2; exit 1; }
node -e 'const [major, minor] = process.versions.node.split(".").map(Number); process.exit(major > 22 || (major === 22 && minor >= 18) ? 0 : 1)' ||
  { echo 'Node.js 22.18+ is required.' >&2; exit 1; }
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' ||
  { echo 'Python 3.11+ is required.' >&2; exit 1; }
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
