#!/usr/bin/env python3
"""Unit tests for the effects' timing and the failure rule (effects.py); no
window needed. Run: tests/effects_unit.py"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from aiterm import effects  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(f"  {'ok  ' if ok else 'FAIL'} {name}" + ("" if ok else f"\n       {detail}"))


offsets = [effects.shake_offset(i / 100) for i in range(101)]
check("the shake starts and ends in place", offsets[0] == 0 and offsets[-1] == 0, offsets[:3] + offsets[-3:])
check("…swings both ways within the amplitude",
      max(offsets) <= effects.SHAKE_AMPLITUDE and min(offsets) < 0 < max(offsets), (min(offsets), max(offsets)))
check("…and dies down", max(abs(o) for o in offsets[66:]) < max(abs(o) for o in offsets[:34]))
check("the stripe fades from full to nothing",
      effects.fade(0) == 1 and effects.fade(1) == 0 and effects.fade(0.5) < 0.5, [effects.fade(t) for t in (0, .5, 1)])

for text, program in [("ls -l", "ls"), ("make 2>&1 | grep error", "grep"), ("LC_ALL=C /usr/bin/diff a b", "diff"),
                      ("cd /tmp && test -f x", "test"), ("[ -d x ]", "["), ("echo 'a | b'", "echo"), ("", "")]:
    check(f"last_program({text!r}) is {program!r}", effects.last_program(text) == program, effects.last_program(text))

for text, code, failed in [("ls nope", 2, True), ("false", 1, True), ("grep x f", 1, False), ("grep x f", 2, True),
                           ("sleep 9", 130, False), ("vim", 148, False), ("true", 0, False),
                           ("cat f | grep x", 1, False), ("[ -f x ]", 1, False)]:
    check(f"{text!r} with code {code} {'is' if failed else 'is not'} a failure",
          effects.is_failure(text, code) == failed)

print(f"  {sum(results)}/{len(results)} effects checks passed")
sys.exit(0 if all(results) else 1)
