#!/usr/bin/env bash
# packaging/build-deb.sh — builds dist/aiterm_<version>_all.deb from the
# files git tracks. No build step: the package carries the Python app as it
# is in the repository, under /usr/lib/aiterm, with the commands in /usr/bin.
#   packaging/build-deb.sh
set -euo pipefail

ROOT=$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)
VERSION=$(python3 -c "import sys; sys.path.insert(0, '$ROOT/src'); import aiterm; print(aiterm.VERSION)")
PKG=$(mktemp -d)
trap 'rm -rf "$PKG"' EXIT
LIB="$PKG/usr/lib/aiterm"

# The app, the agy plugin, the launcher template: what git tracks, minus
# what a user does not run (tests, evals, docs, repo files)
mkdir -p "$LIB"
git -C "$ROOT" ls-files -z bin src agy-plugin | (cd "$ROOT" && xargs -0 cp --parents -t "$LIB")
chmod 755 "$LIB"/bin/*

mkdir -p "$PKG/usr/bin"
for f in "$LIB"/bin/*; do
  ln -s "../lib/aiterm/bin/$(basename "$f")" "$PKG/usr/bin/$(basename "$f")"
done

DESKTOP=io.github.khrystofor_main.Aiterm.desktop
mkdir -p "$PKG/usr/share/applications"
sed "s|@BIN@|/usr/bin|" "$ROOT/data/$DESKTOP.in" > "$PKG/usr/share/applications/$DESKTOP"

DOC="$PKG/usr/share/doc/aiterm"
mkdir -p "$DOC"
cp "$ROOT/README.md" "$DOC/"
{
  echo "Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/"
  echo "Upstream-Name: aiterm"
  echo "Source: https://github.com/khrystofor-main/aiterm"
  echo
  echo "Files: *"
  echo "Copyright: $(grep -m1 Copyright "$ROOT/LICENSE" | sed 's/^Copyright (c) //')"
  echo "License: MIT"
  sed 's/^/ /; s/^ $/ ./' "$ROOT/LICENSE"
} > "$DOC/copyright"

mkdir -p "$PKG/DEBIAN"
cat > "$PKG/DEBIAN/control" <<EOF
Package: aiterm
Version: $VERSION
Architecture: all
Maintainer: Oleksandr Khrystofor <khrystofor-main@users.noreply.github.com>
Depends: python3 (>= 3.12), python3-gi, gir1.2-gtk-4.0, gir1.2-adw-1 (>= 1.8), gir1.2-vte-3.91 (>= 0.80), jq
Recommends: bubblewrap
Section: x11
Priority: optional
Homepage: https://github.com/khrystofor-main/aiterm
Installed-Size: $(du -sk "$PKG/usr" | cut -f1)
Description: AI terminal for GNOME with an agent that works in your shell
 A GTK 4 terminal with the Antigravity CLI (agy) in a side panel. The agent
 reads your terminal and runs commands in it, in plain sight, through an MCP
 server; every command waits for your approval. agy itself is not included:
 install it from https://antigravity.google/docs/cli/install
EOF

# Owned by root (--root-owner-group), readable by all, whatever the umask
chmod -R u=rwX,go=rX "$PKG"
chmod 755 "$LIB"/bin/*

mkdir -p "$ROOT/dist"
OUT="$ROOT/dist/aiterm_${VERSION}_all.deb"
dpkg-deb --root-owner-group --build "$PKG" "$OUT" >/dev/null
echo "$OUT"
