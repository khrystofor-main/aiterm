"""Preferences → Animations: the main switch, the preset (Duplicate, Delete,
Open Preset File) and a row per effect with its switch, intensity and Show,
which plays it in the preview terminal on the page.

Built-in presets are fixed: their rows are read-only until the preset is
duplicated. Changes to the user's own presets are saved at once, as the
differences from the base (animations.py)."""

from gi.repository import Adw, Gio, GLib, GObject, Gtk

from aiterm.animations import EFFECTS, Animations
from aiterm.approval import ApprovalBar
from aiterm.chat_view import ChatView
from aiterm.settings import Settings
from aiterm.terminal import PADDING_Y, Terminal

PROMPT = "\x1b[1;34m~/project\x1b[0m$ "
PREVIEW_TEXT = (
    f"{PROMPT}ls missing-folder\r\n"
    "ls: cannot access 'missing-folder': No such file or directory\r\n"
    f"{PROMPT}make\r\n"
    "building a\r\nbuilding b\r\nbuilding c\r\n"
    f"{PROMPT}\x1b[?25l"  # no cursor
)
PREVIEW_ROWS = 7
CHAT_EFFECTS = ("chat_message", "typing", "stream", "tool_card", "scroll", "link")
WINDOW_EFFECTS = ("panel", "search", "approval")


class PreviewProcess(GObject.Object):
    """Stands in for agy behind the preview chat: the chat's events come from
    a script (play_chat)."""

    __gsignals__ = {
        "event": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        "exited": (GObject.SignalFlags.RUN_FIRST, None, (bool, str)),
    }

    def __init__(self):
        super().__init__()
        self.busy = self.switching = self.running = False
        self.conversation_id = None

    def send(self, _text):
        self.busy = True

    def stop(self):
        pass


def _step(index, **fields):
    return {"event": "step_update", "step_update": {"step_index": index, **fields}}


def play_chat(chat):
    """A short exchange in the preview chat, showing every chat effect: the
    message, the dots, a command block with its running bar that lights up
    as the "agent" runs it, the answer streaming in, the scroll."""
    child = chat.messages.get_first_child()
    while child:
        chat.messages.remove(child)
        child = chat.messages.get_first_child()
    chat.steps.clear()
    chat.fades.clear()
    chat.process.busy = False
    chat.send("Why did the build fail?")
    tool = {"ServerName": "aiterm_terminal", "ToolName": "run_command", "Arguments": {"command": "make test"}}
    answer = ["The test ", "`test_parse` fails: ", "the config file ", "has a trailing comma ", "on line 4."]
    script = [
        (600, lambda: chat.on_event(_step(1, step_type="tool", state="RUNNING", tool_name="call_mcp_tool",
                                          tool_info={"parameters": tool}))),
        (300, lambda: chat.approval.emit("agent-ran", "make test")),
        (900, lambda: chat.on_event(_step(1, step_type="tool", state="DONE", tool_name="call_mcp_tool",
                                          tool_info={"parameters": tool,
                                                     "output": "$ make test\n2 passed, 1 failed\n[exit code 2]"}))),
        *[(150, lambda piece=piece: chat.on_event(_step(2, step_type="agent_response", text_delta=piece)))
          for piece in answer],
        (200, lambda: chat.on_event({"event": "result", "result": {"status": "SUCCESS"}})),
    ]

    def run(i=0):
        if i < len(script):
            delay, action = script[i]
            GLib.timeout_add(delay, lambda: (action(), run(i + 1)) and False)
    run()


def play(terminal, effect, window=None):
    """Plays one effect, even when it is off: in the preview terminal, or
    for the window's own parts (the agent panel, the find and approval
    bars) in `window`, behind the dialog."""
    if effect in WINDOW_EFFECTS:
        if window is not None and hasattr(window, "agent_panel"):
            play_in_window(window, effect)
        return
    top = terminal.top_row()
    if effect == "shake":
        terminal.effects.shake(top, force=True)  # the failed `ls`
    elif effect == "stripe":
        terminal.effects.light(top + 3, top + 5, force=True)  # make's output
    elif effect == "fail_mark":
        terminal.effects.mark(top, 2, force=True)
    elif effect == "theme":
        terminal.show_color_change()


