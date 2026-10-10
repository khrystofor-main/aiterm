"""A VTE terminal widget that runs the user's shell and follows the system
look and the user's preferences."""

import itertools
import os
import shlex

from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk, Pango, Vte

from aiterm import prompt
from aiterm.commands import CommandLog
from aiterm.effects import TerminalEffects
from aiterm.palettes import PALETTES
from aiterm.settings import Settings
from aiterm.shortcuts import add_capture_shortcuts

# Space around the text, set through CSS by the application
PADDING_Y, PADDING_X = 4, 8

CURSOR_SHAPES = {
    "block": Vte.CursorShape.BLOCK,
    "ibeam": Vte.CursorShape.IBEAM,
    "underline": Vte.CursorShape.UNDERLINE,
}

# term.* action -> key, the same as GNOME Terminal and Ptyxis
SHORTCUTS = {
    "copy": "<Control><Shift>c",
    "paste": "<Control><Shift>v",
    "select-all": "<Control><Shift>a",
    "previous-prompt": "<Control><Shift>Up",
    "next-prompt": "<Control><Shift>Down",
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


def _same_colors(a, b):
    flat = lambda colors: [c.to_string() for c in colors[:2] + tuple(colors[2])]
    return flat(a) == flat(b)


# Loaded instead of ~/.bashrc (it loads that itself), see the file
BASH_INTEGRATION = os.path.join(os.path.dirname(__file__), "shell", "integration.bash")


def user_shell():
    return os.environ.get("SHELL") or Vte.get_user_shell() or "/bin/bash"


def shell_command():
    shell = user_shell()
    if os.path.basename(shell) == "bash":
        return [shell, "--rcfile", BASH_INTEGRATION]
    return [shell]


class Terminal(Vte.Terminal):
    """Runs one shell. Emits `title-changed` and `exited`."""

    __gsignals__ = {
        "title-changed": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "exited": (GObject.SignalFlags.RUN_FIRST, None, ()),
        # exit code, seconds; details in command_log.commands[-1]
        "command-finished": (GObject.SignalFlags.RUN_FIRST, None, (int, float)),
    }
    _serials = itertools.count(1)

    def __init__(self, cwd=None, argv=None, env=None):
        """Runs the user's shell, or `argv` (the agent panel runs agy this
        way). `env` adds to or, with None values, removes from our environment."""
        super().__init__(hexpand=True, vexpand=True)
        self.set_mouse_autohide(True)

        self.settings = Settings.get()
        self._colors = None  # (foreground, background, palette) last set
        self.effects = TerminalEffects(self)
        self._interface = self._interface_settings()
        self._handlers = []
        self._appliers = {
            self._apply_colors: [(Adw.StyleManager.get_default(), "notify::dark"),
                                 (self.settings, "notify::palette")],
            self._apply_font: [(self.settings, "notify::use-system-font"),
                               (self.settings, "notify::font")],
            self._apply_behavior: [(self.settings, f"notify::{name}") for name in (
                "unlimited-scrollback", "scrollback-lines", "cursor-shape", "cursor-blink",
                "audible-bell")],
        }
        if self._interface:
            self._appliers[self._apply_font].append((self._interface, "changed::monospace-font-name"))
        for apply in self._appliers:
            apply()

        # VTE >= 0.78 reports the OSC title as a terminal property; older
        # versions only have the (now deprecated) window-title-changed signal
        if hasattr(Vte, "TERMPROP_XTERM_TITLE"):
            self.connect(f"termprop-changed::{Vte.TERMPROP_XTERM_TITLE}", self._on_title)
        else:
            self.connect("window-title-changed", self._on_title)
        self.connect("child-exited", self._on_child_exited)
        self.command_log = CommandLog(
            self, lambda c: self.emit("command-finished", c.exit_code, c.seconds))
        self._add_clipboard_actions()
        self._add_links()
        self._add_file_drop()

        self._row_offset = None  # see row_offset()
        self.serial = next(self._serials)  # names the terminal in notifications
        self._pid = None
        # The shell's process group, seen at its first prompt: not the
        # spawned pid when the shell runs under a wrapper (bwrap, toolbox)
        self._shell_group = None
        env = dict(env or {})
        if not argv:  # the user's shell: integration.bash reads Aiterm's prompt from here
            env.setdefault("AITERM_PROMPT_FILE", prompt.prompt_file())
        self._spawn(cwd or GLib.get_home_dir(), argv or shell_command(), env)

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
            "previous-prompt": lambda *_: self.scroll_to_prompt(older=True),
            "next-prompt": lambda *_: self.scroll_to_prompt(older=False),
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
        self._menu_command = None
        for name, callback in {
            "open-link": lambda *_: self.open_link(self._menu_link),
            "copy-link": lambda *_: self.get_clipboard().set(self._menu_link),
            "copy-output": lambda *_: self.get_clipboard().set(self._menu_command.output),
        }.items():
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self.actions.add_action(action)

        self.set_context_menu_model(self.context_menu_at(None, None))
        self.connect("setup-context-menu", self._on_setup_context_menu)

    def _add_file_drop(self):
        """Dropping files (from Files, a browser download bar…) types their
        paths, quoted for the shell, like other GNOME terminals."""
        drop = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop.connect("drop", lambda _target, files, *_: self.paste_files(files.get_files()))
        self.add_controller(drop)

    def paste_files(self, files):
        paths = [f.get_path() or f.get_uri() for f in files]
        if not paths:
            return False
        self.paste_text(" ".join(shlex.quote(p) for p in paths) + " ")
        self.grab_focus()
        return True

    def link_at(self, x, y):
        """The link under widget coordinates x, y, or None."""
        return self.check_hyperlink_at(x, y) or self.check_match_at(x, y)[0]

    def open_link(self, uri):
        if uri:
            Gtk.UriLauncher.new(uri).launch(self.get_root(), None, None, None)

    def scroll_to_prompt(self, older):
        """Scrolls the previous / next prompt to the top of the view."""
        offset = self.row_offset()
        top = self.get_vadjustment().get_value() + offset
        rows = self.command_log.prompt_rows
        targets = [r for r in rows if r < top] if older else [r for r in rows if r > top]
        if targets:
            self.get_vadjustment().set_value((max(targets) if older else min(targets)) - offset)

    def row_offset(self):
        """VTE numbers rows from the start of the scrollback (the cursor, the
        text, the command log), but scrolls in rows counted from the oldest
        row it still keeps: the two part ways once old rows are dropped
        (`clear`, a full scrollback). This is the difference, found by
        matching the visible text against the rows the cursor allows."""
        rows = self.get_row_count()
        adjustment = self.get_vadjustment()
        value = round(adjustment.get_value())
        # The screen's top in scroll rows; the cursor is on the screen
        screen_top = round(adjustment.get_upper()) - rows
        _, cursor = self.get_cursor_position()
        visible = self.get_text_format(Vte.Format.TEXT)
        candidates = [self._row_offset] + [cursor - row - screen_top for row in range(rows - 1, -1, -1)]
        for offset in candidates:
            if offset is None or offset < 0:
                continue
            text, _ = self.get_text_range_format(Vte.Format.TEXT, value + offset, 0, value + offset + rows, 0)
            if text == visible:
                self._row_offset = offset
                return offset
        return self._row_offset or 0

    def top_row(self):
        """The row (counted from the start of the scrollback) at the top of the view."""
        return round(self.get_vadjustment().get_value()) + self.row_offset()

    def row_at(self, y):
        """The terminal row (counted from the start of the scrollback) at widget y."""
        return int(self.top_row() + (y - PADDING_Y) // self.get_char_height())

    def context_menu_at(self, x, y):
        """The right-click menu, with link items when (x, y) is on a link and
        Copy Output when it is on a command."""
        self._menu_link = self.link_at(x, y) if x is not None else None
        self._menu_command = self.command_log.command_at_row(self.row_at(y)) if y is not None else None
        menu = Gio.Menu()
        if self._menu_link:
            link = Gio.Menu()
            link.append("Open Link", "term.open-link")
            link.append("Copy Link", "term.copy-link")
            menu.append_section(None, link)
        if self._menu_command and self._menu_command.output:
            command = Gio.Menu()
            command.append("Copy Output", "term.copy-output")
            menu.append_section(None, command)
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
        group = self._foreground_group()
        if group is None or group in (self._pid, self._shell_group):
            return None
        try:
            with open(f"/proc/{group}/comm") as f:
                return f.read().strip()
        except OSError:
            return "a program"

    def _foreground_group(self):
        pty = self.get_pty()
        if not pty or not self._pid:
            return None
        try:
            return os.tcgetpgrp(pty.get_fd())
        except OSError:
            return None

    def remember_shell_group(self):
        """Called by the command log at a prompt, when the shell itself is in
        the foreground."""
        if self._shell_group is None:
            self._shell_group = self._foreground_group()

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

    def _spawn(self, cwd, argv, env):
        environment = dict(os.environ)
        for key, value in env.items():
            if value is None:
                environment.pop(key, None)
            else:
                environment[key] = value
        self.spawn_async(
            Vte.PtyFlags.DEFAULT,
            cwd,
            argv,
            [f"{k}={v}" for k, v in environment.items()],  # VTE adds TERM and friends
            GLib.SpawnFlags.DEFAULT,
            None, None,  # child setup
            -1,  # no timeout
            None,  # cancellable
            self._on_spawned,
        )

    def _on_spawned(self, _terminal, pid, error, *_):
        self._pid = pid if pid > 0 else None
        if error:
            self.feed(f"aiterm: could not start the program: {error.message}\r\n".encode())

    def _on_child_exited(self, *_):
        self._pid = None
        self.emit("exited")

    def do_snapshot(self, snapshot):
        Vte.Terminal.do_snapshot(self, snapshot)
        self.effects.draw(snapshot, lambda s: Vte.Terminal.do_snapshot(self, s))

    def _on_title(self, *_):
        self.emit("title-changed", self.title())

    # The style manager, settings and GNOME's interface settings outlive the
    # tab: follow them only while the terminal is in a window, so a closed tab
    # leaves no handlers behind
    def do_root(self):
        Vte.Terminal.do_root(self)
        for apply, sources in self._appliers.items():
            apply()  # catch up on changes made while out of a window
            for source, signal in sources:
                self._handlers.append((source, source.connect(signal, lambda *_, f=apply: f())))

    def do_unroot(self):
        for source, handler in self._handlers:
            source.disconnect(handler)
        self._handlers = []
        Vte.Terminal.do_unroot(self)

    def _colors_for(self, dark):
        palette = PALETTES[self.settings.palette]
        fg, bg = palette.dark if dark else palette.light
        return _rgba(fg), _rgba(bg), [_rgba(c) for c in palette.colors]

    def _apply_colors(self):
        """Sets the palette's colors; a change is animated (effects.py)."""
        new = self._colors_for(Adw.StyleManager.get_default().get_dark())
        old, self._colors = self._colors, new
        if old is None or _same_colors(old, new) or not self.effects.blend_colors(old, new, self._set_colors):
            self._set_colors(new)

    def _set_colors(self, colors):
        fg, bg, palette = colors
        self.set_colors(fg, bg, palette)

    def show_color_change(self):
        """Moves to the other light/dark style's colors and back (a preview)."""
        other = self._colors_for(not Adw.StyleManager.get_default().get_dark())
        self.effects.blend_colors(self._colors, other, self._set_colors, force=True)

        def back():
            self.effects.blend_colors(other, self._colors, self._set_colors, force=True)
            return GLib.SOURCE_REMOVE
        GLib.timeout_add(1000, back)

    def palette_color(self, index):
        """One of the palette's 16 colors, e.g. 1 for red, 2 for green."""
        return _rgba(PALETTES[self.settings.palette].colors[index])

    def _apply_behavior(self):
        s = self.settings
        self.set_scrollback_lines(-1 if s.unlimited_scrollback else s.scrollback_lines)
        self.set_cursor_shape(CURSOR_SHAPES.get(s.cursor_shape, Vte.CursorShape.BLOCK))
        self.set_cursor_blink_mode(Vte.CursorBlinkMode.SYSTEM if s.cursor_blink else Vte.CursorBlinkMode.OFF)
        self.set_audible_bell(s.audible_bell)

    @staticmethod
    def _interface_settings():
        # The schema exists on GNOME; elsewhere VTE's default font is used
        source = Gio.SettingsSchemaSource.get_default()
        if source and source.lookup(INTERFACE_SCHEMA, True):
            return Gio.Settings.new(INTERFACE_SCHEMA)
        return None

    @classmethod
    def system_font(cls):
        """GNOME's monospace font, e.g. "Ubuntu Sans Mono 11", or None."""
        interface = cls._interface_settings()
        return interface.get_string("monospace-font-name") if interface else None

    def _apply_font(self):
        if not self.settings.use_system_font:
            name = self.settings.font
        elif self._interface:
            name = self._interface.get_string("monospace-font-name")
        else:
            name = None  # VTE's default
        self.set_font(Pango.FontDescription.from_string(name) if name else None)
