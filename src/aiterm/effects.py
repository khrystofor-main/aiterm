"""Animated effects drawn over the terminal. They never hold text back: VTE
draws the output at once, and the effects are painted on top of it.

  * a failed command's line shakes sideways: the row is drawn again, shifted,
    over a strip of the terminal's background;
  * new output gets a thin stripe in the left padding that fades away.

Rows come from the command log (commands.py); the numbers from the current
animation preset (animations.py), taken when an effect starts. Timing is a
pure function of the time since then, read from `clock`, so tests can move
the time by hand.
"""

import math
import re
import shlex

from gi.repository import Adw, Gdk, GLib, Graphene

from aiterm.animations import Animations, progress

# New output is gathered into one zone at most this often
STRIPE_UPDATE_MS = 100

# Exit codes that are not failures: Ctrl+C, Ctrl+Z
NOT_FAILURES = {130, 148}
# Commands whose exit code 1 means "no" (no match, files differ), not an
# error; presets have their own list
CODE_1_ANSWERS = ("grep", "diff", "test", "[")


def shake_offset(t, amplitude, cycles, curve="ease-out"):
    """Sideways offset in px at t (0..1 of the shake): a sine that dies down
    along the curve."""
    if not 0 <= t < 1:
        return 0.0
    return amplitude * (1 - progress(curve, t)) * math.sin(2 * math.pi * cycles * t)


def fade(t, curve="ease-out"):
    """Opacity at t (0..1 of the fade), from 1 to 0 along the curve."""
    if t >= 1:
        return 0.0
    return min(max(1 - progress(curve, t), 0.0), 1.0)


SEPARATORS = {"|", "|&", "||", "&&", ";", "&"}


def last_program(text):
    """The program whose exit code bash reports for a command line: the last
    simple command of it (`make | grep x` -> grep), without VAR=value
    prefixes and folders (`LC_ALL=C /bin/grep` -> grep)."""
    lexer = shlex.shlex(text or "", posix=True, punctuation_chars=True)
    try:
        tokens = list(lexer)
    except ValueError:  # an unclosed quote
        tokens = (text or "").split()
    words = []
    for token in tokens:
        if token in SEPARATORS:
            words = []
        elif not (words or re.match(r"^\w+=", token)):
            words.append(token)
    if words:
        return words[0].rsplit("/", 1)[-1]
    return ""


def is_failure(text, exit_code, code_1_answers=CODE_1_ANSWERS):
    if exit_code == 0 or exit_code in NOT_FAILURES:
        return False
    return not (exit_code == 1 and last_program(text) in code_1_answers)


def _monotonic_ms():
    return GLib.get_monotonic_time() / 1000


def _color(spec):
    """A preset color: "accent" (the system's) or "#rrggbb"."""
    if spec == "accent":
        return Adw.StyleManager.get_default().get_accent_color_rgba()
    color = Gdk.RGBA()
    color.parse(spec)
    return color


class _Running:
    """One effect in progress: where, since when, with which numbers."""

    def __init__(self, first, last, started, params, forced):
        self.first, self.last, self.started, self.params = first, last, started, params
        self.forced = forced  # a preview: runs even with animations off

    def t(self, now):
        return (now - self.started) / self.params["duration"]


