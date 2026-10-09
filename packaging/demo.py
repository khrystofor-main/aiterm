#!/usr/bin/env python3
"""Records docs/demo.gif: a real Aiterm window with the real agy in the chat
view, fixing a broken script. Frames are snapshots of the window (no screen
recorder needed), put together with Pillow.

    packaging/demo.py [out.gif]

Needs a graphical session, a signed-in agy and python3-pil. agy runs with a
temporary HOME (your login and the aiterm plugin only), like the evals. The
shell is sandboxed like the evals' too, with a plain prompt.
"""

import importlib.util
import io
import os
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
os.environ["AITERM_CONFIG_DIR"] = tempfile.mkdtemp(prefix="aiterm-demo-config-")
spec = importlib.util.spec_from_file_location("evals_run", os.path.join(ROOT, "evals", "run.py"))
evals = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evals)

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Vte", "3.91")
from gi.repository import Adw, Gio, GLib, Graphene, Gtk  # noqa: E402
from PIL import Image  # noqa: E402

from aiterm.application import Application  # noqa: E402
from aiterm.settings import Settings  # noqa: E402
from aiterm.terminal import BASH_INTEGRATION  # noqa: E402
from aiterm.window import Window  # noqa: E402

OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "docs", "demo.gif")
WIDTH, HEIGHT, SCALE = 1280, 720, 0.75
PROMPT = "Why did that fail? Fix it."
SETUP = """
cat > config.json <<'JSON'
{
  "port": 8080,
  "name": "shop",
}
JSON
cat > server.py <<'PY'
import json

config = json.load(open("config.json"))
print(f"Serving {config['name']} on port {config['port']}")
PY
"""
frames = []  # (PIL image, milliseconds)


def wait_for(predicate, seconds):
    context = GLib.MainContext.default()
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if predicate():
            return True
        context.iteration(False)
        time.sleep(0.01)
    return predicate()


def pause(seconds):
    wait_for(lambda: False, seconds)


def frame(window, ms):
    snapshot = Gtk.Snapshot()
    Gtk.WidgetPaintable(widget=window).snapshot(snapshot, window.get_width(), window.get_height())
    node = snapshot.to_node()
    if node is None:  # not drawn yet
        return
    texture = window.get_renderer().render_texture(
        node, Graphene.Rect().init(0, 0, window.get_width(), window.get_height()))
    image = Image.open(io.BytesIO(texture.save_to_png_bytes().get_data())).convert("RGB")
    image = image.resize((int(image.width * SCALE), int(image.height * SCALE)), Image.LANCZOS)
    if frames and frames[-1][0].tobytes() == image.tobytes():
        frames[-1] = (frames[-1][0], frames[-1][1] + ms)  # nothing changed: hold the last frame
    else:
        frames.append((image, ms))


def type_text(window, widget_feed, text, ms=60):
    for ch in text:
        widget_feed(ch)
        pause(ms / 1000)
        frame(window, ms)


def run(app):
    try:
        record(app)
    finally:
        for w in app.get_windows():
            w.destroy()
        app.quit()


def record(app):
    Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)
    settings = Settings.get()
    settings.agent_view, settings.approve_agent_commands = "chat", True
    settings.agent_panel_width, settings.agent_panel_visible = 560, True
    work = tempfile.mkdtemp(prefix="aiterm-demo-")
    home = os.path.join(work, "home")
    project = os.path.join(home, "project")
    os.makedirs(project)
    subprocess.run(evals.sandbox(home, project) + ["bash", "-e", "-c", SETUP], check=True)
    with open(os.path.join(home, ".bashrc"), "w") as f:
        f.write("PS1='\\[\\e[1;34m\\]\\w\\[\\e[0m\\]$ '\n")

    for w in app.get_windows():
        w.destroy()
    agy_home = evals.agy_home(evals.DEFAULT_MODEL)
    os.environ["AITERM_CHAT_AGENT"] = " ".join([
        "env", f"HOME={agy_home}", evals.agy_binary(), "--model", evals.DEFAULT_MODEL,
        "--output-format", "stream-json", "--input-format", "stream-json", "--print="])
    window = Window(application=app, cwd=project,
                    argv=evals.sandbox(home, project) + ["bash", "--rcfile", BASH_INTEGRATION])
    window.set_default_size(WIDTH, HEIGHT)
    window.present()
    terminal = window.current_terminal()
    wait_for(lambda: terminal.command_log._prompt is not None and window.get_width() > 0, 20)
    pause(1.5)
    frame(window, 800)

    type_text(window, lambda ch: terminal.feed_child(ch.encode()), "python3 server.py")
    terminal.feed_child(b"\n")
    wait_for(lambda: terminal.command_log.commands, 10)
    pause(0.5)
    frame(window, 1500)

    chat = window.agent_panel.chat
    chat.focus()
    buffer = chat.input.get_buffer()
    type_text(window, lambda ch: buffer.insert_at_cursor(ch), PROMPT, 45)
    frame(window, 400)
    chat.send()
    while chat.process.busy or not chat.process.running:
        if window.approval.pending:
            for _ in range(4):
                pause(0.5)
                frame(window, 500)
            window.approval.answer(True)
        pause(0.5)
        frame(window, 500)
    pause(1)
    frame(window, 4000)
    chat.process.stop()
    shutil.rmtree(work, ignore_errors=True)
    shutil.rmtree(agy_home, ignore_errors=True)


app = Application(application_id="io.github.khrystofor_main.Aiterm.Demo", flags=Gio.ApplicationFlags.NON_UNIQUE)
app.connect("activate", lambda app: GLib.idle_add(lambda: run(app) and False))
app.run([])
if frames:
    images = [f[0].quantize(colors=128, method=Image.Quantize.MEDIANCUT) for f in frames]
    images[0].save(OUT, save_all=True, append_images=images[1:], duration=[f[1] for f in frames], loop=0,
                   optimize=True)
    print(f"{OUT}: {len(frames)} frames, {os.path.getsize(OUT) // 1024} KB")
shutil.rmtree(os.environ["AITERM_CONFIG_DIR"], ignore_errors=True)
