#!/usr/bin/env python3
"""Smoke test for the GTK app: opens a real window with a real shell.

Needs a graphical session (Wayland or X11) and gir1.2-vte-3.91; exits with 77
(skipped) without them. Runs under its own non-unique application ID, so it
never talks to a running Aiterm. Note: the clipboard checks overwrite the
desktop clipboard. Run: tests/gtk_smoke.py
"""

import json
import os
import shutil
import sys
import tempfile
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
    from gi.repository import Adw, Gio, GLib, Gtk, Pango, Vte
except (ImportError, ValueError) as e:
    print(f"  skip GTK smoke test: {e}")
    sys.exit(SKIP)

import aiterm.window as window_module  # noqa: E402
from aiterm import animations, dbus_api, effects  # noqa: E402
from aiterm.application import Application  # noqa: E402
from aiterm.animations import Animations  # noqa: E402
from aiterm.chat_view import CommandRow, EditRow, markdown_to_pango  # noqa: E402
from aiterm.palettes import PALETTES  # noqa: E402
from aiterm.preferences import PreferencesDialog  # noqa: E402
from aiterm.settings import Settings  # noqa: E402

os.environ["SHELL"] = "/bin/bash"
# Preferences go to a throwaway folder, never the user's ~/.config/aiterm
CONFIG_DIR = tempfile.mkdtemp(prefix="aiterm-test-")
os.environ["AITERM_CONFIG_DIR"] = CONFIG_DIR
# The agent panel runs a plain bash instead of agy: no login, no tokens
os.environ["AITERM_AGENT"] = "/bin/bash --norc --noprofile"
# …and the chat view a fake agy that speaks the same NDJSON (tests/fake_agy.py)
os.environ["AITERM_CHAT_AGENT"] = f"{sys.executable} {os.path.join(ROOT, 'tests', 'fake_agy.py')}"
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
    """Everything from the top of the scrollback down to the cursor. (Not
    get_text_format: it reads the visible area, which is empty while a new
    tab in a background window has no size yet.)"""
    first = int(term.get_vadjustment().get_lower())
    _, last = term.get_cursor_position()
    return term.get_text_range_format(Vte.Format.TEXT, first, 0, last, 10_000)[0] or ""


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


def _finish_call(connection, result):
    try:
        return connection.call_finish(result).unpack()
    except GLib.Error as error:
        return error


def widgets(box):
    child, out = box.get_first_child(), []
    while child:
        out.append(child)
        child = child.get_next_sibling()
    return out


def menu_labels(menu):
    """All item labels of a Gio.MenuModel, sections flattened."""
    labels = []
    for i in range(menu.get_n_items()):
        section = menu.get_item_link(i, "section")
        if section:
            labels += menu_labels(section)
        else:
            labels.append(menu.get_item_attribute_value(i, "label", None).get_string())
    return labels


def clipboard_text(term):
    """Reads the clipboard the way another app would (it is async in GTK 4)."""
    box = []
    clipboard = term.get_clipboard()
    clipboard.read_text_async(None, lambda c, res: box.append(c.read_text_finish(res)))
    wait_for(lambda: box, 3)
    return box[0] if box else None


def animations_page(page):
    """Preferences → Animations, on a throwaway presets folder."""
    engine, settings = Animations.get(), Settings.get()
    settings.animation_preset = "subtle"
    page.switch_row.set_active(False)
    check("the Animations switch turns them off explicitly", settings.animations == "off" and not engine.enabled)
    page.switch_row.set_active(True)
    model = page.preset_row.get_model()
    names = [model.get_string(i) for i in range(model.get_n_items())]
    check("the built-in presets are offered", names[:2] == ["Subtle", "Expressive"], str(names))
    row = page.effect_rows["shake"]
    check("a built-in preset's rows are read-only",
          not row.switch.get_sensitive() and not page.delete_button.get_sensitive())
    preview = page.preview.effects
    for effect in ("shake", "fail_mark", "theme"):
        page.show_effect(effect)
    check("Show plays the effects in the preview",
          len(preview.shakes) == 1 and len(preview.marks) == 1 and preview.blend is not None)
    chat = page.chat_preview
    page.get_ancestor(Adw.PreferencesDialog).set_visible_page(page)
    wait_for(lambda: chat.get_mapped() or page.previews.set_visible_child_name("chat"), 2)
    page.show_effect("stream")
    check("Show for a chat effect plays a short exchange in a preview chat",
          page.previews.get_visible_child_name() == "chat" and chat.process.busy)
    user = chat.messages.get_first_child()
    check("…a new message fades in, rising from below", user.get_opacity() < 1 and user.get_margin_top() > 0,
          f"{user.get_opacity()} {user.get_margin_top()}")
    animations.finish_all()
    check("…and settles", user.get_opacity() == 1 and user.get_margin_top() == 0)
    check("…three dots pulse while the agent works",
          chat.thinking.get_visible() and chat.dots.has_css_class("typing-animated"))
    blocks = lambda: [w for w in widgets(chat.messages) if isinstance(w, CommandRow)]
    check("…a command block has a running bar while it runs",
          wait_for(lambda: blocks(), 3) and blocks()[0].running_bar.get_visible()
          and not blocks()[0].spinner.get_visible())
    check("…and lights up as the agent runs it in the terminal",
          wait_for(lambda: blocks()[0].has_css_class("agent-link"), 3))
    check("…the bar goes when it is done", wait_for(lambda: not blocks()[0].running, 3)
          and not blocks()[0].running_bar.get_visible())
    answer = lambda: [w for w in widgets(chat.messages) if w.has_css_class("chat-agent")]
    check("…the answer fades in piece by piece as it streams",
          wait_for(lambda: answer() and answer()[0].get_attributes() is not None, 3))
    check("…and the dots go at the end", wait_for(lambda: not chat.thinking.get_visible(), 5))
    animations.finish_all()
    for i in range(12):
        chat.add_note(f"note {i}")
    animations.finish_all()
    scroll = chat.scroller.get_vadjustment()
    wait_for(lambda: scroll.get_upper() > scroll.get_page_size() * 1.5, 3)
    animations.finish_all()  # the glide to the end
    scroll.set_value(0)  # the user scrolls up
    chat.add_note("one more")
    check("scrolled up, new messages do not move the chat, a button offers them",
          wait_for(lambda: chat.new_button.get_visible(), 2) and scroll.get_value() == 0, str(scroll.get_value()))
    chat.new_button.emit("clicked")
    animations.finish_all()
    check("…and takes you down to them", not chat.new_button.get_visible()
          and scroll.get_value() >= scroll.get_upper() - scroll.get_page_size() - 1,
          f"{scroll.get_value()} {scroll.get_upper()} {scroll.get_page_size()}")
    window = page.get_root()
    page.show_effect("approval")
    check("…and the window's own parts behind the dialog", window.approval.get_reveal_child()
          and not window.approval.pending)
    window.approval.answer(False)
    page.duplicate_button.emit("clicked")
    preset = engine.preset
    check("Duplicate makes an editable copy and picks it",
          not preset.builtin and wait_for(lambda: row.switch.get_sensitive(), 2) and page.delete_button.get_sensitive(),
          preset.key)
    row.scale.set_value(1.5)
    row.switch.set_active(False)
    with open(preset.path) as f:
        saved = json.load(f)
    check("rows change the copy's file", saved.get("effects", {}).get("shake") == {"intensity": 1.5, "enabled": False},
          str(saved))
    check("…and what the effects read", engine.effect("shake") is None)
    with open(preset.path, "w") as f:
        f.write('{"base": "subtle", "effects": {"shake": {"amplitude": "huge"}}}')
    check("a hand-edited file applies at once, problems shown on the page",
          wait_for(lambda: page.warning_row.get_visible(), 3) and "amplitude" in page.warning_row.get_subtitle(),
          page.warning_row.get_subtitle())
    engine.delete(preset.key)
    check("Delete goes back to the built-in preset", wait_for(lambda: page.preset_row.get_selected() == 0, 2)
          and settings.animation_preset == "subtle")