class TerminalEffects:
    """The effects of one terminal. The terminal calls `draw` from its
    snapshot, after VTE has drawn the text."""

    def __init__(self, terminal):
        self.terminal = terminal
        self.animations = Animations.get()
        self.clock = _monotonic_ms
        self.shakes = []
        self.stripes = []
        self._seen_row = None  # the last row of output that has a stripe
        self._update_pending = False
        self._tick = None
        terminal.connect("contents-changed", self._on_contents_changed)
        terminal.connect("command-finished", self._on_command_finished)

    def active(self):
        """True while something is still animating."""
        return bool(self.shakes or self.stripes)

    def clear(self):
        self.shakes, self.stripes = [], []
        self.terminal.queue_draw()

    # The effects; `force` shows them even when they are off (previews)

    def shake(self, row, force=False):
        params = self.animations.effect("shake", force)
        if params:
            self.shakes.append(_Running(row, row, self.clock(), params, force))
            self._start()

    def light(self, first, last, force=False):
        """A stripe on rows first..last; the same rows again only restart it."""
        params = self.animations.effect("stripe", force)
        if not params:
            return
        now = self.clock()
        if self.stripes and (self.stripes[-1].first, self.stripes[-1].last) == (first, last):
            self.stripes[-1].started = now
        else:
            self.stripes.append(_Running(first, last, now, params, force))
        self._start()

    # New output

    def _on_contents_changed(self, _terminal):
        if not self._update_pending and self.animations.effect("stripe"):
            self._update_pending = True
            GLib.timeout_add(STRIPE_UPDATE_MS, self._update_stripes)

    def _update_stripes(self):
        self._update_pending = False
        log = self.terminal.command_log
        if not log.running or log.input_row is None:
            self._seen_row = None
            return GLib.SOURCE_REMOVE
        col, row = self.terminal.get_cursor_position()
        # At the start of a line: the output so far ended with a newline
        self._light_output(log.input_row, row - 1 if col == 0 else row)
        return GLib.SOURCE_REMOVE

    def _light_output(self, input_row, last, again=True):
        """A stripe on the rows after `input_row` (the command's own line) up
        to `last` that have none yet. With `again`, a row being rewritten in
        place (a progress bar) lights up again."""
        if self._seen_row is None or self._seen_row < input_row:
            self._seen_row = input_row
        if last <= input_row or (last <= self._seen_row and not again):
            return
        self.light(min(self._seen_row + 1, last), last)
        self._seen_row = max(self._seen_row, last)

    # A command ended

    def _on_command_finished(self, terminal, code, _seconds):
        command = terminal.command_log.commands[-1]
        if not command.end_row:
            self._seen_row = None
            return
        # Output of a quick command, finished before the next update
        self._light_output(command.input_row, command.end_row - 1, again=False)
        self._seen_row = None
        if is_failure(command.text, code, self.animations.code_1_answers()) and self._row_visible(command.input_row):
            self.shake(command.input_row)

    def _row_visible(self, row):
        top = self.terminal.top_row()
        return top <= row < top + self.terminal.get_row_count()

    # Frames

    def _start(self):
        if self._tick is None:
            self._tick = self.terminal.add_tick_callback(self._on_tick)
        self.terminal.queue_draw()

    def _on_tick(self, *_):
        self.prune()
        self.terminal.queue_draw()
        if self.active():
            return GLib.SOURCE_CONTINUE
        self._tick = None
        return GLib.SOURCE_REMOVE

    def prune(self):
        """Drops effects that have run their course, and all but previews
        once animations are turned off."""
        now, enabled = self.clock(), self.animations.enabled
        keep = lambda running: running.t(now) < 1 and (enabled or running.forced)
        self.shakes = [s for s in self.shakes if keep(s)]
        self.stripes = [s for s in self.stripes if keep(s)]

    def draw(self, snapshot, draw_terminal):
        """Paints the effects over the terminal. `draw_terminal(snapshot)`
        draws the terminal's text again (for the shaking row)."""
        self.prune()
        if not self.active():
            return
        from aiterm.terminal import PADDING_X  # the terminal imports us

        # The snapshot starts at the terminal's text area, inside its padding
        term = self.terminal
        now = self.clock()
        top = term.top_row()
        height = term.get_char_height()
        width = term.get_width()
        y_of = lambda row: (row - top) * height

        for shake in self.shakes:
            p = shake.params
            dx = shake_offset(shake.t(now), p["amplitude"] * p["intensity"], p["cycles"], p["curve"])
            rect = Graphene.Rect().init(-PADDING_X, y_of(shake.first), width + 2 * PADDING_X, height)
            snapshot.append_color(term.get_color_background_for_draw(), rect)
            snapshot.push_clip(rect)
            snapshot.save()
            snapshot.translate(Graphene.Point().init(dx, 0))
            draw_terminal(snapshot)
            snapshot.restore()
            snapshot.pop()

        for stripe in self.stripes:
            p = stripe.params
            alpha = min(p["alpha"] * p["intensity"], 1.0) * fade(stripe.t(now), p["curve"])
            y1, y2 = max(y_of(stripe.first), 0), min(y_of(stripe.last + 1), term.get_height())
            if alpha <= 0 or y2 <= y1:
                continue
            color = _color(p["color"])
            color = Gdk.RGBA(red=color.red, green=color.green, blue=color.blue, alpha=alpha)
            stripe_width = min(p["width"], PADDING_X)
            x = -(PADDING_X + stripe_width) / 2  # centered in the left padding
            snapshot.append_color(color, Graphene.Rect().init(x, y1, stripe_width, y2 - y1))
