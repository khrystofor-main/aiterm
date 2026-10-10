#!/usr/bin/env python3
"""Unit tests for Aiterm's own prompt (prompt.py) and how
shell/integration.bash shows it: a real `bash -i` reads the prompt file, no
window. Files go to a throwaway AITERM_CONFIG_DIR and HOME.
Run: tests/prompt_unit.py"""

import os
import shutil
import subprocess
import sys
import re
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
CONFIG_DIR = tempfile.mkdtemp(prefix="aiterm-test-")
os.environ["AITERM_CONFIG_DIR"] = CONFIG_DIR

from aiterm import prompt  # noqa: E402
from aiterm.palettes import PALETTES  # noqa: E402
from aiterm.settings import Settings  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(f"  {'ok  ' if ok else 'FAIL'} {name}" + ("" if ok else f"\n       {detail}"))


# The segments text
items = prompt.parse_segments(prompt.DEFAULT_SEGMENTS)
check("the default lists every segment once", [i.key for i in items] == list(prompt.SEGMENTS), items)
check("…and reads back the same", prompt.format_segments(items) == prompt.DEFAULT_SEGMENTS,
      prompt.format_segments(items))
items = prompt.parse_segments("cwd:blue, nope:red, -git:purple, cwd:red")
check("unknown segments and repeats are dropped, unknown colors are Default",
      [(i.key, i.color, i.enabled) for i in items[:2]] == [("cwd", "blue", True), ("git", "default", False)],
      items)
check("…and missing segments come last, off, in their default colors",
      [(i.key, i.enabled) for i in items[2:]] == [(k, False) for k in prompt.SEGMENTS if k not in ("cwd", "git")]
      and items[2].color == prompt.DEFAULT_COLORS[items[2].key], items)

# The preview
markup = prompt.preview_markup("cwd:blue,-git:red", "arrow", True, False, PALETTES["GNOME"], True)
check("the preview shows the segments on in the palette's colors, then the symbol",
      markup == '<span foreground="#1c71d8">~/projects/aiterm</span>\n<span foreground="#ffffff">❯</span> ',
      markup)


# The file and the shell
settings = Settings.get()
settings.custom_prompt = False
prompt.follow(settings)
check("no file while the user keeps their own prompt", not os.path.exists(prompt.prompt_file()))
settings.custom_prompt = True
check("turning it on writes the file", os.path.exists(prompt.prompt_file()))

home = tempfile.mkdtemp(prefix="aiterm-home-")
os.makedirs(os.path.join(home, "repo", ".git"))
os.makedirs(os.path.join(home, "repo", "sub"))
with open(os.path.join(home, "repo", ".git", "HEAD"), "w") as f:
    f.write("ref: refs/heads/feature/$(touch pwned)\n")
with open(os.path.join(home, ".bashrc"), "w") as f:
    f.write("PS1='own> '\n")


def prompts(script, cwd=home):
    """The prompts bash shows for `script`, without colors and Aiterm's marks."""
    env = {"HOME": home, "PATH": os.environ["PATH"], "TERM": "dumb", "AITERM_PROMPT_FILE": prompt.prompt_file()}
    run = subprocess.run(["bash", "--rcfile", os.path.join(ROOT, "src/aiterm/shell/integration.bash"), "-i"],
                         input=script, capture_output=True, text=True, cwd=cwd, env=env, timeout=10)
    text = re.sub(r"\x1b\][^\x1b]*\x1b\\|\x1b\[[0-9;]*m", "", run.stderr)
    return text.replace("\x01", "").replace("\x02", "")


settings.prompt_segments = "cwd:blue,git:magenta,venv:yellow,status:red"
settings.prompt_symbol = "arrow"
out = prompts("false\nVIRTUAL_ENV=/x/.venv\ncd /\n", cwd=os.path.join(home, "repo", "sub"))
check("bash shows the folder, the branch and the symbol", "~/repo/sub feature/$(touch pwned) ❯ " in out, out)
check("…without running what a branch name holds", not os.path.exists(os.path.join(home, "repo", "sub", "pwned")))
check("…the exit code after a failed command", "~/repo/sub feature/$(touch pwned) ✗ 1 ❯ " in out, out)
check("…the Python environment", "(.venv) ❯ " in out, out)
check("…and no branch outside a repository", "\n/ (.venv) ❯ " in out, out)
settings.prompt_two_lines = True
out = prompts("true\n")
check("the symbol on its own line", "~ \n❯ " in out, repr(out))
settings.custom_prompt = False
check("turning it off removes the file", not os.path.exists(prompt.prompt_file()))
check("…and bash shows the user's own prompt", "own> " in prompts("true\n"), prompts("true\n"))
with open(settings.path) as f:
    saved = f.read().replace('"prompt_symbol": "arrow"', '"prompt_symbol": "bogus"')
with open(settings.path, "w") as f:
    f.write(saved)
check("an unknown symbol in the settings file loads as $", Settings().prompt_symbol == "dollar")

shutil.rmtree(CONFIG_DIR)
shutil.rmtree(home)
print(f"{results.count(True)} passed, {results.count(False)} failed")
sys.exit(0 if all(results) else 1)
