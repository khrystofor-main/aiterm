#!/usr/bin/env bash
# uninstall.sh — removes everything install.sh added. Your own settings stay.
set -euo pipefail

ROOT=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
BIN="$HOME/.local/bin"
RULES="$HOME/.gemini/GEMINI.md"
PLUGIN="$HOME/.gemini/config/plugins/aiterm"
AGY_SETTINGS="$HOME/.gemini/antigravity-cli/settings.json"

for f in "$ROOT"/bin/*; do
  target="$BIN/$(basename "$f")"
  # Only our own links
  if [ -L "$target" ] && [ "$(readlink -f "$target")" = "$f" ]; then
    rm "$target"
  fi
done
rm -f "$HOME/.local/share/applications/aiterm.desktop" \
  "$HOME/.local/share/applications/io.github.khrystofor_main.Aiterm.desktop"

# Only our own link
if [ -L "$PLUGIN" ] && [ "$(readlink -f "$PLUGIN")" = "$ROOT/agy-plugin" ]; then
  rm "$PLUGIN"
fi

# The rules block of versions before the plugin
if [ -f "$RULES" ]; then
  tmp=$(mktemp)
  awk '$0 == "<!-- aiterm:begin -->" {skip=1} !skip {print} $0 == "<!-- aiterm:end -->" {skip=0}' "$RULES" > "$tmp"
  cat "$tmp" > "$RULES" && rm "$tmp"
fi

if [ -f "$AGY_SETTINGS" ] && command -v jq >/dev/null; then
  tmp=$(mktemp)
  jq 'if .permissions.allow then .permissions.allow -= ["command(aiterm-left)", "command(aiterm-run)",
      "mcp(aiterm_terminal/*)", "mcp(aiterm_terminal/read_terminal)", "mcp(aiterm_terminal/get_cwd)",
      "mcp(aiterm_terminal/wait_for_command)"]
    else . end' "$AGY_SETTINGS" > "$tmp" && cat "$tmp" > "$AGY_SETTINGS" && rm "$tmp"
fi

echo "aiterm removed."
