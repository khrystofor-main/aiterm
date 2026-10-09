"""Asking the user before the agent runs a command: a bar above the
terminal with the command and Run / Don't Run.

The bar floats over the terminal instead of taking space from it: a resize
would make bash redraw its prompt just as the command is typed.

The terminal API (dbus_api.py) calls ask() from RunCommand when the
"Ask Before the Agent Runs a Command" preference is on, so the check lives
in the app, not in the agent's own permission settings: whatever the agent
is told or configured to do, nothing is typed until the user clicks Run.
"""

from gi.repository import GLib, Gtk, Pango

from aiterm import animations


class ApprovalBar(Gtk.Revealer):
    def __init__(self):
        super().__init__(transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN)
        self._callback = None

        self.command = Gtk.Label(
            xalign=0, hexpand=True, selectable=True, wrap=True,
            wrap_mode=Pango.WrapMode.WORD_CHAR, lines=4, ellipsize=Pango.EllipsizeMode.END,
        )
        self.command.add_css_class("monospace")
        title = Gtk.Label(label="The agent wants to run", xalign=0)
        title.add_css_class("heading")
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
        text.append(title)
        text.append(self.command)

        self.run_button = Gtk.Button(label="Run", valign=Gtk.Align.CENTER)
        self.run_button.add_css_class("suggested-action")
        self.run_button.connect("clicked", lambda *_: self.answer(True))
        self.skip_button = Gtk.Button(label="Don't Run", valign=Gtk.Align.CENTER)
        self.skip_button.connect("clicked", lambda *_: self.answer(False))

        box = Gtk.Box(spacing=12, margin_start=12, margin_end=12, margin_top=8, margin_bottom=8)
        for widget in (Gtk.Image(icon_name="utilities-terminal-symbolic"), text, self.skip_button, self.run_button):
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
        self._callback = callback
        if terminal is not None:
            _, row = terminal.get_cursor_position()
            on_screen = row - terminal.top_row()
            top = on_screen >= terminal.get_row_count() / 2
            self.set_valign(Gtk.Align.START if top else Gtk.Align.END)
            self.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN if top
                                     else Gtk.RevealerTransitionType.SLIDE_UP)
        self.command.set_label(command)
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
