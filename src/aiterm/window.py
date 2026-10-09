"""The main window: a header bar on top, terminal tabs below."""

from gi.repository import Adw, Gio, GLib, Gtk

from aiterm.agent_panel import AgentPanel
from aiterm.approval import ApprovalBar
from aiterm.search import SearchBar
from aiterm.settings import Settings
from aiterm.shortcuts import add_capture_shortcuts
from aiterm.terminal import Terminal

APP_NAME = "Aiterm"

# Action -> key. Switching tabs (Ctrl+PgUp/PgDn, Ctrl+Tab, Alt+1…9,
# Ctrl+Shift+PgUp/PgDn to move) comes with Adw.TabView
SHORTCUTS = {
    "win.new-tab": "<Control><Shift>t",
    "win.close-tab": "<Control><Shift>w",
    "app.new-window": "<Control><Shift>n",
    "win.find": "<Control><Shift>f",
    # Alt+Enter switches between the shell and the agent, as in the tmux version
    "win.switch-to-agent": "<Alt>Return",
    "app.preferences": "<Control>comma",
    "win.zoom-in": "<Control>plus|<Control>equal|<Control>KP_Add",
    "win.zoom-out": "<Control>minus|<Control>KP_Subtract",
    "win.zoom-reset": "<Control>0|<Control>KP_0",
}

# A command this long, finished out of sight, gets a desktop notification
LONG_COMMAND_SECONDS = 10

# Font zoom, for every tab of a window; new tabs get the window's zoom
ZOOM_STEP = 1.1
ZOOM_MIN, ZOOM_MAX = 0.5, 3.0

# Ctrl+Home/End belong to programs running in the terminal (editors, less)
TAB_VIEW_SHORTCUTS = Adw.TabViewShortcuts.ALL_SHORTCUTS & ~(
    Adw.TabViewShortcuts.CONTROL_HOME | Adw.TabViewShortcuts.CONTROL_END
    | Adw.TabViewShortcuts.CONTROL_SHIFT_HOME | Adw.TabViewShortcuts.CONTROL_SHIFT_END
)


