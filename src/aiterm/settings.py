"""User preferences, stored as JSON in ~/.config/aiterm/settings.json.

One shared Settings object per process. Every preference is a GObject
property, so terminals and the preferences dialog follow changes through
notify:: signals, and each change is saved right away.
"""

import json
import os

from gi.repository import GLib, GObject

from aiterm.palettes import DEFAULT_PALETTE, PALETTES

CURSOR_SHAPES = ("block", "ibeam", "underline")
AGENT_VIEWS = ("terminal", "chat")
ANIMATIONS = ("auto", "on", "off")


def config_dir():
    # Tests point this somewhere else, so they never touch the user's file
    return os.environ.get("AITERM_CONFIG_DIR") or os.path.join(GLib.get_user_config_dir(), "aiterm")


class Settings(GObject.Object):
    use_system_font = GObject.Property(type=bool, default=True)
    font = GObject.Property(type=str, default="Monospace 11")
    palette = GObject.Property(type=str, default=DEFAULT_PALETTE)
    unlimited_scrollback = GObject.Property(type=bool, default=False)
    scrollback_lines = GObject.Property(type=int, default=10_000, minimum=100, maximum=1_000_000)
    cursor_shape = GObject.Property(type=str, default="block")
    cursor_blink = GObject.Property(type=bool, default=True)
    audible_bell = GObject.Property(type=bool, default=False)
    notify_long_commands = GObject.Property(type=bool, default=True)
    # "auto" follows GNOME's Reduce Animations until the user picks "on" or
    # "off"; the preset is a key of animations.Animations.presets
    animations = GObject.Property(type=str, default="auto")
    animation_preset = GObject.Property(type=str, default="subtle")
    # The agent's commands wait for Run in the approval bar (approval.py)
    approve_agent_commands = GObject.Property(type=bool, default=True)
    # agy's own interface in a terminal, or the app's chat (agent_panel.py)
    agent_view = GObject.Property(type=str, default="terminal")
    # Not in the dialog: the size of the last window closed, for the next one
    window_width = GObject.Property(type=int, default=960, minimum=200, maximum=20_000)
    window_height = GObject.Property(type=int, default=600, minimum=150, maximum=20_000)
    window_maximized = GObject.Property(type=bool, default=False)
    agent_panel_visible = GObject.Property(type=bool, default=False)
    agent_panel_width = GObject.Property(type=int, default=380, minimum=240, maximum=5_000)

    _instance = None

    @classmethod
    def get(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        super().__init__()
        self.path = os.path.join(config_dir(), "settings.json")
        self._load()
        self.connect("notify", lambda *_: self.save())

    def keys(self):
        return [p.name.replace("-", "_") for p in self.list_properties()]

    def _load(self):
        try:
            with open(self.path) as f:
                data = json.load(f)
        except (OSError, ValueError):
            return  # first run or a broken file: defaults
        for key in self.keys():
            if key in data:
                try:
                    self.set_property(key, data[key])
                except (TypeError, ValueError, OverflowError):
                    pass  # a hand-edited value of the wrong type
        if self.palette not in PALETTES:
            self.palette = DEFAULT_PALETTE
        if self.cursor_shape not in CURSOR_SHAPES:
            self.cursor_shape = "block"
        if self.agent_view not in AGENT_VIEWS:
            self.agent_view = "terminal"
        if self.animations not in ANIMATIONS:
            self.animations = "auto"

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump({key: self.get_property(key) for key in self.keys()}, f, indent=2)
            f.write("\n")
        os.replace(tmp, self.path)  # never leave a half-written file
