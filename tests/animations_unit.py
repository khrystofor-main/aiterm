#!/usr/bin/env python3
"""Unit tests for the animation presets (animations.py) and the effects'
timing and failure rule (effects.py); no window needed. Presets are written to
a throwaway AITERM_CONFIG_DIR. Run: tests/animations_unit.py"""

import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
CONFIG_DIR = tempfile.mkdtemp(prefix="aiterm-test-")
os.environ["AITERM_CONFIG_DIR"] = CONFIG_DIR

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from aiterm import animations, effects  # noqa: E402
from aiterm.animations import BUILTIN, Animations, load_preset_file, parse_preset, progress  # noqa: E402
from aiterm.settings import Settings  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(f"  {'ok  ' if ok else 'FAIL'} {name}" + ("" if ok else f"\n       {detail}"))


# Curves
for curve in animations.CURVES:
    values = [progress(curve, i / 50) for i in range(51)]
    check(f"the {curve} curve runs from 0 to 1", abs(values[0]) < 1e-9 and abs(values[-1] - 1) < 1e-9,
          values[:2] + values[-2:])
for curve in ("linear", "ease-out", "ease-in-out"):
    values = [progress(curve, i / 50) for i in range(51)]
    check(f"…{curve} never goes back", all(a <= b for a, b in zip(values, values[1:])))
check("…spring overshoots, then settles", max(progress("spring", i / 50) for i in range(51)) > 1)
check("…time is clamped to 0..1", progress("ease-out", -1) == 0 and progress("ease-out", 2) == 1)
check("ease-out is quick to start", progress("ease-out", 0.2) > 0.4, progress("ease-out", 0.2))

# Shake and fade
offsets = [effects.shake_offset(i / 100, 6, 3) for i in range(101)]
check("the shake starts and ends in place", offsets[0] == 0 and offsets[-1] == 0, offsets[:3] + offsets[-3:])
check("…swings both ways within the amplitude",
      max(offsets) <= 6 and min(offsets) < 0 < max(offsets), (min(offsets), max(offsets)))
check("…and dies down", max(abs(o) for o in offsets[66:]) < max(abs(o) for o in offsets[:34]))
crossings = lambda cycles: sum(1 for i in range(100) if (effects.shake_offset(i / 100, 6, cycles) < 0)
                               != (effects.shake_offset((i + 1) / 100, 6, cycles) < 0))
check("…more cycles, more swings", crossings(5) > crossings(3), (crossings(3), crossings(5)))
check("the stripe fades from full to nothing",
      effects.fade(0) == 1 and effects.fade(1) == 0 and effects.fade(0.5) < 0.5, [effects.fade(t) for t in (0, .5, 1)])

# What counts as a failure
for text, program in [("ls -l", "ls"), ("make 2>&1 | grep error", "grep"), ("LC_ALL=C /usr/bin/diff a b", "diff"),
                      ("cd /tmp && test -f x", "test"), ("[ -d x ]", "["), ("echo 'a | b'", "echo"), ("", "")]:
    check(f"last_program({text!r}) is {program!r}", effects.last_program(text) == program, effects.last_program(text))
for text, code, failed in [("ls nope", 2, True), ("false", 1, True), ("grep x f", 1, False), ("grep x f", 2, True),
                           ("sleep 9", 130, False), ("vim", 148, False), ("true", 0, False),
                           ("cat f | grep x", 1, False), ("[ -f x ]", 1, False)]:
    check(f"{text!r} with code {code} {'is' if failed else 'is not'} a failure",
          effects.is_failure(text, code) == failed)
check("a preset's own list of code-1 answers", not effects.is_failure("cmp a b", 1, ["cmp"])
      and effects.is_failure("grep x f", 1, ["cmp"]))

# Presets: the user's own on top of a built-in one
preset = parse_preset("mine", {"name": "Mine", "base": "expressive", "effects": {"shake": {"amplitude": 12}},
                               "code_1_answers": ["cmp"]}, "/x/mine.json")
values = preset.values()
check("a preset keeps its own values", values["effects"]["shake"]["amplitude"] == 12 and not preset.warnings,
      preset.warnings)
check("…and takes the rest from its base",
      values["effects"]["shake"]["duration"] == BUILTIN["expressive"]["effects"]["shake"]["duration"]
      and values["effects"]["stripe"] == BUILTIN["expressive"]["effects"]["stripe"])
check("…including its list of code-1 answers", values["code_1_answers"] == ["cmp"])
BUILTIN["expressive"]["effects"]["stripe"]["width"] += 1
check("improvements to the base reach it", preset.values()["effects"]["stripe"]["width"]
      == BUILTIN["expressive"]["effects"]["stripe"]["width"])
