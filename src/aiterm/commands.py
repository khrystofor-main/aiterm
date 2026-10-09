"""The terminal as a list of commands: what was run, its output, exit code
and duration. This is what the agent will read instead of guessing command
boundaries from the prompt text.

The shell reports through VTE terminal properties (OSC 666):

  vte.shell.preexec / precmd / postexec   Ubuntu's /etc/profile.d/vte-2.91.sh:
                                          command started / prompt is back /
                                          exit code
  vte.ext.aiterm.prompt / command         shell/integration.bash: where the
                                          prompt ends (the cursor) / the
                                          command's text

VTE reports property changes in batches, after it has processed a chunk of
output, in no particular order, and the values can only be read during the
change signal. So each value is read right away, and the batch is looked at
as a whole once it is over. Positions are cursor positions at the end of the
batch; the prompt mark is the last thing the shell prints before it waits
for input, so its position is exact.
"""

import base64
import time
from dataclasses import dataclass, field

from gi.repository import GLib, Vte

PROMPT_MARK = "vte.ext.aiterm.prompt"
COMMAND_TEXT = "vte.ext.aiterm.command"
Vte.install_termprop(PROMPT_MARK, Vte.PropertyType.INT, Vte.PropertyFlags.EPHEMERAL)
Vte.install_termprop(COMMAND_TEXT, Vte.PropertyType.DATA, Vte.PropertyFlags.EPHEMERAL)

STARTED = Vte.TERMPROP_SHELL_PREEXEC
FINISHED = Vte.TERMPROP_SHELL_PRECMD
EXIT_CODE = Vte.TERMPROP_SHELL_POSTEXEC

MAX_COMMANDS = 500
# How far above its prompt a command is looked for when the rows were renumbered
SEARCH_ROWS = 2000
# How long an ended command waits for its prompt before it is logged without one
PROMPT_WAIT_MS = 1000


@dataclass
class Command:
    text: str
    output: str
    exit_code: int
    seconds: float
    # Rows in the terminal (VTE counts from the start of the scrollback):
    # the prompt's first row, the row the command was typed on, and the row
    # of the next prompt
    start_row: int = 0
    end_row: int = 0
    input_row: int = 0


@dataclass
class _Prompt:
    row: int  # first row of the prompt
    input_row: int  # where the command starts
    input_col: int


@dataclass
class _Batch:
    started: bool = False
    finished: bool = False
    prompt: _Prompt = None
    names: set = field(default_factory=set)


