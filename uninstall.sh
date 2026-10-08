#!/usr/bin/env bash
# uninstall.sh — removes everything install.sh added. Your own settings stay.
set -euo pipefail

ROOT=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
BIN="$HOME/.local/bin"
RULES="$HOME/.gemini/GEMINI.md"
AGY_SETTINGS="$HOME/.gemini/antigravity-cli/settings.json"

for f in "$ROOT"/bin/*; do
  target="$BIN/$(basename "$f")"
  # Only our own links
  if [ -L "$target" ] && [ "$(readlink -f "$target")" = "$f" ]; then
    rm "$target"
  fi
done
rm -f "$HOME/.local/share/applications/aiterm.desktop"

if [ -f "$RULES" ]; then
  tmp=$(mktemp)
  awk '$0 == "<!-- aiterm:begin -->" {skip=1} !skip {print} $0 == "<!-- aiterm:end -->" {skip=0}' "$RULES" > "$tmp"
  mv "$tmp" "$RULES"
fi

if [ -f "$AGY_SETTINGS" ] && command -v jq >/dev/null; then
  tmp=$(mktemp)
  jq 'if .permissions.allow then .permissions.allow -= ["command(aiterm-left)", "command(aiterm-run)"] else . end' \
    "$AGY_SETTINGS" > "$tmp" && mv "$tmp" "$AGY_SETTINGS"
fi

tmux -L aiterm kill-server 2>/dev/null || true
echo "aiterm removed."
