#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
bash scripts/setup.sh
.venv/bin/python -m backend.app.cli config
bash scripts/install-local.sh
