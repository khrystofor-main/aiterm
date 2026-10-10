"""The chat in the agent panel: the conversation with agy, drawn by the app.

Fed by AgentProcess (chat.py). Each step of agy's stream is one row, keyed
by its step index, and updated as more events for it arrive: the agent's
text grows with each delta, a tool call gets its output when it is done.
run_command is a collapsible block with the command, its output and exit
code, and Run / Don't Run while it waits for approval (approval.py). A
change to a file is a card with its diff: edit_file / write_file with
Apply / Reject while it waits, agy's own file tools as a record of what
they wrote (edits.py, diff_view.py).

Animations follow the preset (animations.py): new rows fade in rising from
below, the agent's text fades in piece by piece as it streams, command
blocks open smoothly and show a running bar, three dots pulse while the
agent works, the view glides to new messages unless the user scrolled up
(then a "New messages" button appears), and a command the agent runs in
the terminal lights up its block in the same color as its output's stripe.
"""

import html
import os
import re

from gi.repository import Gdk, GLib, Gtk, Pango

from aiterm import animations, edits
from aiterm.diff_view import DiffView, summary

# Tools of agy's own, shown as one quiet line: name -> (verb, parameter to show)
TOOL_LABELS = {
    "view_file": ("Read", "AbsolutePath"),
    "list_dir": ("Listed", "DirectoryPath"),
    "write_to_file": ("Wrote", "TargetFile"),
    "replace_file_content": ("Edited", "TargetFile"),
    "multi_replace_file_content": ("Edited", "TargetFile"),
    "grep_search": ("Searched for", "Query"),
    "find_by_name": ("Searched for", "Pattern"),
    "run_command": ("Ran in its own shell", "CommandLine"),
    "read_url_content": ("Read", "Url"),
    "search_web": ("Searched the web for", "query"),
}
TERMINAL_SERVER = "aiterm_terminal"
EDIT_TOOLS = ("edit_file", "write_file")
OPEN_LINES = 30  # a diff up to this long shows open; a longer one opens on a click
OUTPUT_LINES = 400  # the rest is in the user's terminal
HIDDEN = object()  # a step not shown (see is_internal)


def markdown_to_pango(text):
    """The Markdown models write most (code blocks, `code`, **bold**,
    headings, links) as Pango markup for a Gtk.Label."""
    parts = re.split(r"^```[^\n]*\n(.*?)^```[ \t]*$", text, flags=re.S | re.M)
    out = []
    for i, part in enumerate(parts):
        if i % 2:  # inside a fence
            out.append(f"<tt>{html.escape(part.rstrip(chr(10)), quote=False)}</tt>")
            continue
        part = html.escape(part, quote=False)
        part = re.sub(r"`([^`\n]+)`", r"<tt>\1</tt>", part)
        part = re.sub(r"\*\*([^*\n]+)\*\*", r"<b>\1</b>", part)
        part = re.sub(r"^#{1,6} +(.+)$", r"<b>\1</b>", part, flags=re.M)
        part = re.sub(r"\[([^\]\n]+)\]\(((?:https?|file)://[^)\s]+)\)",
                      lambda m: f'<a href="{html.escape(html.unescape(m[2]))}">{m[1]}</a>', part)
        part = re.sub(r"^([ \t]*)[*-] ", "\\1• ", part, flags=re.M)
        out.append(part)
    return "".join(out).strip("\n")


def _label(text="", css=(), markup=False, mono=False, **kwargs):
    kwargs = {"xalign": 0, "wrap": True, "wrap_mode": Pango.WrapMode.WORD_CHAR, "selectable": True, **kwargs}
    label = Gtk.Label(**kwargs)
    if markup:
        label.set_markup(text)
    else:
        label.set_label(text)
    for name in (*css, *(["monospace"] if mono else [])):
        label.add_css_class(name)
    return label


