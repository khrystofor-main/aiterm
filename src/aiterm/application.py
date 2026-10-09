"""The application object: one process, any number of windows."""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Vte", "3.91")

from gi.repository import Adw, Gdk, Gtk  # noqa: E402

from aiterm import APP_ID  # noqa: E402
from aiterm.window import Window  # noqa: E402

# Keeps text off the window edges
CSS = "vte-terminal { padding: 4px 8px; }"


class Application(Adw.Application):
    def __init__(self):
        # Adw.Application follows the system light/dark preference on its own
        super().__init__(application_id=APP_ID)

    def do_startup(self):
        Adw.Application.do_startup(self)
        css = Gtk.CssProvider()
        css.load_from_string(CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    def do_activate(self):
        # Launching the app again (menu, dock) opens another window in the same
        # process, like other GNOME terminals
        Window(application=self).present()


def main(argv):
    return Application().run(argv)
