"""A VTE terminal widget that runs the user's shell and follows the system look."""

import os

from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk, Pango, Vte

# Adwaita named colors, the same family GNOME apps use
PALETTE = [
    "#241f31", "#c01c28", "#2ec27e", "#e5a50a", "#1c71d8", "#9141ac", "#0ab9dc", "#c0bfbc",
    "#5e5c64", "#ed333b", "#57e389", "#f8e45c", "#51a1ff", "#c061cb", "#4fd2fd", "#f6f5f4",
]
# Foreground and background match libadwaita's "view" colors, so the terminal
# blends with the header bar in both themes
LIGHT = ("#1e1e1e", "#ffffff")
DARK = ("#ffffff", "#1d1d20")

SCROLLBACK_LINES = 10_000

# term.* action -> key, the same as GNOME Terminal and Ptyxis
SHORTCUTS = {
    "copy": "<Control><Shift>c",
    "paste": "<Control><Shift>v",
    "select-all": "<Control><Shift>a",
}
INTERFACE_SCHEMA = "org.gnome.desktop.interface"


def _rgba(spec):
    color = Gdk.RGBA()
    color.parse(spec)
    return color


def user_shell():
    return os.environ.get("SHELL") or Vte.get_user_shell() or "/bin/bash"


class Terminal(Vte.Terminal):
    """Runs one shell. Emits `title-changed` and `exited`."""

    __gsignals__ = {
        "title-changed": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "exited": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, cwd=None):
        super().__init__(hexpand=True, vexpand=True)
        self.set_scrollback_lines(SCROLLBACK_LINES)
        self.set_mouse_autohide(True)

        self._palette = [_rgba(c) for c in PALETTE]
        style = Adw.StyleManager.get_default()
        style.connect("notify::dark", lambda *_: self._apply_colors())
        self._apply_colors()

        self._interface = self._interface_settings()
        if self._interface:
            self._interface.connect("changed::monospace-font-name", lambda *_: self._apply_font())
        self._apply_font()

        # VTE >= 0.78 reports the OSC title as a terminal property; older
        # versions only have the (now deprecated) window-title-changed signal
        if hasattr(Vte, "TERMPROP_XTERM_TITLE"):
            self.connect(f"termprop-changed::{Vte.TERMPROP_XTERM_TITLE}", self._on_title)
        else:
            self.connect("window-title-changed", self._on_title)
        self.connect("child-exited", lambda *_: self.emit("exited"))
        self._add_clipboard_actions()

        self._spawn(cwd or GLib.get_home_dir())

    def title(self):
        if hasattr(Vte, "TERMPROP_XTERM_TITLE"):
            value = self.get_termprop_string(Vte.TERMPROP_XTERM_TITLE)
            # PyGObject returns (string, length) for this call
            if isinstance(value, tuple):
                value = value[0]
        else:
            value = self.get_window_title()
        return value or ""

    def _add_clipboard_actions(self):
        """Copy / paste / select all as `term.*` actions, a right-click menu for
        them. Middle-click pastes the primary selection, built into VTE."""
        group = self.actions = Gio.SimpleActionGroup()
        actions = {
            "copy": lambda *_: self.copy_clipboard_format(Vte.Format.TEXT),
            "paste": lambda *_: self.paste_clipboard(),
            "select-all": lambda *_: self.select_all(),
        }
        for name, callback in actions.items():
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            group.add_action(action)
        self.insert_action_group("term", group)

        copy = group.lookup_action("copy")
        copy.set_enabled(False)
        self.connect("selection-changed", lambda *_: copy.set_enabled(self.get_has_selection()))

        # VTE turns every key it gets into terminal input, so the shortcuts are
        # caught before it, in the capture phase. They always count as handled:
        # Ctrl+Shift+C without a selection must not reach the shell as Ctrl+C
        keys = Gtk.ShortcutController(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        for name, trigger in SHORTCUTS.items():
            keys.add_shortcut(Gtk.Shortcut(
                trigger=Gtk.ShortcutTrigger.parse_string(trigger),
                action=Gtk.CallbackAction.new(self._shortcut, name),
            ))
        self.add_controller(keys)

        menu = Gio.Menu()
        menu.append("Copy", "term.copy")
        menu.append("Paste", "term.paste")
        menu.append("Select All", "term.select-all")
        self.set_context_menu_model(menu)

    def _shortcut(self, _widget, _args, name):
        if self.actions.get_action_enabled(name):
            self.actions.activate_action(name, None)
        return True

    def _spawn(self, cwd):
        shell = user_shell()
        self.spawn_async(
            Vte.PtyFlags.DEFAULT,
            cwd,
            [shell],
            None,  # inherit our environment; VTE adds TERM and friends
            GLib.SpawnFlags.DEFAULT,
            None, None,  # child setup
            -1,  # no timeout
            None,  # cancellable
            self._on_spawned,
        )

    def _on_spawned(self, _terminal, _pid, error, *_):
        if error:
            self.feed(f"aiterm: could not start the shell: {error.message}\r\n".encode())

    def _on_title(self, *_):
        self.emit("title-changed", self.title())

    def _apply_colors(self):
        fg, bg = DARK if Adw.StyleManager.get_default().get_dark() else LIGHT
        self.set_colors(_rgba(fg), _rgba(bg), self._palette)

    @staticmethod
    def _interface_settings():
        # The schema exists on GNOME; elsewhere VTE's default font is used
        source = Gio.SettingsSchemaSource.get_default()
        if source and source.lookup(INTERFACE_SCHEMA, True):
            return Gio.Settings.new(INTERFACE_SCHEMA)
        return None

    def _apply_font(self):
        if self._interface:
            name = self._interface.get_string("monospace-font-name")
            self.set_font(Pango.FontDescription.from_string(name))