def play_in_window(window, effect):
    if effect == "panel":
        panel = window.agent_panel
        if panel.get_visible():
            panel.slide(showing=False, done=lambda: panel.slide(showing=True))
        else:
            panel.set_visible(True)
            window._place_panel_border()
            panel.slide(showing=True, done=lambda: GLib.timeout_add(
                600, lambda: panel.slide(showing=False, done=lambda: panel.set_visible(False)) and False))
    elif effect == "search":
        window.search.open()
        GLib.timeout_add(1200, lambda: window.search.set_search_mode(False) and False)
    elif effect == "approval":
        window.approval.preview("make test")


class AnimationsPage(Adw.PreferencesPage):
    def __init__(self):
        super().__init__(title="Animations", icon_name="applications-science-symbolic")
        self.animations = Animations.get()
        self.settings = Settings.get()
        self._updating = False

        main = Adw.PreferencesGroup()
        self.switch_row = Adw.SwitchRow(
            title="Animations",
            subtitle="Follows the system's Reduce Animations setting until you change it here",
        )
        self.switch_row.connect("notify::active", self._on_switch)
        main.add(self.switch_row)
        self.add(main)

        presets = Adw.PreferencesGroup(title="Preset")
        buttons = Gtk.Box(spacing=6)
        self.duplicate_button = icon_button("edit-copy-symbolic", "Duplicate", self._on_duplicate)
        self.delete_button = icon_button("user-trash-symbolic", "Delete", self._on_delete)
        self.open_button = icon_button("document-edit-symbolic", "Open Preset File", self._on_open)
        for button in (self.duplicate_button, self.delete_button, self.open_button):
            buttons.append(button)
        presets.set_header_suffix(buttons)
        self.preset_row = Adw.ComboRow(title="Preset")
        self.preset_row.connect("notify::selected", self._on_preset_selected)
        presets.add(self.preset_row)
        self.name_row = Adw.EntryRow(title="Name", show_apply_button=True)
        self.name_row.connect("apply", lambda row: self.animations.rename(self.animations.preset.key, row.get_text()))
        presets.add(self.name_row)
        self.warning_row = Adw.ActionRow(title="Problems in the preset file")
        self.warning_row.add_prefix(Gtk.Image(icon_name="dialog-warning-symbolic"))
        self.warning_row.add_css_class("warning")
        presets.add(self.warning_row)
        self.add(presets)

        preview = Adw.PreferencesGroup(title="Preview")
        self.preview = Terminal(argv=["sleep", "infinity"])
        self.chat_preview = ChatView(PreviewProcess(), ApprovalBar(), force_animations=True)
        self.chat_preview.set_size_request(-1, 260)
        self.chat_preview.bottom.set_visible(False)  # nothing to type here
        self.preview.set_input_enabled(False)
        self.preview.set_focusable(False)
        self.preview.set_size(80, PREVIEW_ROWS)
        fit = lambda height: self.preview.set_size_request(-1, PREVIEW_ROWS * height + 2 * PADDING_Y)
        self.preview.connect("char-size-changed", lambda _t, _w, height: fit(height))
        fit(self.preview.get_char_height())
        self.preview.feed(PREVIEW_TEXT.encode())
        self.previews = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.previews.add_named(self.preview, "terminal")
        self.previews.add_named(self.chat_preview, "chat")
        frame = Gtk.Frame(child=self.previews, overflow=Gtk.Overflow.HIDDEN)
        frame.add_css_class("card")
        preview.add(frame)
        self.add(preview)

        self.effects_group = Adw.PreferencesGroup(title="Effects")
        self.effect_rows = {}
        for name, effect in EFFECTS.items():
            self.effect_rows[name] = row = EffectRow(name, effect, self)
            self.effects_group.add(row)
        self.add(self.effects_group)

        self._handler = self.animations.connect("changed", lambda *_: self.refresh())
        self.connect("unrealize", lambda *_: self.animations.disconnect(self._handler))
        self.refresh()

    def refresh(self):
        """Puts the current state into the widgets (after any change, also
        one made to a preset file by hand)."""
        self._updating = True
        try:
            self.switch_row.set_active(self.animations.enabled)
            keys = list(self.animations.presets)
            names = [self.animations.presets[k].name for k in keys]
            model = self.preset_row.get_model()
            if model is None or [model.get_string(i) for i in range(model.get_n_items())] != names:
                self.preset_row.set_model(Gtk.StringList.new(names))
            self._keys = keys
            preset = self.animations.preset
            self.preset_row.set_selected(keys.index(preset.key))
            self.preset_row.set_subtitle("" if not preset.builtin else "Built in. Duplicate it to make your own")
            self.name_row.set_visible(not preset.builtin)
            if self.name_row.get_text() != preset.name:
                self.name_row.set_text(preset.name)
            self.delete_button.set_sensitive(not preset.builtin)
            self.open_button.set_sensitive(not preset.builtin)
            self.warning_row.set_visible(bool(preset.warnings))
            self.warning_row.set_subtitle(GLib.markup_escape_text("\n".join(preset.warnings)))
            values = preset.values()["effects"]
            for name, row in self.effect_rows.items():
                row.show_values(values[name], editable=not preset.builtin)
        finally:
            self._updating = False

    def _on_switch(self, row, _pspec):
        if not self._updating:
            self.settings.animations = "on" if row.get_active() else "off"

    def _on_preset_selected(self, row, _pspec):
        if not self._updating and 0 <= row.get_selected() < len(self._keys):
            self.settings.animation_preset = self._keys[row.get_selected()]

    def _on_duplicate(self, _button):
        self.animations.duplicate(self.animations.preset.key)

    def _on_delete(self, _button):
        preset = self.animations.preset
        dialog = Adw.AlertDialog(heading="Delete Preset?", body=f"“{preset.name}” and its file will be deleted.")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_close_response("cancel")
        dialog.connect("response", lambda _d, r: r == "delete" and self.animations.delete(preset.key))
        dialog.present(self)

    def _on_open(self, _button):
        preset = self.animations.preset
        if preset.path:
            Gtk.FileLauncher.new(Gio.File.new_for_path(preset.path)).launch(self.get_root(), None, None, None)

    def set_param(self, effect, param, value):
        if not self._updating and not self.animations.preset.builtin:
            self.animations.set_param(effect, param, value)

    def show_effect(self, effect):
        if effect in CHAT_EFFECTS:
            self.previews.set_visible_child_name("chat")
            play_chat(self.chat_preview)
        else:
            if effect not in WINDOW_EFFECTS:
                self.previews.set_visible_child_name("terminal")
            play(self.preview, effect, self.get_root())


