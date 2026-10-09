"""Animation presets: every effect reads its numbers from here, so a new
preset needs no change to the effects.

A preset is a set of numbers per effect (on/off, duration, curve, intensity
and the effect's own ones) plus the commands whose exit code 1 is not a
failure. Two presets are built in and fixed; the user's own are JSON files in
~/.config/aiterm/animations/ that keep only what differs from a built-in one
(their `base`), so improvements to the built-ins reach them too:

    {
      "name": "Mine",
      "base": "subtle",
      "effects": {"shake": {"amplitude": 10, "cycles": 4}},
      "code_1_answers": ["grep", "diff", "test", "[", "cmp"]
    }

A broken file or an unknown name never stops the app: the preset falls back
to its base's values and carries a warning for the Preferences page.

Whether anything animates: the switch in Preferences, which until the user
flips it follows GNOME's Reduce Animations (`gtk-enable-animations`). In
power saver mode the subtle preset stands in for any other.
"""

import copy
import json
import math
import os
import re

from gi.repository import Adw, Gio, GLib, GObject, Gtk

from aiterm.settings import Settings, config_dir

# Curves: t in 0..1 -> progress in 0..1 (a spring overshoots a little)


def linear(t):
    return t


def ease_out_cubic(t):
    return 1 - (1 - t) ** 3


def ease_in_out_cubic(t):
    return 4 * t ** 3 if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2


def spring(t, damping=6.0, frequency=1.6):
    """A damped spring released at 0, settling on 1."""
    if t >= 1:
        return 1.0
    return 1 - math.exp(-damping * t) * math.cos(2 * math.pi * frequency * t)


CURVES = {
    "linear": linear,
    "ease-out": ease_out_cubic,
    "ease-in-out": ease_in_out_cubic,
    "spring": spring,
}


def progress(curve, t):
    """The curve's value at t, with t clamped to 0..1."""
    return CURVES.get(curve, ease_out_cubic)(min(max(t, 0.0), 1.0))


class Param:
    """One number (or choice) of an effect: its type and the values allowed."""

    def __init__(self, kind, low=None, high=None, choices=None):
        self.kind, self.low, self.high, self.choices = kind, low, high, choices

    def check(self, value):
        """The value if it is valid (numbers clamped to the range), else None."""
        if self.kind is bool:
            return value if isinstance(value, bool) else None
        if self.kind is str:
            if not isinstance(value, str):
                return None
            if self.choices is not None and value not in self.choices:
                return None
            if self.choices is None and not re.fullmatch(r"accent|success|error|#[0-9a-fA-F]{6}", value):
                return None  # a color: the theme's accent, the palette's green or red, or #rrggbb
            return value
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return min(max(self.kind(value), self.low), self.high)


COMMON = {
    "enabled": Param(bool),
    "duration": Param(int, 50, 5000),  # ms
    "curve": Param(str, choices=tuple(CURVES)),
    "intensity": Param(float, 0.0, 2.0),  # scales the effect's size or opacity
}


class Effect:
    def __init__(self, title, subtitle, **params):
        self.title, self.subtitle = title, subtitle
        self.params = {**COMMON, **params}


# Every effect, in the order of the Preferences page. Labels are the page's
EFFECTS = {
    "shake": Effect(
        "Shake on Failure", "A failed command's line shakes sideways",
        amplitude=Param(float, 0, 40),  # px at intensity 1
        cycles=Param(int, 1, 10),
    ),
    "stripe": Effect(
        "New Output Stripe", "A stripe on the left of new output, fading away",
        width=Param(int, 1, 8),  # px
        alpha=Param(float, 0.05, 1.0),  # at intensity 1
        color=Param(str),  # while the command runs; then green or red by its exit code
        flood=Param(int, 100, 100_000),  # lines/s above which output gets no stripe
    ),
    "fail_mark": Effect(
        "Failure Mark", "✗ and the exit code slide in at the end of a failed command's line",
        distance=Param(float, 0, 200),  # px it slides in from, at intensity 1
        color=Param(str),
    ),
    "theme": Effect(
        "Color Changes", "Terminal colors blend into a new palette or light/dark style",
    ),
    "panel": Effect(
        "Agent Panel", "The panel slides in and out; the terminal resizes once, at the end",
    ),
    "search": Effect("Find Bar", "The find bar slides down from the top"),
    "approval": Effect(
        "Command Approval", "The bar asking to run the agent's command slides in, and Run pulses while it waits",
        pulse=Param(int, 300, 10_000),  # ms per pulse of the Run button
    ),
}