class CommandRow(Gtk.Box):
    """run_command: `$ command`, collapsible output and exit code, and the
    approval buttons while the command waits for the user."""

    def __init__(self, command, approval, force_animations=False):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.add_css_class("card")
        self.add_css_class("command-card")
        self.command = command
        self.approval = approval
        self.force_animations = force_animations
        self.running = True
        # A running bar under the header while the command works, or a
        # spinner when that effect is off
        bar = animations.Animations.get().effect("tool_card", force_animations) is not None
        self.spinner = Gtk.Spinner(spinning=True, visible=not bar)
        self.icon = Gtk.Image(visible=False)
        title = _label(f"$ {command}", mono=True, hexpand=True, selectable=False)
        header = Gtk.Box(spacing=8)
        for widget in (self.spinner, self.icon, title):
            header.append(widget)
        # The expander only toggles; the output opens in a revealer below it,
        # which can animate both ways
        self.expander = Gtk.Expander(label_widget=header, margin_start=8, margin_end=8, margin_top=6, margin_bottom=6)
        self.output = _label(mono=True, css=("dim-label",), margin_start=8, margin_end=8)
        self.revealer = Gtk.Revealer(child=self.output, transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.expander.connect("notify::expanded", lambda *_: self._on_expanded())
        self.append(self.expander)
        self.append(self.revealer)
        self.running_bar = Gtk.Box(visible=bar, margin_start=8, margin_end=8)
        self.running_bar.add_css_class("running-bar")
        self.append(self.running_bar)

        self.status = _label(css=("caption", "dim-label"), margin_start=8, margin_end=8, visible=False)
        self.append(self.status)
        self.buttons = Gtk.Box(spacing=6, halign=Gtk.Align.END, margin_end=8, margin_bottom=8, visible=False)
        self.run_button = Gtk.Button(label="Run")
        self.run_button.add_css_class("suggested-action")
        self.run_button.connect("clicked", lambda *_: approval.answer(True))
        self.skip_button = Gtk.Button(label="Don't Run")
        self.skip_button.connect("clicked", lambda *_: approval.answer(False))
        self.buttons.append(self.skip_button)
        self.buttons.append(self.run_button)
        self.append(self.buttons)
        self._handler = approval.connect("changed", lambda *_: self._sync_approval())
        self._sync_approval()

    def _on_expanded(self):
        self.revealer.set_transition_duration(animations.duration(self, "tool_card", self.force_animations))
        self.revealer.set_reveal_child(self.expander.get_expanded())

    def light_up(self):
        """The agent ran this command in the terminal: the block lights up in
        the color of its output's stripe there, then fades back (CSS)."""
        params = animations.Animations.get().effect("link", self.force_animations)
        if params is None:
            return
        # Held for a while, then fading out (a CSS transition, see animated_css)
        self.add_css_class("agent-link")
        GLib.timeout_add(int(params["duration"] * 0.4),
                         lambda: self.remove_css_class("agent-link") or GLib.SOURCE_REMOVE)

    def _sync_approval(self):
        waiting = self.approval.pending and self.approval.kind == "command" and self.running
        self.approval.show_in(self, waiting)
        self.buttons.set_visible(waiting)
        self.status.set_visible(waiting)
        self.status.set_label("Waiting for you to run it in your terminal")

    def finish(self, output, failed):
        """The tool's result: the text the model got."""
        if self._handler:
            self.approval.disconnect(self._handler)
            self._handler = None
            self.approval.show_in(self, False)
        self.buttons.set_visible(False)
        self.running = False
        self.spinner.set_visible(False)
        self.running_bar.set_visible(False)
        lines = output.split("\n")
        if lines and lines[0].startswith("[terminal folder:"):
            lines = lines[1:]
        if lines and lines[0].startswith("$ "):
            lines = lines[1:]  # the command, already in the title
        exit_code = 0
        if lines and (m := re.fullmatch(r"\[exit code (\d+)\]", lines[-1])):
            exit_code = int(m[1])
            lines = lines[:-1]
        self.output.set_label("\n".join(lines[-OUTPUT_LINES:]) or "(no output)")
        failed = failed or exit_code != 0
        self.icon.set_from_icon_name("dialog-error-symbolic" if failed else "object-select-symbolic")
        self.icon.add_css_class("error" if failed else "success")
        self.icon.set_visible(True)
        if failed:
            self.status.set_label(f"Exit code {exit_code}" if exit_code else output.split("\n")[0])
            self.status.remove_css_class("dim-label")
            self.status.add_css_class("error")
            self.status.set_visible(True)
        else:
            self.status.set_visible(False)


class EditRow(Gtk.Box):
    """A change to a file: its name, the size of the change and the diff,
    with Apply / Reject while it waits for the user. `approval` is None for
    agy's own file tools, which write without asking."""

    def __init__(self, path, diff, approval=None, force_animations=False):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.add_css_class("card")
        self.add_css_class("edit-card")
        self.path = path
        self.approval = approval
        self.force_animations = force_animations
        self.running = True
        self.proposed = False  # the app has the real diff (with context) from ProposeEdit
        self.change = None  # (before, after) once the app wrote it, for Undo; before None: a new file
        self.spinner = Gtk.Spinner(spinning=True)
        self.icon = Gtk.Image(visible=False)
        self.title = _label(css=("monospace",), hexpand=True, selectable=False, wrap=False,
                            ellipsize=Pango.EllipsizeMode.START)
        self.size = _label(css=("caption", "dim-label"), selectable=False, wrap=False)
        header = Gtk.Box(spacing=8)
        for widget in (self.spinner, self.icon, self.title, self.size):
            header.append(widget)
        self.expander = Gtk.Expander(label_widget=header, margin_start=8, margin_end=8, margin_top=6, margin_bottom=6)
        self.diff = DiffView(margin_start=8, margin_end=8, margin_bottom=4)
        self.revealer = Gtk.Revealer(child=self.diff, transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.expander.connect("notify::expanded", lambda *_: self._on_expanded())
        self.append(self.expander)
        self.append(self.revealer)
        self.status = _label(css=("caption", "dim-label"), margin_start=8, margin_end=8, visible=False)
        self.append(self.status)
        self.buttons = Gtk.Box(spacing=6, halign=Gtk.Align.END, margin_end=8, margin_bottom=8, visible=False)
        self.reject_button = Gtk.Button(label="Reject")
        self.reject_button.connect("clicked", lambda *_: self._answer(False))
        self.apply_button = Gtk.Button(label="Apply")
        self.apply_button.add_css_class("suggested-action")
        self.apply_button.connect("clicked", lambda *_: self._answer(True))
        self.undo_button = Gtk.Button(label="Undo", visible=False,
                                      tooltip_text="Put the file back as it was before this change")
        self.undo_button.connect("clicked", lambda *_: self._undo())
        self.buttons.append(self.reject_button)
        self.buttons.append(self.apply_button)
        self.buttons.append(self.undo_button)
        self.append(self.buttons)
        self.set_diff(path, diff)
        self._handler = None
        if approval is not None:
            self._handler = approval.connect("changed", lambda *_: self._sync_approval())
            self._sync_approval()

    def set_diff(self, path, diff, proposed=False):
        self.path = path or self.path
        self.proposed = self.proposed or proposed
        new = bool(diff) and diff[0] == "--- /dev/null"
        self.title.set_label(f"{'Create' if new else 'Edit'} {os.path.basename(self.path)}")
        self.set_tooltip_text(self.path)
        self.size.set_label(summary(diff) if diff else "")
        self.diff.set_diff(diff)
        self.expander.set_expanded(bool(diff) and (self.waiting or len(diff) <= OPEN_LINES))

    @property
    def waiting(self):
        a = self.approval
        return bool(a and self.running and a.pending and a.kind == "edit" and a.path == self.path)

    def _on_expanded(self):
        self.revealer.set_transition_duration(animations.duration(self, "tool_card", self.force_animations))
        self.revealer.set_reveal_child(self.expander.get_expanded())

    def _answer(self, apply):
        if self.waiting:
            self.approval.answer(apply)

    def _sync_approval(self):
        waiting = self.waiting
        self.approval.show_in(self, waiting)
        self.buttons.set_visible(waiting)
        if waiting:
            self.expander.set_expanded(True)
        if self.running:
            self.status.set_label("Waiting for you to apply it")
            self.status.set_visible(waiting)

    def finish(self, output, failed):
        if self._handler:
            self.approval.disconnect(self._handler)
            self._handler = None
            self.approval.show_in(self, False)
        self.running = False
        self.buttons.set_visible(False)
        self.spinner.set_visible(False)
        self.icon.set_from_icon_name("dialog-error-symbolic" if failed else "object-select-symbolic")
        self.icon.add_css_class("error" if failed else "success")
        self.icon.set_visible(True)
        if failed:
            # The first sentence: "The user rejected the change to …", or why it failed
            self.status.set_label(output.split(". ")[0].rstrip(".") + "." if output else "Not changed.")
            self.status.remove_css_class("dim-label")
            self.status.add_css_class("error")
            self.status.set_visible(True)
            self.expander.set_expanded(False)
        elif self.approval is None:
            self.status.set_label("Written by agy's own file tool, without asking")
            self.status.set_visible(True)
        else:
            self.status.set_visible(False)
            if self.change is not None:
                self.reject_button.set_visible(False)
                self.apply_button.set_visible(False)
                self.undo_button.set_visible(True)
                self.buttons.set_visible(True)

    def _undo(self):
        """Puts the file back, if nothing changed it since (edits.undo).
        The agent is not told: it reads the file again before its next edit."""
        try:
            edits.undo(self.path, *self.change)
        except edits.EditError as error:
            self.status.set_label(str(error))
            self.status.remove_css_class("dim-label")
            self.status.add_css_class("error")
        else:
            self.buttons.set_visible(False)
            self.icon.remove_css_class("success")
            self.icon.set_from_icon_name("edit-undo-symbolic")
            self.status.set_label("Undone: the file is back as it was")
            self.expander.set_expanded(False)
        self.status.set_visible(True)


class ToolRow(Gtk.Box):
    """Any other tool: one quiet line, with a spinner while it runs."""

    def __init__(self, text):
        super().__init__(spacing=8, margin_start=4)
        self.running = True
        self.spinner = Gtk.Spinner(spinning=True)
        self.append(self.spinner)
        self.label = _label(text, css=("caption", "dim-label"), selectable=False, hexpand=True,
                            ellipsize=Pango.EllipsizeMode.MIDDLE, wrap=False)
        self.append(self.label)

    def finish(self, output, failed):
        self.running = False
        self.spinner.set_visible(False)
        if failed:
            self.label.remove_css_class("dim-label")
            self.label.add_css_class("error")
            self.label.set_tooltip_text(output)


def tool_text(name, info):
    params = info.get("parameters") or {}
    if name == "call_mcp_tool":
        server, tool = params.get("ServerName", ""), params.get("ToolName", "")
        if server == TERMINAL_SERVER:
            return {"read_terminal": "Read your terminal", "get_cwd": "Checked your terminal's folder",
                    "wait_for_command": "Waited for the command in your terminal"}.get(tool, tool)
        return f"{server}: {tool}"
    verb, key = TOOL_LABELS.get(name, (name.replace("_", " ").capitalize(), None))
    value = params.get(key) if key else None
    if isinstance(value, str) and value.startswith("/"):
        value = os.path.basename(value.rstrip("/")) or value  # the full path is in the tooltip
    return f"{verb} {value}" if value else verb


def planned_edit(tool, arguments):
    """(path, diff) for an edit_file / write_file call, from its arguments
    alone, until the app sends the real one (ProposeEdit)."""
    path = arguments.get("path") if isinstance(arguments.get("path"), str) else "file"
    if tool == "write_file":
        content = arguments.get("content")
        return path, edits.diff(None, content, path) if isinstance(content, str) else []
    old, new = arguments.get("old_text"), arguments.get("new_text")
    if not (isinstance(old, str) and isinstance(new, str)):
        return path, []
    return path, ["@@" if line.startswith("@@") else line for line in edits.diff(old, new, path)]


def is_internal(name, info):
    """agy reading an MCP tool's schema from its own cache before calling it:
    not something the user did or needs to see."""
    path = (info.get("parameters") or {}).get("AbsolutePath", "")
    return name == "view_file" and "/mcp/" in path and path.endswith(".json") and "/.gemini/" in path


class StreamFade:
    """Fades in the newest text of a label as the agent's answer streams:
    Pango's foreground alpha on the bytes each delta added, rising to full."""

    def __init__(self, label, force=False):
        self.label, self.force = label, force
        self.pieces = []  # [first byte, end byte, started (ms)]
        self.params = None
        self._tick = None

    def grew(self, old_end):
        """The label's text was set again; it used to end at byte old_end."""
        end = len(self.label.get_text().encode())
        params = animations.Animations.get().effect("stream", self.force)
        if end > old_end and params and (self.force or animations.window_active(self.label)):
            self.params = params
            self.pieces.append([old_end, end, GLib.get_monotonic_time() / 1000])
            if self._tick is None:
                self._tick = self.label.add_tick_callback(lambda *_: self.apply())
            # Never left see-through, should frames stop (a hidden window)
            GLib.timeout_add(int(params["duration"]) + 100, lambda: self.apply() and False)
        self.apply()

    def apply(self):
        now = GLib.get_monotonic_time() / 1000
        if self.params:
            duration = self.params["duration"]
            self.pieces = [p for p in self.pieces if now - p[2] < duration]
        attributes = Pango.AttrList()
        for first, end, started in self.pieces:
            shown = animations.progress(self.params["curve"], (now - started) / self.params["duration"])
            alpha = Pango.attr_foreground_alpha_new(max(1, min(65535, int(65535 * shown))))
            alpha.start_index, alpha.end_index = first, end
            attributes.insert(alpha)
        self.label.set_attributes(attributes if self.pieces else None)
        if self.pieces:
            return GLib.SOURCE_CONTINUE
        self._tick = None
        return GLib.SOURCE_REMOVE


class ChatView(Gtk.Box):
    def __init__(self, process, approval, force_animations=False):
        """`force_animations`: animate even when animations are off (the
        preview on the Animations page)."""
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.process = process
        self.approval = approval
        self.force_animations = force_animations
        self.steps = {}  # step index -> (row, accumulated text)
        self.fades = {}  # step index -> StreamFade
        self._linked = None  # (command, time) the agent ran before its block showed
        self._proposed = None  # (path, diff, time) a change proposed before its card showed
        self._applied = None  # (path, (before, after), time) a change written before its card showed
        process.connect("event", lambda _p, event: self.on_event(event))
        process.connect("exited", lambda _p, stopped, stderr: self.on_exited(stopped, stderr))

        self.messages = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                                margin_start=12, margin_end=12, margin_top=12, margin_bottom=12)
        self.placeholder = _label("Ask the agent about your terminal, or ask it to do something there.",
                                  css=("dim-label",), selectable=False, justify=Gtk.Justification.CENTER,
                                  xalign=0.5, vexpand=True, valign=Gtk.Align.CENTER)
        self.messages.append(self.placeholder)
        self.scroller = Gtk.ScrolledWindow(child=self.messages, vexpand=True,
                                           hscrollbar_policy=Gtk.PolicyType.NEVER)
        # Rows get their natural height, not their minimum: a diff's own
        # scroller would otherwise shrink to a few lines
        self.scroller.get_child().set_vscroll_policy(Gtk.ScrollablePolicy.NATURAL)
        # Keep the newest message in sight while it grows, unless the user
        # scrolled up: then a button offers the new messages
        self._stick = True
        self._scrolling = None  # the glide to the end, while it runs
        self._setting = False  # the value is ours, not the user's
        self._upper = 0
        adjustment = self.scroller.get_vadjustment()
        adjustment.connect("value-changed", lambda a: self._on_scrolled(a))
        adjustment.connect("changed", lambda a: self._on_grown(a))
        self.new_button = Gtk.Button(label="↓ New messages", halign=Gtk.Align.CENTER, valign=Gtk.Align.END,
                                     margin_bottom=8, visible=False)
        self.new_button.add_css_class("pill")
        self.new_button.add_css_class("osd")
        self.new_button.connect("clicked", lambda *_: self.scroll_to_end())
        overlay = Gtk.Overlay(child=self.scroller, vexpand=True)
        overlay.add_overlay(self.new_button)
        self.append(overlay)

        # Three dots that pulse while the agent works (CSS, timed by the preset)
        self.dots = Gtk.Box(spacing=4, valign=Gtk.Align.CENTER)
        for name in ("first", "second", "third"):
            dot = Gtk.Box(valign=Gtk.Align.CENTER)
            dot.add_css_class("typing-dot")
            dot.add_css_class(name)
            self.dots.append(dot)
        self.thinking = Gtk.Box(spacing=8, margin_start=14, margin_bottom=6, visible=False)
        self.thinking.append(self.dots)
        self.thinking.append(_label("Working…", css=("dim-label", "caption"), selectable=False))
        self.append(self.thinking)
        self._ran_handler = None
        self.connect("realize", lambda *_: self._follow_agent_commands(True))
        self.connect("unrealize", lambda *_: self._follow_agent_commands(False))

        self.input = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, accepts_tab=False,
                                  top_margin=8, bottom_margin=8, left_margin=10, right_margin=10)
        self.input.update_property([Gtk.AccessibleProperty.LABEL], ["Message to the agent"])
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.input.add_controller(keys)
        input_scroller = Gtk.ScrolledWindow(child=self.input, hexpand=True, propagate_natural_height=True,
                                            max_content_height=160, hscrollbar_policy=Gtk.PolicyType.NEVER)
        input_scroller.add_css_class("card")
        self.send_button = Gtk.Button(icon_name="go-up-symbolic", tooltip_text="Send (Enter)",
                                      valign=Gtk.Align.END)
        self.send_button.add_css_class("circular")
        self.send_button.add_css_class("suggested-action")
        self.send_button.connect("clicked", lambda *_: self.send())
        self.stop_button = Gtk.Button(icon_name="media-playback-stop-symbolic", tooltip_text="Stop",
                                      valign=Gtk.Align.END, visible=False)
        self.stop_button.add_css_class("circular")
        self.stop_button.connect("clicked", lambda *_: self.process.stop())
        self.bottom = Gtk.Box(spacing=8, margin_start=12, margin_end=12, margin_bottom=12, margin_top=4)
        for widget in (input_scroller, self.send_button, self.stop_button):
            self.bottom.append(widget)
        self.append(self.bottom)

    def focus(self):
        self.input.grab_focus()

    def add_note(self, text):
        """A quiet line in the conversation, from the app rather than agy."""
        self._add(_label(text, css=("caption", "dim-label"), selectable=False, xalign=0.5,
                         justify=Gtk.Justification.CENTER))

    def text(self):
        buffer = self.input.get_buffer()
        return buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)

    def send(self, text=None):
        text = (text if text is not None else self.text()).strip()
        if not text or self.process.busy:
            return
        self.input.get_buffer().set_text("")
        self._add(_label(text, css=("card", "chat-user"), halign=Gtk.Align.END))
        self._stick = True
        self.process.send(text)
        self._set_busy(True)

    def _on_key(self, _controller, keyval, _keycode, state):
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and not state & Gdk.ModifierType.SHIFT_MASK:
            self.send()
            return True
        return False

    def _on_scrolled(self, adjustment):
        if self._setting:
            return
        self._stick = adjustment.get_value() >= adjustment.get_upper() - adjustment.get_page_size() - 40
        if self._stick:
            self.new_button.set_visible(False)

    def _on_grown(self, adjustment):
        grew = adjustment.get_upper() > self._upper
        self._upper = adjustment.get_upper()
        if self._stick:
            self._glide()
        elif grew:
            self.new_button.set_visible(True)

    def scroll_to_end(self):
        self._stick = True
        self.new_button.set_visible(False)
        self._glide()

    def _glide(self):
        """Scrolls to the end, smoothly; the end moves while text streams in."""
        if self._scrolling:
            return  # already on its way, and the frames follow the end
        adjustment = self.scroller.get_vadjustment()
        start = adjustment.get_value()

        def frame(p):
            if self._stick:
                end = adjustment.get_upper() - adjustment.get_page_size()
                self._set_scroll(start + (end - start) * min(p, 1.0))

        def done():
            self._scrolling = None
            if self._stick:
                self._set_scroll(adjustment.get_upper() - adjustment.get_page_size())
        self._scrolling = True
        self._scrolling = animations.play(self, "scroll", frame, done, self.force_animations)

    def _set_scroll(self, value):
        self._setting = True
        self.scroller.get_vadjustment().set_value(value)
        self._setting = False

    def _set_busy(self, busy):
        if animations.Animations.get().effect("typing", self.force_animations):
            self.dots.add_css_class("typing-animated")
        else:
            self.dots.remove_css_class("typing-animated")
        self.thinking.set_visible(busy)
        self.send_button.set_visible(not busy)
        self.stop_button.set_visible(busy)

    def _add(self, widget):
        """Adds a row: it fades in, rising from below."""
        if self.placeholder.get_parent():
            self.messages.remove(self.placeholder)
        self.messages.append(widget)
        params = animations.Animations.get().effect("chat_message", self.force_animations) or {}
        rise = params.get("distance", 0) * params.get("intensity", 1)

        def frame(p):
            widget.set_opacity(min(max(p, 0.0), 1.0))
            widget.set_margin_top(max(0, round(rise * (1 - p))))
        # The chat itself: the new row is not shown yet
        animations.play(self, "chat_message", frame, None, self.force_animations)
        return widget

    # The agent's commands in the terminal

    def _follow_agent_commands(self, follow):
        if follow and self._ran_handler is None:
            self._ran_handler = (
                self.approval.connect("agent-ran", lambda _a, command: self._on_agent_ran(command)),
                self.approval.connect("edit-proposed", lambda _a, path, diff: self._on_edit_proposed(path, diff)),
                self.approval.connect("edit-applied", lambda _a, path, before, existed, after:
                                      self._on_edit_applied(path, before if existed else None, after)))
        elif not follow and self._ran_handler is not None:
            for handler in self._ran_handler:
                self.approval.disconnect(handler)
            self._ran_handler = None

    def _on_agent_ran(self, command):
        """The agent ran `command` in the terminal: its block lights up, now or
        as soon as it shows."""
        for row, _ in self.steps.values():
            if isinstance(row, CommandRow) and row.command == command and row.running:
                row.light_up()
                return
        self._linked = (command, GLib.get_monotonic_time())

    def _on_edit_proposed(self, path, diff):
        """The app got the agent's change (ProposeEdit): its card shows the
        real diff, with the lines around the change, now or as it shows."""
        diff = diff.split("\n") if diff else []
        for row, _ in reversed(list(self.steps.values())):
            if isinstance(row, EditRow) and row.approval is not None and row.running and not row.proposed:
                row.set_diff(path, diff, proposed=True)
                row._sync_approval()
                return
        self._proposed = (path, diff, GLib.get_monotonic_time())

    def _on_edit_applied(self, path, before, after):
        """The app wrote the agent's change: its card can undo it."""
        for row, _ in reversed(list(self.steps.values())):
            if (isinstance(row, EditRow) and row.approval is not None and row.running and row.proposed
                    and row.path == path and row.change is None):
                row.change = (before, after)
                return
        self._applied = (path, (before, after), GLib.get_monotonic_time())

    # Events from agy

    def on_event(self, event):
        kind = event.get("event")
        if kind == "step_update":
            self._on_step(event.get("step_update") or {})
        elif kind == "result":
            self._on_result(event.get("result") or {})

    def _on_step(self, step):
        index, kind, state = step.get("step_index"), step.get("step_type"), step.get("state")
        if kind == "agent_response":
            row, text = self.steps.get(index) or (None, "")
            text += step.get("text_delta") or ""
            if row is None and text.strip():
                row = self._add(_label(css=("chat-agent",)))
                row.connect("activate-link", lambda _l, uri: _open_uri(self, uri))
                self.fades[index] = StreamFade(row, self.force_animations)
            if row:
                old_end = len(row.get_text().encode())
                row.set_markup(markdown_to_pango(text))
                self.fades[index].grew(old_end)
            self.steps[index] = (row, text)
        elif kind == "tool":
            name = step.get("tool_name") or ""
            info = step.get("tool_info") or {}
            row, text = self.steps.get(index, (None, ""))
            if text is HIDDEN:
                return
            if row is None:
                params = info.get("parameters") or {}
                if params.get("ServerName") == TERMINAL_SERVER and params.get("ToolName") == "run_command":
                    command = (params.get("Arguments") or {}).get("command", "")
                    row = CommandRow(command, self.approval, self.force_animations)
                    linked, self._linked = self._linked, None
                    if linked and linked[0] == command and GLib.get_monotonic_time() - linked[1] < 3_000_000:
                        GLib.idle_add(lambda: row.light_up() and False)
                elif params.get("ServerName") == TERMINAL_SERVER and params.get("ToolName") in EDIT_TOOLS:
                    row = EditRow(*planned_edit(params.get("ToolName"), params.get("Arguments") or {}),
                                  self.approval, self.force_animations)
                    proposed, self._proposed = self._proposed, None
                    if proposed and GLib.get_monotonic_time() - proposed[2] < 3_000_000:
                        row.set_diff(proposed[0], proposed[1], proposed=True)
                        row._sync_approval()
                    applied, self._applied = self._applied, None
                    if applied and applied[0] == row.path and GLib.get_monotonic_time() - applied[2] < 3_000_000:
                        row.change = applied[1]
                elif (native := edits.diff_native(name, params)) is not None:
                    row = EditRow(*native, force_animations=self.force_animations)
                elif is_internal(name, info):
                    self.steps[index] = (None, HIDDEN)  # nothing to show
                    return
                else:
                    row = ToolRow(tool_text(name, info))
                    path = next((v for v in (info.get("parameters") or {}).values()
                                 if isinstance(v, str) and v.startswith("/")), None)
                    row.set_tooltip_text(path)
                self._add(row)
                self.steps[index] = (row, "")
            if state in ("DONE", "ERROR"):
                error = info.get("error") or {}
                row.finish(info.get("output") or error.get("message") or "", state == "ERROR")

    def _on_result(self, result):
        self._set_busy(False)
        if result.get("status") not in (None, "SUCCESS") and result.get("error"):
            self._add(_label(result["error"], css=("error",)))
        usage = result.get("usage") or {}
        seconds = result.get("duration_seconds")
        if usage.get("total_tokens") is not None and seconds is not None:
            self._add(_label(f"{usage['total_tokens']:,} tokens · {seconds:.1f} s",
                             css=("caption", "dim-label"), selectable=False))
        self.steps.clear()
        self.fades.clear()

    def on_exited(self, stopped, stderr):
        self._set_busy(False)
        if self.process.switching:
            self.process.switching = False
            self.steps.clear()
            return
        for row, _ in self.steps.values():
            if isinstance(row, (CommandRow, EditRow, ToolRow)) and row.running:
                row.finish("Stopped", True)
        self.steps.clear()
        if stopped:
            self._add(_label("Stopped.", css=("caption", "dim-label"), selectable=False))
        else:
            self._add(_label(f"The agent stopped unexpectedly. {stderr}".strip(), css=("error",)))


