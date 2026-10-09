"""The side panel where the agent works: agy in its own terminal, next to
the user's.

agy gets AITERM_WINDOW, AITERM_BUS_NAME and AITERM_OBJECT_PATH, so the MCP
server it starts (aiterm-mcp, from the aiterm plugin) reaches this window's
selected tab over D-Bus (dbus_api.py), and this checkout's bin/ comes first
in its PATH, so the server matches the app. It starts the first time the
panel opens.
"""

import os
import shlex
import shutil

from gi.repository import Adw, Gtk

from aiterm.terminal import Terminal

MIN_WIDTH = 240
BIN = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "bin"))
INSTALL_URL = "https://antigravity.google/docs/cli/install"


def agent_command():
    """The agent to run, as argv; AITERM_AGENT overrides it (tests use a plain
    shell). None when agy is not installed."""
    if os.environ.get("AITERM_AGENT"):
        return shlex.split(os.environ["AITERM_AGENT"])
    agy = shutil.which("agy") or os.path.expanduser("~/.local/bin/agy")
    return [agy] if os.access(agy, os.X_OK) else None


class AgentPanel(Adw.Bin):
    def __init__(self, window):
        super().__init__(width_request=MIN_WIDTH)
        self.add_css_class("view")
        self.window = window
        self.terminal = None

    def start(self):
        """Starts the agent unless it is running."""
        if self.terminal:
            return
        argv = agent_command()
        if argv is None or not shutil.which(argv[0]):
            self._show_status(
                "Agent Not Found",
                f"Install the Antigravity CLI (agy) and sign in, then reopen this panel.\n"
                f'<a href="{INSTALL_URL}">How to install agy</a>',
            )
            return
        app = self.window.get_application()
        user_terminal = self.window.current_terminal()
        self.terminal = Terminal(
            cwd=user_terminal.current_directory() if user_terminal else None,
            argv=argv,
            env={
                "AITERM_WINDOW": str(self.window.get_id()),
                "AITERM_BUS_NAME": app.get_dbus_connection().get_unique_name(),
                "AITERM_OBJECT_PATH": app.get_dbus_object_path(),
                "PATH": f"{BIN}:{os.environ.get('PATH', '')}",
            },
        )
        self.terminal.connect("exited", lambda *_: self._on_exited())
        self.set_child(Gtk.ScrolledWindow(child=self.terminal, hscrollbar_policy=Gtk.PolicyType.NEVER))

    def focus(self):
        self.start()
        if self.terminal:
            self.terminal.grab_focus()

    def has_focus(self):
        return bool(self.terminal and self.terminal.has_focus())

    def _on_exited(self):
        self.terminal = None
        restart = Gtk.Button(label="Restart Agent", halign=Gtk.Align.CENTER)
        restart.add_css_class("pill")
        restart.add_css_class("suggested-action")
        restart.connect("clicked", lambda *_: self.focus())
        self._show_status("Agent Stopped", "The agent has exited.", restart)

    def _show_status(self, title, description, child=None):
        self.set_child(Adw.StatusPage(
            icon_name="chat-message-new-symbolic", title=title, description=description,
            child=child, vexpand=True,
        ))
