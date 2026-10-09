"""A VTE terminal widget that runs the user's shell and follows the system look."""

import os

from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk, Pango, Vte

from aiterm.shortcuts import add_capture_shortcuts

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

# Plain-text links: a scheme, then anything up to whitespace or quotes, but
# not trailing punctuation ("see https://example.com." ends before the dot)
URL_PATTERN = r"""(?:https?|ftp|file)://[^\s<>"'`]*[^\s<>"'`.,;:!?)\]}]"""
PCRE2_MULTILINE = 0x00000400  # VTE requires it for match regexes


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
        self.connect("child-exited", self._on_child_exited)
        self._add_clipboard_actions()
        self._add_links()

        self._pid = None
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

        # Without a selection Ctrl+Shift+C does nothing, rather than reaching
        # the shell as Ctrl+C
        add_capture_shortcuts(self, {
            trigger: (lambda name=name: self._activate_if_enabled(name))
            for name, trigger in SHORTCUTS.items()
        })


    def _add_links(self):
        """Ctrl+click opens a link: plain-text URLs and OSC 8 hyperlinks
        (ls --hyperlink, gcc errors, systemd). Links also get Open / Copy
        Link in the right-click menu."""
        self.set_allow_hyperlink(True)
        tag = self.match_add_regex(Vte.Regex.new_for_match(URL_PATTERN, -1, PCRE2_MULTILINE), 0)
        self.match_set_cursor_name(tag, "pointer")

        # Capture phase: before VTE starts a selection with this click
        click = Gtk.GestureClick(button=1, propagation_phase=Gtk.PropagationPhase.CAPTURE)
        click.connect("pressed", self._on_click)
        self.add_controller(click)

        self._menu_link = None
        for name, callback in {
            "open-link": lambda *_: self.open_link(self._menu_link),
            "copy-link": lambda *_: self.get_clipboard().set(self._menu_link),
        }.items():
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self.actions.add_action(action)

        self.set_context_menu_model(self.context_menu_at(None, None))
        self.connect("setup-context-menu", self._on_setup_context_menu)

    def link_at(self, x, y):
        """The link under widget coordinates x, y, or None."""
        return self.check_hyperlink_at(x, y) or self.check_match_at(x, y)[0]

    def open_link(self, uri):
        if uri:
            Gtk.UriLauncher.new(uri).launch(self.get_root(), None, None, None)

    def context_menu_at(self, x, y):
        """The right-click menu, with link items when (x, y) is on a link."""
        self._menu_link = self.link_at(x, y) if x is not None else None
        menu = Gio.Menu()
        if self._menu_link:
            link = Gio.Menu()
            link.append("Open Link", "term.open-link")
            link.append("Copy Link", "term.copy-link")
            menu.append_section(None, link)
        edit = Gio.Menu()
        edit.append("Copy", "term.copy")
        edit.append("Paste", "term.paste")
        edit.append("Select All", "term.select-all")
        menu.append_section(None, edit)
        return menu

    def _on_setup_context_menu(self, _terminal, context):
        if context is None:
            return  # the menu is closing
        _, x, y = context.get_coordinates()
        self.set_context_menu_model(self.context_menu_at(x, y))

    def _on_click(self, gesture, _n_press, x, y):
        if not gesture.get_current_event_state() & Gdk.ModifierType.CONTROL_MASK:
            return
        link = self.link_at(x, y)
        if link:
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            self.open_link(link)

    def _activate_if_enabled(self, name):
        if self.actions.get_action_enabled(name):
            self.actions.activate_action(name, None)

    def running_program(self):
        """The program running in the foreground (e.g. "vim"), or None while
        the shell just waits at its prompt.

        The terminal's foreground process group is the shell's own while it
        waits for input, and the running job's while a command runs."""
        pty = self.get_pty()
        if not pty or not self._pid:
            return None
        try:
            group = os.tcgetpgrp(pty.get_fd())
        except OSError:
            return None
        if group == self._pid:
            return None
        try:
            with open(f"/proc/{group}/comm") as f:
                return f.read().strip()
        except OSError:
            return "a program"

    def current_directory(self):
        """The shell's folder: from OSC 7 (Ubuntu's bash sends it through
        /etc/profile.d/vte-2.91.sh), else from /proc."""
        uri = self.ref_termprop_uri(Vte.TERMPROP_CURRENT_DIRECTORY_URI)
        if uri and uri.get_scheme() == "file":
            return GLib.filename_from_uri(uri.to_string())[0]
        if self._pid:
            try:
                return os.readlink(f"/proc/{self._pid}/cwd")
            except OSError:
                pass
        return None

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

    def _on_spawned(self, _terminal, pid, error, *_):
        self._pid = pid if pid > 0 else None
        if error:
            self.feed(f"aiterm: could not start the shell: {error.message}\r\n".encode())

    def _on_child_exited(self, *_):
        self._pid = None
        self.emit("exited")

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
