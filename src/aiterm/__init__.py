"""aiterm — a GTK 4 terminal for GNOME, the base for the AI terminal."""

APP_ID = "io.github.khrystofor_main.Aiterm"
VERSION = "0.2.0.dev0"

# The terminal API on the session bus (dbus_api.py serves it, tools.py calls it)
TERMINAL_INTERFACE = "io.github.khrystofor_main.Aiterm.Terminal"
OBJECT_PATH = "/io/github/khrystofor_main/Aiterm"
