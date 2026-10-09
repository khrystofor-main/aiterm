#!/usr/bin/env python3
"""Smoke test for the GTK app: opens a real window with a real shell.

Needs a graphical session (Wayland or X11) and gir1.2-vte-3.91; exits with 77
(skipped) without them. Runs under its own non-unique application ID, so it
never talks to a running Aiterm. Note: the clipboard checks overwrite the
desktop clipboard. Run: tests/gtk_smoke.py
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
    from gi.repository import Adw, Gio, GLib, Gtk, Vte
except (ImportError, ValueError) as e:
    print(f"  skip GTK smoke test: {e}")
    sys.exit(SKIP)

from aiterm.application import Application  # noqa: E402

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


def capture_shortcuts(widget):
    """{trigger: Gtk.Shortcut} from the widget's capture-phase controllers."""
    found = {}
    controllers = widget.observe_controllers()
    for i in range(controllers.get_n_items()):
        c = controllers.get_item(i)
        if isinstance(c, Gtk.ShortcutController) and c.get_propagation_phase() == Gtk.PropagationPhase.CAPTURE:
            for j in range(c.get_n_items()):
                shortcut = c.get_item(j)
                found[shortcut.get_trigger().to_string()] = shortcut
    return found


def clipboard_text(term):
    """Reads the clipboard the way another app would (it is async in GTK 4)."""
    box = []
    clipboard = term.get_clipboard()
    clipboard.read_text_async(None, lambda c, res: box.append(c.read_text_finish(res)))
    wait_for(lambda: box, 3)
    return box[0] if box else None


def run(app):
    try:
        steps(app)
    except Exception:
        results.append(False)
        raise
    finally:
        app.quit()


def steps(app):
    win = app.get_active_window()
    term = win.current_terminal()
    style = Adw.StyleManager.get_default()

    check("window has one tab", win.tabs.get_n_pages() == 1)

    # The shell starts asynchronously; input sent before the prompt is lost
    check("shell shows a prompt", wait_for(lambda: "$ " in screen_text(term)))
    term.feed_child(b"echo aiterm-$((6*7))\n")
    ok = wait_for(lambda: "aiterm-42" in screen_text(term))
    check("bash runs commands", ok, screen_text(term)[-300:])

    # sleep keeps the next prompt from putting bash's own title back
    term.feed_child(b"printf '\\033]0;smoke-title\\007'; sleep 1\n")
    ok = wait_for(lambda: win.header_title.get_title() == "smoke-title")
    check("header shows the terminal title", ok, f"title: {win.header_title.get_title()!r}")
    # Back at the prompt once bash puts its own title back
    wait_for(lambda: win.header_title.get_title() != "smoke-title")

    style.set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)
    wait_for(lambda: not style.get_dark(), 2)
    light = background(term)
    style.set_color_scheme(Adw.ColorScheme.FORCE_DARK)
    wait_for(lambda: style.get_dark(), 2)
    dark = background(term)
    check("terminal follows the light/dark theme", light == (255, 255, 255) and dark != light,
          f"light {light}, dark {dark}")

    check("copy is disabled without a selection",
          not term.actions.get_action_enabled("copy"))
    keys = capture_shortcuts(term)
    ctrl_shift = {k: Gtk.ShortcutTrigger.parse_string(f"<Control><Shift>{k}").to_string() for k in "cva"}
    check("Ctrl+Shift+C/V/A are caught before VTE", set(ctrl_shift.values()) <= set(keys), str(list(keys)))
    copy_key = keys.get(ctrl_shift["c"])
    handled = copy_key and copy_key.get_action().activate(Gtk.ShortcutActionFlags(0), term, None)
    check("Ctrl+Shift+C without a selection is swallowed, not sent as Ctrl+C", handled)
    term.activate_action("term.select-all")
    term.activate_action("term.copy")
    copied = clipboard_text(term)
    check("select all + copy puts the screen on the clipboard", copied and "aiterm-42" in copied,
          f"clipboard: {copied!r}"[:200])

    term.get_clipboard().set("echo pasted-$((40+2))")
    term.activate_action("term.paste")
    # Paste reads the clipboard asynchronously; press Enter once it is typed
    wait_for(lambda: "echo pasted-" in screen_text(term))
    term.feed_child(b"\n")
    check("paste types the clipboard into the shell",
          wait_for(lambda: "pasted-42" in screen_text(term)))
    check("right-click menu has Copy, Paste, Select All",
          term.get_context_menu_model().get_n_items() == 3)

    term.feed_child(b"cd /tmp\n")
    check("terminal knows the shell's folder", wait_for(lambda: term.current_directory() == "/tmp"),
          f"folder: {term.current_directory()!r}")
    win_keys = capture_shortcuts(win)
    check("Ctrl+Shift+T/W are caught before VTE",
          {Gtk.ShortcutTrigger.parse_string(f"<Control><Shift>{k}").to_string() for k in "tw"} <= set(win_keys),
          str(list(win_keys)))
    shortcuts = win.tabs.get_shortcuts()
    check("tab switching keys on, Ctrl+Home/End left to programs",
          shortcuts & Adw.TabViewShortcuts.CONTROL_PAGE_DOWN and shortcuts & Adw.TabViewShortcuts.ALT_DIGITS
          and not shortcuts & Adw.TabViewShortcuts.CONTROL_HOME)

    win.activate_action("win.new-tab")
    second = win.current_terminal()
    check("new tab opens and is selected", win.tabs.get_n_pages() == 2 and second is not term)
    wait_for(lambda: "$ " in screen_text(second))
    second.feed_child(b"pwd\n")
    check("new tab starts in the current tab's folder",
          wait_for(lambda: "\n/tmp\n" in screen_text(second)), screen_text(second)[-200:])
    win.activate_action("win.close-tab")
    check("close tab goes back to the first one",
          win.tabs.get_n_pages() == 1 and win.current_terminal() is term)

    closed = []
    win.connect("close-request", lambda *_: closed.append(True) and False)
    term.feed_child(b"exit\n")
    check("window closes when the shell exits", wait_for(lambda: closed))


app = Application(application_id="io.github.khrystofor_main.Aiterm.Test",
                  flags=Gio.ApplicationFlags.NON_UNIQUE)
# The handler runs before Application.do_activate opens the window
app.connect("activate", lambda app: GLib.idle_add(run, app))
app.run([])
sys.exit(0 if results and all(results) else 1)