BUILTIN = {
    "subtle": {
        "name": "Subtle",
        "effects": {
            "shake": {"enabled": True, "duration": 250, "curve": "ease-out", "intensity": 1.0,
                      "amplitude": 5, "cycles": 2},
            "stripe": {"enabled": True, "duration": 900, "curve": "ease-out", "intensity": 1.0,
                       "width": 3, "alpha": 0.8, "color": "accent", "flood": 1000},
            "fail_mark": {"enabled": True, "duration": 200, "curve": "ease-out", "intensity": 1.0,
                          "distance": 16, "color": "error"},
            "theme": {"enabled": True, "duration": 300, "curve": "ease-in-out", "intensity": 1.0},
            "panel": {"enabled": True, "duration": 220, "curve": "ease-out", "intensity": 1.0},
            "search": {"enabled": True, "duration": 180, "curve": "ease-out", "intensity": 1.0},
            "approval": {"enabled": True, "duration": 200, "curve": "ease-out", "intensity": 1.0,
                         "pulse": 1800},
        },
        "code_1_answers": ["grep", "diff", "test", "["],
    },
    "expressive": {
        "name": "Expressive",
        "effects": {
            "shake": {"enabled": True, "duration": 450, "curve": "spring", "intensity": 1.0,
                      "amplitude": 9, "cycles": 3},
            "stripe": {"enabled": True, "duration": 1500, "curve": "ease-in-out", "intensity": 1.0,
                       "width": 4, "alpha": 1.0, "color": "accent", "flood": 1000},
            "fail_mark": {"enabled": True, "duration": 400, "curve": "spring", "intensity": 1.0,
                          "distance": 36, "color": "error"},
            "theme": {"enabled": True, "duration": 500, "curve": "ease-in-out", "intensity": 1.0},
            "panel": {"enabled": True, "duration": 420, "curve": "spring", "intensity": 1.0},
            "search": {"enabled": True, "duration": 320, "curve": "ease-out", "intensity": 1.0},
            "approval": {"enabled": True, "duration": 350, "curve": "spring", "intensity": 1.0,
                         "pulse": 1200},
        },
        "code_1_answers": ["grep", "diff", "test", "["],
    },
}
DEFAULT_PRESET = "subtle"


class Preset:
    """A built-in preset, or the user's own on top of one."""

    def __init__(self, key, name, base, overrides=None, path=None, warnings=()):
        self.key, self.name, self.base = key, name, base
        self.overrides = overrides or {}  # {"effects": {...}, "code_1_answers": [...]}
        self.path = path
        self.warnings = list(warnings)

    @property
    def builtin(self):
        return self.path is None

    def values(self):
        """The full set of numbers: the base's, with this preset's on top."""
        values = copy.deepcopy(BUILTIN[self.base])
        for effect, params in self.overrides.get("effects", {}).items():
            values["effects"][effect].update(params)
        if "code_1_answers" in self.overrides:
            values["code_1_answers"] = list(self.overrides["code_1_answers"])
        return values

    def to_json(self):
        data = {"name": self.name, "base": self.base}
        data.update(copy.deepcopy(self.overrides))
        return data


def parse_preset(key, data, path):
    """A user preset from its JSON data. Anything wrong (not an object, an
    unknown base, effect or parameter, a value of the wrong type) is dropped
    with a warning; the rest still applies."""
    warnings = []
    if not isinstance(data, dict):
        return Preset(key, key, DEFAULT_PRESET, path=path, warnings=["The file is not a JSON object"])
    name = data.get("name") if isinstance(data.get("name"), str) and data.get("name").strip() else key
    base = data.get("base", DEFAULT_PRESET)
    if base not in BUILTIN:
        warnings.append(f"Unknown base preset “{base}”, using “{DEFAULT_PRESET}”")
        base = DEFAULT_PRESET
    overrides = {"effects": {}}
    effects = data.get("effects", {})
    if not isinstance(effects, dict):
        warnings.append("“effects” is not an object")
        effects = {}
    for effect, params in effects.items():
        if effect not in EFFECTS:
            warnings.append(f"Unknown effect “{effect}”")
            continue
        if not isinstance(params, dict):
            warnings.append(f"“{effect}” is not an object")
            continue
        for param, value in params.items():
            spec = EFFECTS[effect].params.get(param)
            if spec is None:
                warnings.append(f"Unknown parameter “{effect}.{param}”")
                continue
            checked = spec.check(value)
            if checked is None:
                warnings.append(f"Bad value for “{effect}.{param}”: {json.dumps(value)}")
                continue
            overrides["effects"].setdefault(effect, {})[param] = checked
    if not overrides["effects"]:
        del overrides["effects"]
    if "code_1_answers" in data:
        answers = data["code_1_answers"]
        if isinstance(answers, list) and all(isinstance(a, str) for a in answers):
            overrides["code_1_answers"] = answers
        else:
            warnings.append("“code_1_answers” is not a list of command names")
    for extra in set(data) - {"name", "base", "effects", "code_1_answers"}:
        warnings.append(f"Unknown key “{extra}”")
    return Preset(key, name, base, overrides, path, warnings)


