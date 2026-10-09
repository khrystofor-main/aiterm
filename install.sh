#!/usr/bin/env bash
# install.sh — installs aiterm for the current user (no sudo needed).
#   - links bin/* into ~/.local/bin
#   - adds Aiterm to the applications menu
#   - adds the agent rules to ~/.gemini/GEMINI.md (between aiterm markers)
#   - lets agy run aiterm-left / aiterm-run without asking every time
# Running it again updates everything in place.
set -euo pipefail

ROOT=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
BIN="$HOME/.local/bin"
APPS="$HOME/.local/share/applications"
RULES="$HOME/.gemini/GEMINI.md"
AGY_SETTINGS="$HOME/.gemini/antigravity-cli/settings.json"
BEGIN='<!-- aiterm:begin -->'
END='<!-- aiterm:end -->'

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

# 3. Agent rules: replace our block, keep everything else in the file
mkdir -p "$(dirname "$RULES")"
touch "$RULES"
tmp=$(mktemp)
awk -v b="$BEGIN" -v e="$END" '$0 == b {skip=1} !skip {print} $0 == e {skip=0}' "$RULES" > "$tmp"
# No more than one blank line at the end before our block
sed -i -e :a -e '/^\n*$/{$d;N;ba' -e '}' "$tmp"
{
  [ -s "$tmp" ] && echo
  echo "$BEGIN"
  cat "$ROOT/rules/aiterm.md"
  echo "$END"
} >> "$tmp"
mv "$tmp" "$RULES"
echo "✓ Agent rules in $RULES"

# 4. agy permissions: aiterm-left / aiterm-run run without a prompt
mkdir -p "$(dirname "$AGY_SETTINGS")"
[ -s "$AGY_SETTINGS" ] || echo '{}' > "$AGY_SETTINGS"
tmp=$(mktemp)
jq '.permissions.allow = ((.permissions.allow // []) + ["command(aiterm-left)", "command(aiterm-run)"] | unique)' \
  "$AGY_SETTINGS" > "$tmp" && mv "$tmp" "$AGY_SETTINGS"
echo "✓ agy may run aiterm-left and aiterm-run without asking ($AGY_SETTINGS)"

cat <<'EOF'

Done. Start it with `aiterm` or from the menu (Aiterm); Alt+Enter opens the agent.

Optional:
  - agy only auto-approves simple commands, so it may still ask about
    aiterm-run 'command'. To stop the prompts, set Tool Permission to
    always-proceed in agy's /config. sudo stays protected: aiterm-run makes
    you type your password for every sudo command the agent runs.
  - Want answers in your language? Add a line like "Always reply in Russian."
    to ~/.gemini/GEMINI.md outside the aiterm markers.
EOF
