"""aiterm-mcp: the user's terminal as MCP tools for the agent.

agy starts this as a child process (agy-plugin/mcp_config.json) and talks to
it over stdin/stdout: MCP's stdio transport, one JSON-RPC 2.0 message per
line. It is another client of the terminal API on D-Bus (client.py), so it
works on the same window as the agent through AITERM_WINDOW, which it
inherits from agy. Outside aiterm it lists no tools.

No MCP SDK: the server needs four methods, and a plain implementation keeps
it dependency-free next to the system Python. Try it by hand:

    echo '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | bin/aiterm-mcp
"""

import json
import os
import sys
import threading
import time

from aiterm import VERSION
from aiterm.client import NotInside, TerminalClient, Unreachable, format_command

# Newest first; an older client gets its own version back if we know it
PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
PARSE_ERROR, METHOD_NOT_FOUND, INVALID_PARAMS = -32700, -32601, -32602
MAX_TIMEOUT = 3600

INSTRUCTIONS = """\
The user works in aiterm: their own shell is on the left, you are in the panel \
on the right. These tools work in that shell, in plain sight: run shell \
commands with run_command, not with your own hidden shell, and read what the \
user did with read_terminal. The user sees every command and its output."""

TOOLS = [
    {
        "name": "read_terminal",
        "title": "Read the user's terminal",
        "description": (
            "Shows what happened in the user's terminal: their last command, its output and its exit "
            "code, and the terminal's current folder. Call it first when the user asks about an error, "
            "a command's output or 'what's in the terminal'. Widen the context only when that is not "
            "enough: `commands` for the last N commands (when the user says 'above' or 'before that'), "
            "`whole_screen` for the whole scrollback (full-screen programs, output that is not a command)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "commands": {"type": "integer", "minimum": 1, "default": 1,
                             "description": "How many of the last commands to show."},
                "whole_screen": {"type": "boolean", "default": False,
                                 "description": "Show the whole scrollback instead of the command list."},
            },
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "run_command",
        "title": "Run a command in the user's terminal",
        "description": (
            "Types a shell command into the user's terminal, waits for it to finish and returns its "
            "output, its exit code and the terminal's folder afterwards. The user sees it run. Use it "
            "for every shell command. Check `exit_code` before saying a command worked. "
            "Give long commands (apt, builds, downloads) a longer `timeout`. "
            "The shell is the user's own: `cd` and `export` change it for them, so do not change its "
            "folder or environment without a reason. "
            "Never start full-screen or interactive programs (nano, vim, less, top) and turn pagers "
            "off (git --no-pager, systemctl --no-pager). "
            "For sudo, tell the user first that they will type their password in their terminal; never "
            "ask for it in the chat. Every sudo command asks for the password. "
            "If the result's status is `timeout`, the command is still running or waits for input: "
            "tell the user what to type, if anything, then call wait_for_command. "
            "The user may be asked to approve each command; if they decline, do not run it another "
            "way. If the tool refuses because the user is typing or a program is running, tell the "
            "user why; do not try to get around it."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The command line, as typed at a bash prompt."},
                "timeout": {"type": "integer", "minimum": 1, "maximum": MAX_TIMEOUT, "default": 60,
                            "description": "Seconds to wait before returning the output so far."},
            },
            "required": ["command"],
        },
        "annotations": {"destructiveHint": True, "openWorldHint": True},
    },
    {
        "name": "wait_for_command",
        "title": "Wait for the running command",
        "description": (
            "Waits for the command that is already running in the user's terminal (after run_command "
            "returned status `timeout`, or for a command the user started) and returns its output and "
            "exit code. Types nothing."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "timeout": {"type": "integer", "minimum": 1, "maximum": MAX_TIMEOUT, "default": 60,
                            "description": "Seconds to wait before returning the output so far."},
            },
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "get_cwd",
        "title": "Get the terminal's folder",
        "description": "Returns the current folder of the user's terminal. Relative paths the user "
                       "mentions are relative to it.",
        "inputSchema": {"type": "object", "properties": {}},
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
]


class ToolError(Exception):
    """A failure the model should read and act on: returned as isError."""


# Tools: each takes the client and the arguments, returns (text, structured)

def read_terminal(client, commands=1, whole_screen=False):
    if whole_screen:
        folder, text = client.read_screen()
        return f"[terminal folder: {folder}]\n{text}", {"folder": folder, "screen": text}
    folder, rows = client.read_commands(_int(commands, "commands", 1, None))
    structured = {"folder": folder, "commands": [row._asdict() for row in rows]}
    if not rows:
        # Nothing in the command log (no shell integration, a full-screen
        # program): the tail of the screen is the best guess
        _, screen = client.read_screen()
        tail = "\n".join(screen.split("\n")[-200:])
        structured["screen"] = tail
        return f"[terminal folder: {folder}]\n[no commands in the log; the end of the screen:]\n{tail}", structured
    text = "\n".join(format_command(*row[:3]) for row in rows)
    return f"[terminal folder: {folder}]\n{text}", structured


def run_command(client, command=None, timeout=60):
    if not isinstance(command, str) or not command.strip():
        raise ToolError("`command` is required: the command line to run.")
    return _result(client.run(command, _int(timeout, "timeout", 1, MAX_TIMEOUT)))


def wait_for_command(client, timeout=60):
    return _result(client.wait(_int(timeout, "timeout", 1, MAX_TIMEOUT)))


def get_cwd(client):
    folder, _ = client.read_commands(1)
    return folder, {"folder": folder}