class EffectRow(Adw.ActionRow):
    """An effect: on/off, intensity, and Show."""

    def __init__(self, name, effect, page):
        super().__init__(title=effect.title, subtitle=effect.subtitle)
        self.name, self.page = name, page
        self.scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 2, 0.05)
        self.scale.set_size_request(120, -1)
        self.scale.set_valign(Gtk.Align.CENTER)
        self.scale.set_tooltip_text("Intensity")
        self.scale.add_mark(1, Gtk.PositionType.BOTTOM, None)
        self.scale.connect("value-changed", self._on_intensity)
        self.show_button = icon_button("media-playback-start-symbolic", "Show",
                                       lambda _b: page.show_effect(name))
        self.switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.switch.connect("notify::active", lambda s, _: page.set_param(name, "enabled", s.get_active()))
        for widget in (self.scale, self.show_button, self.switch):
            self.add_suffix(widget)
        self._preview_pending = False

    def show_values(self, values, editable):
        self.switch.set_active(values["enabled"])
        self.scale.set_value(values["intensity"])
        self.switch.set_sensitive(editable)
        self.scale.set_sensitive(editable)

    def _on_intensity(self, scale):
        if self.page._updating:
            return
        self.page.set_param(self.name, "intensity", round(scale.get_value(), 2))
        # Shows the new intensity once the slider rests
        if not self._preview_pending:
            self._preview_pending = True
            GLib.timeout_add(250, self._preview)

    def _preview(self):
        self._preview_pending = False
        self.page.show_effect(self.name)
        return GLib.SOURCE_REMOVE


def icon_button(icon, tooltip, callback):
    button = Gtk.Button(icon_name=icon, tooltip_text=tooltip, valign=Gtk.Align.CENTER)
    button.add_css_class("flat")
    button.connect("clicked", callback)
    return button
