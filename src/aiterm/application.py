"""The application object: one process, any number of windows."""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Vte", "3.91")

from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from aiterm import APP_ID, VERSION  # noqa: E402
from aiterm import approval, chat_view, diff_view, prompt, prompt_page, terminal, window  # noqa: E402
from aiterm.animations import Animations  # noqa: E402
from aiterm.dbus_api import TerminalApi  # noqa: E402
from aiterm.preferences import PreferencesDialog  # noqa: E402
from aiterm.settings import Settings  # noqa: E402
from aiterm.window import Window  # noqa: E402

# Keeps text off the window edges
CSS = f"vte-terminal {{ padding: {terminal.PADDING_Y}px {terminal.PADDING_X}px; }}"

# The Keyboard Shortcuts dialog: (section, [(title, action name or accel)]).
# Actions show the keys registered for them; Adw.TabView's own keys are listed
# as they are
SHORTCUTS_HELP = [
    ("Terminal", [
        ("Copy", "term.copy"),
        ("Paste", "term.paste"),
        ("Select All", "term.select-all"),
        ("Find", "win.find"),
        ("Previous Prompt", "term.previous-prompt"),
        ("Next Prompt", "term.next-prompt"),
    ]),
    ("Tabs", [
        ("New Tab", "win.new-tab"),
        ("Close Tab", "win.close-tab"),
        ("Next Tab", "<Control>Page_Down"),
        ("Previous Tab", "<Control>Page_Up"),
        ("Switch to Tab 1…9", "<Alt>1...9"),
        ("Move Tab Right", "<Control><Shift>Page_Down"),
        ("Move Tab Left", "<Control><Shift>Page_Up"),
    ]),
    ("Windows", [
        ("New Window", "app.new-window"),
        ("Switch Between Shell and Agent", "win.switch-to-agent"),
        ("Preferences", "app.preferences"),
    ]),
    ("View", [
        ("Zoom In", "win.zoom-in"),
        ("Zoom Out", "win.zoom-out"),
        ("Reset Zoom", "win.zoom-reset"),
    ]),
    ("Agent Chat", [
        ("Run or Apply the Agent's Request", "<Control>Return"),
        ("Don't Run or Reject It", "Escape"),
    ]),
]


class Application(Adw.Application):
    def __init__(self, application_id=APP_ID, **kwargs):
        # Adw.Application follows the system light/dark preference on its own.
        # Tests pass their own ID so they never reach a running Aiterm
        super().__init__(application_id=application_id, **kwargs)

    def do_startup(self):
        Adw.Application.do_startup(self)
        css = Gtk.CssProvider()
        css.load_from_string(CSS + approval.CSS + chat_view.CSS + diff_view.CSS + prompt_page.CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        # CSS animations timed by the animation preset, reloaded when it changes
        self.animated_css = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), self.animated_css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        Animations.get().connect("changed", lambda *_: self._load_animated_css())
        self._load_animated_css()
        # The shell prompt set up in Preferences → Prompt, read by every bash tab
        prompt.follow(Settings.get())
        # Terminals and windows catch these keys themselves (see shortcuts.py);
        # registering them here only shows them next to the items in menus
        for name, accel in terminal.SHORTCUTS.items():
            self.set_accels_for_action(f"term.{name}", [accel])
        for action, accels in window.SHORTCUTS.items():
            self.set_accels_for_action(action, accels.split("|"))

        for name, callback in {
            "new-window": self.new_window,
            "preferences": self.show_preferences,
            "animation-preferences": lambda: self.show_preferences("animations"),
            "shortcuts": self.show_shortcuts,
            "about": self.show_about,
        }.items():
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda *_, callback=callback: callback())
            self.add_action(action)
        # From a "command finished" notification: (window id, terminal serial)
        show = Gio.SimpleAction.new("show-terminal", GLib.VariantType.new("(uu)"))
        show.connect("activate", lambda _action, target: self.show_terminal(*target.unpack()))
        self.add_action(show)

    def _load_animated_css(self):
        values = Animations.get().values()
        self.animated_css.load_from_string(approval.animated_css(values) + chat_view.animated_css(values))

    def do_dbus_register(self, connection, object_path):
        # The agent's tools reach the terminals through this (see dbus_api.py)
        self.terminal_api = TerminalApi(self, connection, object_path)
        return Adw.Application.do_dbus_register(self, connection, object_path)

    def do_dbus_unregister(self, connection, object_path):
        self.terminal_api.unregister()
        Adw.Application.do_dbus_unregister(self, connection, object_path)

    def do_activate(self):
        # Launching the app again (menu, dock) opens another window in the same
        # process, like other GNOME terminals
        Window(application=self).present()

    def new_window(self):
        """Opens a window in the folder of the current tab."""
        current = self.get_active_window()
        terminal = current.current_terminal() if current else None
        Window(application=self, cwd=terminal.current_directory() if terminal else None).present()

    def show_terminal(self, window_id, serial):
        window = self.get_window_by_id(window_id)
        if window:
            window.show_terminal(serial)

    def show_preferences(self, page=None):
        dialog = PreferencesDialog()
        if page == "animations":
            dialog.set_visible_page(dialog.animations_page)
        dialog.present(self.get_active_window())

    def show_shortcuts(self):
        dialog = Adw.ShortcutsDialog()
        for title, items in SHORTCUTS_HELP:
            section = Adw.ShortcutsSection(title=title)
            for item_title, accel in items:
                if accel.startswith(("app.", "win.", "term.")):
                    section.add(Adw.ShortcutsItem.new_from_action(item_title, accel))
                else:
                    section.add(Adw.ShortcutsItem.new(item_title, accel))
            dialog.add(section)
        dialog.present(self.get_active_window())

    def show_about(self):
        Adw.AboutDialog(
            application_name="Aiterm",
            application_icon="utilities-terminal",
            version=VERSION,
            developer_name="Oleksandr Khrystofor",
            website="https://github.com/khrystofor-main/aiterm",
            issue_url="https://github.com/khrystofor-main/aiterm/issues",
            license_type=Gtk.License.MIT_X11,
            comments="An AI terminal for GNOME: your shell, and an agent that works in it in plain sight.",
        ).present(self.get_active_window())


def main(argv):
    return Application().run(argv)
