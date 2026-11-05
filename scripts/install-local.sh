#!/usr/bin/env bash
set -euo pipefail

script_path="$(readlink -f "${BASH_SOURCE[0]}")"
project_root="$(cd "$(dirname "$script_path")/.." && pwd)"
launcher="$project_root/scripts/projecthub"
bin_dir="${XDG_BIN_HOME:-$HOME/.local/bin}"
target="$bin_dir/projecthub"

if [[ ! -x "$launcher" ]]; then
  echo "install-local: launcher is not executable: $launcher" >&2
  exit 1
fi

mkdir -p "$bin_dir"
if [[ -e "$target" || -L "$target" ]]; then
  existing_target="$(readlink -f "$target" 2>/dev/null || true)"
  if [[ "$existing_target" != "$launcher" ]]; then
    echo "install-local: refusing to replace existing command: $target" >&2
    exit 1
  fi
else
  ln -s "$launcher" "$target"
fi

echo "Installed ProjectHub command: $target"
if [[ ":$PATH:" != *":$bin_dir:"* ]]; then
  echo "Add this directory to PATH: $bin_dir"
fi
echo "Run: projecthub"
