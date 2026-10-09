#!/usr/bin/env bash
# Runs a command on an invisible display: a private GNOME Shell in headless
# mode on its own session bus. Nothing shows up on the desktop, yet frames
# are really drawn (Broadway stops drawing without a browser attached), so
# this is the one for recordings and screenshots:
#
#   tests/headless.sh packaging/animations_demo.py
set -u
if [ -z "${AITERM_HEADLESS_BUS:-}" ]; then
  # The shell starts its own services on the private bus; their chatter goes
  # nowhere, the command's own output (fds 3, 4) comes through
  exec 3>&1 4>&2
  exec env AITERM_HEADLESS_BUS=1 dbus-run-session -- "$0" "$@" >/dev/null 2>&1
fi

runtime=$(mktemp -d)
chmod 700 "$runtime"
export XDG_RUNTIME_DIR=$runtime
gnome-shell --headless --wayland --no-x11 --virtual-monitor 1600x1000 \
  --wayland-display aiterm-headless >"$runtime/shell.log" 2>&1 &
shell=$!
for _ in $(seq 100); do
  [ -S "$runtime/aiterm-headless" ] && break
  sleep 0.1
done
if [ ! -S "$runtime/aiterm-headless" ]; then
  echo "headless.sh: GNOME Shell did not start:" >&4
  tail -5 "$runtime/shell.log" >&4
  kill $shell 2>/dev/null
  exit 1
fi
WAYLAND_DISPLAY=aiterm-headless GDK_BACKEND=wayland "$@" >&3 2>&4
code=$?
kill $shell
wait $shell 2>/dev/null
rm -rf "$runtime"
exit $code
