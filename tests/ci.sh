#!/usr/bin/env bash
# tests/ci.sh — tests/run.sh as CI runs it, as root in a fresh Ubuntu 26.04
# (VTE 0.80+, libadwaita 1.8): installs the dependencies, then runs the tests
# as a plain user with GTK under a virtual X display.
# .github/workflows/tests.yml runs it in its job container; dev/ci-tests runs
# it in docker anywhere else.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")/.."

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
  python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-vte-3.91 jq bubblewrap git ca-certificates \
  xvfb xauth dbus-x11 dbus libgl1-mesa-dri fonts-dejavu-core

id tester >/dev/null 2>&1 || useradd -m tester
chown -R tester .
# Xvfb has no GPU: GTK's GL renderer falls back to slow software GL, and a
# busy screen (the chat's spinners) then starves the main loop. The cairo
# renderer is the quick one without a GPU
export GSK_RENDERER=cairo
su tester -w GSK_RENDERER -c 'dbus-run-session -- xvfb-run -a -s "-screen 0 1600x1000x24" tests/run.sh'
