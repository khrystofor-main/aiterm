"""Find in the terminal's scrollback: a search bar under the header."""

from gi.repository import Adw, GLib, Gtk, Vte

from aiterm import animations

# PCRE2 compile flags (VTE uses PCRE2; the constants are not in the GIR)
PCRE2_CASELESS = 0x00000008
PCRE2_MULTILINE = 0x00000400


class SearchBar(Gtk.SearchBar):
    """Searches the terminal returned by `get_terminal()`. Typing jumps to the
    newest match; Enter / Shift+Enter go to older / newer matches."""

    def __init__(self, get_terminal):
        super().__init__(show_close_button=True)
        self._get_terminal = get_terminal

        self.entry = Gtk.SearchEntry(placeholder_text="Find in terminal", hexpand=True)
        self.entry.connect("search-changed", lambda *_: self.apply())
        # Older output is above, so "next" goes up the scrollback
        self.entry.connect("activate", lambda *_: self.find(older=True))
        self.entry.connect("next-match", lambda *_: self.find(older=True))
        self.entry.connect("previous-match", lambda *_: self.find(older=False))
        # Capture phase: before the entry turns Shift+Enter into "activate"
        shift_enter = Gtk.ShortcutController(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        shift_enter.add_shortcut(Gtk.Shortcut(
            trigger=Gtk.ShortcutTrigger.parse_string("<Shift>Return"),
            action=Gtk.CallbackAction.new(lambda *_: self.find(older=False) or True),
        ))
        self.entry.add_controller(shift_enter)

        self.match_case = Gtk.ToggleButton(label="Aa", tooltip_text="Match Case")
        self.regex = Gtk.ToggleButton(label=".*", tooltip_text="Regular Expression")
        for toggle in (self.match_case, self.regex):
            toggle.connect("toggled", lambda *_: self.apply())
        up = Gtk.Button(icon_name="go-up-symbolic", tooltip_text="Older Match (Enter)")
        up.connect("clicked", lambda *_: self.find(older=True))
        down = Gtk.Button(icon_name="go-down-symbolic", tooltip_text="Newer Match (Shift+Enter)")
        down.connect("clicked", lambda *_: self.find(older=False))

        box = Gtk.Box(spacing=6)
        for widget in (self.entry, self.match_case, self.regex, up, down):
            box.append(widget)
        self.set_child(Adw.Clamp(child=box, maximum_size=640))
        self.connect_entry(self.entry)
        self.connect("notify::search-mode-enabled", lambda *_: self._on_toggled())

    def open(self):
        self._time_transition()
        self.set_search_mode(True)
        self.entry.grab_focus()
        self.entry.select_region(0, -1)

    def apply(self):
        """Sets the search on the current terminal and jumps to the newest match."""
        terminal = self._get_terminal()
        if not terminal:
            return
        self.entry.remove_css_class("error")
        text = self.entry.get_text()
        if not text or not self.get_search_mode():
            terminal.search_set_regex(None, 0)
            return
        pattern = text if self.regex.get_active() else GLib.Regex.escape_string(text, -1)
        flags = PCRE2_MULTILINE | (0 if self.match_case.get_active() else PCRE2_CASELESS)
        try:
            regex = Vte.Regex.new_for_search(pattern, -1, flags)
        except GLib.Error:
            self.entry.add_css_class("error")  # a regex that does not compile
            terminal.search_set_regex(None, 0)
            return
        terminal.search_set_regex(regex, 0)
        terminal.search_set_wrap_around(True)
        terminal.unselect_all()
        self.find(older=True)

    def find(self, older):
        terminal = self._get_terminal()
        if not terminal or not terminal.search_get_regex():
            return False
        found = terminal.search_find_previous() if older else terminal.search_find_next()
        if found:
            self.entry.remove_css_class("error")
        else:
            self.entry.add_css_class("error")
        return found

    def _time_transition(self):
        """The bar slides down in its revealer; the preset times it."""
        revealer = self.get_first_child()
        if isinstance(revealer, Gtk.Revealer):
            revealer.set_transition_duration(animations.duration(self, "search"))

    def _on_toggled(self):
        self._time_transition()  # for closing, too
        if self.get_search_mode():
            return
        # Closed (Escape or the close button): drop the highlight, back to typing
        terminal = self._get_terminal()
        if terminal:
            terminal.search_set_regex(None, 0)
            terminal.grab_focus()
