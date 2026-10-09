"""Asking the user before the agent runs a command: a bar above the
terminal with the command and Run / Don't Run.

The terminal API (dbus_api.py) calls ask() from RunCommand when the
"Ask Before the Agent Runs a Command" preference is on, so the check lives
in the app, not in the agent's own permission settings: whatever the agent
is told or configured to do, nothing is typed until the user clicks Run.
"""

from gi.repository import Gtk, Pango


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
        frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        frame.add_css_class("background")
        frame.append(box)
        frame.append(Gtk.Separator())
        self.set_child(frame)

    @property
    def pending(self):
        return self._callback is not None

    def ask(self, command, callback):
        """Shows the command; calls callback(True) for Run, (False) for Don't Run."""
        self._callback = callback
        self.command.set_label(command)
        self.set_reveal_child(True)

    def answer(self, run):
        callback, self._callback = self._callback, None
        self.set_reveal_child(False)
        if callback:
            callback(run)

    def focus(self):
        self.run_button.grab_focus()
