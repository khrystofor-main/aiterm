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


def spin(seconds):
    wait_for(lambda: False, seconds)


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
    edit = term.get_context_menu_model().get_item_link(0, "section")
    check("right-click menu has Copy, Paste, Select All", edit and edit.get_n_items() == 3)

    term.feed_child(b"cd /tmp\n")
    check("terminal knows the shell's folder", wait_for(lambda: term.current_directory() == "/tmp"),
          f"folder: {term.current_directory()!r}")
    win_keys = capture_shortcuts(win)
    check("Ctrl+Shift+T/W/N are caught before VTE",
          {Gtk.ShortcutTrigger.parse_string(f"<Control><Shift>{k}").to_string() for k in "twn"} <= set(win_keys),
          str(list(win_keys)))
    shortcuts = win.tabs.get_shortcuts()
    check("tab switching keys on, Ctrl+Home/End left to programs",
          shortcuts & Adw.TabViewShortcuts.CONTROL_PAGE_DOWN and shortcuts & Adw.TabViewShortcuts.ALT_DIGITS
          and not shortcuts & Adw.TabViewShortcuts.CONTROL_HOME)

    check("Ctrl+Shift+F is caught before VTE",
          Gtk.ShortcutTrigger.parse_string("<Control><Shift>f").to_string() in win_keys)
    check("Ctrl+plus/minus/0 are caught before VTE",
          {"<Control>plus|<Control>equal|<Control>KP_Add", "<Control>minus|<Control>KP_Subtract",
           "<Control>0|<Control>KP_0"} <= set(win_keys))
    # Row 0: a plain URL, row 1: an OSC 8 hyperlink labelled "docs"
    term.feed_child(b"clear; echo 'see https://example.com/a?b=1.'; "
                    b"printf '\\033]8;;https://osc8.example/\\033\\\\docs\\033]8;;\\033\\\\\\n'\n")
    wait_for(lambda: "docs" in screen_text(term) and "see https" in screen_text(term))
    spin(0.3)  # let VTE lay out the rows before asking about cells
    cell = lambda col, row: (8 + (col + 0.5) * term.get_char_width(), 4 + (row + 0.5) * term.get_char_height())
    check("a plain URL is a link, without the trailing dot",
          term.link_at(*cell(10, 0)) == "https://example.com/a?b=1", repr(term.link_at(*cell(10, 0))))
    check("an OSC 8 hyperlink is a link", term.link_at(*cell(1, 1)) == "https://osc8.example/",
          repr(term.link_at(*cell(1, 1))))
    check("plain text is not a link", term.link_at(*cell(1, 0)) is None)
    menu = term.context_menu_at(*cell(10, 0))
    check("right-click on a link offers Open / Copy Link",
          menu.get_n_items() == 2 and menu.get_item_link(0, "section").get_n_items() == 2)
    term.actions.activate_action("copy-link", None)
    check("Copy Link copies the URL", clipboard_text(term) == "https://example.com/a?b=1")
    check("right-click elsewhere has no link items", term.context_menu_at(*cell(1, 0)).get_n_items() == 1)

    term.feed_child(b"printf 'needle-%s\\n' 1 2 3\n")
    wait_for(lambda: "needle-3" in screen_text(term))
    win.activate_action("win.find")
    search = win.search
    check("Ctrl+Shift+F opens the search bar", search.get_search_mode())
    selected = lambda: term.get_text_selected(Vte.Format.TEXT) or ""
    search.entry.set_text("NEEDLE-3")
    search.apply()  # search-changed is delayed while typing; apply now
    check("search finds the newest match, ignoring case", selected() == "needle-3", repr(selected()))
    search.regex.set_active(True)
    search.entry.set_text("needle-[0-9]")
    search.apply()
    check("regex search finds the newest match", selected() == "needle-3", repr(selected()))
    search.find(older=True)
    check("Enter goes to an older match", selected() == "needle-2", repr(selected()))
    search.find(older=False)
    check("Shift+Enter goes to a newer match", selected() == "needle-3", repr(selected()))
    search.entry.set_text("needle-(")
    search.apply()
    check("a broken regex marks the entry", search.entry.has_css_class("error"))
    search.regex.set_active(False)
    search.set_search_mode(False)
    check("closing the search clears it", term.search_get_regex() is None)

    win.activate_action("win.zoom-in")
    win.activate_action("win.zoom-in")
    check("zoom in scales the font", term.get_font_scale() == 1.21, f"{term.get_font_scale()}")

    win.activate_action("win.new-tab")
    second = win.current_terminal()
    check("new tab gets the window's zoom", second.get_font_scale() == 1.21)
    check("new tab opens and is selected", win.tabs.get_n_pages() == 2 and second is not term)
    wait_for(lambda: "$ " in screen_text(second))
    second.feed_child(b"pwd\n")
    check("new tab starts in the current tab's folder",
          wait_for(lambda: "\n/tmp\n" in screen_text(second)), screen_text(second)[-200:])
    win.activate_action("win.zoom-out")
    check("zoom applies to every tab", term.get_font_scale() == second.get_font_scale() == 1.1)
    win.activate_action("win.zoom-reset")
    check("Ctrl+0 resets the zoom", term.get_font_scale() == 1.0)
    win.activate_action("win.close-tab")
    check("close tab goes back to the first one",
          win.tabs.get_n_pages() == 1 and win.current_terminal() is term)

    app.activate_action("new-window", None)
    other = app.get_active_window()
    check("new window opens", len(app.get_windows()) == 2 and other is not win)
    other_term = other.current_terminal()
    wait_for(lambda: "$ " in screen_text(other_term))
    check("new window starts in the current tab's folder",
          wait_for(lambda: other_term.current_directory() == "/tmp"), f"{other_term.current_directory()!r}")
    other.close()
    wait_for(lambda: len(app.get_windows()) == 1)
    win.present()
    wait_for(lambda: app.get_active_window() is win, 2)

    term.feed_child(b"sleep 60\n")
    check("knows a program is running", wait_for(lambda: term.running_program() == "sleep"),
          f"{term.running_program()!r}")
    win.activate_action("win.close-tab")
    dialog = win.get_visible_dialog()
    check("closing a busy tab asks first", dialog is not None and win.tabs.get_n_pages() == 1)
    if dialog:
        dialog.emit("response", "cancel")
        dialog.force_close()
    check("Cancel keeps the tab", win.tabs.get_n_pages() == 1)
    win.close()
    dialog = win.get_visible_dialog()
    check("closing a window with a busy tab asks first", dialog is not None and win in app.get_windows())
    if dialog:
        dialog.emit("response", "cancel")
        dialog.force_close()
    win.activate_action("win.new-tab")
    busy = win.current_terminal()
    wait_for(lambda: "$ " in screen_text(busy))
    busy.feed_child(b"sleep 60\n")
    wait_for(lambda: busy.running_program() == "sleep")
    win.activate_action("win.close-tab")
    dialog = win.get_visible_dialog()
    if dialog:
        dialog.emit("response", "close")
        dialog.force_close()
    check("Close Tab in the dialog closes the busy tab",
          wait_for(lambda: win.tabs.get_n_pages() == 1 and win.current_terminal() is term, 3))
    term.feed_child(b"\x03")  # Ctrl+C
    check("an idle shell is not a running program", wait_for(lambda: term.running_program() is None))

    for action in ("shortcuts", "about"):
        app.activate_action(action, None)
        dialog = win.get_visible_dialog()
        check(f"main menu opens {action}", dialog is not None)
        if dialog:
            dialog.force_close()

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
