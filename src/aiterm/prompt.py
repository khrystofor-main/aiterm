"""Aiterm's own shell prompt, set up in Preferences → Prompt (no GTK here).

The prompt is a row of segments (user@host, folder, git branch…), each in one
of the palette's colors, then a symbol. build_ps1() turns it into a bash PS1;
the app writes that to prompt_file(), and shell/integration.bash reads the
file at every prompt, so a change shows at the next prompt of every open tab.
No file: the user's own prompt from ~/.bashrc. Nothing outside Aiterm's config
folder is changed.

Colors are ANSI color numbers, so the prompt follows the terminal's palette.
Segments that can be empty (git branch, Python environment, exit code) come
from variables integration.bash sets before each prompt, and drop out with the
space after them when there is nothing to show.
"""

import os
import unicodedata
from dataclasses import dataclass

from aiterm.settings import Settings, config_dir

# key: (label, SGR code, index in the palette's 16 colors or None for the foreground)
COLORS = {
    "default": ("Default", 39, None),
    "red": ("Red", 31, 1),
    "green": ("Green", 32, 2),
    "yellow": ("Yellow", 33, 3),
    "blue": ("Blue", 34, 4),
    "magenta": ("Magenta", 35, 5),
    "cyan": ("Cyan", 36, 6),
    "gray": ("Gray", 90, 8),
}


@dataclass(frozen=True)
class Segment:
    label: str
    sample: str  # what the preview shows
    escape: str = ""  # a PS1 escape, always shown
    var: str = ""  # or a variable from integration.bash, shown when set
    template: str = "{}"  # the variable's value in context


SEGMENTS = {
    "user_host": Segment("User and Host", "lex@ubuntu", escape=r"\u@\h"),
    "cwd": Segment("Folder", "~/projects/aiterm", escape=r"\w"),
    "git": Segment("Git Branch", "main", var="__aiterm_git"),
    "venv": Segment("Python Environment", "(.venv)", var="__aiterm_venv", template="({})"),
    "time": Segment("Time", "14:05", escape=r"\A"),
    "status": Segment("Exit Code of a Failed Command", "✗ 1", var="__aiterm_status", template="✗ {}"),
}

# key: (label, PS1 text, preview)
SYMBOLS = {
    "dollar": ("$ (# as root)", r"\$", "$"),
    "arrow": ("❯", "❯", "❯"),
    "chevron": ("›", "›", "›"),
    "lambda": ("λ", "λ", "λ"),
    "percent": ("%", "%", "%"),
}

DEFAULT_SEGMENTS = Settings.find_property("prompt-segments").get_default_value()
DEFAULT_COLORS = dict(part.lstrip("-").split(":") for part in DEFAULT_SEGMENTS.split(","))


@dataclass
class Item:
    key: str
    color: str
    enabled: bool


def parse_segments(spec):
    """'user_host:green,-time:gray,…' → [Item]: every known segment once, in
    that order; a leading '-' turns it off. Segments the text leaves out come
    last, off, so an older settings file still gets newer segments."""
    items, seen = [], set()
    for part in spec.split(","):
        part = part.strip()
        enabled = not part.startswith("-")
        key, _, color = part.lstrip("-").partition(":")
        if key in SEGMENTS and key not in seen:
            seen.add(key)
            items.append(Item(key, color if color in COLORS else "default", enabled))
    for key in SEGMENTS:
        if key not in seen:
            items.append(Item(key, DEFAULT_COLORS[key], False))
    return items


def format_segments(items):
    return ",".join(f"{'' if i.enabled else '-'}{i.key}:{i.color}" for i in items)


def _sgr(color, bold):
    code = COLORS[color][1]
    return rf"\[\e[{'1;' if bold else ''}{code}m\]"


RESET = r"\[\e[0m\]"


def clean_text(text):
    """The user's own text for a segment, on one line: control characters
    (newlines, escape sequences) would break the prompt, so they go. Emoji
    joiners and variation selectors stay."""
    return "".join(c for c in (text or "") if unicodedata.category(c) not in ("Cc", "Zl", "Zp")).strip()


def ps1_literal(text):
    r"""`text` as PS1 that bash shows as it is. Bash first decodes the
    prompt's backslash escapes (\u, \w…), then expands $, `…` and \ in the
    result as in double quotes (promptvars). So a \ is written \\\\ and $ and `
    get \\ in front: decoding leaves one backslash, which the expansion
    takes as "literally". Emoji and other printable text stay as they are,
    outside \[ \], so bash counts their width."""
    return clean_text(text).replace("\\", "\\" * 4).replace("$", r"\\$").replace("`", r"\\`")


def build_ps1(spec, symbol="dollar", two_lines=False, bold=True, user_host=""):
    """The PS1 for these settings; `user_host`, when set, is shown instead
    of user@host."""
    parts = []
    for item in parse_segments(spec):
        if not item.enabled:
            continue
        segment = SEGMENTS[item.key]
        if segment.escape:
            escape = segment.escape
            if item.key == "user_host" and clean_text(user_host):
                escape = ps1_literal(user_host)
            parts.append(f"{_sgr(item.color, bold)}{escape}{RESET} ")
        else:
            value = segment.template.format("${%s}" % segment.var)
            parts.append("${%s:+%s%s%s }" % (segment.var, _sgr(item.color, bold), value, RESET))
    parts.append("\\n" if two_lines else "")
    parts.append(f"{_sgr('default', bold)}{SYMBOLS.get(symbol, SYMBOLS['dollar'])[1]}{RESET} ")
    return "".join(parts)


def preview_markup(spec, symbol, two_lines, bold, palette, dark, user_host=""):
    """Pango markup of the prompt with sample values, in `palette`'s colors."""
    from html import escape

    foreground = (palette.dark if dark else palette.light)[0]

    def span(text, color):
        index = COLORS[color][2]
        value = foreground if index is None else palette.colors[index]
        weight = ' weight="bold"' if bold else ""
        return f'<span foreground="{value}"{weight}>{escape(text)}</span>'

    def sample(key):
        if key == "user_host" and clean_text(user_host):
            return clean_text(user_host)
        return SEGMENTS[key].sample

    text = "".join(span(sample(i.key), i.color) + " " for i in parse_segments(spec) if i.enabled)
    if two_lines:
        text = text.rstrip(" ") + "\n"
    return text + span(SYMBOLS.get(symbol, SYMBOLS["dollar"])[2], "default") + " "


def prompt_file():
    return os.path.join(config_dir(), "prompt")


def write_prompt_file(settings):
    """Writes the PS1 for open and new shells, or removes the file when the
    user keeps their own prompt."""
    path = prompt_file()
    if not settings.custom_prompt:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(build_ps1(settings.prompt_segments, settings.prompt_symbol,
                          settings.prompt_two_lines, settings.prompt_bold, settings.prompt_user_host))
    os.replace(tmp, path)  # a shell never reads half a prompt


PROPERTIES = ("custom-prompt", "prompt-segments", "prompt-symbol", "prompt-two-lines", "prompt-bold",
              "prompt-user-host")


def follow(settings):
    """Keeps the prompt file in step with the settings."""
    write_prompt_file(settings)
    for name in PROPERTIES:
        settings.connect(f"notify::{name}", lambda s, _: write_prompt_file(s))