class CommandLog:
    """Follows one terminal. `on_finished(command)` is called for every
    command; `commands` keeps the latest ones, oldest first."""

    def __init__(self, terminal, on_finished):
        self.terminal = terminal
        self.on_finished = on_finished
        self.commands = []
        self.prompt_rows = []
        self._prompt = None  # the prompt the running or next command was typed at
        self._running = None  # (start time, text) while a command runs
        self._ending = None  # (text, seconds) of a command that ended before its prompt
        self._batch = _Batch()
        # The shell can send these a batch before the event they belong to
        self._text = None
        self._exit_code = 0
        for name in (STARTED, FINISHED, EXIT_CODE, PROMPT_MARK, COMMAND_TEXT):
            terminal.connect(f"termprop-changed::{name}", self._on_property)

    @property
    def running(self):
        """True between a command's start and its end."""
        return self._running is not None

    @property
    def input_row(self):
        """The row the running or next command is typed on, or None."""
        return self._prompt.input_row if self._prompt else None

    def typed_text(self):
        """What the user has typed after the current prompt, or None when
        there is no prompt mark to tell (a shell without the integration)."""
        if self._prompt is None or self._running is not None:
            return None
        _, row = self.terminal.get_cursor_position()
        if self._prompt.input_row > row:
            return None  # rows renumbered since that prompt (see _finish)
        text, _ = self.terminal.get_text_range_format(
            Vte.Format.TEXT, self._prompt.input_row, self._prompt.input_col, row, 10_000)
        return (text or "").strip()

    def screen_since_prompt(self):
        """Everything after the current prompt: a running command and its
        output so far."""
        if self._prompt is None:
            return ""
        _, row = self.terminal.get_cursor_position()
        text, _ = self.terminal.get_text_range_format(
            Vte.Format.TEXT, self._prompt.input_row, self._prompt.input_col, row, 10_000)
        return (text or "").rstrip("\n")

    def command_at_row(self, row):
        for command in reversed(self.commands):
            if command.start_row <= row < command.end_row:
                return command
        return None

    def _on_property(self, terminal, name):
        batch = self._batch
        if not batch.names:
            # Above redraws: at idle priority, a busy screen (a spinner, a
            # slow renderer) could starve it and the marks would never land
            GLib.idle_add(self._process, priority=GLib.PRIORITY_HIGH_IDLE)
        batch.names.add(name)
        if name == STARTED:
            batch.started = True
        elif name == FINISHED:
            batch.finished = True
        elif name == EXIT_CODE:
            valid, code = terminal.get_termprop_uint(name)
            self._exit_code = code if valid else 0
        elif name == COMMAND_TEXT:
            data = terminal.get_termprop_data(name)
            self._text = data.decode(errors="replace") if data else None
        elif name == PROMPT_MARK:
            valid, lines = terminal.get_termprop_int(name)
            col, row = terminal.get_cursor_position()
            batch.prompt = _Prompt(row - (lines if valid else 0), row, col)

    def _process(self):
        batch, self._batch = self._batch, _Batch()
        now = time.monotonic()
        if self._ending and batch.prompt:
            self._finish(*self._ending, batch)
        elif self._running is not None and batch.finished:
            # The running command ended (a new one starting in the same
            # batch means it was typed ahead; it goes unrecorded)
            started, text = self._running
            self._end(text, batch, now - started)
        elif batch.started and batch.finished:
            self._end(self._text, batch, 0.0)  # started and ended in one go
        elif batch.started:
            self._running = (now, self._text)
        if batch.prompt:
            if self._running is None:
                self.terminal.remember_shell_group()
            self._prompt = batch.prompt
            self.prompt_rows.append(batch.prompt.row)
            del self.prompt_rows[:-MAX_COMMANDS]
        return GLib.SOURCE_REMOVE

    def _end(self, text, batch, seconds):
        """The command ended. Its output runs up to the next prompt, which can
        come a moment later, in the next batch: then it is finished there
        (or after PROMPT_WAIT_MS without one). Until then it counts as
        running, so nobody takes the old prompt line for typed text."""
        if batch.prompt:
            return self._finish(text, seconds, batch)
        self._ending = (text, seconds)
        GLib.timeout_add(PROMPT_WAIT_MS, self._end_without_prompt, self._ending)

    def _end_without_prompt(self, ending):
        if self._ending is ending:
            self._finish(*ending, _Batch())
        return GLib.SOURCE_REMOVE

    def _finish(self, text, seconds, batch):
        self._running = self._ending = None
        command = Command(text or "", "", self._exit_code, seconds)
        self._text, self._exit_code = None, 0
        start, end = self._prompt, batch.prompt
        if start and end and start.row > end.row and text:
            # The rows were renumbered since the command's prompt (`clear`
            # erased the scrollback, a smaller scrollback was set): find the
            # command just above the new prompt instead
            first = max(int(self.terminal.get_vadjustment().get_lower()), end.row - SEARCH_ROWS)
            above, _ = self.terminal.get_text_range_format(Vte.Format.TEXT, first, 0, end.row, 0)
            at = (above or "").rfind(text)
            if at >= 0:
                row = first + above[:at].count("\n")
                start = _Prompt(row, row, len(above[:at].rsplit("\n", 1)[-1]))
        if start and end and start.row <= end.row:
            command.start_row, command.end_row, command.input_row = start.row, end.row, start.input_row
            # Every prompt starts at column 0 (OSC 133;L), so the command and
            # its output run up to the start of the next prompt's first row
            block, _ = self.terminal.get_text_range_format(
                Vte.Format.TEXT, start.input_row, start.input_col, end.row, 0)
            block = block or ""
            if text and not block.startswith(text):
                # Text typed ahead (by the agent, right after the previous
                # command) can be echoed before the prompt mark is handled,
                # which then sits a few columns too far: find the command
                # on the prompt's line instead
                line, _ = self.terminal.get_text_range_format(
                    Vte.Format.TEXT, start.input_row, 0, end.row, 0)
                at = (line or "").find(text)
                if at >= 0:
                    block = line[at:]
            command.text, command.output = split_command(block, text)
        self.commands.append(command)
        del self.commands[:-MAX_COMMANDS]
        self.on_finished(command)


def split_command(block, text):
    """Splits the screen text after a prompt into (command, output). `text` is
    the command as the shell reported it, or None (then the first line is the
    command)."""
    if text and block.startswith(text):
        command, output = text, block[len(text):]
    else:
        command, _, output = block.partition("\n")
        command = command.strip()
    output = output.removeprefix("\n").removesuffix("\n")
    return command, output
