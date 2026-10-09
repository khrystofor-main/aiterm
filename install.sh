#!/usr/bin/env bash
# install.sh — installs aiterm for the current user (no sudo needed).
#   - links bin/* into ~/.local/bin
#   - adds Aiterm to the applications menu
#   - links the agy plugin (agy-plugin/: the MCP server and its rules) into
#     ~/.gemini/config/plugins/aiterm
#   - lets agy use the terminal tools without its own prompt: aiterm asks
#     before each command itself (Preferences → Agent)
# Running it again updates everything in place.
set -euo pipefail

ROOT=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
BIN="$HOME/.local/bin"
APPS="$HOME/.local/share/applications"

missing=()
python3 -c 'import gi; gi.require_version("Adw", "1"); gi.require_version("Vte", "3.91")' 2>/dev/null ||
  missing+=("GTK 4, libadwaita and VTE for Python (sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-vte-3.91)")
command -v jq >/dev/null || missing+=("jq (sudo apt install jq)")
command -v agy >/dev/null || [ -x "$BIN/agy" ] || missing+=("agy (https://antigravity.google/docs/cli/install)")
if [ ${#missing[@]} -gt 0 ]; then
  echo "Missing dependencies:" >&2
  printf '  - %s\n' "${missing[@]}" >&2
  exit 1
fi

# 1. Commands
mkdir -p "$BIN"
for f in "$ROOT"/bin/*; do
  name=$(basename "$f")
  target="$BIN/$name"
  # Keep a backup of a file that is not our own link
  if [ -e "$target" ] && [ "$(readlink -f "$target")" != "$f" ]; then
    mv "$target" "$target.bak.$(date +%s)"
    echo "Backed up the old $target"
  fi
  ln -sfn "$f" "$target"
done
echo "✓ Commands linked into $BIN: $(ls "$ROOT/bin" | tr '\n' ' ')"

# Links to commands this repository no longer has (aiterm-gtk)
for target in "$BIN"/aiterm*; do
  if [ -L "$target" ] && [[ $(readlink "$target") == "$ROOT"/bin/* ]] && [ ! -e "$target" ]; then
    rm "$target"
  fi
done

# 2. Applications menu launcher; the tmux version's "AI Terminal" goes
mkdir -p "$APPS"
rm -f "$APPS/aiterm.desktop"
DESKTOP="io.github.khrystofor_main.Aiterm.desktop"
sed "s|@BIN@|$BIN|" "$ROOT/data/$DESKTOP.in" > "$APPS/$DESKTOP"
update-desktop-database "$APPS" 2>/dev/null || true
echo "✓ Launcher: Aiterm ($APPS/$DESKTOP)"

# 3. agy: the aiterm plugin, its permissions, the old rules out of GEMINI.md
"$ROOT/bin/aiterm-agent-setup"

cat <<'EOF'

Done. Start it with `aiterm` or from the menu (Aiterm); Alt+Enter opens the agent.
Restart agy if it is running, so it loads the plugin.

Optional:
  - Aiterm asks before every command the agent runs (Run / Don't Run above
    your terminal). To make it hands-free, turn off "Ask Before the Agent
    Runs a Command" in Preferences → Agent. sudo stays protected: you type
    your password for every sudo command the agent runs.
  - Want answers in your language? Add a line like "Always reply in Russian."
    to ~/.gemini/GEMINI.md.
EOF
