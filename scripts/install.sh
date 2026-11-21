#!/usr/bin/env bash
set -euo pipefail
skill_mode=ask
for argument in "$@"; do
  case "$argument" in
    --install-skill) skill_mode=yes ;;
    --skip-skill) skill_mode=no ;;
    --help|-h)
      echo 'Usage: bash scripts/install.sh [--install-skill | --skip-skill]'
      echo 'Interactive installs offer the optional skill for Codex and Claude Code found on PATH.'
      exit 0
      ;;
    *) echo "Unknown install option: $argument" >&2; exit 1 ;;
  esac
done
cd "$(dirname "${BASH_SOURCE[0]}")/.."
bash scripts/setup.sh
.venv/bin/python -m backend.app.cli config
bash scripts/install-local.sh
if [[ "$skill_mode" == ask ]]; then
  if [[ -t 0 ]]; then
    echo 'Optional: install the ProjectHub skill for Codex and Claude Code found on PATH.'
    response=''
    read -r -p 'Install ProjectHub skill? [y/N] ' response || true
    case "$response" in
      y|Y|yes|YES) skill_mode=yes ;;
    esac
  fi
fi
if [[ "$skill_mode" == yes ]]; then
  bash scripts/install-skill.sh
else
  echo 'ProjectHub skill skipped. Install later with: projecthub install-skill'
fi
