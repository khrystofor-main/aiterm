"""The main window: a header bar on top, terminal tabs below."""

from gi.repository import Adw, Gtk

from aiterm.terminal import Terminal

APP_NAME = "Aiterm"


class Window(Adw.ApplicationWindow):
    def __init__(self, **kwargs):
        super().__init__(default_width=960, default_height=600, title=APP_NAME, **kwargs)

        self.header_title = Adw.WindowTitle(title=APP_NAME)
        header = Adw.HeaderBar(title_widget=self.header_title)

        # Tabs are there from the start so new-tab and friends only add
        # actions; the tab bar hides itself while there is a single tab
        self.tabs = Adw.TabView()
        self.tabs.connect("notify::selected-page", lambda *_: self._sync_title())
        self.tabs.connect("close-page", self._on_close_page)
        tab_bar = Adw.TabBar(view=self.tabs, autohide=True)

        toolbar = Adw.ToolbarView(content=self.tabs)
        toolbar.add_top_bar(header)
        toolbar.add_top_bar(tab_bar)
        self.set_content(toolbar)

        self.add_tab()

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
