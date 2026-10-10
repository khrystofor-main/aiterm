"""The terminal API for the agent's tools, on the session bus.

The application is already on the bus under its ID; this adds the interface
below on the same object, so there is no socket or port of our own and only
the same user can call it. aiterm-left / aiterm-run are its clients now, the
MCP server of v0.4 will be another. Try it:

    gdbus introspect --session --dest io.github.khrystofor_main.Aiterm \\
        --object-path /io/github/khrystofor_main/Aiterm

Every method takes the id of the window whose selected tab to work on (the
agent gets it as AITERM_WINDOW).
"""

import re
import subprocess

from gi.repository import Gio, GLib, Vte

from aiterm import TERMINAL_INTERFACE as INTERFACE
from aiterm import edits
from aiterm.settings import Settings
ERROR_NO_WINDOW = "io.github.khrystofor_main.Aiterm.Error.NoWindow"

XML = f"""
<node>
  <interface name="{INTERFACE}">
    <method name="ReadCommands">
      <arg name="window" type="u" direction="in"/>
      <arg name="count" type="i" direction="in"/>
      <arg name="folder" type="s" direction="out"/>
      <arg name="commands" type="a(ssid)" direction="out"/>
    </method>
    <method name="ReadScreen">
      <arg name="window" type="u" direction="in"/>
      <arg name="folder" type="s" direction="out"/>
      <arg name="text" type="s" direction="out"/>
    </method>
    <method name="RunCommand">
      <arg name="window" type="u" direction="in"/>
      <arg name="command" type="s" direction="in"/>
      <arg name="timeout" type="u" direction="in"/>
      {"".join(f'<arg name="{n}" type="{t}" direction="out"/>' for n, t in
               (("status", "s"), ("folder", "s"), ("command", "s"), ("output", "s"), ("exit_code", "i")))}
    </method>
    <method name="ProposeEdit">
      <arg name="window" type="u" direction="in"/>
      <arg name="path" type="s" direction="in"/>
      <arg name="before" type="s" direction="in"/>
      <arg name="existed" type="b" direction="in"/>
      <arg name="after" type="s" direction="in"/>
      <arg name="status" type="s" direction="out"/>
      <arg name="detail" type="s" direction="out"/>
    </method>
    <method name="Wait">
      <arg name="window" type="u" direction="in"/>
      <arg name="timeout" type="u" direction="in"/>
      {"".join(f'<arg name="{n}" type="{t}" direction="out"/>' for n, t in
               (("status", "s"), ("folder", "s"), ("command", "s"), ("output", "s"), ("exit_code", "i")))}
    </method>
  </interface>
</node>
"""

# Long output keeps its first lines (often the command's own header) and its
# tail, where errors usually are
OUTPUT_HEAD, OUTPUT_TAIL = 2, 300
SUDO = re.compile(r"(^|[^\w-])sudo([^\w-]|$)")


def cap_output(text):
    lines = text.split("\n")
    if len(lines) <= OUTPUT_HEAD + OUTPUT_TAIL:
        return text
    skipped = len(lines) - OUTPUT_HEAD - OUTPUT_TAIL
    return "\n".join(lines[:OUTPUT_HEAD] + [f"[… {skipped} lines skipped …]"] + lines[-OUTPUT_TAIL:])


