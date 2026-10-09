"""The chat in the agent panel: the conversation with agy, drawn by the app.

Fed by AgentProcess (chat.py). Each step of agy's stream is one row, keyed
by its step index, and updated as more events for it arrive: the agent's
text grows with each delta, a tool call gets its output when it is done.
run_command is a collapsible block with the command, its output and exit
code, and Run / Don't Run while it waits for approval (approval.py).
"""

import html
import os
import re

from gi.repository import Gdk, GLib, Gtk, Pango

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

    def __init__(self, command, approval):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.add_css_class("card")
        self.approval = approval
        self.spinner = Gtk.Spinner(spinning=True)
        self.icon = Gtk.Image(visible=False)
        title = _label(f"$ {command}", mono=True, hexpand=True, selectable=False)
        header = Gtk.Box(spacing=8)
        for widget in (self.spinner, self.icon, title):
            header.append(widget)
        self.expander = Gtk.Expander(label_widget=header, margin_start=8, margin_end=8, margin_top=6, margin_bottom=6)
        self.output = _label(mono=True, css=("dim-label",))
        self.expander.set_child(self.output)
        self.append(self.expander)

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
        self._handler = approval.connect("notify::reveal-child", lambda *_: self._sync_approval())
        self._sync_approval()

    def _sync_approval(self):
        waiting = self.approval.pending
        self.buttons.set_visible(waiting)
        self.status.set_visible(waiting)
        self.status.set_label("Waiting for you to run it in your terminal")

    def finish(self, output, failed):
        """The tool's result: the text the model got."""
        if self._handler:
            self.approval.disconnect(self._handler)
            self._handler = None
        self.buttons.set_visible(False)
        self.spinner.set_visible(False)
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


class ToolRow(Gtk.Box):
    """Any other tool: one quiet line, with a spinner while it runs."""

    def __init__(self, text):
        super().__init__(spacing=8, margin_start=4)
        self.spinner = Gtk.Spinner(spinning=True)
        self.append(self.spinner)
        self.label = _label(text, css=("caption", "dim-label"), selectable=False, hexpand=True,
                            ellipsize=Pango.EllipsizeMode.MIDDLE, wrap=False)
        self.append(self.label)

    def finish(self, output, failed):
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


def is_internal(name, info):
    """agy reading an MCP tool's schema from its own cache before calling it:
    not something the user did or needs to see."""
    path = (info.get("parameters") or {}).get("AbsolutePath", "")
    return name == "view_file" and "/mcp/" in path and path.endswith(".json") and "/.gemini/" in path


class ChatView(Gtk.Box):
    def __init__(self, process, approval):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.process = process
        self.approval = approval
        self.steps = {}  # step index -> (row, accumulated text)
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
        # Keep the newest message in sight while it grows, unless the user scrolled up
        self._stick = True
        adjustment = self.scroller.get_vadjustment()
        adjustment.connect("value-changed", lambda a: self._on_scrolled(a))
        adjustment.connect("changed", lambda a: self._stick and a.set_value(a.get_upper()))
        self.append(self.scroller)

        self.thinking = Gtk.Box(spacing=8, margin_start=12, margin_bottom=6, visible=False)
        self.thinking.append(Gtk.Spinner(spinning=True))
        self.thinking.append(_label("Working…", css=("dim-label", "caption"), selectable=False))
        self.append(self.thinking)

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
        bottom = Gtk.Box(spacing=8, margin_start=12, margin_end=12, margin_bottom=12, margin_top=4)
        for widget in (input_scroller, self.send_button, self.stop_button):
            bottom.append(widget)
        self.append(bottom)

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
        self._stick = adjustment.get_value() >= adjustment.get_upper() - adjustment.get_page_size() - 40

    def _set_busy(self, busy):
        self.thinking.set_visible(busy)
        self.send_button.set_visible(not busy)
        self.stop_button.set_visible(busy)

    def _add(self, widget):
        if self.placeholder.get_parent():
            self.messages.remove(self.placeholder)
        self.messages.append(widget)
        return widget

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
            if row:
                row.set_markup(markdown_to_pango(text))
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
                    row = CommandRow(command, self.approval)
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

    def on_exited(self, stopped, stderr):
        self._set_busy(False)
        if self.process.switching:
            self.process.switching = False
            self.steps.clear()
            return
        for row, _ in self.steps.values():
            if isinstance(row, (CommandRow, ToolRow)) and row.spinner.get_visible():
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
"""
