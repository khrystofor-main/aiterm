"""agy as a chat backend: one process per conversation, talking NDJSON.

    agy --output-format stream-json --input-format stream-json --print=

Each line written to its stdin, {"event": "user", "message": {"content": …}},
runs a turn. Its stdout is a stream of typed events, one JSON object per
line: "init" (the conversation id), "step_update" (the user's message, the
agent's text in deltas, each tool call with its parameters and output, token
usage) and one "result" per turn. AgentProcess parses them and emits each
as the "event" signal; chat_view.py draws them.

Stop kills the process (agy has no way to cancel a turn over stdin); the
next message starts it again with --conversation, so the conversation goes on.
"""

import json
import os
import shlex

from gi.repository import Gio, GLib, GObject

STREAM_ARGS = ["--output-format", "stream-json", "--input-format", "stream-json", "--print="]


def chat_command(agent):
    """The chat backend's argv, from the agent's (agent_panel.agent_command);
    AITERM_CHAT_AGENT overrides it (tests use a fake agy)."""
    if os.environ.get("AITERM_CHAT_AGENT"):
        return shlex.split(os.environ["AITERM_CHAT_AGENT"])
    return agent + STREAM_ARGS if agent else None


class AgentProcess(GObject.Object):
    __gsignals__ = {
        # A parsed event from agy's stdout (dict)
        "event": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        # The process ended: (stopped by the user, the end of its stderr)
        "exited": (GObject.SignalFlags.RUN_FIRST, None, (bool, str)),
    }

    def __init__(self, argv, cwd, env):
        super().__init__()
        self.argv, self.cwd, self.env = argv, cwd, env
        self.conversation_id = None
        self.busy = False  # a turn is running
        self._process = None
        self._stopping = False

    @property
    def running(self):
        return self._process is not None

    def send(self, text):
        """Starts a turn; starts agy first if it is not running."""
        if self._process is None:
            self._start()
        line = json.dumps({"event": "user", "message": {"content": text}}, ensure_ascii=False) + "\n"
        self.busy = True
        self._process.get_stdin_pipe().write_all_async(
            line.encode(), GLib.PRIORITY_DEFAULT, None, lambda pipe, res: _finish_write(pipe, res))

    def stop(self):
        """Ends the running turn; the conversation can go on with send()."""
        if self._process:
            self._stopping = True
            self._process.force_exit()

    def _start(self):
        argv = list(self.argv)
        if self.conversation_id and "--conversation" not in argv:
            argv += ["--conversation", self.conversation_id]
        launcher = Gio.SubprocessLauncher.new(
            Gio.SubprocessFlags.STDIN_PIPE | Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE)
        launcher.set_cwd(self.cwd or GLib.get_home_dir())
        for key, value in self.env.items():
            launcher.setenv(key, value, True)
        self._process = launcher.spawnv(argv)
        self._stopping = False
        self._stderr = []
        self._read(Gio.DataInputStream.new(self._process.get_stdout_pipe()), self._on_line)
        self._read(Gio.DataInputStream.new(self._process.get_stderr_pipe()), self._stderr.append)
        self._process.wait_async(None, self._on_exit)

    def _read(self, stream, handle):
        def on_line(stream, result):
            try:
                line, _ = stream.read_line_finish_utf8(result)
            except GLib.Error:
                return
            if line is None:  # end of stream
                return
            handle(line)
            stream.read_line_async(GLib.PRIORITY_DEFAULT, None, on_line)

        stream.read_line_async(GLib.PRIORITY_DEFAULT, None, on_line)

    def _on_line(self, line):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return  # not an event: agy prints nothing else on stdout, but be safe
        if not isinstance(event, dict):
            return
        if event.get("event") == "init":
            self.conversation_id = event.get("conversation_id") or self.conversation_id
        elif event.get("event") == "result":
            self.busy = False
        self.emit("event", event)

    def _on_exit(self, process, result):
        try:
            process.wait_finish(result)
        except GLib.Error:
            pass
        self._process = None
        self.busy = False
        # Let the last lines of stderr arrive before reporting them
        GLib.timeout_add(100, lambda: self.emit("exited", self._stopping, "\n".join(self._stderr[-5:])) and False)


def _finish_write(pipe, result):
    try:
        pipe.write_all_finish(result)
    except GLib.Error:
        pass  # the process is gone; "exited" says so
