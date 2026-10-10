"""The Preferences dialog. Every row is bound to a Settings property, so
changes apply to open terminals at once and are saved right away."""

from gi.repository import Adw, GObject, Gtk, Pango

from aiterm.animations_page import AnimationsPage
from aiterm.palettes import PALETTES
from aiterm.prompt_page import PromptPage
from aiterm.settings import AGENT_VIEWS, CURSOR_SHAPES, Settings
from aiterm.terminal import Terminal

BOTH_WAYS = GObject.BindingFlags.BIDIRECTIONAL | GObject.BindingFlags.SYNC_CREATE
CURSOR_LABELS = {"block": "Block", "ibeam": "I-Beam", "underline": "Underline"}
VIEW_LABELS = {"terminal": "Terminal", "chat": "Chat"}
CONTENT_WIDTH = 780


class PreferencesDialog(Adw.PreferencesDialog):
    def __init__(self):
        # Wide enough for the five page names in the header: narrower, the
        # page switcher moves to a bar at the bottom
        super().__init__(title="Preferences", content_width=CONTENT_WIDTH)
        self.settings = settings = Settings.get()

        # Appearance
        colors = Adw.PreferencesGroup(title="Colors")
        self.palette_row = combo_row("Palette", list(PALETTES), settings, "palette")
        colors.add(self.palette_row)

        text = Adw.PreferencesGroup(title="Text")
        self.system_font_row = Adw.SwitchRow(title="Use System Font")
        settings.bind_property("use-system-font", self.system_font_row, "active", BOTH_WAYS)
        text.add(self.system_font_row)
        self.font_button = Gtk.FontDialogButton(
            dialog=Gtk.FontDialog(title="Terminal Font"), valign=Gtk.Align.CENTER,
            use_font=True, level=Gtk.FontLevel.FONT,
        )
        # While the system font is on, the button starts from it, so turning
        # it off keeps the same look until another font is picked
        start_font = settings.font
        if settings.use_system_font and Terminal.system_font():
            start_font = Terminal.system_font()
        self.font_button.set_font_desc(Pango.FontDescription.from_string(start_font))
        self.font_button.connect(
            "notify::font-desc",
            lambda b, _: setattr(settings, "font", b.get_font_desc().to_string()),
        )
        font_row = Adw.ActionRow(title="Font", activatable_widget=self.font_button)
        font_row.add_suffix(self.font_button)
        settings.bind_property("use-system-font", font_row, "sensitive",
                               GObject.BindingFlags.SYNC_CREATE | GObject.BindingFlags.INVERT_BOOLEAN)
        text.add(font_row)

        appearance = Adw.PreferencesPage(title="Appearance", icon_name="applications-graphics-symbolic")
        appearance.add(colors)
        appearance.add(text)
        self.add(appearance)

        self.prompt_page = PromptPage()
        self.add(self.prompt_page)

        # Behavior
        scrolling = Adw.PreferencesGroup(title="Scrollback")
        unlimited = Adw.SwitchRow(title="Unlimited Scrollback", subtitle="Keeps all output; uses more memory")
        settings.bind_property("unlimited-scrollback", unlimited, "active", BOTH_WAYS)
        scrolling.add(unlimited)
        self.lines_row = Adw.SpinRow.new_with_range(100, 1_000_000, 1000)
        self.lines_row.set_title("Lines to Keep")
        settings.bind_property("scrollback-lines", self.lines_row, "value", BOTH_WAYS)
        settings.bind_property("unlimited-scrollback", self.lines_row, "sensitive",
                               GObject.BindingFlags.SYNC_CREATE | GObject.BindingFlags.INVERT_BOOLEAN)
        scrolling.add(self.lines_row)

        cursor = Adw.PreferencesGroup(title="Cursor")
        cursor.add(combo_row("Shape", list(CURSOR_SHAPES), settings, "cursor_shape", CURSOR_LABELS))
        blink = Adw.SwitchRow(title="Blink", subtitle="Follows the system setting when on")
        settings.bind_property("cursor-blink", blink, "active", BOTH_WAYS)
        cursor.add(blink)

        sound = Adw.PreferencesGroup(title="Sound and Notifications")
        bell = Adw.SwitchRow(title="Terminal Bell")
        settings.bind_property("audible-bell", bell, "active", BOTH_WAYS)
        sound.add(bell)
        notify = Adw.SwitchRow(
            title="Notify When a Long Command Finishes",
            subtitle="For commands over 10 seconds in a tab or window you are not looking at",
        )
        settings.bind_property("notify-long-commands", notify, "active", BOTH_WAYS)
        sound.add(notify)

        behavior = Adw.PreferencesPage(title="Behavior", icon_name="preferences-system-symbolic")
        for group in (scrolling, cursor, sound):
            behavior.add(group)
        self.add(behavior)

        # Agent
        commands = Adw.PreferencesGroup(
            title="Commands",
            description="The agent runs commands in your terminal, where you see them and their output.",
        )
        self.approve_row = Adw.SwitchRow(
            title="Ask Before the Agent Runs a Command",
            subtitle="Each command waits for Run above the terminal. Commands with sudo always ask for "
                     "your password",
        )
        settings.bind_property("approve-agent-commands", self.approve_row, "active", BOTH_WAYS)
        commands.add(self.approve_row)
        files = Adw.PreferencesGroup(
            title="Files",
            description="The agent changes files through Aiterm, which shows each change as a diff.",
        )
        self.approve_edits_row = Adw.SwitchRow(
            title="Ask Before the Agent Changes a File",
            subtitle="Each change waits for Apply. Changes agy makes with its own tools are shown but not asked",
        )
        settings.bind_property("approve-agent-edits", self.approve_edits_row, "active", BOTH_WAYS)
        files.add(self.approve_edits_row)
        panel = Adw.PreferencesGroup(title="Panel")
        self.view_row = combo_row("View", list(AGENT_VIEWS), settings, "agent_view", VIEW_LABELS)
        self.view_row.set_subtitle("Terminal: agy's own interface, with its commands and settings. "
                                   "Chat: messages and command blocks drawn by Aiterm")
        panel.add(self.view_row)
        agent = Adw.PreferencesPage(title="Agent", icon_name="chat-message-new-symbolic")
        agent.add(panel)
        agent.add(commands)
        agent.add(files)
        self.add(agent)

        self.animations_page = AnimationsPage()
        self.add(self.animations_page)


def combo_row(title, values, settings, key, labels=None):
    """An Adw.ComboRow that picks one of `values` for settings.<key>."""
    row = Adw.ComboRow(title=title, model=Gtk.StringList.new([(labels or {}).get(v, v) for v in values]))
    current = settings.get_property(key)
    row.set_selected(values.index(current) if current in values else 0)
    row.connect("notify::selected", lambda r, _: settings.set_property(key, values[r.get_selected()]))
    return row
