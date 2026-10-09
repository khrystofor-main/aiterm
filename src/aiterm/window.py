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


def confirm_dialog(heading, body, close_label):
    """Cancel / <close_label>; responds "close" or "cancel"."""
    dialog = Adw.AlertDialog(heading=heading, body=body)
    dialog.add_response("cancel", "Cancel")
    dialog.add_response("close", close_label)
    dialog.set_response_appearance("close", Adw.ResponseAppearance.DESTRUCTIVE)
    dialog.set_default_response("cancel")
    dialog.set_close_response("cancel")
    return dialog


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

        self._closing_confirmed = False
        self.connect("close-request", lambda *_: self._on_close_request())

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

    def terminals(self):
        pages = self.tabs.get_pages()
        return [pages.get_item(i).get_child().get_child() for i in range(pages.get_n_items())]

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
        program = page.get_child().get_child().running_program()
        if not program:
            self._finish_close_page(page, True)
            return True  # handled
        dialog = confirm_dialog(
            "Close Tab?", f"“{program}” is still running in this tab. Closing the tab stops it.",
            "Close Tab",
        )
        dialog.connect("response", lambda _d, response: self._finish_close_page(page, response == "close"))
        dialog.present(self)
        return True

    def _finish_close_page(self, page, confirmed):
        self.tabs.close_page_finish(page, confirmed)
        if confirmed and self.tabs.get_n_pages() == 0:
            self.close()

    def _on_close_request(self):
        """Asks before closing a window with programs still running in it."""
        if self._closing_confirmed:
            return False
        running = [p for p in (t.running_program() for t in self.terminals()) if p]
        if not running:
            return False
        names = ", ".join(f"“{p}”" for p in running)
        dialog = confirm_dialog(
            "Close Window?", f"Still running: {names}. Closing the window stops them.",
            "Close Window",
        )
        dialog.connect("response", lambda _d, response: self._confirm_close_window(response == "close"))
        dialog.present(self)
        return True  # keep the window open for now

    def _confirm_close_window(self, confirmed):
        if confirmed:
            self._closing_confirmed = True
            self.close()
