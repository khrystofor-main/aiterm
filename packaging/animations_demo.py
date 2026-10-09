#!/usr/bin/env python3
"""Records a short GIF of the terminal effects (effects.py): output with its
fading stripe, then a failed command whose line shakes. Frames are snapshots
of the window, taken as fast as the effects run, so the GIF shows each frame
of the animation; no agent involved.

    packaging/animations_demo.py [out.gif]

Needs python3-pil. Run it on an invisible display, so no window shows up on
the desktop:

    tests/headless.sh packaging/animations_demo.py
"""

import io
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
os.environ["AITERM_CONFIG_DIR"] = tempfile.mkdtemp(prefix="aiterm-demo-config-")
os.environ["SHELL"] = "/bin/bash"
os.environ["AITERM_AGENT"] = "/bin/bash --norc --noprofile"

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Vte", "3.91")
from gi.repository import Adw, Gio, GLib, Graphene, Gsk, Gtk  # noqa: E402
from PIL import Image  # noqa: E402

from aiterm.application import Application  # noqa: E402
from aiterm.settings import Settings  # noqa: E402
from aiterm.window import Window  # noqa: E402

OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "docs", "animations.gif")
WIDTH, HEIGHT, SCALE = 760, 340, 1.0
FRAME_MS = 30
frames = []  # (PIL image, milliseconds)


renderer = None


def frame(window):
    """The window's contents as an image, drawn by our own renderer from the
    widgets' snapshots."""
    global renderer
    if not window.get_width():
        return None  # not laid out yet
    if renderer is None:
        renderer = Gsk.CairoRenderer()
        renderer.realize_for_display(window.get_display())
    snapshot = Gtk.Snapshot()
    child = window.get_first_child()
    if child and not child.get_width():
        return None
    while child:
        window.snapshot_child(child, snapshot)
        child = child.get_next_sibling()
    node = snapshot.to_node()
    if node is None:
        return None
    texture = renderer.render_texture(node, Graphene.Rect().init(0, 0, window.get_width(), window.get_height()))
    image = Image.open(io.BytesIO(texture.save_to_png_bytes().get_data())).convert("RGB")
    if SCALE != 1:
        image = image.resize((int(image.width * SCALE), int(image.height * SCALE)), Image.LANCZOS)
    return image


film_ms = 0.0  # the GIF's own clock, which the effects follow


def record(window, seconds, until=None):
    """Takes a frame every FRAME_MS of GIF time for `seconds`, or until
    until() is true. The effects run on the GIF's clock, so the time spent
    drawing a frame never shows: every frame of an animation is in the GIF.
    (The shell runs in real time, so its pauses look shorter.)"""
    global film_ms
    context = GLib.MainContext.default()
    terminal = window.current_terminal()
    terminal.effects.clock = lambda: film_ms
    end = film_ms + seconds * 1000
    while film_ms < end and not (until and until()):
        start = time.monotonic()
        while time.monotonic() - start < FRAME_MS / 1000:
            context.iteration(False)
            time.sleep(0.002)
        film_ms += FRAME_MS
        terminal.queue_draw()
        image = frame(window)
        if image is None:
            continue
        if frames and frames[-1][0].tobytes() == image.tobytes():
            frames[-1] = (frames[-1][0], frames[-1][1] + FRAME_MS)
        else:
            frames.append((image, FRAME_MS))


def type_text(window, terminal, text):
    for ch in text:
        terminal.feed_child(ch.encode())
        record(window, 0.05)


def run(app):
    try:
        demo(app)
    finally:
        if renderer:
            renderer.unrealize()
        for w in app.get_windows():
            w.destroy()
        app.quit()


def demo(app):
    Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)
    Gtk.Settings.get_default().set_property("gtk-decoration-layout", "")  # no window buttons
    Settings.get().agent_panel_visible = False
    Settings.get().animations = "on"  # whatever the display says about Reduce Animations
    for w in app.get_windows():
        w.destroy()
    window = Window(application=app)
    window.set_default_size(WIDTH, HEIGHT)
    window.present()
    terminal = window.current_terminal()
    # A plain prompt instead of the user's own
    record(window, 1.0, until=lambda: terminal.command_log.input_row is not None)
    terminal.feed_child(b" PS1='\\[\\e[1;34m\\]~/project\\[\\e[0m\\]$ '; printf '\\e]0;Terminal\\a'; clear\n")
    record(window, 1.0)
    frames.clear()
    record(window, 0.6)

    type_text(window, terminal, "for f in a b c d e; do echo \"building $f\"; sleep 0.25; done")
    terminal.feed_child(b"\n")
    record(window, 3.2)
    type_text(window, terminal, "ls missing-folder")
    terminal.feed_child(b"\n")
    record(window, 2.0)



app = Application(application_id="io.github.khrystofor_main.Aiterm.AnimationsDemo",
                  flags=Gio.ApplicationFlags.NON_UNIQUE)
app.connect("activate", lambda app: GLib.idle_add(lambda: run(app) and False))
app.run([])
if frames:
    images = [f[0].quantize(colors=128, method=Image.Quantize.MEDIANCUT) for f in frames]
    images[0].save(OUT, save_all=True, append_images=images[1:], duration=[f[1] for f in frames], loop=0,
                   optimize=True)
    print(f"{OUT}: {len(frames)} frames, {sum(f[1] for f in frames) / 1000:.1f} s, "
          f"{os.path.getsize(OUT) // 1024} KB")
