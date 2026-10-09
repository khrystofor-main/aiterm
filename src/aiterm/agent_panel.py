"""The side panel where the agent lives. Empty in v0.2: v0.3 puts the agent
(agy) here, next to the terminal it reads and types into."""

from gi.repository import Adw, Gtk

MIN_WIDTH = 240


class AgentPanel(Adw.Bin):
    def __init__(self):
        super().__init__(width_request=MIN_WIDTH)
        self.add_css_class("view")
        self.set_child(Adw.StatusPage(
            icon_name="chat-message-new-symbolic",
            title="Agent",
            description="The AI agent will work here, next to your terminal. Coming in v0.3.",
            vexpand=True,
        ))
