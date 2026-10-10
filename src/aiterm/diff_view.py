"""A diff drawn in the app: added lines green, removed lines red, the
hunk headers quiet. Used by the chat's edit cards (chat_view.py) and the
approval bar (approval.py); the diff itself comes from edits.diff().
"""

import html

from gi.repository import Gtk

from aiterm import edits

MAX_LINES = 400  # more than anyone reads in a panel; the file has the rest
WIDE = 160  # lines are padded to the longest, up to this, so colors make blocks

STYLES = {
    "+": 'foreground="#26a269" background="#2ec27e" bgalpha="18%"',
    "-": 'foreground="#c01c28" background="#e01b24" bgalpha="16%"',
    "@": 'alpha="55%"',
}


def body(lines):
    """The diff without its ---/+++ header lines."""
    return [line for line in lines if not line.startswith(("--- ", "+++ "))]


def markup(lines):
    """Pango markup for the diff's lines."""
    lines = body(lines)
    hidden = len(lines) - MAX_LINES
    lines = lines[:MAX_LINES]
    width = min(max((len(line) for line in lines), default=0), WIDE)
    out = []
    for line in lines:
        text = html.escape(line.ljust(width), quote=False)
        style = STYLES.get(line[:1])
        out.append(f"<span {style}>{text}</span>" if style else text)
    if hidden > 0:
        out.append(f'<span alpha="55%">… {hidden} more lines</span>')
    return "\n".join(out)


def summary(lines):
    """`+3 −1`, the size of the change."""
    added, removed = edits.counts(lines)
    return f"+{added} −{removed}"


class DiffView(Gtk.ScrolledWindow):
    """The diff, scrolling past `height` pixels."""

    def __init__(self, height=320, **kwargs):
        super().__init__(propagate_natural_height=True, max_content_height=height,
                         hscrollbar_policy=Gtk.PolicyType.AUTOMATIC, vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
                         **kwargs)
        self.add_css_class("diff-view")
        self.label = Gtk.Label(xalign=0, yalign=0, selectable=True, wrap=False, margin_start=8, margin_end=8,
                               margin_top=6, margin_bottom=6)
        self.label.add_css_class("monospace")
        self.set_child(self.label)
        self.lines = []

    def set_diff(self, lines):
        self.lines = list(lines)
        self.label.set_markup(markup(self.lines))


CSS = """
.diff-view { background: alpha(@view_bg_color, 0.6); border-radius: 6px; }
.diff-view label { font-size: 0.9em; }
"""
