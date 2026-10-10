#!/usr/bin/env python3
"""Unit tests for the chat's Markdown (markdown.py): blocks, inline markup,
shell coloring and the palette's colors. No GTK; markup is checked with
Pango's own parser when it is there.
Run: tests/markdown_unit.py"""

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from aiterm import markdown  # noqa: E402
from aiterm.markdown import Block, blocks, code, inline, shell, to_pango  # noqa: E402
from aiterm.palettes import PALETTES  # noqa: E402

try:
    import gi
    gi.require_version("Pango", "1.0")
    from gi.repository import GLib, Pango
except (ImportError, ValueError):
    Pango = None

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(f"  {'ok  ' if ok else 'FAIL'} {name}" + ("" if ok else f"\n       {detail}"))


def valid(markup):
    if Pango is None:
        return True
    try:
        Pango.parse_markup(re.sub(r'<a href="[^"]*">|</a>', "", markup), -1, "\0")  # <a> is Gtk.Label's
        return True
    except GLib.Error:
        return False


COLORS = markdown.colors_for(PALETTES["GNOME"], dark=True)

answer = """Two ways:

---

## Way 1

1. Create a link:
   ```bash
   ln -s "$(pwd)/CLAUDE.md" ~/Vault/CLAUDE.md
   ```
2. Open it.
"""
got = blocks(answer)
check("text, a rule, text, the code block in the list item, text",
      [b.kind for b in got] == ["text", "rule", "text", "code", "text"], str(got))
check("…the code block inside the list item is dedented and keeps its language",
      got[3] == Block("code", 'ln -s "$(pwd)/CLAUDE.md" ~/Vault/CLAUDE.md', "bash", 3), repr(got[3]))
check("…no backticks are left in the text", not any("```" in b.text for b in got))
check("a fence at the margin has no indent", blocks("```\nls\n```") == [Block("code", "ls", "", 0)])
check("~~~ fences and longer ``` fences work",
      blocks("~~~sh\nls\n~~~") == [Block("code", "ls", "sh")]
      and blocks("````\na\n```\nb\n````") == [Block("code", "a\n```\nb")])
check("an unclosed fence runs to the end (the answer is still streaming)",
      blocks("Run:\n```bash\nls -l") == [Block("text", "Run:"), Block("code", "ls -l", "bash")])
check("***, ___ and - - - are rules too",
      [b.kind for b in blocks("a\n***\nb\n___\nc\n- - -\nd")] == ["text", "rule"] * 3 + ["text"])
check("a list item is not a rule", [b.kind for b in blocks("- one\n- two")] == ["text"])
check("blank lines around blocks are dropped",
      [b.text for b in blocks("\n\nhi\n\n---\n\n")] == ["hi", ""])

check("inline code gets the palette's color and a faint background",
      inline("run `ls -l` now", COLORS) == f'run <span font_family="monospace" foreground="{COLORS.code}" '
      f'background="{COLORS.code}" bgalpha="14%">ls -l</span> now', inline("run `ls -l` now", COLORS))
check("…and is plain <tt> without colors", inline("`x`") == "<tt>x</tt>")
check("nothing inside inline code is formatted or left unescaped",
      inline("`**a** <b> *c*`") == "<tt>**a** &lt;b&gt; *c*</tt>", inline("`**a** <b> *c*`"))
check("headings by level",
      inline("# One\n## Two\n### Three") == '<span weight="bold" size="x-large">One</span>\n'
      '<span weight="bold" size="large">Two</span>\n<span weight="bold" size="medium">Three</span>',
      inline("# One\n## Two\n### Three"))
check("bold, italic, struck", inline("**b** *i* _j_ ~~s~~") == "<b>b</b> <i>i</i> <i>j</i> <s>s</s>",
      inline("**b** *i* _j_ ~~s~~"))
check("bullets; an asterisk between spaces and snake_case stay as they are",
      inline("- a\n  * b\n2 * 3 * 4 my_var_name") == "• a\n  • b\n2 * 3 * 4 my_var_name",
      inline("- a\n  * b\n2 * 3 * 4 my_var_name"))
check("links, with formatting characters in the address left alone",
      inline("[docs](https://x.org/a_b_c*d)") == '<a href="https://x.org/a_b_c*d">docs</a>',
      inline("[docs](https://x.org/a_b_c*d)"))
check("escapes < > &", inline("a < b && c") == "a &lt; b &amp;&amp; c")

line = shell('$ echo "hi $USER" | grep -v x 2>/dev/null  # note', COLORS)
check("shell: the command's name, its flags, strings, operators and comments",
      f'<span foreground="{COLORS.command}" weight="bold">echo</span>' in line
      and f'<span foreground="{COLORS.string}">"hi $USER"</span>' in line
      and f'<span foreground="{COLORS.command}" weight="bold">grep</span>' in line
      and f'<span foreground="{COLORS.flag}">-v</span>' in line
      and '<span fgalpha="65%">2&gt;</span>/dev/null' in line
      and '<span fgalpha="55%"><i># note</i></span>' in line
      and line.startswith('<span fgalpha="55%">$</span>'), line)
check("…an argument is not a command, the word after sudo is",
      'weight="bold">x<' not in line
      and f'<span foreground="{COLORS.command}" weight="bold">apt</span>' in shell("sudo apt install x", COLORS))
check("…a new line starts a command, a continued one does not",
      shell("ls \\\n  -l\ncd", COLORS).count('weight="bold"') == 2, shell("ls \\\n  -l\ncd", COLORS))
check("…variables", f'<span foreground="{COLORS.variable}">$HOME</span>' in shell("cd $HOME", COLORS))
check("other languages are only escaped", code("if a < b: pass", "python", COLORS) == "if a &lt; b: pass")

whole = to_pango(answer, COLORS)
check("the whole answer as one label's markup is valid Pango markup", valid(whole), whole)
check("…every inline piece is valid markup too",
      all(valid(inline(t, COLORS)) for t in ("**a *b** c*", "`a", "[x](https://a.b/<>)", "a & b")))
check("…with the old plain form when there are no colors",
      to_pango("a < b `x` **y**\n```sh\nls <dir>\n```") == "a &lt; b <tt>x</tt> <b>y</b>\n<tt>ls &lt;dir&gt;</tt>")

for name, palette in PALETTES.items():
    for dark in (False, True):
        colors = markdown.colors_for(palette, dark)
        background = (palette.dark if dark else palette.light)[1]
        worst = min(markdown.contrast(c, background) for c in vars(colors).values())
        check(f"{name} {'dark' if dark else 'light'}: every color reads on the background ({worst:.1f}:1)",
              worst >= markdown.MIN_CONTRAST - 0.05)
check("colors follow the palette and the style",
      markdown.colors_for(PALETTES["GNOME"], False) != markdown.colors_for(PALETTES["GNOME"], True)
      != markdown.colors_for(PALETTES["Tango"], True))

print(f"{results.count(True)} passed, {results.count(False)} failed")
sys.exit(0 if all(results) else 1)