BUILTIN["expressive"]["effects"]["stripe"]["width"] -= 1

bad = parse_preset("bad", {"base": "nope", "effects": {"shake": {"amplitude": "big", "wobble": 3, "cycles": 99},
                                                        "sparkle": {}}, "extra": 1}, "/x/bad.json")
check("bad values are dropped with a warning each", len(bad.warnings) == 5, bad.warnings)
check("…an unknown base falls back to the default", bad.base == animations.DEFAULT_PRESET)
check("…numbers out of range are clamped", bad.values()["effects"]["shake"]["cycles"] == 10)
check("…the rest comes from the base",
      bad.values()["effects"]["shake"]["amplitude"] == BUILTIN["subtle"]["effects"]["shake"]["amplitude"])
check("colors must be 'accent' or #rrggbb",
      animations.EFFECTS["stripe"].params["color"].check("#00ff7f") == "#00ff7f"
      and animations.EFFECTS["stripe"].params["color"].check("red") is None)
check("true is not a number", animations.EFFECTS["shake"].params["amplitude"].check(True) is None)

broken = os.path.join(CONFIG_DIR, "broken.json")
with open(broken, "w") as f:
    f.write('{"name": "Broken", "effects": {')
preset = load_preset_file(broken)
check("a broken JSON file gives the base's values and a warning",
      preset.values() == BUILTIN["subtle"] and "not valid JSON" in preset.warnings[0], preset.warnings)
os.remove(broken)

# The engine: picking, duplicating, changing, deleting presets
settings = Settings.get()
engine = Animations.get()
settings.animations = "on"
check("the default preset is Subtle", engine.preset.key == "subtle" and engine.preset.builtin)
check("effects read the current preset's numbers", engine.effect("shake") == BUILTIN["subtle"]["effects"]["shake"])
settings.animation_preset = "expressive"
check("…and follow a change of preset", engine.effect("shake")["duration"] == 450)
settings.animations = "off"
check("with animations off, no effect runs", engine.effect("shake") is None)
check("…unless forced (a preview)", engine.effect("shake", force=True) is not None)
settings.animations = "on"
gtk_settings = animations.Gtk.Settings.get_default()
if gtk_settings:
    settings.animations = "auto"
    gtk_settings.set_property("gtk-enable-animations", False)
    check("until the user picks, the system's Reduce Animations decides", not engine.enabled)
    gtk_settings.set_property("gtk-enable-animations", True)
    check("…both ways", engine.enabled)
    settings.animations = "on"

try:
    engine.set_param("shake", "amplitude", 20)
    check("built-in presets cannot be changed", False)
except ValueError:
    check("built-in presets cannot be changed", True)
copy = engine.duplicate("expressive")
check("Duplicate saves a copy and picks it",
      engine.preset is copy and copy.name == "Expressive Copy" and os.path.exists(copy.path), copy.path)
engine.set_param("shake", "amplitude", 20)
engine.set_param("stripe", "enabled", False)
with open(copy.path) as f:
    saved = json.load(f)
check("its file keeps only the differences",
      saved == {"name": "Expressive Copy", "base": "expressive",
                "effects": {"shake": {"amplitude": 20.0}, "stripe": {"enabled": False}}}, saved)
check("…which the effects read", engine.effect("shake")["amplitude"] == 20 and engine.effect("stripe") is None)
engine.set_param("stripe", "enabled", True)
with open(copy.path) as f:
    saved = json.load(f)
check("setting a value back to the base's drops it from the file", "stripe" not in saved["effects"], saved)
engine.reload()
check("the copy is still there after a reload",
      engine.preset.key == copy.key and engine.effect("shake")["amplitude"] == 20)
engine.rename(copy.key, "  Calm  ")
with open(copy.path) as f:
    check("a copy can be renamed", json.load(f)["name"] == "Calm" and engine.preset.name == "Calm")
engine.rename("subtle", "Mine")
check("…a built-in one cannot", engine.presets["subtle"].name == "Subtle")
second = engine.duplicate(copy.key)
check("a second copy gets its own name and file", second.name == "Calm Copy" and second.key != copy.key)
engine.delete(second.key)
check("Delete removes the file and goes back to the base",
      not os.path.exists(second.path) and engine.preset.key == "expressive")
engine.delete("subtle")
check("built-in presets cannot be deleted", "subtle" in engine.presets)
settings.animation_preset = "gone"
check("a missing preset falls back to the default", engine.preset.key == "subtle")

shutil.rmtree(CONFIG_DIR, ignore_errors=True)
print(f"  {sum(results)}/{len(results)} animation checks passed")
sys.exit(0 if all(results) else 1)
