"""aiterm — an AI terminal for GNOME: the user's shell, and an agent that works in it."""

APP_ID = "io.github.khrystofor_main.Aiterm"
VERSION = "1.0.0"

# The terminal API on the session bus (dbus_api.py serves it, tools.py calls it)
TERMINAL_INTERFACE = "io.github.khrystofor_main.Aiterm.Terminal"
OBJECT_PATH = "/io/github/khrystofor_main/Aiterm"
