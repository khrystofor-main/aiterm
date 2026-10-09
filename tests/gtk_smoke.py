#!/usr/bin/env python3
"""Smoke test for the GTK app: opens a real window with a real shell.

Needs a graphical session (Wayland or X11) and gir1.2-vte-3.91; exits with 77
(skipped) without them. Runs under its own non-unique application ID, so it
never talks to a running Aiterm. Run: tests/gtk_smoke.py
"""

import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

SKIP = 77
if not (os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY")):
    print("  skip GTK smoke test: no display")
    sys.exit(SKIP)
try:
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    gi.require_version("Vte", "3.91")
    from gi.repository import Adw, Gio, GLib, Vte
except (ImportError, ValueError) as e:
    print(f"  skip GTK smoke test: {e}")
    sys.exit(SKIP)

from aiterm.window import Window  # noqa: E402

os.environ["SHELL"] = "/bin/bash"
results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(f"  {'ok  ' if ok else 'FAIL'} {name}" + ("" if ok or not detail else f"\n       {detail}"))


def wait_for(predicate, timeout=10):
    """Spins the main loop until predicate() is true."""
    ctx = GLib.MainContext.default()
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        ctx.iteration(False)
        time.sleep(0.01)
    return predicate()


def screen_text(term):
    if hasattr(term, "get_text_format"):
        return term.get_text_format(Vte.Format.TEXT) or ""
    return term.get_text(None, None)[0] or ""


def background(term):
    c = term.get_color_background_for_draw()
    return round(c.red * 255), round(c.green * 255), round(c.blue * 255)


def run(app):
    win = Window(application=app)
    win.present()
    term = win.current_terminal()
    style = Adw.StyleManager.get_default()

    check("window has one tab", win.tabs.get_n_pages() == 1)

    # The shell starts asynchronously; input sent before the prompt is lost
    check("shell shows a prompt", wait_for(lambda: "$ " in screen_text(term)))
    term.feed_child(b"echo aiterm-$((6*7))\n")
    ok = wait_for(lambda: "aiterm-42" in screen_text(term))
    check("bash runs commands", ok, screen_text(term)[-300:])

    # sleep keeps the next prompt from putting bash's own title back
    term.feed_child(b"printf '\\033]0;smoke-title\\007'; sleep 3\n")
    ok = wait_for(lambda: win.header_title.get_title() == "smoke-title")
    check("header shows the terminal title", ok, f"title: {win.header_title.get_title()!r}")

    style.set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)
    wait_for(lambda: not style.get_dark(), 2)
    light = background(term)
    style.set_color_scheme(Adw.ColorScheme.FORCE_DARK)
    wait_for(lambda: style.get_dark(), 2)
    dark = background(term)
    check("terminal follows the light/dark theme", light == (255, 255, 255) and dark != light,
          f"light {light}, dark {dark}")

    closed = []
    win.connect("close-request", lambda *_: closed.append(True) and False)
    term.feed_child(b"exit\n")
    check("window closes when the shell exits", wait_for(lambda: closed))
    app.quit()


app = Adw.Application(application_id="io.github.khrystofor_main.Aiterm.Test",
                      flags=Gio.ApplicationFlags.NON_UNIQUE)
app.connect("activate", run)
app.run([])
sys.exit(0 if results and all(results) else 1)
