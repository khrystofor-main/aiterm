"""The Markdown models write, as Pango markup: no GTK here, so the parsing
and the coloring are tested on their own (tests/markdown_unit.py).

blocks() splits a message into paragraphs of text, fenced code blocks
(also the indented ones inside list items) and horizontal rules; the chat
draws each as its own widget (chat_view.AgentMessage). inline() and code()
turn a block into markup: headings, **bold**, *italic*, `code`, links, and
shell commands colored by token. The colors come from the terminal's
palette (colors_for), so the chat matches the terminal in light and dark.
"""

import html
import re
from dataclasses import dataclass

FENCE = re.compile(r"^([ \t]*)(`{3,}|~{3,})[ \t]*([\w+.-]*)[^\n`]*$")
RULE = re.compile(r"^[ \t]{0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*$")
HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)(?:[ \t]+#+)?[ \t]*$")
HEADING_SIZES = ("x-large", "large", "medium", "medium", "medium", "medium")
SHELL_LANGUAGES = ("", "bash", "sh", "shell", "zsh", "console", "terminal")


@dataclass(frozen=True)
class Block:
    kind: str  # "text", "code" or "rule"
    text: str = ""
    language: str = ""
    indent: int = 0  # the fence's indent: a code block inside a list item


@dataclass(frozen=True)
class Colors:
    """Pango colors (#rrggbb) for the chat, from a terminal palette."""
    code: str  # `inline code`
    command: str  # the command's name in a shell line
    flag: str  # -x, --long
    string: str
    variable: str  # $HOME, ${x}, $(...)


def _luminance(color):
    channels = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    r, g, b = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    """WCAG contrast ratio of two #rrggbb colors, 1 to 21."""
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def _mix(a, b, amount):
    """#rrggbb `amount` (0 to 1) of the way from a to b."""
    return "#" + "".join(f"{round(int(a[i:i + 2], 16) * (1 - amount) + int(b[i:i + 2], 16) * amount):02x}"
                         for i in (1, 3, 5))


MIN_CONTRAST = 4.0


def colors_for(palette, dark):
    """The chat's colors from a palette (palettes.Palette): each is the
    normal or the bright ANSI color, whichever reads better on the
    terminal's background in this light/dark style, moved toward the text
    color until it is easy to read (GNOME's green on white is not)."""
    foreground, background = palette.dark if dark else palette.light

    def pick(index):
        color = max(palette.colors[index], palette.colors[index + 8], key=lambda c: contrast(c, background))
        for step in range(1, 11):
            if contrast(color, background) >= MIN_CONTRAST:
                break
            color = _mix(color, foreground, 0.1 * step / 2)
        return color
    return Colors(code=pick(5), command=pick(4), flag=pick(6), string=pick(2), variable=pick(5))


def blocks(text):
    """The message's blocks, in order. An unclosed fence runs to the end:
    the message is still streaming in."""
    out, paragraph = [], []

    def flush():
        joined = "\n".join(paragraph).strip("\n")
        if joined.strip():
            out.append(Block("text", joined))
        paragraph.clear()

    lines = text.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        fence = FENCE.match(line)
        if fence:
            flush()
            indent, marker, language = len(fence[1].expandtabs(4)), fence[2], fence[3].lower()
            closing = re.compile(rf"^[ \t]*{re.escape(marker[0])}{{{len(marker)},}}[ \t]*$")
            body = []
            i += 1
            while i < len(lines) and not closing.match(lines[i]):
                body.append(_dedent(lines[i], indent))
                i += 1
            out.append(Block("code", "\n".join(body).rstrip("\n"), language, indent))
            i += 1  # the closing fence
            continue
        if RULE.match(line):
            flush()
            out.append(Block("rule"))
        else:
            paragraph.append(line)
        i += 1
    flush()
    return out


def _dedent(line, indent):
    """The line without up to `indent` columns of leading whitespace."""
    line = line.expandtabs(4)
    strip = len(line) - len(line.lstrip(" "))
    return line[min(strip, indent):]


def _escape(text):
    return html.escape(text, quote=False)


def code_span(text, colors=None):
    """`text` as inline code: monospace, and in color with a faint
    background of that color when there are colors."""
    if colors is None:
        return f"<tt>{_escape(text)}</tt>"
    return (f'<span font_family="monospace" foreground="{colors.code}" '
            f'background="{colors.code}" bgalpha="14%">{_escape(text)}</span>')


# `code`, ``code with ` in it``: as in CommonMark, the closing run of
# backticks is as long as the opening one, so a shorter or longer run inside
# (a ``` written in the text) is part of the code
CODE_SPAN = re.compile(r"(?<!`)(`+)(?!`)(.+?)(?<!`)\1(?!`)")


def _code_text(text):
    """A code span's text: one space inside each backtick run is padding
    (`` `x` ``), unless the span is only spaces."""
    if len(text) > 1 and text[0] == text[-1] == " " and text.strip(" "):
        return text[1:-1]
    return text


def inline(text, colors=None):
    """A text block as markup: headings, list bullets, `code`, **bold**,
    *italic*, ~~struck~~ and links."""
    spans = []

    def stash(match):
        spans.append(code_span(_code_text(match[2]), colors))
        return f"\x00{len(spans) - 1}\x00"
    # Code first, so nothing inside it is taken for formatting
    text = CODE_SPAN.sub(stash, text.replace("\x00", ""))

    def link(match):  # the tags stashed too: no formatting in the address
        spans.extend((f'<a href="{html.escape(match[2])}">', "</a>"))
        return f"\x00{len(spans) - 2}\x00{match[1]}\x00{len(spans) - 1}\x00"
    text = re.sub(r"\[([^\]\n]+)\]\(((?:https?|file)://[^)\s]+)\)", link, text)
    lines = []
    for line in _escape(text).split("\n"):
        heading = HEADING.match(line)
        if heading:
            size = HEADING_SIZES[len(heading[1]) - 1]
            line = f'<span weight="bold" size="{size}">{heading[2]}</span>'
        else:
            line = re.sub(r"^([ \t]*)[*+-][ \t]+", "\\1• ", line)
        lines.append(line)
    text = "\n".join(lines)
    # Emphasis the model crossed (**a *b** c*) is left as it is written:
    # tags must nest
    for pattern, tag in EMPHASIS:
        marked = re.sub(pattern, rf"<{tag}>\1</{tag}>", text)
        if _nested(marked):
            text = marked
    return re.sub("\x00(\\d+)\x00", lambda m: spans[int(m[1])], text)


EMPHASIS = (
    (r"\*\*(.+?)\*\*", "b"),
    (r"(?<![\w*])\*(?![\s*])([^*\n]+?)(?<!\s)\*(?![\w*])", "i"),
    (r"(?<![\w_])_(?![\s_])([^_\n]+?)(?<!\s)_(?![\w_])", "i"),
    (r"~~(.+?)~~", "s"),
)


def _nested(markup):
    """Whether the tags in the markup close in the order they opened."""
    stack = []
    for closing, name in re.findall(r"<(/?)(\w+)[^>]*>", markup):
        if not closing:
            stack.append(name)
        elif not stack or stack.pop() != name:
            return False
    return not stack


SHELL_TOKEN = re.compile(r"""
    (?P<comment>(?<![^\s;|&(])\#[^\n]*)
  | (?P<string>'[^']*'?|"(?:[^"\\]|\\.)*"?)
  | (?P<variable>\$\{[^}\n]*\}?|\$[A-Za-z_][A-Za-z0-9_]*|\$[0-9@#?$!*-])
  | (?P<subshell>\$\(|`)
  | (?P<operator>\|\||&&|[|;&()]|[0-9]*>>?&?[0-9]*|<)
  | (?P<word>[^\s'"$|;&<>()`]+)
  | (?P<space>\s+)
  | (?P<other>.)
""", re.X)


def shell(text, colors):
    """Shell code as markup, colored by token: the command's name, its
    flags, strings, variables; comments and operators dimmed."""
    out = []
    command = True  # the next word is a command's name
    continued = False  # the line ended with a backslash
    for m in SHELL_TOKEN.finditer(text):
        kind, token = m.lastgroup, m[0]
        piece = _escape(token)
        if kind == "space":
            if "\n" in token:
                command = command or not continued
            continued = False
            out.append(piece)
            continue
        continued = token == "\\"
        if kind == "comment":
            out.append(f'<span fgalpha="55%"><i>{piece}</i></span>')
        elif kind == "string":
            out.append(f'<span foreground="{colors.string}">{piece}</span>')
            command = False
        elif kind == "variable":
            out.append(f'<span foreground="{colors.variable}">{piece}</span>')
            command = False
        elif kind in ("subshell", "operator"):
            out.append(f'<span foreground="{colors.variable}">{piece}</span>' if kind == "subshell"
                       else f'<span fgalpha="65%">{piece}</span>')
            command = token in ("$(", "`", "|", "||", "&&", ";", "&", "(")
        elif kind == "word":
            if command and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", token):
                out.append(piece)  # NAME=value before the command
            elif command:
                out.append(f'<span foreground="{colors.command}" weight="bold">{piece}</span>')
                command = token in ("sudo", "env", "time", "exec", "nohup", "xargs", "then", "do", "else", "!")
            elif token.startswith("-") and len(token) > 1:
                out.append(f'<span foreground="{colors.flag}">{piece}</span>')
            else:
                out.append(piece)
        elif token == "$" and command:
            out.append(f'<span fgalpha="55%">{piece}</span>')  # a prompt: $ ls
        else:
            out.append(piece)
            command = False
    return "".join(out)


def code(text, language="", colors=None):
    """A fenced code block's text as markup: shell colored by token,
    other languages as they are."""
    if colors is not None and language in SHELL_LANGUAGES:
        return shell(text, colors)
    return _escape(text)


def to_pango(text, colors=None):
    """The whole message as markup for one label (where the blocks are not
    drawn apart): code blocks in monospace, rules as a dimmed line."""
    out = []
    for block in blocks(text):
        if block.kind == "text":
            out.append(inline(block.text, colors))
        elif block.kind == "code":
            out.append(f"<tt>{code(block.text, block.language, colors)}</tt>")
        else:
            out.append('<span fgalpha="40%">' + "─" * 12 + "</span>")
    return "\n".join(out)
