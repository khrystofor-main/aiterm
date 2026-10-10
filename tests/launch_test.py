#!/usr/bin/env python3
"""`aiterm` typed in a terminal (launch.py, Application.do_handle_local_options):
bin/aiterm runs in a pseudo-terminal, as from a shell, under its own
application ID (AITERM_APP_ID), a throwaway HOME and settings folder, and a
plain bash for the agent. Checks that it prints nothing and returns at once,
that the app's output goes to the log, that errors about the command still
show with their exit code, and that --foreground stays in the terminal. The
parts that open a window need a display (run it on Broadway) and are skipped
without one. Run: tests/launch_test.py"""

import os
import pty
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AITERM = os.path.join(ROOT, "bin", "aiterm")
APP_ID = "io.github.khrystofor_main.Aiterm.LaunchTest"

HOME = tempfile.mkdtemp(prefix="aiterm-launch-")
LOG = os.path.join(HOME, ".local", "state", "aiterm", "aiterm.log")
ENV = dict(
    os.environ,
    HOME=HOME,
    AITERM_APP_ID=APP_ID,
    AITERM_CONFIG_DIR=os.path.join(HOME, "config"),
    AITERM_AGY_DIR=os.path.join(HOME, "agy"),
    AITERM_AGENT="/bin/bash --norc --noprofile",
    AITERM_GH=f"{sys.executable} {os.path.join(ROOT, 'tests', 'fake_gh.py')}",
    FAKE_GH_STATE=os.path.join(HOME, "fake-gh.json"),
    SHELL="/bin/bash",
)
# Plenty of output from the app: for the log, or the terminal if it leaked there
DEBUG = {"G_MESSAGES_DEBUG": "all"}
ENV.pop("XDG_STATE_HOME", None)
ENV.pop("AITERM_DETACHED", None)

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"  {'ok  ' if ok else 'FAIL'} {name}" + (f"\n       {detail}" if detail and not ok else ""))


def run_in_pty(args, wait=5, linger=1.5, env=None):
    """Runs aiterm ARGS with a pseudo-terminal as stdin, stdout and stderr.
    Returns (exit code or None if still running after `wait` s, seconds it
    took, all it printed until `linger` s after it exited, the process)."""
    master, slave = pty.openpty()
    start = time.monotonic()
    proc = subprocess.Popen([AITERM, *args], stdin=slave, stdout=slave, stderr=slave, env=dict(ENV, **(env or {})), cwd=HOME)
    os.close(slave)
    out = b""
    deadline = start + wait
    while True:
        now = time.monotonic()
        if proc.poll() is not None and deadline > now + linger:
            deadline = now + linger  # exited: keep listening a little for late output
        if now >= deadline:
            break
        ready, _, _ = select.select([master], [], [], deadline - now)
        if ready:
            try:
                data = os.read(master, 4096)
            except OSError:  # EIO: nothing holds the terminal any more
                data = b""
            if not data:
                time.sleep(max(0, min(0.1, deadline - time.monotonic())))
            out += data
    took = time.monotonic() - start
    os.close(master)
    return proc.poll(), took, out.decode(errors="replace"), proc


def app_pid():
    """The pid of the process that owns APP_ID on the session bus, or None."""
    from gi.repository import Gio, GLib
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION)
        reply = bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                              "GetConnectionUnixProcessID", GLib.Variant("(s)", (APP_ID,)),
                              GLib.VariantType("(u)"), Gio.DBusCallFlags.NONE, 5000)
    except GLib.Error:  # nobody owns it
        return None
    return reply.unpack()[0]


def children(pid):
    found = []
    for task in os.listdir(f"/proc/{pid}/task"):
        with open(f"/proc/{pid}/task/{task}/children") as f:
            found += [int(child) for child in f.read().split()]
    return found


def environ(pid):
    try:
        with open(f"/proc/{pid}/environ", "rb") as f:
            return f.read()
    except OSError:
        return b""


def wait_for(condition, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = condition()
        if value:
            return value
        time.sleep(0.1)
    return None


def stop(pid):
    if pid:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        wait_for(lambda: app_pid() is None, 5)


print("errors and help (no window)")
code, took, out, _ = run_in_pty(["/no/such/dir"])
check("`aiterm /no/such/dir` exits with code 1", code == 1, f"code {code}")
check("…and says why in the calling terminal", "/no/such/dir: no such folder" in out, out)
code, took, out, _ = run_in_pty(["--no-such-option"])
check("a bad option exits with an error code", code not in (0, None), f"code {code}")
check("…and says so", "no-such-option" in out, out)
code, took, out, _ = run_in_pty(["--help"])
check("--help exits with code 0", code == 0, f"code {code}")
check("…and lists --foreground", "--foreground" in out, out)
check("nothing started for these", app_pid() is None and not os.path.exists(LOG))

if not (os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY")
        or os.environ.get("BROADWAY_DISPLAY")):
    print("  skip the window part: no display")
else:
    print("a window, detached from the calling terminal")
    pid = None
    try:
        code, took, out, _ = run_in_pty([], env=DEBUG)
        check("`aiterm` exits with code 0", code == 0, f"code {code}")
        check("…at once", took < 4, f"{took:.1f} s")
        check("…and prints nothing, then or later", out == "", repr(out[:300]))
        pid = wait_for(app_pid)
        check("the app runs on in its own process", pid is not None)
        if pid:
            check("…in its own session, away from that terminal", os.getsid(pid) == pid)
            shells = wait_for(lambda: children(pid))
            check("…and its shells don't get the detach marker",
                  shells and all(b"AITERM_DETACHED=" not in environ(child) for child in shells))
        check("its output goes to the log", wait_for(lambda: os.path.exists(LOG) and os.path.getsize(LOG) > 0))

        sub = os.path.join(HOME, "sub")
        os.mkdir(sub)
        code, took, out, _ = run_in_pty(["sub"])
        check("a second `aiterm DIR` goes to the running app: code 0", code == 0, f"code {code}")
        check("…quietly", out == "", repr(out[:300]))
        check("…and no second app", app_pid() == pid)
        code, took, out, _ = run_in_pty(["/no/such/dir"])
        check("`aiterm /no/such/dir` while it runs still says why (code 1)",
              code == 1 and "no such folder" in out, f"code {code}: {out!r}")
    finally:
        stop(pid or app_pid())

    print("--foreground")
    proc = None
    try:
        code, took, out, proc = run_in_pty(["--foreground"], wait=4, linger=0, env=DEBUG)
        check("`aiterm --foreground` stays in the terminal", code is None, f"code {code}")
        check("…and prints its output there", out != "", "nothing printed")
    finally:
        if proc and proc.poll() is None:
            proc.terminate()
            proc.wait(5)
        stop(app_pid())

shutil.rmtree(HOME, ignore_errors=True)
sys.exit(0 if all(results) else 1)
