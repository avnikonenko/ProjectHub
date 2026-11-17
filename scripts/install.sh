#!/usr/bin/env bash
set -euo pipefail
skill_mode=ask
for argument in "$@"; do
  case "$argument" in
    --install-skill) skill_mode=yes ;;
    --skip-skill) skill_mode=no ;;
    --help|-h)
      echo 'Usage: bash scripts/install.sh [--install-skill | --skip-skill]'
      echo 'Interactive installs offer the optional Codex notes skill; unattended installs skip it by default.'
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
    echo 'Optional: link the ProjectHub notes skill into ~/.agents/skills for Codex.'
    response=''
    read -r -p 'Install Codex skill? [y/N] ' response || true
    case "$response" in
      y|Y|yes|YES) skill_mode=yes ;;
    esac
  fi
fi
if [[ "$skill_mode" == yes ]]; then
  bash scripts/install-skill.sh
else
  echo 'Codex skill skipped. Install later with: bash scripts/install-skill.sh'
fi
