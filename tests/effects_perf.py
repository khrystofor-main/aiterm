#!/usr/bin/env python3
"""How much the terminal effects slow down fast output: the time `seq 1 200000`
takes in a real Aiterm window, animations on and off, a few runs each.

Timings depend on the machine, so this is a measurement, not part of
tests/run.sh. Run it on a display that really draws frames, without windows
on the desktop:

    tests/headless.sh tests/effects_perf.py
"""

import os
import statistics
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
os.environ["AITERM_CONFIG_DIR"] = tempfile.mkdtemp(prefix="aiterm-perf-")
os.environ["SHELL"] = "/bin/bash"
os.environ["AITERM_AGENT"] = "/bin/bash --norc --noprofile"

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Vte", "3.91")
from gi.repository import Gio, GLib  # noqa: E402

from aiterm.application import Application  # noqa: E402
from aiterm.settings import Settings  # noqa: E402

COMMAND = "seq 1 200000"
RUNS = 5
results = {}


def wait_for(predicate, seconds):
    context = GLib.MainContext.default()
    end = time.monotonic() + seconds
    while time.monotonic() < end and not predicate():
        context.iteration(False)
    return predicate()


def measure(app):
    window = app.get_active_window()
    terminal = window.current_terminal()
    terminal.effects.window_active = lambda: True
    log = terminal.command_log
    wait_for(lambda: log.input_row is not None, 20)
    for animations in ["on", "off"] * RUNS:
        Settings.get().animations = animations
        count = len(log.commands)
        start = time.monotonic()
        terminal.feed_child(f"{COMMAND}\n".encode())
        wait_for(lambda: len(log.commands) > count, 60)
        results.setdefault(animations, []).append(time.monotonic() - start)
        terminal.feed_child(b"clear\n")
        wait_for(lambda: len(log.commands) > count + 1, 10)
    app.quit()


app = Application(application_id="io.github.khrystofor_main.Aiterm.Perf", flags=Gio.ApplicationFlags.NON_UNIQUE)
app.connect("activate", lambda app: GLib.idle_add(lambda: measure(app) and False))
app.run([])
on, off = statistics.median(results["on"]), statistics.median(results["off"])
print(f"{COMMAND}: {on:.2f} s with effects, {off:.2f} s without (median of {RUNS}); "
      f"{(on / off - 1) * 100:+.0f}%")