def run(app):
    try:
        steps(app)
    except Exception:
        results.append(False)
        raise
    finally:
        app.quit()


def steps(app):
    # No animations (spinners, revealers): on a display without vsync, such
    # as CI's Xvfb, a never-ending animation redraws back to back and starves
    # the main loop's lower-priority work, like writing to the shell's pty
    Gtk.Settings.get_default().set_property("gtk-enable-animations", False)
    win = app.get_active_window()
    term = win.current_terminal()
    style = Adw.StyleManager.get_default()

    check("window has one tab", win.tabs.get_n_pages() == 1)
    # The approval bar has its own checks below; everything else runs straight away
    Settings.get().approve_agent_commands = False

    # The shell starts asynchronously; input sent before the prompt is lost
    check("shell shows a prompt", wait_for(lambda: "$ " in screen_text(term)))
    term.feed_child(b"echo aiterm-$((6*7))\n")
    ok = wait_for(lambda: "aiterm-42" in screen_text(term))
    check("bash runs commands", ok, screen_text(term)[-300:])

    log = term.command_log
    last = lambda: log.commands[-1] if log.commands else None
    term.feed_child(b"echo one; echo two\n")
    check("the command log records a command and its output",
          wait_for(lambda: last() and last().text == "echo one; echo two") and last().output == "one\ntwo",
          repr(last()))
    term.feed_child(b"false\n")
    check("…and its exit code", wait_for(lambda: last().text == "false") and last().exit_code == 1, repr(last()))
    # Ubuntu's ~/.bashrc keeps commands with a leading space out of the
    # history; then the command comes from the screen
    term.feed_child(b" echo hidden\n")
    check("…also for commands kept out of the history",
          wait_for(lambda: last().text == "echo hidden") and last().output == "hidden", repr(last()))
    term.feed_child(b"printf 'no newline'\n")
    check("output without a final newline still ends before the prompt",
          wait_for(lambda: last().text == "printf 'no newline'") and last().output == "no newline", repr(last()))
    check("the prompt after it starts on its own line",
          wait_for(lambda: "no newline\n" in screen_text(term)), screen_text(term)[-200:])

    # `clear` drops the scrollback: VTE's scroll rows then part ways with
    # the rows the command log counts, and the effects need the latter
    before = log.input_row
    term.feed_child(b"clear\n")
    ok = wait_for(lambda: log.input_row != before and term.top_row() == log.input_row, 5)
    check("after clear, the view's top row is the new prompt's", ok,
          f"top {term.top_row()} prompt {log.input_row} offset {term.row_offset()}")

    original_window_active = animations.window_active
    # Effects (effects.py), on a clock moved by hand. Animations are on
    # explicitly: GTK's own are off for this test (see the top)
    Settings.get().animations = "on"
    shake_ms = Animations.get().effect("shake")["duration"]
    stripe_ms = Animations.get().effect("stripe")["duration"]
    fx = term.effects
    now = [0.0]
    fx.clock = lambda: now[0]
    fx.window_active = lambda: True  # whatever the test display says
    fx.clear()
    term.feed_child(b"ls /no-such-folder\n")
    ok = wait_for(lambda: last().text == "ls /no-such-folder" and fx.shakes)
    rows = lambda running: [(r.first, r.last) for r in running]
    check("a failed command's line shakes", ok and fx.shakes[0].first == last().input_row,
          f"{rows(fx.shakes)} {last()}")
    check("…its output gets a stripe",
          any(r.first <= last().input_row + 1 <= r.last for r in fx.stripes), f"{rows(fx.stripes)} {last()}")
    check("…not the command's own line", all(r.first > last().input_row for r in fx.stripes),
          str(rows(fx.stripes)))
    check("…which turns red as the command failed", all(r.color == "error" for r in fx.stripes),
          str([r.color for r in fx.stripes]))
    check("…and the line gets ✗ with the exit code, sliding in",
          [(m.first, m.code) for m in fx.marks] == [(last().input_row, 2)] and fx.marks[0].started == now[0],
          str([(m.first, m.code, m.started) for m in fx.marks]))
    check("the first failed command ever shows where animations are set up",
          Settings.get().animations_hint_shown)
    now[0] += shake_ms
    fx.prune()
    check("the shake is over after its time", not fx.shakes and fx.stripes, f"{rows(fx.shakes)} {rows(fx.stripes)}")
    now[0] += stripe_ms
    check("…and the stripe has faded", wait_for(lambda: not fx.active(), 2), str(rows(fx.stripes)))
    for command in (b"grep -q nothing /dev/null\n", b"sh -c 'kill -INT $$'\n"):
        term.feed_child(command)
        wait_for(lambda: last().text == command.decode().strip())
    check("no shake for grep's 'no match' or Ctrl+C", not fx.shakes, f"{rows(fx.shakes)} {last()}")
    fx.clear()
    term.feed_child(b"for i in 1 2 3; do echo line $i; sleep 0.3; done\n")
    ok = wait_for(lambda: len(fx.stripes) >= 2 and log.running, 3)
    check("output of a running command lights up as it comes", ok, str(rows(fx.stripes)))
    check("…in the accent color while it runs", all(r.color is None for r in fx.stripes),
          str([(r.first, r.last, r.color) for r in fx.stripes]))
    wait_for(lambda: not log.running)
    check("…then green, as it succeeded", fx.stripes and all(r.color == "success" for r in fx.stripes)
          and not fx.marks, str([r.color for r in fx.stripes]))
    fx.clear()
    fx.agent_command()
    term.feed_child(b"echo linked\n")
    wait_for(lambda: last().text == "echo linked")
    check("the agent's command lights its output in the link color, kept to the end",
          fx.stripes and all(r.color == "link" for r in fx.stripes) and not fx._linked,
          str([r.color for r in fx.stripes]))
    fx.clear()

    term.feed_child(b"seq 1 200000\n")
    wait_for(lambda: last().text == "seq 1 200000", 20)
    check("output flooding in gets no stripe", all(r.last - r.first < 50 for r in fx.stripes)
          and not any(r.last >= last().end_row - 5 for r in fx.stripes), str(rows(fx.stripes)))
    # The alternate screen numbers its rows on its own: in a new tab they come
    # after the command's, as if they were its output
    fresh = win.add_tab()
    wait_for(lambda: fresh.command_log.input_row is not None, 10)
    fresh.effects.window_active = lambda: True
    fresh.feed_child(b"printf '\\e[?1049h'; for i in 1 2 3 4 5 6; do echo full $i; sleep 0.3; done; "
                     b"printf '\\e[?1049l'\n")
    wait_for(lambda: fresh.command_log.running, 3)
    spin(0.6)
    check("full-screen programs (the alternate screen) get no stripes",
          fresh.command_log.running and not fresh.effects.stripes, str(rows(fresh.effects.stripes)))
    wait_for(lambda: not fresh.command_log.running)
    win.tabs.close_page(win.tabs.get_page(fresh.get_parent()))
    wait_for(lambda: win.tabs.get_n_pages() == 1)
    fx.clear()
    remote = os.path.join(CONFIG_DIR, "ssh")
    with open(remote, "w") as f:
        f.write("#!/bin/bash\nfor i in 1 2 3 4 5 6; do echo remote $i; sleep 0.3; done\n")
    os.chmod(remote, 0o755)
    term.feed_child(f"{remote}\n".encode())
    wait_for(lambda: log.running, 3)
    spin(0.5)
    check("nor does ssh", log.running and term.running_program() == "ssh" and not fx.stripes,
          f"{term.running_program()} {rows(fx.stripes)}")
    wait_for(lambda: not log.running)

    fx.clear()
    fx.window_active = lambda: False
    term.feed_child(b"false\n")
    wait_for(lambda: last().text == "false" and fx.marks)
    check("in a background window nothing moves, the ✗ is just there",
          not fx.shakes and not fx.stripes and fx.marks and fx.marks[0].started is None,
          f"{rows(fx.shakes)} {[(m.first, m.started) for m in fx.marks]}")
    fx.window_active = lambda: True

    old_background, old_palette = background(term), Settings.get().palette
    Settings.get().palette = "Solarized"
    new = term._colors[1]  # Solarized's background in the current light/dark style
    solarized = (round(new.red * 255), round(new.green * 255), round(new.blue * 255))
    check("a palette change is animated", fx.blend is not None and background(term) == old_background)
    now[0] += Animations.get().effect("theme")["duration"] / 2
    fx.prune()
    check("…passing through the colors in between", background(term) not in (old_background, solarized),
          str(background(term)))
    now[0] += Animations.get().effect("theme")["duration"]
    fx.prune()
    check("…and ends on the new palette", fx.blend is None and background(term) == solarized,
          str(background(term)))
    Settings.get().palette = old_palette

    # The window's parts, animated with Adw animations: this display may not
    # draw frames, so they are finished by hand
    animations.window_active = lambda widget: True
    panel_action = win.lookup_action("agent-panel")
    panel = win.agent_panel
    panel_action.change_state(GLib.Variant.new_boolean(True))
    check("the agent panel slides in from the right", panel.get_visible() and panel._offset == 1,
          f"{panel.get_visible()} {panel._offset}")
    animations.finish_all()
    check("…and settles in place", panel._offset == 0)
    panel_action.change_state(GLib.Variant.new_boolean(False))
    check("closing, it slides out before it hides (the terminal resizes once)", panel.get_visible())
    animations.finish_all()
    check("…then hides", not panel.get_visible() and panel._offset == 0)
    win.search.open()
    revealer = win.search.get_first_child()
    check("the find bar slides down, timed by the preset", isinstance(revealer, Gtk.Revealer)
          and revealer.get_transition_duration() == Animations.get().effect("search")["duration"])
    win.search.set_search_mode(False)
    answers = []
    win.approval.ask("echo pulse", answers.append)
    check("the approval bar slides in, timed by the preset, Run pulsing",
          win.approval.get_transition_duration() == Animations.get().effect("approval")["duration"]
          and win.approval.run_button.has_css_class("approval-pulse"))
    win.approval.answer(False)
    check("…and stops pulsing once answered", answers == [False]
          and not win.approval.run_button.has_css_class("approval-pulse"))
    animations.window_active = original_window_active
    now[0] += 10_000
    fx.prune()
    Settings.get().animations = "off"
    term.feed_child(b"false\n")
    wait_for(lambda: last().text == "false" and not log.running)
    spin(0.3)
    fx.prune()  # what the next frame does
    check("with animations off, nothing animates", not fx.active(), f"{rows(fx.shakes)} {rows(fx.stripes)}")
    fx.clock = effects._monotonic_ms
    fx.clear()

    # The agent's API, called over D-Bus like the tools do
    def call(method, args, signature, reply_type, seconds=15):
        box = []
        app.get_dbus_connection().call(
            app.get_dbus_connection().get_unique_name(), app.get_dbus_object_path(), dbus_api.INTERFACE, method,
            GLib.Variant(signature, args), GLib.VariantType(reply_type), Gio.DBusCallFlags.NONE,
            seconds * 1000, None, lambda conn, res: box.append(_finish_call(conn, res)))
        wait_for(lambda: box, seconds)
        return box[0] if box else "no reply"

    run = lambda command, timeout=10: call("RunCommand", (win.get_id(), command, timeout), "(usu)", "(ssssi)")
    result = run("echo dbus-$((2+3))")
    check("RunCommand types the command and returns its output",
          result == ("done", os.path.expanduser("~"), "echo dbus-$((2+3))", "dbus-5", 0), str(result))
    result = run("false")
    check("RunCommand returns the exit code", result[:1] == ("done",) and result[4] == 1, str(result))
    result = call("ReadCommands", (win.get_id(), 2), "(ui)", "(sa(ssid))")
    check("ReadCommands returns the last commands",
          [c[:3] for c in result[1]] == [("echo dbus-$((2+3))", "dbus-5", 0), ("false", "", 1)], str(result))
    result = call("ReadScreen", (win.get_id(),), "(u)", "(ss)")
    check("ReadScreen returns the scrollback", "dbus-5" in result[1], str(result)[:200])
    term.feed_child(b"half-typed")
    wait_for(lambda: log.typed_text() == "half-typed")
    result = run("echo no")
    check("RunCommand refuses while the user is typing", result[:3] == ("typing", result[1], "half-typed"),
          str(result))
    term.feed_child(b"\x15")  # Ctrl+U clears the line
    wait_for(lambda: not log.typed_text())
    term.feed_child(b"sleep 2\n")
    wait_for(lambda: term.running_program() == "sleep")
    result = run("echo no")
    check("RunCommand refuses while a program runs", result[:1] == ("busy",) and result[2] == "sleep",
          str(result))
    result = call("Wait", (win.get_id(), 10), "(uu)", "(ssssi)")
    check("Wait waits for the running command", result[0] == "done" and result[2] == "sleep 2", str(result))
    result = run("sleep 3; echo late", timeout=1)
    check("RunCommand gives up after its timeout", result[0] == "timeout" and result[2] == "sleep", str(result))
    result = call("Wait", (win.get_id(), 10), "(uu)", "(ssssi)")
    check("…and Wait gets the rest", result[0] == "done" and result[3] == "late", str(result))
    result = call("ReadScreen", (999_999,), "(u)", "(ss)")
    check("an unknown window is an error", isinstance(result, GLib.Error) and "NoWindow" in result.message,
          str(result))

    # Approval: with the preference on, RunCommand waits for Run / Don't Run
    Settings.get().approve_agent_commands = True

    def run_async(command):
        box = []
        app.get_dbus_connection().call(
            app.get_dbus_connection().get_unique_name(), app.get_dbus_object_path(), dbus_api.INTERFACE,
            "RunCommand", GLib.Variant("(usu)", (win.get_id(), command, 10)), GLib.VariantType("(ssssi)"),
            Gio.DBusCallFlags.NONE, 15_000, None, lambda conn, res: box.append(_finish_call(conn, res)))
        return box

    count = len(log.commands)
    pending = run_async("echo approved")
    check("an agent command waits for approval", wait_for(lambda: win.approval.pending)
          and win.approval.get_reveal_child() and win.approval.command.get_label() == "echo approved")
    spin(0.3)
    check("…and nothing is typed meanwhile", not pending and len(log.commands) == count)
    result = run("echo second")
    check("a second command is refused while one waits", result[0] == "busy" and "approval" in result[2],
          str(result))
    win.approval.run_button.emit("clicked")
    check("Run types it and returns the output",
          wait_for(lambda: pending) and pending[0][:1] == ("done",) and pending[0][3] == "approved", str(pending))
    check("…and hides the bar", not win.approval.pending and not win.approval.get_reveal_child())
    pending = run_async("echo declined")
    wait_for(lambda: win.approval.pending)
    win.approval.skip_button.emit("clicked")
    check("Don't Run returns denied and types nothing",
          wait_for(lambda: pending) and pending[0][0] == "denied" and last().text != "echo declined",
          str(pending))
    pending = run_async("echo late")
    wait_for(lambda: win.approval.pending)
    term.feed_child(b"half")
    wait_for(lambda: log.typed_text() == "half")
    win.approval.run_button.emit("clicked")
    check("Run after the user started typing is refused",
          wait_for(lambda: pending) and pending[0][0] == "typing", str(pending))
    term.feed_child(b"\x15")
    wait_for(lambda: not log.typed_text())
    Settings.get().approve_agent_commands = False

    # aiterm-left / aiterm-run as the agent runs them (separate processes)
    def run_tool(*argv, seconds=20):
        launcher = Gio.SubprocessLauncher.new(Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_MERGE)
        launcher.setenv("AITERM_WINDOW", str(win.get_id()), True)
        launcher.setenv("AITERM_BUS_NAME", app.get_dbus_connection().get_unique_name(), True)
        launcher.setenv("AITERM_OBJECT_PATH", app.get_dbus_object_path(), True)
        process = launcher.spawnv([os.path.join(ROOT, "bin", argv[0]), *argv[1:]])
        box = []
        process.communicate_utf8_async(None, None, lambda p, res: box.append(p.communicate_utf8_finish(res)))
        wait_for(lambda: box, seconds)
        return (process.get_exit_status(), box[0][1]) if box else (None, "no reply")

    home = os.path.expanduser("~")
    result = run_tool("aiterm-run", "echo tool-$((3*3))")
    check("aiterm-run runs a command through the app",
          result == (0, f"[terminal folder: {home}]\n$ echo tool-$((3*3))\ntool-9\n"), repr(result))
    result = run_tool("aiterm-run", "ls /no-such-dir")
    check("aiterm-run shows a failed command's exit code",
          result[0] == 0 and "No such file" in result[1] and result[1].endswith("[exit code 2]\n"), repr(result))
    result = run_tool("aiterm-left", "2")
    check("aiterm-left N shows the last commands",
          "$ echo tool-$((3*3))\ntool-9\n$ ls /no-such-dir" in result[1], repr(result))
    result = run_tool("aiterm-left", "all")
    check("aiterm-left all shows the whole terminal", result[0] == 0 and "tool-9" in result[1], repr(result)[:200])
    term.feed_child(b"sleep 2\n")
    wait_for(lambda: term.running_program() == "sleep")
    result = run_tool("aiterm-run", "echo no")
    check("aiterm-run refuses while a program runs (code 3)", result[0] == 3 and "«sleep»" in result[1],
          repr(result))
    result = run_tool("aiterm-run", "-w")
    check("aiterm-run -w waits for it", result[0] == 0 and "$ sleep 2" in result[1], repr(result))

    # The MCP server, as agy runs it: a child process speaking JSON-RPC
    launcher = Gio.SubprocessLauncher.new(Gio.SubprocessFlags.STDIN_PIPE | Gio.SubprocessFlags.STDOUT_PIPE)
    launcher.setenv("AITERM_WINDOW", str(win.get_id()), True)
    launcher.setenv("AITERM_BUS_NAME", app.get_dbus_connection().get_unique_name(), True)
    launcher.setenv("AITERM_OBJECT_PATH", app.get_dbus_object_path(), True)
    server = launcher.spawnv([os.path.join(ROOT, "bin", "aiterm-mcp")])
    server_out = Gio.DataInputStream.new(server.get_stdout_pipe())
    ids = iter(range(1, 1000))

    def mcp(method, params, seconds=20):
        id_ = next(ids)
        line = json.dumps({"jsonrpc": "2.0", "id": id_, "method": method, "params": params}) + "\n"
        server.get_stdin_pipe().write_all(line.encode(), None)
        box = []
        server_out.read_line_async(GLib.PRIORITY_DEFAULT, None,
                                   lambda s, res: box.append(s.read_line_finish_utf8(res)[0]))
        wait_for(lambda: box, seconds)
        reply = json.loads(box[0]) if box and box[0] else {}
        return reply.get("result", reply)

    def tool(name, **arguments):
        result = mcp("tools/call", {"name": name, "arguments": arguments})
        return result.get("isError"), result.get("content", [{}])[0].get("text", ""), result.get(
            "structuredContent", result)

    mcp("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test"}})
    check("the MCP server lists its tools inside aiterm", len(mcp("tools/list", {}).get("tools", [])) == 6)
    error, text, data = tool("run_command", command="echo mcp-$((4*4))")
    check("run_command runs in the user's terminal",
          not error and data["output"] == "mcp-16" and data["exit_code"] == 0
          and last().text == "echo mcp-$((4*4))", repr(data))
    check("…and shows the model the folder and the command",
          text == f"[terminal folder: {home}]\n$ echo mcp-$((4*4))\nmcp-16", repr(text))
    error, text, data = tool("run_command", command="cd /tmp && false")
    check("run_command reports the exit code and the new folder",
          not error and data["exit_code"] == 1 and data["folder"] == "/tmp" and text.endswith("[exit code 1]"),
          repr(text))
    error, text, data = tool("get_cwd")
    check("get_cwd returns the terminal's folder", not error and data == {"folder": "/tmp"}, repr(data))
    error, text, data = tool("read_terminal", commands=2)
    check("read_terminal returns the last commands",
          [c["command"] for c in data.get("commands", [])] == ["echo mcp-$((4*4))", "cd /tmp && false"]
          and "$ cd /tmp && false\n[exit code 1]" in text, repr(data))
    error, text, data = tool("read_terminal", whole_screen=True)
    check("read_terminal can read the whole screen", not error and "mcp-16" in data.get("screen", ""),
          repr(text[-200:]))
    term.feed_child(b"sleep 2\n")
    wait_for(lambda: term.running_program() == "sleep")
    error, text, data = tool("run_command", command="echo no")
    check("run_command refuses while a program runs, as an error the model reads",
          error and "«sleep» is running" in text, repr(text))
    error, text, data = tool("wait_for_command", timeout=10)
    check("wait_for_command waits for it", not error and data.get("command") == "sleep 2", repr(data))
    error, text, data = tool("run_command", command="sleep 3; echo slow", timeout=1)
    check("run_command returns on its timeout and says what to do",
          not error and data.get("status") == "timeout" and "wait_for_command" in text, repr(text))
    error, text, data = tool("wait_for_command", timeout=10)
    check("…and wait_for_command gets the rest", not error and data.get("output") == "slow", repr(data))
    Settings.get().approve_agent_commands = True
    GLib.timeout_add(300, lambda: win.approval.answer(False))
    error, text, data = tool("run_command", command="echo never")
    check("a declined command is a tool error that says so", error and "chose not to run" in text, repr(text))
    Settings.get().approve_agent_commands = False

    # The file tools: the change waits for Apply in the bar, then the app writes it
    edited = os.path.join(CONFIG_DIR, "edit-me.py")
    with open(edited, "w") as f:
        f.write("def total(items):\n    totla = 0\n    return totla\n")
    Settings.get().approve_agent_edits = True
    seen = []

    def look_and_answer(apply):
        seen.append((win.approval.kind, win.approval.title.get_label(), win.approval.run_button.get_label(),
                     win.approval.diff.label.get_text(), open(edited).read()))
        win.approval.answer(apply)
    GLib.timeout_add(300, lambda: look_and_answer(True) and False)
    error, text, data = tool("edit_file", path=edited, old_text="totla", new_text="total", replace_all=True)
    check("edit_file shows the change above the terminal with Apply",
          seen and seen[0][:3] == ("edit", "The agent wants to change edit-me.py", "Apply")
          and "-    totla = 0" in seen[0][3] and "+    total = 0" in seen[0][3], repr(seen))
    check("…writes nothing before Apply", seen and "totla" in seen[0][4], repr(seen))
    check("…and the change after it", not error and open(edited).read().count("total") == 3
          and data.get("status") == "applied" and (data["added"], data["removed"]) == (2, 2), repr((text, data)))
    GLib.timeout_add(300, lambda: win.approval.answer(False) and False)
    error, text, data = tool("edit_file", path=edited, old_text="return total", new_text="return 0")
    check("a rejected change is a tool error that says so, and nothing is written",
          error and "rejected the change" in text and "return total" in open(edited).read(), repr(text))

    def change_meanwhile():
        with open(edited, "a") as f:
            f.write("# the user typed this\n")
        win.approval.answer(True)
    GLib.timeout_add(300, lambda: change_meanwhile() and False)
    error, text, data = tool("edit_file", path=edited, old_text="return total", new_text="return 0")
    check("a file that changed while the user looked is left alone, and the model told to read it again",
          error and "Read it again" in text and "return total" in open(edited).read(), repr(text))
    Settings.get().approve_agent_edits = False
    term.feed_child(f"cd {CONFIG_DIR}\n".encode())
    wait_for(lambda: term.current_directory() == CONFIG_DIR)
    error, text, data = tool("write_file", path="notes/new.txt", content="hello\n")
    check("without asking, write_file creates a file at once, relative to the terminal's folder",
          not error and open(os.path.join(CONFIG_DIR, "notes", "new.txt")).read() == "hello\n"
          and not win.approval.pending, repr(text))
    term.feed_child(b"cd ~\n")
    wait_for(lambda: last().text == "cd ~")
    exited = []
    server.wait_async(None, lambda p, res: exited.append(p.wait_finish(res)))
    server.get_stdin_pipe().close(None)
    check("the MCP server exits when agy closes its input", wait_for(lambda: exited, 5))

    term.feed_child(b"seq 300\n")
    wait_for(lambda: last().text == "seq 300")
    seq = last()
    wait_for(lambda: term.top_row() > seq.start_row, 2)
    term.actions.activate_action("previous-prompt", None)
    check("Ctrl+Shift+Up scrolls to the previous prompt", term.top_row() == seq.start_row,
          f"{term.top_row()} vs {seq.start_row}")
    y = (seq.start_row + 3 - term.top_row() + 0.5) * term.get_char_height() + 4
    menu = term.context_menu_at(10, y)
    check("right-click on a command offers Copy Output", "Copy Output" in menu_labels(menu),
          str(menu_labels(menu)))
    term.actions.activate_action("copy-output", None)
    copied = clipboard_text(term) or ""
    check("Copy Output copies that command's output", copied.startswith("1\n2\n") and copied.endswith("\n300"),
          repr(copied[:20]))
    term.actions.activate_action("next-prompt", None)
    term.feed_child(b"clear\n")
    wait_for(lambda: last().text == "clear")

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

    settings = Settings.get()
    settings.palette = "Solarized"
    check("palette applies to open terminals", background(term) == (0, 43, 54), f"{background(term)}")
    settings.unlimited_scrollback = True
    # VTE reports "unlimited" as the largest possible number
    check("unlimited scrollback", term.get_scrollback_lines() > 1_000_000_000, f"{term.get_scrollback_lines()}")
    settings.cursor_shape = "ibeam"
    check("cursor shape", term.get_cursor_shape() == Vte.CursorShape.IBEAM)
    settings.use_system_font = False
    settings.font = "Monospace 14"
    check("custom font", term.get_font().get_size() == 14 * Pango.SCALE, term.get_font().to_string())
    with open(os.path.join(CONFIG_DIR, "settings.json")) as f:
        saved = json.load(f)
    check("preferences are saved", saved.get("palette") == "Solarized" and saved.get("font") == "Monospace 14",
          str(saved))
    check("preferences load back", Settings().cursor_shape == "ibeam")

    app.activate_action("preferences", None)
    dialog = win.get_visible_dialog()
    check("main menu opens preferences", isinstance(dialog, PreferencesDialog))
    if dialog:
        dialog.palette_row.set_selected(list(PALETTES).index("Tango"))
        check("the dialog changes the palette", settings.palette == "Tango")
        dialog.system_font_row.set_active(True)
        check("the dialog switches back to the system font", settings.use_system_font)
        dialog.approve_row.set_active(True)
        check("the dialog turns command approval on", settings.approve_agent_commands)
        animations_page(dialog.animations_page)
        dialog.force_close()
    for key in settings.keys():  # back to defaults for the rest of the test
        settings.set_property(key, settings.find_property(key.replace("_", "-")).get_default_value())
    settings.approve_agent_commands = False

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
    check("select all + copy puts the screen on the clipboard", copied and "smoke-title" in copied,
          f"clipboard: {copied!r}"[:200])

    os.makedirs(os.path.join(CONFIG_DIR, "with space"), exist_ok=True)
    dropped = [Gio.File.new_for_path(os.path.join(CONFIG_DIR, "with space")),
               Gio.File.new_for_path("/tmp/it's")]
    term.feed_child(b"printf '<%s>' ")
    term.paste_files(dropped)
    term.feed_child(b"\n")
    expected = f"<{CONFIG_DIR}/with space></tmp/it's>"
    check("dropped files are typed as quoted paths", wait_for(lambda: expected in screen_text(term)),
          screen_text(term)[-200:])

    term.get_clipboard().set("echo pasted-$((40+2))")
    term.activate_action("term.paste")
    # Paste reads the clipboard asynchronously; press Enter once it is typed
    wait_for(lambda: "echo pasted-" in screen_text(term))
    term.feed_child(b"\n")
    check("paste types the clipboard into the shell",
          wait_for(lambda: "pasted-42" in screen_text(term)))
    check("right-click menu has Copy, Paste, Select All",
          menu_labels(term.get_context_menu_model()) == ["Copy", "Paste", "Select All"])

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
          menu_labels(menu)[:2] == ["Open Link", "Copy Link"], str(menu_labels(menu)))
    term.actions.activate_action("copy-link", None)
    check("Copy Link copies the URL", clipboard_text(term) == "https://example.com/a?b=1")
    check("right-click elsewhere has no link items",
          "Open Link" not in menu_labels(term.context_menu_at(*cell(1, 0))))

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
    wait_for(lambda: second.command_log.prompt_rows)  # bash is ready
    second.feed_child(b"pwd\n")
    check("new tab starts in the current tab's folder",
          wait_for(lambda: "\n/tmp\n" in screen_text(second)), repr(screen_text(second).rstrip()[-300:]))
    # A long command in a background tab: tab A (term) runs it while B is open
    window_module.LONG_COMMAND_SECONDS = 1
    sent, finished = [], []
    app.send_notification = lambda notification_id, notification: sent.append(notification_id)
    handler = term.connect("command-finished", lambda _t, code, seconds: finished.append((code, seconds)))
    term.feed_child(b"sleep 1.5; false\n")
    check("shell integration reports the command's exit code and time",
          wait_for(lambda: finished) and finished[0][0] == 1 and finished[0][1] >= 1.4, str(finished))
    check("a long command in a background tab sends a notification",
          wait_for(lambda: sent) and sent == [f"command-{term.serial}"], str(sent))
    first_page = win.tabs.get_page(term.get_parent())
    check("…and marks the tab", first_page.get_needs_attention())
    app.activate_action("show-terminal", GLib.Variant("(uu)", (win.get_id(), term.serial)))
    check("clicking the notification shows that tab",
          win.current_terminal() is term and not first_page.get_needs_attention())
    term.feed_child(b"true\n")
    wait_for(lambda: len(finished) == 2)
    check("short commands send no notification", len(sent) == 1, str(sent))
    term.disconnect(handler)
    win.tabs.set_selected_page(win.tabs.get_page(second.get_parent()))

    win.activate_action("win.zoom-out")
    check("zoom applies to every tab", term.get_font_scale() == second.get_font_scale() == 1.1)
    win.activate_action("win.zoom-reset")
    check("Ctrl+0 resets the zoom", term.get_font_scale() == 1.0)
    win.activate_action("win.close-tab")
    check("close tab goes back to the first one",
          win.tabs.get_n_pages() == 1 and win.current_terminal() is term)
    check("a closed tab stops following the preferences", second._handlers == [])

    check("the agent panel starts hidden", not win.agent_panel.get_visible())
    check("Alt+Enter is caught before VTE",
          Gtk.ShortcutTrigger.parse_string("<Alt>Return").to_string() in win_keys)
    win.activate_action("win.switch-to-agent")
    check("Alt+Enter opens the agent panel", win.agent_panel.get_visible() and settings.agent_panel_visible)
    agent = win.agent_panel.terminal
    check("…and starts the agent in it", agent is not None)
    wait_for(lambda: "$ " in screen_text(agent))
    agent.feed_child(b"echo window=$AITERM_WINDOW\n")
    check("the agent knows its window", wait_for(lambda: f"window={win.get_id()}" in screen_text(agent)),
          screen_text(agent)[-200:])
    # End to end: the agent's tool types into the user's terminal
    agent.feed_child(b"aiterm-run 'echo from-agent'\n")
    check("the agent's aiterm-run runs in the user's terminal",
          wait_for(lambda: log.commands and log.commands[-1].text == "echo from-agent")
          and log.commands[-1].output == "from-agent", repr(log.commands[-1] if log.commands else None))
    check("…and gets the output back",
          wait_for(lambda: "$ echo from-agent\nfrom-agent" in screen_text(agent)), screen_text(agent)[-300:])
    agent.feed_child(b"exit\n")
    check("when the agent exits, the panel offers a restart", wait_for(lambda: win.agent_panel.terminal is None))
    win.agent_panel.focus()
    check("restart starts it again", win.agent_panel.terminal is not None)
    if wait_for(lambda: win.paned.get_width() > 600, 3):
        check("the panel opens at its saved width",
              abs(win.paned.get_width() - win.paned.get_position() - 380) <= 1,
              f"{win.paned.get_width()} - {win.paned.get_position()}")
        win.paned.set_position(win.paned.get_width() - 500)
        check("dragging the border sets the panel width", settings.agent_panel_width == 500,
              f"{settings.agent_panel_width}")
    else:
        print("  skip panel width: the window has no size (in the background)")

    # The chat view, with the fake agy
    settings.agent_view = "chat"
    chat = win.agent_panel.chat
    check("switching the view to Chat restarts the panel with the chat",
          chat is not None and win.agent_panel.terminal is None and win.agent_panel.get_child() is chat)
    check("the chat has no agent process until the first message", not chat.process.running)
    texts = lambda: [w.get_label() for w in widgets(chat.messages) if isinstance(w, Gtk.Label)]
    chat.input.get_buffer().set_text("hello")
    chat.send()
    check("a message starts the agent and shows the reply",
          wait_for(lambda: any("1,234 tokens" in t for t in texts())) and "hello" in texts(), str(texts()))
    check("the reply's Markdown is drawn", any("You said: <b>hello</b>" in t for t in texts()), str(texts()))
    check("the input is empty again and Send is back", chat.text() == "" and chat.send_button.get_visible())

    Settings.get().approve_agent_commands = True
    chat.send("run: echo chat-$((5*5))")
    rows = lambda: [w for w in widgets(chat.messages) if isinstance(w, CommandRow)]
    check("run_command shows a command block", wait_for(lambda: rows()) and "$ echo chat-$((5*5))" in
          rows()[0].expander.get_label_widget().get_last_child().get_label())
    check("…with Run / Don't Run while it waits for approval",
          wait_for(lambda: rows()[0].buttons.get_visible()) and win.approval.pending)
    check("…asked only there: the bar above the terminal stays hidden", not win.approval.get_reveal_child())
    win.agent_panel.set_visible(False)
    check("…until the chat is out of sight: then the bar asks", win.approval.get_reveal_child())
    win.agent_panel.set_visible(True)
    check("…and hides again when the chat is back", wait_for(lambda: not win.approval.get_reveal_child()))
    rows()[0].run_button.emit("clicked")
    check("Run in the chat runs it in the user's terminal",
          wait_for(lambda: last().text == "echo chat-$((5*5))", 20) and last().output == "chat-25",
          f"{last()!r} {rows()[0].status.get_label()!r} {rows()[0].output.get_label()!r} "
          f"typed={log.typed_text()!r} running={term.running_program()!r} "
          f"current={win.current_terminal() is term} screen={screen_text(term)[-300:]!r}")
    check("…and the block shows the output",
          wait_for(lambda: rows()[0].output.get_label() == "chat-25") and not rows()[0].buttons.get_visible(),
          rows()[0].output.get_label())
    tool_lines = lambda: [w.label.get_label() for w in widgets(chat.messages) if hasattr(w, "label")]
    check("agy's own tools are quiet lines; its reads of tool schemas are hidden",
          wait_for(lambda: "Read hostname" in tool_lines()) and not any("json" in t for t in tool_lines()),
          str(tool_lines()))
    wait_for(lambda: not chat.process.busy)
    chat.send("run: echo never")
    wait_for(lambda: len(rows()) == 2 and rows()[1].buttons.get_visible())
    rows()[1].skip_button.emit("clicked")
    check("Don't Run marks the block as not run",
          wait_for(lambda: rows()[1].status.has_css_class("error")) and "chose not to run" in rows()[1].status.get_label(),
          rows()[1].status.get_label())
    Settings.get().approve_agent_commands = False
    wait_for(lambda: not chat.process.busy)

    # The agent's file changes: a card with the diff and Apply / Reject
    Settings.get().approve_agent_edits = True
    with open(edited, "w") as f:
        f.write("one\ntwo\nthree\n")
    cards = lambda: [w for w in widgets(chat.messages) if isinstance(w, EditRow)]
    chat.send(f"edit: {edited}|two|2")
    check("edit_file shows a card with the file's name and the size of the change",
          wait_for(lambda: cards() and cards()[0].proposed) and cards()[0].title.get_label() == "Edit edit-me.py"
          and cards()[0].size.get_label() == "+1 \u22121",
          f"{[(c.title.get_label(), c.size.get_label()) for c in cards()]}")
    diff_text = cards()[0].diff.label.get_text() if cards() else ""
    check("…the diff with the lines around the change",
          "-two" in diff_text and "+2" in diff_text and " one" in diff_text, repr(diff_text))
    check("…numbered: old and new line on the left", "2   -two" in diff_text and "  2 +2" in diff_text,
          repr(diff_text))
    check("…and Apply / Reject while it waits", wait_for(lambda: cards()[0].buttons.get_visible())
          and win.approval.pending and win.approval.kind == "edit" and not win.approval.get_reveal_child())
    cards()[0].apply_button.emit("clicked")
    check("Apply in the chat writes the file",
          wait_for(lambda: not cards()[0].running) and open(edited).read() == "one\n2\nthree\n"
          and not cards()[0].apply_button.get_visible() and cards()[0].icon.has_css_class("success"),
          open(edited).read())
    wait_for(lambda: not chat.process.busy)
    chat.send(f"edit: {edited}|three|3")
    wait_for(lambda: len(cards()) == 2 and cards()[1].buttons.get_visible())
    cards()[1].reject_button.emit("clicked")
    check("Reject leaves the file and marks the card",
          wait_for(lambda: cards()[1].status.has_css_class("error")) and "rejected" in cards()[1].status.get_label()
          and open(edited).read() == "one\n2\nthree\n", cards()[1].status.get_label())
    check("an applied change offers Undo, a rejected one does not",
          cards()[0].undo_button.get_visible() and cards()[0].buttons.get_visible()
          and not cards()[1].buttons.get_visible(), f"{[c.buttons.get_visible() for c in cards()]}")
    cards()[0].undo_button.emit("clicked")
    check("…which puts the file back and says so",
          open(edited).read() == "one\ntwo\nthree\n" and not cards()[0].buttons.get_visible()
          and "Undone" in cards()[0].status.get_label(), open(edited).read())
    wait_for(lambda: not chat.process.busy)
    chat.send("native-edit: /tmp/elsewhere.py")
    check("agy's own edits show their diff too, without buttons, and say so",
          wait_for(lambda: len(cards()) == 3 and not cards()[2].running) and cards()[2].approval is None
          and "+new line" in cards()[2].diff.label.get_text() and "without asking" in cards()[2].status.get_label(),
          f"{[c.status.get_label() for c in cards()]}")
    Settings.get().approve_agent_edits = False
    wait_for(lambda: not chat.process.busy)

    first_id = chat.process.conversation_id
    chat.send("wait")
    check("Stop shows while a turn runs", wait_for(lambda: chat.stop_button.get_visible()))
    chat.stop_button.emit("clicked")
    check("Stop ends the turn", wait_for(lambda: "Stopped." in texts()) and not chat.process.running, str(texts()))
    chat.send("again")
    check("the next message goes on with the same conversation",
          wait_for(lambda: any("You said: <b>again</b>" in t for t in texts()))
          and chat.process.conversation_id == first_id,
          f"{chat.process.conversation_id} vs {first_id}")
    wait_for(lambda: not chat.process.busy)
    chat.send("crash")
    check("an agent that dies says so", wait_for(lambda: any("stopped unexpectedly" in t and "something broke" in t
                                                             for t in texts())), str(texts()[-2:]))
    check("markdown_to_pango escapes and formats",
          markdown_to_pango("a < b `x` **y**\n```sh\nls <dir>\n```") == "a &lt; b <tt>x</tt> <b>y</b>\n<tt>ls &lt;dir&gt;</tt>")
    settings.agent_view = "terminal"
    check("switching back to Terminal starts agy's terminal again", win.agent_panel.terminal is not None)
    panel = win.agent_panel
    check("…and remembers the chat's conversation for it", panel.resume == chat.process.conversation_id,
          f"{panel.resume} vs {chat.process.conversation_id}")
    settings.agent_view = "chat"
    check("back in Chat, the same conversation shows again with a note",
          panel.chat is chat and "Back from the Terminal view" in " ".join(texts()))
    count = len(texts())
    chat.send("still here")
    check("…and goes on with the same conversation id",
          wait_for(lambda: any("You said: <b>still here</b>" in t for t in texts()))
          and "--conversation" in chat.process.started_argv, str(chat.process.started_argv))
    wait_for(lambda: not chat.process.busy)
    # A conversation begun in the Terminal view: the chat continues it
    settings.agent_view = "terminal"
    panel.last_chat, panel.resume = None, None  # as if the Terminal view came first
    settings.agent_view = "chat"
    check("from the Terminal view, the chat continues its conversation",
          panel.chat is not chat and panel.chat.process.continue_last
          and "Continuing the conversation" in " ".join(
              w.get_label() for w in widgets(panel.chat.messages) if isinstance(w, Gtk.Label)))
    panel.chat.send("hello again")
    check("…with agy --continue", wait_for(lambda: panel.chat.process.running)
          and "--continue" in panel.chat.process.started_argv, str(panel.chat.process.started_argv))
    wait_for(lambda: not panel.chat.process.busy)
    settings.agent_view = "terminal"

    # Without the agy plugin (as after installing the .deb), the panel asks first
    import aiterm.agent_panel as agent_panel
    real = agent_panel.needs_setup, agent_panel.SETUP
    connected = []
    agent_panel.needs_setup = lambda: not connected
    agent_panel.SETUP = "/bin/true"
    win.agent_panel.restart()
    check("without the agy plugin the panel offers to connect it",
          win.agent_panel.terminal is None and isinstance(win.agent_panel.get_child(), Adw.StatusPage))
    win.agent_panel.skip_button.emit("clicked")
    check("Not Now starts the agent anyway", win.agent_panel.terminal is not None)
    win.agent_panel.setup_declined = False
    win.agent_panel.restart()
    connected.append(True)
    win.agent_panel.connect_button.emit("clicked")
    check("Connect runs the setup and starts the agent", win.agent_panel.terminal is not None)
    agent_panel.needs_setup, agent_panel.SETUP = real

    app.activate_action("new-window", None)
    other = app.get_active_window()
    check("a new window opens with the panel too", other.agent_panel.get_visible())
    check("new window opens", len(app.get_windows()) == 2 and other is not win)
    other_term = other.current_terminal()
    wait_for(lambda: "$ " in screen_text(other_term))
    check("new window starts in the current tab's folder",
          wait_for(lambda: other_term.current_directory() == "/tmp"), f"{other_term.current_directory()!r}")
    other.activate_action("win.agent-panel")
    check("the panel closes", not other.agent_panel.get_visible() and not settings.agent_panel_visible)
    other.set_default_size(700, 400)
    other.close()
    wait_for(lambda: len(app.get_windows()) == 1)
    check("a closed window remembers its size",
          (settings.window_width, settings.window_height) == (700, 400),
          f"{settings.window_width}x{settings.window_height}")
    app.activate_action("new-window", None)
    third = app.get_active_window()
    check("the next window opens at that size", third.get_default_size() == (700, 400),
          f"{third.get_default_size()}")
    third.close()
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
        dialog.force_close()  # = Cancel
    check("Cancel keeps the tab", win.tabs.get_n_pages() == 1)
    win.close()
    dialog = win.get_visible_dialog()
    check("closing a window with a busy tab asks first", dialog is not None and win in app.get_windows())
    if dialog:
        dialog.force_close()  # = Cancel
    win.activate_action("win.new-tab")
    busy = win.current_terminal()
    wait_for(lambda: "$ " in screen_text(busy))
    busy.feed_child(b"sleep 60\n")
    wait_for(lambda: busy.running_program() == "sleep")
    win.activate_action("win.close-tab")
    dialog = win.get_visible_dialog()
    if dialog:
        dialog.emit("response", "close")
        dialog.force_close()  # reports "cancel" too; must be ignored
    check("Close Tab in the dialog closes the busy tab",
          wait_for(lambda: win.tabs.get_n_pages() == 1 and win.current_terminal() is term, 3))
    term.feed_child(b"\x03")  # Ctrl+C
    check("an idle shell is not a running program", wait_for(lambda: term.running_program() is None))
    # A shell under a wrapper (as in the evals' sandbox, or toolbox): the
    # spawned process is not the shell, yet an idle shell is still idle
    from aiterm.terminal import BASH_INTEGRATION
    wrapped = win.add_tab(argv=["/bin/sh", "-c", f"bash --rcfile {BASH_INTEGRATION}; true"])
    wait_for(lambda: wrapped.command_log._prompt is not None)
    check("a wrapped shell at its prompt is not a running program",
          wait_for(lambda: wrapped.running_program() is None, 3), repr(wrapped.running_program()))
    wrapped.feed_child(b"sleep 2\n")
    check("…while its command is", wait_for(lambda: wrapped.running_program() == "sleep"))
    wrapped.feed_child(b"\x03exit\n")
    wait_for(lambda: win.tabs.get_n_pages() == 1)

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
shutil.rmtree(CONFIG_DIR, ignore_errors=True)
sys.exit(0 if results and all(results) else 1)
