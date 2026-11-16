#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 0 ]]; then
  echo 'Usage: bash scripts/install-skill.sh' >&2
  exit 1
fi

script_path="$(readlink -f "${BASH_SOURCE[0]}")"
project_root="$(cd "$(dirname "$script_path")/.." && pwd)"
source_dir="$project_root/skills/projecthub-notes"
skill_parent="$HOME/.agents/skills"
target="$skill_parent/projecthub-notes"

if [[ ! -f "$source_dir/SKILL.md" ]]; then
  echo "install-skill: missing skill: $source_dir/SKILL.md" >&2
  exit 1
fi

if [[ -e "$target" || -L "$target" ]]; then
  if [[ ! -L "$target" || "$(readlink -f "$target" 2>/dev/null || true)" != "$source_dir" ]]; then
    echo "install-skill: refusing to replace existing skill: $target" >&2
    exit 1
  fi
else
  mkdir -p "$skill_parent"
  # -T prevents placing a link inside a directory created at the target path.
  ln -sT "$source_dir" "$target"
fi

echo "Installed Codex skill: $target"
echo 'Keep this checkout: the skill link follows its updates. Open a new Codex session to discover it.'
echo 'ProjectHub must be running to use the skill.'
