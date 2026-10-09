#!/usr/bin/env bash
# Runs a command on an invisible display: a private GNOME Shell in headless
# mode on its own session bus. Nothing shows up on the desktop, yet frames
# are really drawn (Broadway stops drawing without a browser attached), so
# this is the one for recordings and screenshots:
#
#   tests/headless.sh packaging/animations_demo.py
#
# The command itself stays on the user's session bus and runtime folder, so
# what it needs from the session (agy's login in the keyring) is there.
set -u
if [ -z "${AITERM_HEADLESS_BUS:-}" ]; then
  # The shell starts its own services on the private bus; their chatter goes
  # nowhere, the command's own output (fds 3, 4) comes through
  exec 3>&1 4>&2
  exec env AITERM_HEADLESS_BUS=1 AITERM_USER_BUS="${DBUS_SESSION_BUS_ADDRESS:-}" \
    AITERM_USER_RUNTIME="${XDG_RUNTIME_DIR:-}" dbus-run-session -- "$0" "$@" >/dev/null 2>&1
fi

runtime=$(mktemp -d)
chmod 700 "$runtime"
export XDG_RUNTIME_DIR=$runtime
gnome-shell --headless --wayland --no-x11 --virtual-monitor 1600x1000 \
  --wayland-display aiterm-headless >"$runtime/shell.log" 2>&1 &
shell=$!
trap 'kill $shell 2>/dev/null; rm -rf "$runtime"' EXIT
trap 'exit 143' TERM INT
for _ in $(seq 100); do
  [ -S "$runtime/aiterm-headless" ] && break
  sleep 0.1
done
if [ ! -S "$runtime/aiterm-headless" ]; then
  echo "headless.sh: GNOME Shell did not start:" >&4
  tail -5 "$runtime/shell.log" >&4
  exit 1
fi
env -u AITERM_HEADLESS_BUS -u AITERM_USER_BUS -u AITERM_USER_RUNTIME \
  ${AITERM_USER_BUS:+DBUS_SESSION_BUS_ADDRESS=$AITERM_USER_BUS} \
  ${AITERM_USER_RUNTIME:+XDG_RUNTIME_DIR=$AITERM_USER_RUNTIME} \
  WAYLAND_DISPLAY="$runtime/aiterm-headless" GDK_BACKEND=wayland "$@" >&3 2>&4
exit
