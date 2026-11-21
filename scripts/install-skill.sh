#!/usr/bin/env bash
set -euo pipefail

usage='Usage: bash scripts/install-skill.sh [--agent codex|claude [codex|claude]]'
case "${1:-}" in
  -h|--help)
    echo "$usage"
    exit 0
    ;;
  --agent) shift ;;
  --agent=*) first_agent="${1#*=}"; shift; set -- "$first_agent" "$@" ;;
  '')
    for candidate in codex claude; do
      if command -v "$candidate" >/dev/null; then
        set -- "$@" "$candidate"
      fi
    done
    if [[ $# -eq 0 ]]; then
      echo 'Codex and Claude Code were not found on PATH. Use --agent to install the skill explicitly.'
      exit 0
    fi
    ;;
  *)
    echo "$usage" >&2
    exit 2
    ;;
esac
if [[ $# -eq 0 ]]; then
  echo "$usage" >&2
  exit 2
fi
for agent in "$@"; do
  if [[ "$agent" != codex && "$agent" != claude ]]; then
    echo "$usage" >&2
    exit 2
  fi
done

script_path="$(readlink -f "${BASH_SOURCE[0]}")"
project_root="$(cd "$(dirname "$script_path")/.." && pwd)"
source_dir="$project_root/skills/projecthub-notes"
if [[ ! -f "$source_dir/SKILL.md" ]]; then
  echo "install-skill: missing skill: $source_dir/SKILL.md" >&2
  exit 1
fi

install_skill() {
  local agent_name skill_home target
  if [[ "$1" == codex ]]; then
    agent_name=Codex
    skill_home="$HOME/.agents"
  else
    agent_name='Claude Code'
    skill_home="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
    if [[ "$skill_home" == '~/'* ]]; then
      skill_home="$HOME/${skill_home:2}"
    fi
  fi
  target="$skill_home/skills/projecthub-notes"

  if [[ -e "$target" || -L "$target" ]]; then
    if [[ ! -L "$target" || "$(readlink -f "$target" 2>/dev/null || true)" != "$source_dir" ]]; then
      echo "install-skill: refusing to replace existing skill: $target" >&2
      return 1
    fi
  else
    mkdir -p "$skill_home/skills" || return 1
    # -T prevents placing a link inside a directory created at the target path.
    ln -sT "$source_dir" "$target" || return 1
  fi

  echo "Installed $agent_name skill: $target"
  echo "Open a new $agent_name session with ProjectHub running to use the skill."
}

status=0
installed_agents=()
for agent in "$@"; do
  if [[ " ${installed_agents[*]} " == *" $agent "* ]]; then
    continue
  fi
  installed_agents+=("$agent")
  install_skill "$agent" || status=1
done
exit "$status"