class TerminalApi:
    def __init__(self, app, connection, object_path):
        self.app = app
        info = Gio.DBusNodeInfo.new_for_xml(XML).interfaces[0]
        self.registration = connection.register_object(object_path, info, self._on_call, None, None)
        self.connection = connection

    def unregister(self):
        self.connection.unregister_object(self.registration)

    def _on_call(self, _connection, _sender, _path, _interface, method, params, invocation):
        args = params.unpack()
        window = self.app.get_window_by_id(args[0])
        terminal = window.current_terminal() if window else None
        if terminal is None:
            invocation.return_dbus_error(ERROR_NO_WINDOW, f"No Aiterm window with id {args[0]}")
            return
        getattr(self, f"_{method}")(invocation, terminal, *args[1:])

    # Methods

    def _ReadCommands(self, invocation, terminal, count):
        commands = terminal.command_log.commands
        if count > 0:
            commands = commands[-count:]
        rows = [(c.text, cap_output(c.output), c.exit_code, c.seconds) for c in commands]
        invocation.return_value(GLib.Variant("(sa(ssid))", (folder(terminal), rows)))

    def _ReadScreen(self, invocation, terminal):
        first = int(terminal.get_vadjustment().get_lower())
        _, last = terminal.get_cursor_position()
        text, _ = terminal.get_text_range_format(Vte.Format.TEXT, first, 0, last, 10_000)
        invocation.return_value(GLib.Variant("(ss)", (folder(terminal), (text or "").rstrip("\n"))))

    def _RunCommand(self, invocation, terminal, command, timeout):
        if self._refuse(invocation, terminal):
            return
        approval = terminal.get_root().approval
        if not Settings.get().approve_agent_commands:
            return self._type(invocation, terminal, command, timeout)
        if approval.pending:
            return reply(invocation, "busy", terminal, "another agent command waiting for approval", "", 0)

        def answered(run):
            if not run or terminal.get_root() is None:  # Don't Run, or the tab was closed
                return reply(invocation, "denied", terminal, command, "", 0)
            # The user may have typed or started something while deciding
            if not self._refuse(invocation, terminal):
                self._type(invocation, terminal, command, timeout)

        approval.ask(command, answered, terminal)

    def _refuse(self, invocation, terminal):
        """Replies busy or typing when the terminal is not free; True then."""
        program = terminal.running_program()
        if program:
            reply(invocation, "busy", terminal, program, "", 0)
            return True
        typed = terminal.command_log.typed_text()
        if typed:
            reply(invocation, "typing", terminal, typed, "", 0)
            return True
        return False

    def _type(self, invocation, terminal, command, timeout):
        if SUDO.search(command):
            # Drop cached credentials: every sudo command from the agent needs
            # the password, typed by the user in their terminal
            subprocess.run(["sudo", "-K"], stdin=subprocess.DEVNULL, capture_output=True)
        # Its output's stripe and its block in the chat light up in one color
        terminal.effects.agent_command()
        window = terminal.get_root()
        if window is not None and hasattr(window, "approval"):
            window.approval.emit("agent-ran", command)
        terminal.feed_child(command.encode() + b"\r")
        self._wait_for_command(invocation, terminal, timeout)

    def _ProposeEdit(self, invocation, terminal, path, before, existed, after):
        """The agent's change to a file (planned by the MCP server, edits.py):
        the diff goes to the chat, and the file is written once the user
        clicks Apply, if it still holds the text the diff was made from."""
        before = before if existed else None
        window = terminal.get_root()
        diff = edits.diff(before, after, path)
        window.approval.emit("edit-proposed", path, "\n".join(diff))

        def write():
            try:
                edits.write(path, before, after)
            except edits.EditError as error:
                status = "changed" if isinstance(error, edits.Changed) else "failed"
                return invocation.return_value(GLib.Variant("(ss)", (status, str(error))))
            window.approval.emit("edit-applied", path, before or "", before is not None, after)
            invocation.return_value(GLib.Variant("(ss)", ("applied", "")))

        if not Settings.get().approve_agent_edits:
            return write()
        if window.approval.pending:
            return invocation.return_value(GLib.Variant("(ss)", ("busy", "")))
        window.approval.ask_edit(path, diff, lambda apply: write() if apply else invocation.return_value(
            GLib.Variant("(ss)", ("rejected", ""))), terminal)

    def _Wait(self, invocation, terminal, timeout):
        if not terminal.command_log.running and not terminal.running_program():
            last = terminal.command_log.commands[-1] if terminal.command_log.commands else None
            if last:
                return reply(invocation, "done", terminal, last.text, cap_output(last.output), last.exit_code)
            return reply(invocation, "done", terminal, "", "", 0)
        self._wait_for_command(invocation, terminal, timeout)

    def _wait_for_command(self, invocation, terminal, timeout):
        """Replies when the command log reports the next command, or with the
        output so far after `timeout` seconds."""
        pending = {}

        def finished(*_):
            GLib.source_remove(pending["timer"])
            terminal.disconnect(pending["handler"])
            command = terminal.command_log.commands[-1]
            reply(invocation, "done", terminal, command.text, cap_output(command.output), command.exit_code)

        def timed_out():
            terminal.disconnect(pending["handler"])
            reply(invocation, "timeout", terminal, terminal.running_program() or "",
                  cap_output(terminal.command_log.screen_since_prompt()), 0)
            return GLib.SOURCE_REMOVE

        pending["handler"] = terminal.connect("command-finished", finished)
        pending["timer"] = GLib.timeout_add_seconds(max(timeout, 1), timed_out)


def folder(terminal):
    return terminal.current_directory() or ""


def reply(invocation, status, terminal, command, output, exit_code):
    invocation.return_value(GLib.Variant(
        "(ssssi)", (status, folder(terminal), command, output, exit_code)))