def _open_uri(widget, uri):
    Gtk.UriLauncher.new(uri).launch(widget.get_root(), None, None, None)
    return True


CSS = """
.chat-user { padding: 8px 12px; }
.chat-agent { padding: 0 2px; }
.typing-dot { min-width: 6px; min-height: 6px; border-radius: 3px; background-color: alpha(@window_fg_color, 0.55); }
@keyframes typing-pulse {
  0% { opacity: 0.25; }
  50% { opacity: 1; }
  100% { opacity: 0.25; }
}
.running-bar {
  min-height: 2px; margin-bottom: 6px;
  background-image: linear-gradient(to right, transparent, @accent_color, transparent);
  background-size: 30% 100%; background-repeat: no-repeat;
}
@keyframes running-bar {
  from { background-position: -45% 0; }
  to { background-position: 145% 0; }
}
"""


def animated_css(values):
    """The parts of the CSS timed by the animation preset."""
    effects = values["effects"]
    pulse, bar = effects["typing"]["duration"], effects["tool_card"]["bar"]
    link = effects["link"]
    color = "@accent_color" if link["color"] in ("accent", "success", "error") else link["color"]
    return (
        f".typing-animated .typing-dot {{ animation: typing-pulse {pulse}ms ease-in-out infinite; }}\n"
        f".typing-animated .typing-dot.second {{ animation-delay: {pulse // 6}ms; }}\n"
        f".typing-animated .typing-dot.third {{ animation-delay: {pulse // 3}ms; }}\n"
        f".running-bar {{ animation: running-bar {bar}ms linear infinite; }}\n"
        f".command-card {{ transition: box-shadow {int(link['duration'] * 0.6)}ms ease-out; }}\n"
        f".command-card.agent-link {{ box-shadow: inset 0 0 0 2px {color}; transition: none; }}\n"
    )
