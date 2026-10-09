"""Animated effects drawn over the terminal. They never hold text back: VTE
draws the output at once, and the effects are painted on top of it.

  * a failed command's line shakes sideways: the row is drawn again, shifted,
    over a strip of the terminal's background;
  * new output gets a thin stripe in the left padding that fades away: the
    accent color while the command runs, then green or red by its exit code;
  * a failed command's line gets "✗ <code>" at its end, sliding in, and
    keeps it;
  * the terminal's colors move to a new palette or light/dark style; the
    text keeps its contrast on the way.

Rows come from the command log (commands.py); the numbers from the current
animation preset (animations.py), taken when an effect starts. Timing is a
pure function of the time since then, read from `clock`, so tests can move
the time by hand.

Effects stand back when they would be in the way: nothing starts while the
window is in the background (the ✗ appears at once), output that floods in
faster than the preset's `flood` lines per second gets no stripe until it
calms down, and full-screen programs (vim, less, htop: the alternate screen)
and remote shells (ssh) get no output effects.
"""

import math
import re
import shlex
import time

from gi.repository import Adw, Gdk, GLib, Graphene, Gsk, Gtk, Pango, Vte

from aiterm.animations import Animations, progress

# New output is gathered into one zone at most this often
STRIPE_UPDATE_MS = 100
# Programs whose output comes from another machine's shell
REMOTE_PROGRAMS = {"ssh", "mosh-client", "et", "telnet"}
MAX_MARKS = 500
# The ✗ mark: space around its text, and how see-through it gets under the pointer
MARK_PADDING_X, MARK_HOVER_ALPHA = 6, 0.3

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


def mix(a, b, t):
    """The color t of the way from a to b."""
    return Gdk.RGBA(red=a.red + (b.red - a.red) * t, green=a.green + (b.green - a.green) * t,
                    blue=a.blue + (b.blue - a.blue) * t, alpha=a.alpha + (b.alpha - a.alpha) * t)


def luminance(color):
    """WCAG relative luminance."""
    linear = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * linear(color.red) + 0.7152 * linear(color.green) + 0.0722 * linear(color.blue)


def contrast(a, b):
    """WCAG contrast ratio, 1 to 21."""
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


READABLE = 4.5  # WCAG AA for text


def blend_colors(old, new, t):
    """The terminal's colors t of the way from `old` to `new` (foreground,
    background, palette). The background and the palette move evenly; the
    text follows too while it stays readable, else it takes whichever of its
    old and new color reads better: from dark to light, both pass through
    gray halfway, and the text would vanish."""
    background = mix(old[1], new[1], t)
    foreground = mix(old[0], new[0], t)
    if contrast(foreground, background) < READABLE:
        foreground = max(old[0], new[0], key=lambda c: contrast(c, background))
    return foreground, background, [mix(a, b, t) for a, b in zip(old[2], new[2])]


def with_alpha(color, alpha):
    return Gdk.RGBA(red=color.red, green=color.green, blue=color.blue, alpha=color.alpha * alpha)


class _Running:
    """One effect in progress: where, since when, with which numbers."""

    def __init__(self, first, last, started, params, forced=False):
        self.first, self.last, self.started, self.params = first, last, started, params
        self.forced = forced  # a preview: runs even with animations off
        self.color = None  # instead of the preset's (a stripe after its command ended)

    def t(self, now):
        if self.started is None:
            return 1.0  # shown as it ends
        return (now - self.started) / max(self.params["duration"], 1)


class _Mark(_Running):
    def __init__(self, row, code, started, params, forced=False):
        super().__init__(row, row, started, params, forced)
        self.code = code


class _Blend(_Running):
    def __init__(self, old, new, started, params, forced=False):
        # The intensity scales the time a color change takes
        params = dict(params, duration=params["duration"] * params["intensity"])
        super().__init__(0, 0, started, params, forced)
        self.old, self.new = old, new