def load_preset_file(path):
    key = os.path.splitext(os.path.basename(path))[0]
    try:
        with open(path) as f:
            data = json.load(f)
    except OSError as e:
        return Preset(key, key, DEFAULT_PRESET, path=path, warnings=[f"Cannot read the file: {e.strerror}"])
    except ValueError as e:
        return Preset(key, key, DEFAULT_PRESET, path=path, warnings=[f"The file is not valid JSON: {e}"])
    return parse_preset(key, data, path)


def presets_dir():
    return os.path.join(config_dir(), "animations")


def file_key(name):
    """A file name for a preset name: "My Preset" -> "my-preset"."""
    key = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return key or "preset"


class Animations(GObject.Object):
    """The current preset's numbers for the effects; one per process.
    Emits `changed` when anything an effect reads may have changed."""

    __gsignals__ = {"changed": (GObject.SignalFlags.RUN_FIRST, None, ())}
    _instance = None

    @classmethod
    def get(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        super().__init__()
        self.settings = Settings.get()
        self.presets = {}
        self.reload()
        changed = lambda *_: self.emit("changed")
        self.settings.connect("notify::animations", changed)
        self.settings.connect("notify::animation-preset", changed)
        gtk_settings = Gtk.Settings.get_default()
        if gtk_settings:
            gtk_settings.connect("notify::gtk-enable-animations", changed)
        self._power = Gio.PowerProfileMonitor.dup_default()
        self._power.connect("notify::power-saver-enabled", changed)
        self._monitor = None
        self._watch()

    # Which presets exist

    def reload(self):
        presets = {key: Preset(key, data["name"], key) for key, data in BUILTIN.items()}
        try:
            names = sorted(os.listdir(presets_dir()))
        except OSError:
            names = []
        for name in names:
            if name.endswith(".json"):
                preset = load_preset_file(os.path.join(presets_dir(), name))
                if preset.key not in BUILTIN:
                    presets[preset.key] = preset
        self.presets = presets
        self.emit("changed")

    def _watch(self):
        """Edits made to the files by hand apply at once."""
        os.makedirs(presets_dir(), exist_ok=True)
        self._monitor = Gio.File.new_for_path(presets_dir()).monitor_directory(Gio.FileMonitorFlags.NONE, None)
        self._monitor.connect("changed", lambda *_: self._reload_soon())
        self._reload_pending = False

    def _reload_soon(self):
        # An editor's save comes as several events: reload once they settle
        if not self._reload_pending:
            self._reload_pending = True
            GLib.timeout_add(200, self._reload_now)

    def _reload_now(self):
        self._reload_pending = False
        self.reload()
        return GLib.SOURCE_REMOVE

    # The current one

    @property
    def preset(self):
        """The preset picked in Preferences (a missing one: the default)."""
        return self.presets.get(self.settings.animation_preset) or self.presets[DEFAULT_PRESET]

    @property
    def enabled(self):
        choice = self.settings.animations
        if choice == "auto":
            gtk_settings = Gtk.Settings.get_default()
            return bool(gtk_settings is None or gtk_settings.get_property("gtk-enable-animations"))
        return choice == "on"

    @property
    def power_saver(self):
        return self._power.get_power_saver_enabled()

    def values(self):
        """The numbers the effects use now."""
        if self.power_saver:
            return Preset(DEFAULT_PRESET, "", DEFAULT_PRESET).values()
        return self.preset.values()

    def effect(self, name, force=False):
        """The numbers of one effect, or None when it should not run. With
        `force` (a preview), also when it is off."""
        params = self.values()["effects"][name]
        if not force and not (self.enabled and params["enabled"]):
            return None
        return params

    def code_1_answers(self):
        return self.values()["code_1_answers"]

    # The user's own presets

    def duplicate(self, key):
        """Saves a copy of a preset under a new name and picks it."""
        source = self.presets[key]
        name = self._free_name(f"{source.name} Copy")
        new_key = self._free_key(file_key(name))
        preset = Preset(new_key, name, source.base, copy.deepcopy(source.overrides),
                        os.path.join(presets_dir(), new_key + ".json"))
        self.save(preset)
        self.presets[new_key] = preset
        self.settings.animation_preset = new_key
        self.emit("changed")
        return preset

    def delete(self, key):
        preset = self.presets.get(key)
        if preset is None or preset.builtin:
            return
        try:
            os.remove(preset.path)
        except OSError:
            pass
        del self.presets[key]
        if self.settings.animation_preset == key:
            self.settings.animation_preset = preset.base
        self.emit("changed")

    def rename(self, key, name):
        preset = self.presets[key]
        name = name.strip()
        if preset.builtin or not name or name == preset.name:
            return
        preset.name = name
        self.save(preset)
        self.emit("changed")

    def set_param(self, effect, param, value):
        """Changes one number of the current preset, which must be the user's
        own; only differences from the base are kept."""
        preset = self.preset
        if preset.builtin:
            raise ValueError("built-in presets cannot be changed")
        checked = EFFECTS[effect].params[param].check(value)
        if checked is None:
            raise ValueError(f"bad value for {effect}.{param}: {value!r}")
        effects = preset.overrides.setdefault("effects", {})
        if BUILTIN[preset.base]["effects"][effect][param] == checked:
            effects.get(effect, {}).pop(param, None)
            if not effects.get(effect, True):
                del effects[effect]
        else:
            effects.setdefault(effect, {})[param] = checked
        self.save(preset)
        self.emit("changed")

    def save(self, preset):
        os.makedirs(os.path.dirname(preset.path), exist_ok=True)
        tmp = preset.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(preset.to_json(), f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, preset.path)

    def _free_name(self, name):
        names = {p.name for p in self.presets.values()}
        candidate, n = name, 2
        while candidate in names:
            candidate, n = f"{name} {n}", n + 1
        return candidate

    def _free_key(self, key):
        candidate, n = key, 2
        while candidate in self.presets or os.path.exists(os.path.join(presets_dir(), candidate + ".json")):
            candidate, n = f"{key}-{n}", n + 1
        return candidate


# Widgets: Adw.TimedAnimation on the preset's numbers

_playing = set()  # keeps running animations alive; tests can finish them


def window_active(widget):
    """Animations start only in the window the user is looking at."""
    root = widget.get_root()
    return root is None or not isinstance(root, Gtk.Window) or root.is_active()


def duration(widget, effect):
    """The effect's duration in ms for a widget's own transition (a
    Gtk.Revealer's), or 0 when it should not animate."""
    params = Animations.get().effect(effect)
    if not params or not window_active(widget):
        return 0
    return int(params["duration"])


def play(widget, effect, on_frame, on_done=None, force=False):
    """Animates with the effect's numbers: on_frame(p) on every frame of
    `widget`'s window, p going from 0 to 1 along the preset's curve (a spring
    overshoots), then on_done(). When nothing should move (the effect is off,
    the window is in the background or not shown), on_frame(1) and on_done()
    run at once. Returns the Adw.Animation, or None."""
    params = Animations.get().effect(effect, force)
    if not params or not (force or window_active(widget)) or not widget.get_mapped():
        on_frame(1.0)
        if on_done:
            on_done()
        return None
    curve = params["curve"]
    target = Adw.CallbackAnimationTarget.new(lambda value: on_frame(progress(curve, value)))
    animation = Adw.TimedAnimation.new(widget, 0, 1, max(int(params["duration"]), 1), target)
    animation.set_easing(Adw.Easing.LINEAR)  # the curve is applied above
    animation.set_follow_enable_animations_setting(False)  # Animations.enabled decides

    def done(*_):
        _playing.discard(animation)
        if on_done:
            on_done()
    animation.connect("done", done)
    _playing.add(animation)
    on_frame(0.0)  # the first frame is drawn before the animation's first tick
    animation.play()
    return animation


def finish_all():
    """Jumps every running widget animation to its end (for tests, whose
    display may not draw frames)."""
    for animation in list(_playing):
        animation.skip()
