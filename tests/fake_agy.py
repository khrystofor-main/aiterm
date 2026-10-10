#!/usr/bin/env python3
"""A stand-in for `agy --input-format stream-json --output-format stream-json`
in the smoke test's chat: the same NDJSON in and out, no model, no login.

Each message is a script:
    run: COMMAND   runs COMMAND with the real run_command tool (mcp_server.py),
                   through the app, approval included
    edit: PATH|OLD|NEW
                   changes the file with the real edit_file tool, approval included
    native-edit: PATH
                   reports a change by agy's own replace_file_content (writes nothing)
    wait           takes 30 s, so the test can press Stop
    crash          exits with an error
    anything else  answers "You said: …"
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from aiterm import mcp_server  # noqa: E402
from aiterm.client import TerminalClient  # noqa: E402

# A new id per process unless --conversation names one, so the test can tell a
# resumed conversation from a new one
conversation = (sys.argv[sys.argv.index("--conversation") + 1] if "--conversation" in sys.argv
                else f"fake-{os.getpid()}")
step = 0


def emit(event, **body):
    print(json.dumps({"event": event, **body}), flush=True)


def update(step_type, state, **fields):
    emit("step_update", step_update={"conversation_id": conversation, "step_index": step,
                                     "state": state, "step_type": step_type, **fields})


def terminal_tool(tool, handler, **arguments):
    """A call of an aiterm tool, as agy reports it, made for real."""
    global step
    info = {"name": "call_mcp_tool", "parameters": {
        "ServerName": "aiterm_terminal", "ToolName": tool, "Arguments": arguments}}
    update("tool", "ACTIVE", tool_name="call_mcp_tool", tool_info=info)
    try:
        output, _ = handler(TerminalClient(), **arguments)
        update("tool", "DONE", tool_name="call_mcp_tool", tool_info={**info, "output": output})
    except mcp_server.ToolError as error:
        update("tool", "ERROR", tool_name="call_mcp_tool",
               tool_info={**info, "output": str(error), "error": {"type": "TOOL_ERROR", "message": str(error)}})
    step += 1


emit("init", conversation_id=conversation, init={"cwd": os.getcwd(), "tools": ["call_mcp_tool"]})
for line in sys.stdin:
    text = json.loads(line)["message"]["content"]
    update("user_input", "DONE")
    step += 1
    if text == "crash":
        print("fake agy: something broke", file=sys.stderr)
        sys.exit(1)
    if text == "wait":
        time.sleep(30)
    if text.startswith("run: "):
        schema = "/home/u/.gemini/antigravity-cli/mcp/aiterm_terminal/run_command.json"
        update("tool", "DONE", tool_name="view_file",
               tool_info={"name": "view_file", "parameters": {"AbsolutePath": schema}, "output": "1 line"})
        step += 1
        terminal_tool("run_command", mcp_server.run_command, command=text[5:], timeout=20)
        update("tool", "DONE", tool_name="view_file", tool_info={"name": "view_file",
               "parameters": {"AbsolutePath": "/etc/hostname"}, "output": "1 line"})
        step += 1
    if text.startswith("edit: "):
        path, old, new = text[6:].split("|")
        terminal_tool("edit_file", mcp_server.edit_file, path=path, old_text=old, new_text=new)
    if text.startswith("native-edit: "):
        update("tool", "DONE", tool_name="replace_file_content", tool_info={
            "name": "replace_file_content", "output": "Edited", "parameters": {
                "TargetFile": text[13:], "TargetContent": "old line", "ReplacementContent": "new line",
                "StartLine": 4}})
        step += 1
    for delta in ("You said: ", f"**{text}**", "\n\n```\ncode block\n```\n"):
        update("agent_response", "ACTIVE", text_delta=delta)
    update("agent_response", "DONE", text_delta="", usage={"total_tokens": 1234})
    step += 1
    emit("result", result={"conversation_id": conversation, "status": "SUCCESS", "response": text,
                           "duration_seconds": 0.5, "num_turns": 1, "usage": {"total_tokens": 1234}})