class TerminalEffects:
    """The effects of one terminal. The terminal calls `draw` from its
    snapshot, after VTE has drawn the text."""

    def __init__(self, terminal):
        self.terminal = terminal
        self.animations = Animations.get()
        self.clock = _monotonic_ms
        self.shakes = []
        self.stripes = []
        self.marks = []  # stay while their row exists
        self.blend = None  # the terminal's colors on their way to new ones
        self._seen_row = None  # the last row of output that has a stripe
        self._update_pending = False
        self._last_update = None  # (time, cursor row) of the last stripe update
        self._tick = None
        self._pointer = None  # (x, y) over the terminal, for the marks
        terminal.connect("contents-changed", self._on_contents_changed)
        terminal.connect("command-finished", self._on_command_finished)
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", lambda _c, x, y: self._on_pointer((x, y)))
        motion.connect("leave", lambda _c: self._on_pointer(None))
        terminal.add_controller(motion)

    def window_active(self):
        """Effects start only in the window the user is looking at."""
        root = self.terminal.get_root()
        return root is None or not isinstance(root, Gtk.Window) or root.is_active()

    def active(self):
        """True while something is still animating."""
        now = self.clock()
        return bool(self.shakes or self.stripes or self.blend
                    or any(m.t(now) < 1 for m in self.marks))

    def clear(self):
        self.shakes, self.stripes, self.marks = [], [], []
        self.terminal.queue_draw()

    def _params(self, name, force):
        """The effect's numbers if it should run now, else None."""
        params = self.animations.effect(name, force)
        if params and not force and not self.window_active():
            return None
        return params

    # The effects; `force` shows them even when they are off (previews)

    def shake(self, row, force=False):
        params = self._params("shake", force)
        if params:
            self.shakes.append(_Running(row, row, self.clock(), params, force))
            self._start()

    def light(self, first, last, force=False):
        """A stripe on rows first..last; the same rows again only restart it."""
        params = self._params("stripe", force)
        if not params:
            return
        now = self.clock()
        if self.stripes and (self.stripes[-1].first, self.stripes[-1].last) == (first, last):
            self.stripes[-1].started = now
        else:
            self.stripes.append(_Running(first, last, now, params, force))
        self._start()

    def mark(self, row, code, force=False):
        """✗ and the exit code at the end of the row. It is a mark, not only
        motion: with animations off or the window in the background it
        appears without sliding in (and not at all when switched off in the
        preset)."""
        params = self.animations.effect("fail_mark", force=True)
        if not (force or params["enabled"]):
            return
        animate = self._params("fail_mark", force) is not None
        self.marks = [m for m in self.marks if m.first != row][-(MAX_MARKS - 1):]
        self.marks.append(_Mark(row, code, self.clock() if animate else None, params, force))
        self._start()

    def blend_colors(self, old, new, apply, force=False):
        """Moves the terminal's colors from `old` to `new` (each a tuple of
        foreground, background and the palette), calling apply(colors) on
        every frame. False when there is nothing to animate: then the caller
        sets the new colors itself."""
        params = self._params("theme", force)
        if not params or params["duration"] * params["intensity"] < 1:
            return False
        if self.blend:  # mid-way: start from where the colors are now
            old = self._blended(self.clock())
        self.blend = _Blend(old, new, self.clock(), params, force)
        self._apply = apply
        apply(old)
        self._start()
        return True

    def _blended(self, now):
        blend = self.blend
        return blend_colors(blend.old, blend.new, progress(blend.params["curve"], blend.t(now)))

    # New output

    def _on_contents_changed(self, _terminal):
        if not self._update_pending and self.animations.effect("stripe"):
            self._update_pending = True
            GLib.timeout_add(STRIPE_UPDATE_MS, self._update_stripes)

    def _update_stripes(self):
        self._update_pending = False
        log = self.terminal.command_log
        if not log.running or log.input_row is None:
            self._seen_row = self._last_update = None
            return GLib.SOURCE_REMOVE
        col, row = self.terminal.get_cursor_position()
        # At the start of a line: the output so far ended with a newline
        last = row - 1 if col == 0 else row
        if self._flooding(last) or not self._output_effects_apply():
            self._seen_row = max(self._seen_row or 0, last)  # never lit later
            return GLib.SOURCE_REMOVE
        self._light_output(log.input_row, last)
        return GLib.SOURCE_REMOVE

    def _flooding(self, row):
        """True while output comes faster than the preset's lines per second:
        since the last update, or for the first one since the command started."""
        params = self.animations.effect("stripe")
        now = self.clock()
        previous, self._last_update = self._last_update, (now, row)
        if params is None:
            return False
        log = self.terminal.command_log
        if previous is not None:
            rows, seconds = row - previous[1], (now - previous[0]) / 1000
        elif log.running_since is not None and log.input_row is not None:
            rows, seconds = row - log.input_row, time.monotonic() - log.running_since
        else:
            return False
        return rows / max(seconds, STRIPE_UPDATE_MS / 1000) > params["flood"]

    def _output_effects_apply(self):
        """No output effects for full-screen programs and remote shells."""
        if self.terminal.running_program() in REMOTE_PROGRAMS:
            return False
        return not self._alternate_screen()

    def _alternate_screen(self):
        """Full-screen programs (vim, less, htop) draw on VTE's alternate
        screen, which VTE does not report. It has its own rows: the command's
        own line is not there any more."""
        log = self.terminal.command_log
        text = log.running_text
        if not text or log.input_row is None:
            return False
        line, _ = self.terminal.get_text_range_format(
            Vte.Format.TEXT, log.input_row, 0, log.input_row, 10_000)
        first_line = text.split("\n", 1)[0].strip()
        return not line or first_line[:20] not in line

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

    def _on_command_finished(self, terminal, code, seconds):
        command = terminal.command_log.commands[-1]
        if not command.end_row:
            self._seen_row = self._last_update = None
            return
        last = command.end_row - 1
        if self._last_update is None:
            # A quick command, done before the first update: its pace as a whole
            params = self.animations.effect("stripe")
            unseen = last - max(self._seen_row or 0, command.input_row)
            flooding = params is not None and unseen / max(seconds, STRIPE_UPDATE_MS / 1000) > params["flood"]
        else:
            flooding = self._flooding(last)
        if not flooding:
            # Output that came after the last update
            self._light_output(command.input_row, last, again=False)
        self._seen_row = self._last_update = None
        failed = is_failure(command.text, code, self.animations.code_1_answers())
        # Its stripes, still fading, take the color of how it ended
        for stripe in self.stripes:
            if command.input_row < stripe.first and stripe.last < command.end_row:
                stripe.color = "error" if failed else "success"
        if failed:
            if self._row_visible(command.input_row):
                self.shake(command.input_row)
            self.mark(command.input_row, code)

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
        for mark in self.marks:
            if not keep(mark):
                mark.started = None  # in place
        if self.blend:
            if keep(self.blend):
                self._apply(self._blended(now))
            else:
                self._apply(self.blend.new)
                self.blend = None

    def _color(self, spec):
        """A preset color: the theme's accent, the palette's green or red
        ("success", "error"), or "#rrggbb"."""
        if spec == "accent":
            return Adw.StyleManager.get_default().get_accent_color_rgba()
        if spec in ("success", "error"):
            # The bright variants read better on a dark background
            bright = 8 if Adw.StyleManager.get_default().get_dark() else 0
            return self.terminal.palette_color((2 if spec == "success" else 1) + bright)
        color = Gdk.RGBA()
        color.parse(spec)
        return color

    def _on_pointer(self, point):
        hovered = lambda p: p is not None and any(self._mark_rect(m)[0].contains_point(
            Graphene.Point().init(*self._content_point(p))) for m in self._visible_marks())
        was = hovered(self._pointer)
        self._pointer = point
        if hovered(point) != was:
            self.terminal.queue_draw()

    def _content_point(self, point):
        # Pointer events come in the widget's coordinates, drawing in its text area's
        from aiterm.terminal import PADDING_X, PADDING_Y
        return point[0] - PADDING_X, point[1] - PADDING_Y

    def _visible_marks(self):
        if not self.marks:
            return []
        top, rows = self.terminal.top_row(), self.terminal.get_row_count()
        return [m for m in self.marks if top <= m.first < top + rows]

    def _mark_layout(self, mark):
        term = self.terminal
        layout = term.create_pango_layout(f"✗ {mark.code}")
        font = (term.get_font() or Pango.FontDescription.from_string("Monospace 11")).copy()
        if font.get_size():
            font.set_size(int(font.get_size() * term.get_font_scale()))
        font.set_weight(Pango.Weight.BOLD)
        layout.set_font_description(font)
        return layout

    def _mark_rect(self, mark, layout=None):
        """Where the mark's plate sits (in the text area), and its layout."""
        layout = layout or self._mark_layout(mark)
        _, logical = layout.get_pixel_extents()
        height = self.terminal.get_char_height()
        width = logical.width + 2 * MARK_PADDING_X
        y = (mark.first - self.terminal.top_row()) * height
        return Graphene.Rect().init(self.terminal.get_width() - width, y, width, height), layout

    def draw(self, snapshot, draw_terminal):
        """Paints the effects over the terminal. `draw_terminal(snapshot)`
        draws the terminal's text again (for the shaking row)."""
        self.prune()
        marks = self._visible_marks()
        if not (self.shakes or self.stripes or marks):
            return
        from aiterm.terminal import PADDING_X  # the terminal imports us

        # The snapshot starts at the terminal's text area, inside its padding
        term = self.terminal
        now = self.clock()
        top = term.top_row()
        height = term.get_char_height()
        width = term.get_width()
        background = term.get_color_background_for_draw()
        y_of = lambda row: (row - top) * height

        for shake in self.shakes:
            p = shake.params
            dx = shake_offset(shake.t(now), p["amplitude"] * p["intensity"], p["cycles"], p["curve"])
            rect = Graphene.Rect().init(-PADDING_X, y_of(shake.first), width + 2 * PADDING_X, height)
            snapshot.append_color(background, rect)
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
            color = with_alpha(self._color(stripe.color or p["color"]), alpha)
            stripe_width = min(p["width"], PADDING_X)
            x = -(PADDING_X + stripe_width) / 2  # centered in the left padding
            snapshot.append_color(color, Graphene.Rect().init(x, y1, stripe_width, y2 - y1))

        pointer = self._content_point(self._pointer) if self._pointer else None
        for mark in marks:
            p = mark.params
            rect, layout = self._mark_rect(mark)
            t = mark.t(now)
            dx = p["distance"] * p["intensity"] * (1 - progress(p["curve"], t))
            alpha = min(1.0, t * 3)
            if pointer and rect.contains_point(Graphene.Point().init(*pointer)):
                alpha *= MARK_HOVER_ALPHA
            snapshot.save()
            snapshot.translate(Graphene.Point().init(dx, 0))
            plate = Gsk.RoundedRect()
            plate.init_from_rect(rect, height / 4)
            snapshot.push_rounded_clip(plate)
            snapshot.append_color(with_alpha(background, alpha), rect)
            snapshot.pop()
            _, logical = layout.get_pixel_extents()
            snapshot.translate(Graphene.Point().init(rect.get_x() + MARK_PADDING_X,
                                                     rect.get_y() + (height - logical.height) / 2))
            snapshot.append_layout(layout, with_alpha(self._color(p["color"]), alpha))
            snapshot.restore()
