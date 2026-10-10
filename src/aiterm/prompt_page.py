"""Preferences → Prompt: Aiterm's own bash prompt (prompt.py). A switch turns
it on, a preview shows it in the terminal's palette, and a row per segment
turns it on or off, picks its color and moves it. User and Host can show
the user's own text instead."""

from gi.repository import Adw, GObject, Gtk

from aiterm import prompt
from aiterm.palettes import PALETTES
from aiterm.settings import PROMPT_SYMBOLS, Settings
from aiterm.terminal import PADDING_X, PADDING_Y

BOTH_WAYS = GObject.BindingFlags.BIDIRECTIONAL | GObject.BindingFlags.SYNC_CREATE
COLOR_KEYS = list(prompt.COLORS)

# The preview sits on the terminal's background: a class per palette and
# theme, loaded once with the app's CSS (reloading CSS restyles every widget)
def _css_class(name, dark):
    return f"prompt-preview-{name.lower()}-{'dark' if dark else 'light'}"


CSS = f".prompt-preview {{ border-radius: 12px; padding: {PADDING_Y + 6}px {PADDING_X + 6}px; }}" + "".join(
    f".{_css_class(name, dark)} {{ background: {(palette.dark if dark else palette.light)[1]}; }}"
    for name, palette in PALETTES.items() for dark in (False, True))


class SegmentRow(Adw.ActionRow):
    """One segment: a check to show it, its color, and up / down."""

    def __init__(self, page, item):
        super().__init__(title=prompt.SEGMENTS[item.key].label)
        self.item = item
        self.check = Gtk.CheckButton(active=item.enabled, valign=Gtk.Align.CENTER)
        self.check.connect("toggled", lambda b: page.change(item, enabled=b.get_active()))
        self.add_prefix(self.check)
        self.set_activatable_widget(self.check)
        self.color = Gtk.DropDown.new_from_strings([prompt.COLORS[c][0] for c in COLOR_KEYS])
        self.color.set_valign(Gtk.Align.CENTER)
        self.color.set_selected(COLOR_KEYS.index(item.color))
        self.color.connect("notify::selected",
                           lambda d, _: page.change(item, color=COLOR_KEYS[d.get_selected()]))
        self.add_suffix(self.color)
        self.up = Gtk.Button(icon_name="go-up-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Move Up")
        self.down = Gtk.Button(icon_name="go-down-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Move Down")
        for button, step in ((self.up, -1), (self.down, 1)):
            button.add_css_class("flat")
            button.connect("clicked", lambda _b, step=step: page.move(item, step))
            self.add_suffix(button)


class PromptPage(Adw.PreferencesPage):
    def __init__(self):
        super().__init__(title="Prompt", icon_name="utilities-terminal-symbolic")
        self.settings = settings = Settings.get()

        main = Adw.PreferencesGroup(
            description="Your own prompt from ~/.bashrc stays as it is and comes back when this is off. "
                        "Changes show at the next prompt of every tab. Bash only",
        )
        self.switch = Adw.SwitchRow(title="Use Aiterm's Prompt")
        settings.bind_property("custom-prompt", self.switch, "active", BOTH_WAYS)
        main.add(self.switch)
        self.preview = Gtk.Label(xalign=0, wrap=True, selectable=False, margin_top=12)
        main.add(self.preview)
        self.add(main)

        self.segments = Adw.PreferencesGroup(
            title="Segments", description="Branch, environment and exit code show only when there is one")
        self.rows = []
        self.add(self.segments)

        user_host = Adw.PreferencesGroup(
            description="Shown instead of user@host, emoji too. Empty: user@host")
        self.user_host_row = Adw.EntryRow(title="User and Host Text")
        settings.bind_property("prompt-user-host", self.user_host_row, "text", BOTH_WAYS)
        user_host.add(self.user_host_row)
        self.add(user_host)

        style = Adw.PreferencesGroup(title="Style")
        self.symbol_row = Adw.ComboRow(
            title="Symbol", model=Gtk.StringList.new([prompt.SYMBOLS[s][0] for s in PROMPT_SYMBOLS]))
        self.symbol_row.set_selected(PROMPT_SYMBOLS.index(settings.prompt_symbol))
        self.symbol_row.connect("notify::selected", lambda r, _: setattr(
            settings, "prompt_symbol", PROMPT_SYMBOLS[r.get_selected()]))
        style.add(self.symbol_row)
        self.two_lines_row = Adw.SwitchRow(title="Symbol on Its Own Line")
        settings.bind_property("prompt-two-lines", self.two_lines_row, "active", BOTH_WAYS)
        style.add(self.two_lines_row)
        bold = Adw.SwitchRow(title="Bold")
        settings.bind_property("prompt-bold", bold, "active", BOTH_WAYS)
        style.add(bold)
        self.add(style)

        for group in (self.segments, user_host, style):
            settings.bind_property("custom-prompt", group, "sensitive", GObject.BindingFlags.SYNC_CREATE)

        self._fill_segments()
        self.update_preview()
        # Settings and the style manager outlive the dialog
        self._handlers = []
        self.connect("realize", lambda *_: self._follow(True))
        self.connect("unrealize", lambda *_: self._follow(False))

    def _follow(self, on):
        for source, handler in self._handlers:
            source.disconnect(handler)
        self._handlers = []
        if on:
            sources = [(self.settings, f"notify::{name}") for name in prompt.PROPERTIES + ("palette",)]
            sources.append((Adw.StyleManager.get_default(), "notify::dark"))
            self._handlers = [(source, source.connect(signal, lambda *_: self.update_preview()))
                              for source, signal in sources]
            self.update_preview()

    def items(self):
        return prompt.parse_segments(self.settings.prompt_segments)

    def _fill_segments(self):
        for row in self.rows:
            self.segments.remove(row)
        items = self.items()
        self.rows = [SegmentRow(self, item) for item in items]
        for index, row in enumerate(self.rows):
            row.up.set_sensitive(index > 0)
            row.down.set_sensitive(index < len(self.rows) - 1)
            self.segments.add(row)

    def change(self, item, **fields):
        items = self.items()
        for current in items:
            if current.key == item.key:
                for name, value in fields.items():
                    setattr(current, name, value)
        self.settings.prompt_segments = prompt.format_segments(items)

    def move(self, item, step):
        items = self.items()
        index = [i.key for i in items].index(item.key)
        other = index + step
        if 0 <= other < len(items):
            items[index], items[other] = items[other], items[index]
            self.settings.prompt_segments = prompt.format_segments(items)
            self._fill_segments()

    def update_preview(self):
        settings = self.settings
        dark = Adw.StyleManager.get_default().get_dark()
        palette = PALETTES.get(settings.palette) or next(iter(PALETTES.values()))
        self.preview.set_css_classes(["monospace", "prompt-preview", _css_class(palette.name, dark)])
        self.preview.set_markup(prompt.preview_markup(
            settings.prompt_segments, settings.prompt_symbol, settings.prompt_two_lines,
            settings.prompt_bold, palette, dark, settings.prompt_user_host))
