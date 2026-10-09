"""A client of the terminal API (dbus_api.py), for the processes the agent
starts: the MCP server (mcp_server.py) and aiterm-left / aiterm-run
(tools.py). The agent started by Aiterm has AITERM_WINDOW, AITERM_BUS_NAME
and AITERM_OBJECT_PATH in its environment; its children inherit them.
"""

import os
from collections import namedtuple

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from aiterm import APP_ID, OBJECT_PATH, TERMINAL_INTERFACE  # noqa: E402

# status: done, busy (a program is running), typing (the user has text on the
# prompt line), denied (the user clicked Don't Run) or timeout (still running;
# output so far)
Result = namedtuple("Result", "status folder command output exit_code")
Command = namedtuple("Command", "command output exit_code seconds")


class NotInside(Exception):
    """The agent was not started by Aiterm: there is no user terminal."""


class Unreachable(Exception):
    """The Aiterm window does not answer (closed, or the app has quit)."""


class TerminalClient:
    def __init__(self, environ=os.environ):
        window = environ.get("AITERM_WINDOW", "")
        if not window.isdigit():
            raise NotInside("agy is not running inside aiterm: there is no user terminal.")
        self.window = int(window)
        self.bus_name = environ.get("AITERM_BUS_NAME", APP_ID)
        self.object_path = environ.get("AITERM_OBJECT_PATH", OBJECT_PATH)

    def _call(self, method, args, signature, reply_type, seconds=30):
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION)
            result = bus.call_sync(
                self.bus_name, self.object_path, TERMINAL_INTERFACE, method,
                GLib.Variant(signature, (self.window, *args)), GLib.VariantType(reply_type),
                Gio.DBusCallFlags.NONE, seconds * 1000, None)
        except GLib.Error as error:
            raise Unreachable(f"Cannot reach the Aiterm window: {error.message}") from None
        return result.unpack()

    def read_commands(self, count=1):
        """The terminal's folder and the last `count` commands (0: all)."""
        folder, rows = self._call("ReadCommands", (count,), "(ui)", "(sa(ssid))")
        return folder, [Command(*row) for row in rows]

    def read_screen(self):
        """The terminal's folder and the whole scrollback."""
        return self._call("ReadScreen", (), "(u)", "(ss)")

    def run(self, command, timeout=60):
        """Types `command` into the terminal and waits up to `timeout` seconds."""
        return Result(*self._call("RunCommand", (command, timeout), "(usu)", "(ssssi)", timeout + 10))

    def wait(self, timeout=60):
        """Waits for the command that is already running."""
        return Result(*self._call("Wait", (timeout,), "(uu)", "(ssssi)", timeout + 10))


def format_command(command, output, exit_code):
    """`$ command`, its output and `[exit code N]` when it failed: how both the
    tools and the MCP server show a command to the model."""
    lines = [f"$ {command}"]
    if output:
        lines.append(output)
    if exit_code:
        lines.append(f"[exit code {exit_code}]")
    return "\n".join(lines)
