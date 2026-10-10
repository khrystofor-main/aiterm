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

Like agy, it keeps a transcript of each conversation where agy_history.py
reads it (AITERM_AGY_DIR in the tests), and --continue goes on with the
newest one there.
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from aiterm import agy_history, mcp_server  # noqa: E402
from aiterm.client import TerminalClient  # noqa: E402



def newest_conversation():
    if not os.environ.get("AITERM_AGY_DIR"):
        return None  # only a test's own folder, never the user's ~/.gemini
    brain = os.path.join(agy_history.data_dir(), "brain")
    try:
        return max(os.listdir(brain), key=lambda name: os.path.getmtime(agy_history.transcript_path(name)))
    except (OSError, ValueError):
        return None


# A new id per process unless --conversation names one (or --continue finds
# one), so the test can tell a resumed conversation from a new one
conversation = (sys.argv[sys.argv.index("--conversation") + 1] if "--conversation" in sys.argv
                else ("--continue" in sys.argv and newest_conversation()) or f"fake-{os.getpid()}")
step = 0
TRANSCRIPT = agy_history.transcript_path(conversation)
written = agy_history.last_index(conversation)
transcript_step = -1 if written is None else written


def record(kind, **fields):
    """A step in the transcript, as agy writes it. Only into a test's own
    folder: never the user's real ~/.gemini."""
    global transcript_step
    if not os.environ.get("AITERM_AGY_DIR"):
        return
    transcript_step += 1
    os.makedirs(os.path.dirname(TRANSCRIPT), exist_ok=True)
    with open(TRANSCRIPT, "a") as f:
        f.write(json.dumps({"step_index": transcript_step, "type": kind, "status": "DONE",
                            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **fields},
                           ensure_ascii=False) + "\n")


def record_tool(name, parameters, output, failed=False):
    """A tool call and its result: each argument JSON-encoded, as agy does."""
    record("PLANNER_RESPONSE", tool_calls=[{"name": name, "args": {
        **{key: json.dumps(value) for key, value in parameters.items()}, "toolSummary": '"fake"'}}])
    record("GENERIC", content=f"Created At: now\nCompleted At: now\n{output}",
           **({"status": "ERROR", "error": output} if failed else {}))


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
        record_tool("call_mcp_tool", {**info["parameters"], "Arguments": json.dumps(arguments)}, output)
    except mcp_server.ToolError as error:
        update("tool", "ERROR", tool_name="call_mcp_tool",
               tool_info={**info, "output": str(error), "error": {"type": "TOOL_ERROR", "message": str(error)}})
        record_tool("call_mcp_tool", {**info["parameters"], "Arguments": json.dumps(arguments)}, str(error), True)
    step += 1


emit("init", conversation_id=conversation, init={"cwd": os.getcwd(), "tools": ["call_mcp_tool"]})
for line in sys.stdin:
    text = json.loads(line)["message"]["content"]
    update("user_input", "DONE")
    record("USER_INPUT", content=f"<USER_REQUEST>\n{text}\n</USER_REQUEST>\n<ADDITIONAL_METADATA>\n"
                                 f"The current local time is: now.\n</ADDITIONAL_METADATA>")
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
    record("PLANNER_RESPONSE", content=f"You said: **{text}**\n\n```\ncode block\n```", thinking="Hmm.")
    step += 1
    emit("result", result={"conversation_id": conversation, "status": "SUCCESS", "response": text,
                           "duration_seconds": 0.5, "num_turns": 1, "usage": {"total_tokens": 1234}})
