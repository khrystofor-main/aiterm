"""The side panel where the agent works, in one of two views
(Preferences → Agent → View):

- terminal: agy's own interface in a terminal next to the user's;
- chat: a conversation drawn by the app (chat_view.py), with agy running
  headless behind it (chat.py).

agy gets AITERM_WINDOW, AITERM_BUS_NAME and AITERM_OBJECT_PATH, so the MCP
server it starts (aiterm-mcp, from the aiterm plugin) reaches this window's
selected tab over D-Bus (dbus_api.py), and this checkout's bin/ comes first
in its PATH, so the server matches the app. It starts the first time the
panel opens.
"""

import os
import shlex
import shutil
import subprocess

from gi.repository import Adw, GLib, Gtk

from aiterm.chat import AgentProcess, chat_command
from aiterm.chat_view import ChatView
from aiterm.settings import Settings
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


SETUP = os.path.join(BIN, "aiterm-agent-setup")


def needs_setup():
    """True when agy has no aiterm plugin yet (installed from the .deb, not
    with install.sh). Never for an AITERM_AGENT stand-in."""
    if os.environ.get("AITERM_AGENT") or os.environ.get("AITERM_CHAT_AGENT"):
        return False
    return subprocess.run([SETUP, "--check"], capture_output=True).returncode == 1


class AgentPanel(Adw.Bin):
    def __init__(self, window):
        super().__init__(width_request=MIN_WIDTH)
        self.add_css_class("view")
        self.window = window
        self.terminal = None  # the terminal view's agy
        self.chat = None  # the chat view
        self.setup_declined = False
        handler = Settings.get().connect("notify::agent-view", lambda *_: self.restart())
        window.connect("destroy", lambda *_: self._on_window_destroyed(handler))

    def start(self):
        """Starts the agent unless it is running."""
        if self.terminal or self.chat:
            return
        argv = agent_command()
        chat = Settings.get().agent_view == "chat"
        if chat:
            argv = chat_command(argv)
        if argv is None or not shutil.which(argv[0]):
            self._show_status(
                "Agent Not Found",
                f"Install the Antigravity CLI (agy) and sign in, then reopen this panel.\n"
                f'<a href="{INSTALL_URL}">How to install agy</a>',
            )
            return
        if not self.setup_declined and needs_setup():
            return self._offer_setup()
        user_terminal = self.window.current_terminal()
        cwd = user_terminal.current_directory() if user_terminal else None
        if chat:
            self.chat = ChatView(AgentProcess(argv, cwd, self._environment()), self.window.approval)
            self.set_child(self.chat)
            return
        self.terminal = Terminal(cwd=cwd, argv=argv, env=self._environment())
        self.terminal.connect("exited", lambda *_: self._on_exited())
        self.set_child(Gtk.ScrolledWindow(child=self.terminal, hscrollbar_policy=Gtk.PolicyType.NEVER))

    def restart(self):
        """Switching the view: the old agent goes, the new one starts if the
        panel is open."""
        if self.chat:
            self.chat.process.stop()
        self.terminal = self.chat = None
        self.set_child(None)  # closing the terminal hangs up agy
        if self.get_visible():
            self.start()

    def _on_window_destroyed(self, handler):
        Settings.get().disconnect(handler)
        if self.chat:
            self.chat.process.stop()

    def _environment(self):
        app = self.window.get_application()
        return {
            "AITERM_WINDOW": str(self.window.get_id()),
            "AITERM_BUS_NAME": app.get_dbus_connection().get_unique_name(),
            "AITERM_OBJECT_PATH": app.get_dbus_object_path(),
            "PATH": f"{BIN}:{os.environ.get('PATH', '')}",
        }

    def focus(self):
        self.start()
        if self.terminal:
            self.terminal.grab_focus()
        elif self.chat:
            self.chat.focus()

    def has_focus(self):
        if self.chat:
            return self.chat.input.has_focus()
        return bool(self.terminal and self.terminal.has_focus())

    def _on_exited(self):
        self.terminal = None
        restart = Gtk.Button(label="Restart Agent", halign=Gtk.Align.CENTER)
        restart.add_css_class("pill")
        restart.add_css_class("suggested-action")
        restart.connect("clicked", lambda *_: self.focus())
        self._show_status("Agent Stopped", "The agent has exited.", restart)

    def _offer_setup(self):
        """Asks before changing the user's agy configuration: the changes are
        the same as install.sh's (bin/aiterm-agent-setup)."""
        self.connect_button = Gtk.Button(label="Connect")
        self.connect_button.add_css_class("pill")
        self.connect_button.add_css_class("suggested-action")
        self.connect_button.connect("clicked", lambda *_: self._run_setup())
        self.skip_button = Gtk.Button(label="Not Now")
        self.skip_button.add_css_class("pill")
        self.skip_button.connect("clicked", lambda *_: self._skip_setup())
        buttons = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER)
        buttons.append(self.skip_button)
        buttons.append(self.connect_button)
        self._show_status(
            "Connect agy to Aiterm",
            "So the agent can read your terminal and run commands in it, Aiterm adds its plugin to agy "
            "(~/.gemini/config/plugins/aiterm) and lets agy call its tools. Aiterm still asks before "
            "every command. Undo with <tt>aiterm-agent-setup --remove</tt>.",
            buttons,
        )

    def _run_setup(self):
        done = subprocess.run([SETUP], capture_output=True, text=True)
        if done.returncode:
            self._show_status("Could Not Connect agy", GLib.markup_escape_text(
                (done.stderr or done.stdout).strip() or f"aiterm-agent-setup exited with {done.returncode}"))
            return
        self.focus()

    def _skip_setup(self):
        self.setup_declined = True
        self.focus()

    def _show_status(self, title, description, child=None):
        self.set_child(Adw.StatusPage(
            icon_name="chat-message-new-symbolic", title=title, description=description,
            child=child, vexpand=True,
        ))