HANDLERS = {"read_terminal": read_terminal, "run_command": run_command,
            "wait_for_command": wait_for_command, "get_cwd": get_cwd}
SCHEMAS = {tool["name"]: tool["inputSchema"] for tool in TOOLS}


def _int(value, name, low, high):
    if isinstance(value, bool) or not isinstance(value, int) or value < low or (high and value > high):
        limits = f"from {low} to {high}" if high else f"{low} or more"
        raise ToolError(f"`{name}` must be a whole number {limits}.")
    return value


def _result(result):
    structured = result._asdict()
    if result.status == "busy":
        raise ToolError(f"The terminal is busy: «{result.command}» is running. Nothing was typed. "
                        "Wait for it with wait_for_command, or ask the user to finish the program.")
    if result.status == "typing":
        raise ToolError(f"The user is typing in the terminal: «{result.command}». Nothing was typed. "
                        "Ask them to finish or clear the line (Ctrl+C), then retry.")
    if result.status == "denied":
        raise ToolError(f"The user chose not to run «{result.command}». Nothing was typed. Do not run it "
                        "another way; ask the user what they want instead.")
    if result.status == "timeout":
        text = (f"[terminal folder: {result.folder}]\n{result.output}\n"
                f"[status: timeout. The command is still running or waiting for input (now running: "
                f"{result.command}). If it waits for a password or an answer, tell the user what to type "
                f"in their terminal, then call wait_for_command.]")
        return text, structured
    return f"[terminal folder: {result.folder}]\n" + format_command(
        result.command, result.output, result.exit_code), structured


class Server:
    def __init__(self, client, out=sys.stdout, trace=None):
        self.client = client  # None outside aiterm
        self.out = out
        self.trace = trace  # a file to log tool calls to (AITERM_MCP_TRACE), for evals
        self.lock = threading.Lock()
        self.calls = []

    def send(self, message):
        with self.lock:
            self.out.write(json.dumps({"jsonrpc": "2.0", **message}, ensure_ascii=False) + "\n")
            self.out.flush()

    def serve(self, lines):
        for line in lines:
            if line.strip():
                self.handle_line(line)
        # stdin closed: finish the calls in flight, then exit
        for thread in self.calls:
            thread.join()

    def handle_line(self, line):
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            return self.send({"id": None, "error": {"code": PARSE_ERROR, "message": "Parse error"}})
        if isinstance(message, list):  # a batch (2025-03-26 only)
            for item in message:
                self.handle(item)
        else:
            self.handle(message)

    def handle(self, message):
        if not isinstance(message, dict) or "method" not in message:
            return  # a response or junk: we send no requests, so nothing to match
        method, params, id_ = message["method"], message.get("params"), message.get("id")
        params = params if isinstance(params, dict) else {}
        if id_ is None:
            return  # notifications: initialized, cancelled (the command stays visible in the terminal)
        if method == "tools/call":
            # Commands can take minutes; pings and other calls go on meanwhile
            thread = threading.Thread(target=self.reply, args=(id_, self.call_tool, params), daemon=True)
            self.calls = [t for t in self.calls if t.is_alive()] + [thread]
            thread.start()
            return
        handler = {"initialize": self.initialize, "ping": lambda _: {},
                   "tools/list": self.list_tools}.get(method)
        if handler is None:
            return self.send({"id": id_, "error": {"code": METHOD_NOT_FOUND, "message": f"Unknown method {method}"}})
        self.reply(id_, handler, params)

    def reply(self, id_, handler, params):
        try:
            self.send({"id": id_, "result": handler(params)})
        except ValueError as error:
            self.send({"id": id_, "error": {"code": INVALID_PARAMS, "message": str(error)}})

    def initialize(self, params):
        asked = params.get("protocolVersion")
        return {
            "protocolVersion": asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "aiterm", "title": "aiterm", "version": VERSION},
            "instructions": INSTRUCTIONS if self.client else "",
        }

    def list_tools(self, _params):
        return {"tools": TOOLS if self.client else []}

    def call_tool(self, params):
        name, arguments = params.get("name"), params.get("arguments") or {}
        if name not in HANDLERS or not self.client:
            raise ValueError(f"Unknown tool {name}")
        if not isinstance(arguments, dict):
            raise ValueError("Tool arguments must be an object")
        unknown = set(arguments) - set(SCHEMAS[name]["properties"])
        if unknown:
            return _error(f"Unexpected arguments for {name}: {', '.join(sorted(unknown))}")
        started = time.monotonic()
        try:
            text, structured = HANDLERS[name](self.client, **arguments)
            result = {"content": [{"type": "text", "text": text}], "structuredContent": structured,
                      "isError": False}
        except (ToolError, Unreachable) as error:
            result = _error(str(error))
        if self.trace:
            with self.lock:
                self.trace.write(json.dumps({"tool": name, "arguments": arguments, "result": result,
                                             "seconds": round(time.monotonic() - started, 2)},
                                            ensure_ascii=False) + "\n")
                self.trace.flush()
        return result


def _error(text):
    return {"content": [{"type": "text", "text": text}], "isError": True}


def main():
    try:
        client = TerminalClient()
    except NotInside:
        client = None
    # Line-buffered UTF-8 whatever the locale; stdout carries only protocol
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    trace = os.environ.get("AITERM_MCP_TRACE")
    Server(client, trace=open(trace, "a", encoding="utf-8") if trace else None).serve(sys.stdin)


if __name__ == "__main__":
    main()
