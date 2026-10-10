"""Asking the user before the agent runs a command or changes a file: a bar
above the terminal with the command and Run / Don't Run, or the file, its
diff and Apply / Reject.

The bar floats over the terminal instead of taking space from it: a resize
would make bash redraw its prompt just as the command is typed.

The terminal API (dbus_api.py) calls ask() from RunCommand when the
"Ask Before the Agent Runs a Command" preference is on, so the check lives
in the app, not in the agent's own permission settings: whatever the agent
is told or configured to do, nothing is typed until the user clicks Run.
ProposeEdit calls ask_edit() the same way, and writes nothing before Apply.
The chat shows the same request in its own cards, with buttons that answer
here (chat_view.py).
"""

import os


from gi.repository import GLib, GObject, Gtk, Pango

from aiterm import animations
from aiterm.diff_view import DiffView, summary


class ApprovalBar(Gtk.Revealer):
    __gsignals__ = {
        # The agent's command was typed into the terminal (dbus_api.py); the
        # chat lights up its block (chat_view.py)
        "agent-ran": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        # The agent proposed a change to a file (dbus_api.py): the path and
        # its diff, one line per line; the chat shows it in the edit's card
        "edit-proposed": (GObject.SignalFlags.RUN_FIRST, None, (str, str)),
        # The app wrote it: the path, the text before (and whether the file
        # existed) and after, so the chat's card can undo it
        "edit-applied": (GObject.SignalFlags.RUN_FIRST, None, (str, str, bool, str)),
    }

    def __init__(self):
        super().__init__(transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN)
        self._callback = None
        self.kind = None  # what waits: "command" or "edit"
        self.path = None  # the file, for an edit

        self.command = Gtk.Label(
            xalign=0, hexpand=True, selectable=True, wrap=True,
            wrap_mode=Pango.WrapMode.WORD_CHAR, lines=4, ellipsize=Pango.EllipsizeMode.END,
        )
        self.command.add_css_class("monospace")
        self.title = Gtk.Label(label="The agent wants to run", xalign=0)
        self.title.add_css_class("heading")
        self.diff = DiffView(height=180, visible=False, margin_top=4)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
        text.append(self.title)
        text.append(self.command)
        text.append(self.diff)

        self.run_button = Gtk.Button(label="Run", valign=Gtk.Align.CENTER)
        self.run_button.add_css_class("suggested-action")
        self.run_button.connect("clicked", lambda *_: self.answer(True))
        self.skip_button = Gtk.Button(label="Don't Run", valign=Gtk.Align.CENTER)
        self.skip_button.connect("clicked", lambda *_: self.answer(False))

        box = Gtk.Box(spacing=12, margin_start=12, margin_end=12, margin_top=8, margin_bottom=8)
        self.icon = Gtk.Image(icon_name="utilities-terminal-symbolic", valign=Gtk.Align.START, margin_top=2)
        for widget in (self.icon, text, self.skip_button, self.run_button):
            box.append(widget)
        # Floats over the terminal (window.py), away from the cursor
        frame = Gtk.Box(margin_start=8, margin_end=8, margin_top=8, margin_bottom=8)
        frame.add_css_class("card")
        frame.add_css_class("approval")
        frame.append(box)
        self.set_child(frame)

    @property
    def pending(self):
        return self._callback is not None

    def ask(self, command, callback, terminal=None):
        """Shows the command; calls callback(True) for Run, (False) for Don't Run.
        With `terminal`, the bar goes to the half of it away from the cursor,
        so the prompt where the command will appear stays in sight."""
        self._show("command", command, callback, terminal)
        self.title.set_label("The agent wants to run")
        self.icon.set_from_icon_name("utilities-terminal-symbolic")
        self.command.set_label(command)
        self.diff.set_visible(False)
        self.run_button.set_label("Run")
        self.skip_button.set_label("Don't Run")
        self._reveal()

    def ask_edit(self, path, diff, callback, terminal=None):
        """Shows the change to the file at `path` (edits.diff() lines); calls
        callback(True) for Apply, (False) for Reject."""
        self._show("edit", path, callback, terminal)
        new = bool(diff) and diff[0] == "--- /dev/null"
        self.title.set_label(f"The agent wants to {'create' if new else 'change'} {os.path.basename(path)}")
        self.icon.set_from_icon_name("document-edit-symbolic")
        self.command.set_label(f"{path}  {summary(diff)}")
        self.diff.set_diff(diff)
        self.diff.set_visible(True)
        self.run_button.set_label("Apply")
        self.skip_button.set_label("Reject")
        self._reveal()

    def _show(self, kind, subject, callback, terminal):
        self._callback = callback
        self.kind = kind
        self.path = subject if kind == "edit" else None
        if terminal is not None:
            _, row = terminal.get_cursor_position()
            on_screen = row - terminal.top_row()
            top = on_screen >= terminal.get_row_count() / 2
            self.set_valign(Gtk.Align.START if top else Gtk.Align.END)
            self.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN if top
                                     else Gtk.RevealerTransitionType.SLIDE_UP)

    def _reveal(self):
        self.set_transition_duration(animations.duration(self, "approval"))
        # Run pulses while the agent waits (CSS below, timed by the preset)
        if animations.Animations.get().effect("approval"):
            self.run_button.add_css_class("approval-pulse")
        self.set_reveal_child(True)

    def preview(self, command, seconds=2):
        """Shows the bar for a moment, with nobody waiting (Preferences → Animations)."""
        if self.pending:
            return
        self.ask(command, None)
        GLib.timeout_add(seconds * 1000, lambda: (not self.pending and self.answer(False)) and False)

    def answer(self, run):
        callback, self._callback = self._callback, None
        self.kind = self.path = None
        self.run_button.remove_css_class("approval-pulse")
        self.set_transition_duration(animations.duration(self, "approval"))
        self.set_reveal_child(False)
        if callback:
            callback(run)

    def focus(self):
        self.run_button.grab_focus()


CSS = """
.approval { background: alpha(@window_bg_color, 0.97); }
@keyframes approval-pulse {
  0% { box-shadow: 0 0 0 0 alpha(@accent_bg_color, 0.6); }
  70% { box-shadow: 0 0 0 7px alpha(@accent_bg_color, 0); }
  100% { box-shadow: 0 0 0 0 alpha(@accent_bg_color, 0); }
}
"""


def animated_css(values):
    """The parts of the CSS timed by the animation preset."""
    pulse = values["effects"]["approval"]["pulse"]
    return f".approval-pulse {{ animation: approval-pulse {pulse}ms ease-out infinite; }}\n"
