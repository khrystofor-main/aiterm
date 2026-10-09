#!/usr/bin/env bash
# uninstall.sh — removes everything install.sh added. Your own settings stay.
set -euo pipefail

ROOT=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
BIN="$HOME/.local/bin"

for f in "$ROOT"/bin/*; do
  target="$BIN/$(basename "$f")"
  # Only our own links
  if [ -L "$target" ] && [ "$(readlink -f "$target")" = "$f" ]; then
    rm "$target"
  fi
done
rm -f "$HOME/.local/share/applications/aiterm.desktop" \
  "$HOME/.local/share/applications/io.github.khrystofor_main.Aiterm.desktop"

# agy: the plugin link, the permissions, any old rules block
"$ROOT/bin/aiterm-agent-setup" --remove >/dev/null

echo "aiterm removed."
