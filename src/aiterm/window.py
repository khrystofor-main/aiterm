"""The main window: a header bar on top, terminal tabs below."""

from gi.repository import Adw, Gio, Gtk

from aiterm.shortcuts import add_capture_shortcuts
from aiterm.terminal import Terminal

APP_NAME = "Aiterm"

# Action -> key. Switching tabs (Ctrl+PgUp/PgDn, Ctrl+Tab, Alt+1…9,
# Ctrl+Shift+PgUp/PgDn to move) comes with Adw.TabView
SHORTCUTS = {
    "win.new-tab": "<Control><Shift>t",
    "win.close-tab": "<Control><Shift>w",
    "app.new-window": "<Control><Shift>n",
}

# Ctrl+Home/End belong to programs running in the terminal (editors, less)
TAB_VIEW_SHORTCUTS = Adw.TabViewShortcuts.ALL_SHORTCUTS & ~(
    Adw.TabViewShortcuts.CONTROL_HOME | Adw.TabViewShortcuts.CONTROL_END
    | Adw.TabViewShortcuts.CONTROL_SHIFT_HOME | Adw.TabViewShortcuts.CONTROL_SHIFT_END
)


def main_menu():
    menu = Gio.Menu()
    windows = Gio.Menu()
    windows.append("New Window", "app.new-window")
    windows.append("New Tab", "win.new-tab")
    menu.append_section(None, windows)
    app = Gio.Menu()
    app.append("Keyboard Shortcuts", "app.shortcuts")
    app.append("About Aiterm", "app.about")
    menu.append_section(None, app)
    return menu


class Window(Adw.ApplicationWindow):
    def __init__(self, cwd=None, **kwargs):
        super().__init__(default_width=960, default_height=600, title=APP_NAME, **kwargs)

        self.header_title = Adw.WindowTitle(title=APP_NAME)
        header = Adw.HeaderBar(title_widget=self.header_title)
        header.pack_start(Gtk.Button(
            icon_name="tab-new-symbolic", action_name="win.new-tab", tooltip_text="New Tab",
        ))
        header.pack_end(Gtk.MenuButton(
            icon_name="open-menu-symbolic", menu_model=main_menu(), primary=True,
            tooltip_text="Main Menu",
        ))

        # The tab bar hides itself while there is a single tab
        self.tabs = Adw.TabView(shortcuts=TAB_VIEW_SHORTCUTS)
        self.tabs.connect("notify::selected-page", lambda *_: self._on_tab_selected())
        self.tabs.connect("close-page", self._on_close_page)
        tab_bar = Adw.TabBar(view=self.tabs, autohide=True)

        toolbar = Adw.ToolbarView(content=self.tabs)
        toolbar.add_top_bar(header)
        toolbar.add_top_bar(tab_bar)
        self.set_content(toolbar)

        actions = {
            "new-tab": self.new_tab,
            "close-tab": self.close_tab,
        }
        for name, callback in actions.items():
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda *_, callback=callback: callback())
            self.add_action(action)
        add_capture_shortcuts(self, {
            trigger: (lambda name=name: self.activate_action(name))
            for name, trigger in SHORTCUTS.items()
        })

        self.add_tab(cwd)

    def new_tab(self):
        """Opens a tab in the folder of the current one, like Ptyxis."""
        current = self.current_terminal()
        self.add_tab(current.current_directory() if current else None)

    def close_tab(self):
        page = self.tabs.get_selected_page()
        if page:
            self.tabs.close_page(page)

    def add_tab(self, cwd=None):
        terminal = Terminal(cwd)
        scroller = Gtk.ScrolledWindow(child=terminal, hscrollbar_policy=Gtk.PolicyType.NEVER)
        page = self.tabs.append(scroller)
        page.set_title("Terminal")
        terminal.connect("title-changed", self._on_terminal_title, page)
        terminal.connect("exited", lambda *_: self.tabs.close_page(page))
        self.tabs.set_selected_page(page)
        terminal.grab_focus()
        return terminal

    def current_terminal(self):
        page = self.tabs.get_selected_page()
        return page.get_child().get_child() if page else None

    def _on_terminal_title(self, _terminal, title, page):
        page.set_title(title or "Terminal")
        self._sync_title()

    def _on_tab_selected(self):
        self._sync_title()
        terminal = self.current_terminal()
        if terminal:
            terminal.grab_focus()

    def _sync_title(self):
        page = self.tabs.get_selected_page()
        title = page.get_title() if page else APP_NAME
        self.header_title.set_title(title)
        self.set_title(title)

    def _on_close_page(self, view, page):
        view.close_page_finish(page, True)
        if view.get_n_pages() == 0:
            self.close()
        return True  # handled