def format_duration(seconds):
    minutes, seconds = divmod(round(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours} h {minutes} min"
    return f"{minutes} min {seconds} s" if minutes else f"{seconds} s"


def confirm_dialog(heading, body, close_label, on_answer):
    """Cancel / <close_label>; calls on_answer(True) for close, once."""
    dialog = Adw.AlertDialog(heading=heading, body=body)
    answered = []

    def on_response(_dialog, response):
        if not answered:  # closing the dialog can report a second "cancel"
            answered.append(response)
            on_answer(response == "close")

    dialog.connect("response", on_response)
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
    app.append("Preferences", "app.preferences")
    app.append("Keyboard Shortcuts", "app.shortcuts")
    app.append("About Aiterm", "app.about")
    menu.append_section(None, app)
    return menu


class Window(Adw.ApplicationWindow):
    def __init__(self, cwd=None, **kwargs):
        # Opens at the size of the last window closed
        settings = Settings.get()
        super().__init__(
            default_width=settings.window_width, default_height=settings.window_height,
            maximized=settings.window_maximized, title=APP_NAME, **kwargs,
        )

        self.header_title = Adw.WindowTitle(title=APP_NAME)
        header = Adw.HeaderBar(title_widget=self.header_title)
        header.pack_start(Gtk.Button(
            icon_name="tab-new-symbolic", action_name="win.new-tab", tooltip_text="New Tab",
        ))
        header.pack_end(Gtk.MenuButton(
            icon_name="open-menu-symbolic", menu_model=main_menu(), primary=True,
            tooltip_text="Main Menu",
        ))
        header.pack_end(Gtk.ToggleButton(
            icon_name="sidebar-show-right-symbolic", action_name="win.agent-panel",
            tooltip_text="Agent Panel",
        ))

        # The tab bar hides itself while there is a single tab
        self.tabs = Adw.TabView(shortcuts=TAB_VIEW_SHORTCUTS)
        self.tabs.connect("notify::selected-page", lambda *_: self._on_tab_selected())
        self.tabs.connect("close-page", self._on_close_page)
        tab_bar = Adw.TabBar(view=self.tabs, autohide=True)

        # Terminal on the left, the agent panel on the right; drag the border
        # to resize. The panel keeps its width when the window is resized
        self.agent_panel = AgentPanel(self)
        self.agent_panel.set_visible(settings.agent_panel_visible)
        # The agent's commands wait for the user's Run here, above the terminal
        self.approval = ApprovalBar()
        terminal_side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        terminal_side.append(self.approval)
        terminal_side.append(self.tabs)
        self.tabs.set_vexpand(True)
        self.paned = Gtk.Paned(
            start_child=terminal_side, end_child=self.agent_panel,
            resize_end_child=False, shrink_start_child=False, shrink_end_child=False,
        )
        self.paned.connect("notify::max-position", lambda *_: self._place_panel_border())
        self.paned.connect("notify::position", lambda *_: self._save_panel_width())

        toolbar = Adw.ToolbarView(content=self.paned)
        toolbar.add_top_bar(header)
        toolbar.add_top_bar(tab_bar)
        self.search = SearchBar(self.current_terminal)
        toolbar.add_top_bar(self.search)
        self.set_content(toolbar)

        actions = {
            "new-tab": self.new_tab,
            "close-tab": self.close_tab,
            "find": lambda: self.search.open(),
            "zoom-in": lambda: self.set_zoom(self.zoom * ZOOM_STEP),
            "zoom-out": lambda: self.set_zoom(self.zoom / ZOOM_STEP),
            "zoom-reset": lambda: self.set_zoom(1.0),
            "switch-to-agent": self.switch_to_agent,
        }
        for name, callback in actions.items():
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda *_, callback=callback: callback())
            self.add_action(action)
        panel = Gio.SimpleAction.new_stateful(
            "agent-panel", None, GLib.Variant.new_boolean(settings.agent_panel_visible))
        panel.connect("change-state", self._on_agent_panel_toggled)
        self.add_action(panel)
        add_capture_shortcuts(self, {
            trigger: (lambda name=name: self.activate_action(name))
            for name, trigger in SHORTCUTS.items()
        })

        self.zoom = 1.0
        self._closing_confirmed = False
        self.connect("close-request", lambda *_: self._on_close_request())

        self.add_tab(cwd)
        if self.agent_panel.get_visible():
            self.agent_panel.start()

    def new_tab(self):
        """Opens a tab in the folder of the current one, like Ptyxis."""
        current = self.current_terminal()
        self.add_tab(current.current_directory() if current else None)

    def close_tab(self):
        page = self.tabs.get_selected_page()
        if page:
            self.tabs.close_page(page)

    def set_zoom(self, zoom):
        self.zoom = round(min(max(zoom, ZOOM_MIN), ZOOM_MAX), 2)
        for terminal in self.terminals():
            terminal.set_font_scale(self.zoom)

    def add_tab(self, cwd=None):
        terminal = Terminal(cwd)
        terminal.set_font_scale(self.zoom)
        scroller = Gtk.ScrolledWindow(child=terminal, hscrollbar_policy=Gtk.PolicyType.NEVER)
        page = self.tabs.append(scroller)
        page.set_title("Terminal")
        terminal.connect("title-changed", self._on_terminal_title, page)
        terminal.connect("exited", lambda *_: self.tabs.close_page(page))
        terminal.connect("command-finished", self._on_command_finished, page)
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

    def switch_to_agent(self):
        """Alt+Enter: from the shell to the agent (opening the panel), and back."""
        if self.agent_panel.get_visible() and self.agent_panel.has_focus():
            terminal = self.current_terminal()
            if terminal:
                terminal.grab_focus()
            return
        if not self.agent_panel.get_visible():
            self.activate_action("win.agent-panel")
        self.agent_panel.focus()

    def _on_agent_panel_toggled(self, action, state):
        action.set_state(state)
        visible = state.get_boolean()
        self.agent_panel.set_visible(visible)
        Settings.get().agent_panel_visible = visible
        if visible:
            self._place_panel_border()
            self.agent_panel.start()
        else:
            terminal = self.current_terminal()
            if terminal:
                terminal.grab_focus()

    # The paned's position is the terminal's width; the panel takes the rest.
    # max-position changes with the paned's size, so it doubles as a resize
    # signal

    def _place_panel_border(self):
        """Gives the panel its saved width once the paned has a size."""
        width = self.paned.get_width()
        if self.agent_panel.get_visible() and width > 0:
            self._placing = True
            self.paned.set_position(width - Settings.get().agent_panel_width)
            self._placing = False

    def _save_panel_width(self):
        width = self.paned.get_width()
        if getattr(self, "_placing", False) or not self.agent_panel.get_visible() or width <= 0:
            return
        Settings.get().agent_panel_width = max(width - self.paned.get_position(), 240)

    def show_terminal(self, serial):
        """Selects the tab of the terminal with this serial and raises the window."""
        for terminal in self.terminals():
            if terminal.serial == serial:
                self.tabs.set_selected_page(self.tabs.get_page(terminal.get_parent()))
        self.present()

    def _on_command_finished(self, terminal, code, seconds, page):
        out_of_sight = not self.is_active() or self.tabs.get_selected_page() is not page
        if seconds < LONG_COMMAND_SECONDS or not out_of_sight or not Settings.get().notify_long_commands:
            return
        if self.tabs.get_selected_page() is not page:
            page.set_needs_attention(True)
        title = "Command finished" if code == 0 else f"Command failed (exit code {code})"
        notification = Gio.Notification.new(title)
        notification.set_body(f"{page.get_title()} · {format_duration(seconds)}")
        notification.set_default_action_and_target(
            "app.show-terminal", GLib.Variant("(uu)", (self.get_id(), terminal.serial)),
        )
        self.get_application().send_notification(f"command-{terminal.serial}", notification)

    def _on_tab_selected(self):
        page = self.tabs.get_selected_page()
        if page:
            page.set_needs_attention(False)
        self._sync_title()
        if self.search.get_search_mode():
            self.search.apply()
            return  # keep typing in the search entry
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
            "Close Tab", lambda confirmed: self._finish_close_page(page, confirmed),
        )
        dialog.present(self)
        return True

    def _finish_close_page(self, page, confirmed):
        self.tabs.close_page_finish(page, confirmed)
        if confirmed and self.tabs.get_n_pages() == 0:
            self.close()

    def _on_close_request(self):
        """Asks before closing a window with programs still running in it."""
        self._save_size()
        if self._closing_confirmed:
            return False
        running = [p for p in (t.running_program() for t in self.terminals()) if p]
        if not running:
            return False
        names = ", ".join(f"“{p}”" for p in running)
        dialog = confirm_dialog(
            "Close Window?", f"Still running: {names}. Closing the window stops them.",
            "Close Window", self._confirm_close_window,
        )
        dialog.present(self)
        return True  # keep the window open for now

    def _save_size(self):
        settings = Settings.get()
        settings.window_maximized = self.is_maximized()
        if not self.is_maximized():
            # GTK keeps the default size in step with the size the user sets
            width, height = self.get_default_size()
            settings.window_width, settings.window_height = width, height

    def _confirm_close_window(self, confirmed):
        if confirmed:
            self._closing_confirmed = True
            self.close()
