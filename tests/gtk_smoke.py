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
from aiterm import dbus_api  # noqa: E402
from aiterm.application import Application  # noqa: E402
from aiterm.chat_view import CommandRow, markdown_to_pango  # noqa: E402
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
    check("the MCP server lists its tools inside aiterm", len(mcp("tools/list", {}).get("tools", [])) == 4)
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
    term.feed_child(b"cd ~\n")
    wait_for(lambda: last().text == "cd ~")
    exited = []
    server.wait_async(None, lambda p, res: exited.append(p.wait_finish(res)))
    server.get_stdin_pipe().close(None)
    check("the MCP server exits when agy closes its input", wait_for(lambda: exited, 5))

    term.feed_child(b"seq 300\n")
    wait_for(lambda: last().text == "seq 300")
    seq = last()
    adjustment = term.get_vadjustment()
    wait_for(lambda: adjustment.get_value() > seq.start_row, 2)
    term.actions.activate_action("previous-prompt", None)
    check("Ctrl+Shift+Up scrolls to the previous prompt", adjustment.get_value() == seq.start_row,
          f"{adjustment.get_value()} vs {seq.start_row}")
    y = (seq.start_row + 3 - adjustment.get_value() + 0.5) * term.get_char_height() + 4
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
    rows()[0].run_button.emit("clicked")
    check("Run in the chat runs it in the user's terminal",
          wait_for(lambda: last().text == "echo chat-$((5*5))", 20) and last().output == "chat-25",
          f"{last()!r} {rows()[0].status.get_label()!r} {rows()[0].output.get_label()!r} "
          f"typed={log.typed_text()!r} running={term.running_program()!r}")
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
