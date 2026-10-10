#!/usr/bin/env python3
"""Reading agy's own records (src/aiterm/agy_history.py) on synthetic files
shaped like agy's: transcripts, history.jsonl, and what happens when they
are missing or garbled. No window, never the user's ~/.gemini.
Run: tests/agy_history_unit.py
"""

import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from aiterm import agy_history  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(f"  {'ok  ' if ok else 'FAIL'} {name}" + ("" if ok or not detail else f"\n       {detail}"))


home = tempfile.mkdtemp(prefix="aiterm-agy-home-")
os.environ.pop("AITERM_AGY_DIR", None)
os.environ["HOME"] = home
check("agy's folder is in HOME", agy_history.data_dir() == os.path.join(home, ".gemini", "antigravity-cli"))
DATA = tempfile.mkdtemp(prefix="aiterm-agy-")
os.environ["AITERM_AGY_DIR"] = DATA
check("…or where AITERM_AGY_DIR says", agy_history.data_dir() == DATA)


def transcript(conversation, records, raw=""):
    path = agy_history.transcript_path(conversation)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(raw + "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records))


def args(**values):
    return {key: json.dumps(value) for key, value in values.items()}


transcript("c1", [
    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "source": "USER_EXPLICIT",
     "content": "<USER_REQUEST>\nWhat is in my folder?\n</USER_REQUEST>\n<ADDITIONAL_METADATA>\n"
                "The current local time is: 2026-10-10T12:00:00+02:00.\n</ADDITIONAL_METADATA>\n"
                "<USER_SETTINGS_CHANGE>\nThe user changed setting `Model`.\n</USER_SETTINGS_CHANGE>"},
    {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "thinking": "Let me look.",
     "tool_calls": [{"name": "view_file", "args": args(
         AbsolutePath="/h/.gemini/antigravity-cli/mcp/aiterm_terminal/run_command.json",
         toolAction="Viewing schema", toolSummary="schema")}]},
    {"step_index": 2, "type": "GENERIC", "status": "DONE", "content": "Created At: x\nCompleted At: y\n{}"},
    {"step_index": 3, "type": "PLANNER_RESPONSE", "status": "DONE", "content": "Let me check.",
     "tool_calls": [{"name": "call_mcp_tool", "args": {
         "Arguments": json.dumps(json.dumps({"command": "ls"})), "ServerName": '"aiterm_terminal"',
         "ToolName": '"run_command"', "toolSummary": '"List"'}}]},
    {"step_index": 4, "type": "GENERIC", "status": "DONE",
     "content": "Created At: 2026-10-10T12:00:01+02:00\nCompleted At: 2026-10-10T12:00:02+02:00\n$ ls\na.txt\n[exit code 0]"},
    {"step_index": 5, "type": "SYSTEM_MESSAGE", "status": "DONE", "content": "The following is a <SYSTEM_MESSAGE>"},
    {"step_index": 6, "type": "PLANNER_RESPONSE", "status": "DONE",
     "tool_calls": [{"name": "call_mcp_tool", "args": {
         "Arguments": json.dumps(json.dumps({"path": "/d/a.txt", "content": "hi\n"})),
         "ServerName": '"aiterm_terminal"', "ToolName": '"write_file"'}}]},
    {"step_index": 7, "type": "GENERIC", "status": "ERROR", "content": "Created At: x\nCompleted At: y\n",
     "error": "The user rejected the change to /d/a.txt."},
    {"step_index": 8, "type": "PLANNER_RESPONSE", "status": "DONE", "content": "One file: **a.txt**.",
     "thinking": "Done."},
], raw="not json\n[1, 2]\n")
steps = agy_history.read_steps("c1")
check("a transcript reads as user, agent and tool steps; thinking and system messages left out",
      [s["kind"] for s in steps] == ["user", "tool", "agent", "tool", "tool", "agent"],
      str([s["kind"] for s in steps]))
check("…the user's text without agy's tags", steps[0]["text"] == "What is in my folder?", repr(steps[0]["text"]))
check("…tool arguments decoded, the terminal tool's own arguments too",
      steps[3]["name"] == "call_mcp_tool" and steps[3]["parameters"]["ServerName"] == "aiterm_terminal"
      and steps[3]["parameters"]["Arguments"] == {"command": "ls"} and "toolSummary" not in steps[3]["parameters"],
      str(steps[3]))
check("…a tool's output without the times before it",
      steps[3]["output"] == "$ ls\na.txt\n[exit code 0]" and not steps[3]["failed"], repr(steps[3]["output"]))
check("…a failed one with its error", steps[4]["failed"] and "rejected" in steps[4]["output"], str(steps[4]))
check("…each with its step index (a tool's: its result's)",
      [s["index"] for s in steps] == [0, 2, 3, 4, 7, 8], str([s["index"] for s in steps]))
check("the last step index", agy_history.last_index("c1") == 8)
check("no transcript: None", agy_history.read_steps("nope") is None and agy_history.last_index("nope") is None
      and agy_history.read_steps(None) is None)
transcript("garbled", [], raw="{{{\n\x00\n[]\n")
check("a garbled transcript: no steps, no error", agy_history.read_steps("garbled") == [])
transcript("odd", [{"step_index": "x", "type": "USER_INPUT"}, {"step_index": 0, "type": "USER_INPUT", "content": 5},
                   {"step_index": 1, "type": "PLANNER_RESPONSE", "tool_calls": ["x", {"name": 3}]},
                   {"step_index": 2, "type": "GENERIC", "content": None}])
check("…nor one with fields of the wrong kind", agy_history.read_steps("odd") == [])
check("text without <USER_REQUEST>: the tags are dropped",
      agy_history.user_text("hello <ADDITIONAL_METADATA>\nx\n</ADDITIONAL_METADATA>") == "hello")

# history.jsonl: which conversation agy's own interface is in
WS = tempfile.mkdtemp(prefix="aiterm-ws-")
started = time.time()
now = int(started * 1000)


def history(*entries):
    with open(os.path.join(DATA, "history.jsonl"), "w") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")


def prompt(ms, conversation=None, workspace=WS, display="hi"):
    return {"display": display, "timestamp": ms, "workspace": workspace,
            **({"conversationId": conversation} if conversation else {})}


def slash(ms, display, conversation=None):
    return {**prompt(ms, conversation, display=display), "type": "slash_command"}


check("no history.jsonl: not known", agy_history.active_conversation(WS, started) is None)
history(prompt(now - 60_000, "old"), prompt(now + 1000, "c1"), prompt(now + 2000, "other", workspace="/elsewhere"))
check("the conversation last typed in, in this folder, since agy started",
      agy_history.active_conversation(WS, started) == "c1")
check("…nothing typed since: not known", agy_history.active_conversation(WS, started + 10) is None)
history(prompt(now + 1000, "c1"), slash(now + 2000, "/resume", "c1"), prompt(now + 3000, "c2"))
check("after /resume, the conversation typed in next", agy_history.active_conversation(WS, started) == "c2")
history(prompt(now + 1000, "c1"), slash(now + 2000, "/resume", "c1"))
check("…/resume with nothing typed after: not known", agy_history.active_conversation(WS, started) is None)
history(prompt(now + 1000, "c1"), slash(now + 2000, "/clear", "c1"))
check("/clear: a new conversation", agy_history.active_conversation(WS, started) == agy_history.NEW)
history(prompt(now + 1000, "c1"), slash(now + 2000, "/model", "c1"))
check("another slash command tells the conversation it was typed in",
      agy_history.active_conversation(WS, started) == "c1")
fresh = os.path.join(DATA, "brain", "fresh")
os.makedirs(fresh)
os.utime(fresh, (started + 5, started + 5))
os.utime(os.path.join(DATA, "brain", "c1"), (started - 600, started - 600))
history(prompt(now + 1000, "c1"), slash(now + 2000, "/new", "c1"), prompt(now + 5000))
check("a first prompt (no id yet): the conversation it started in brain/",
      agy_history.active_conversation(WS, started) == "fresh")
with open(os.path.join(DATA, "history.jsonl"), "w") as f:
    f.write("garbage\n" + json.dumps(prompt(now + 1000, "c1")) + "\n" + json.dumps({"timestamp": "x"}) + "\n")
check("a garbled history.jsonl: the lines that read", agy_history.active_conversation(WS, started) == "c1")
check("…the folder compared as a real path", agy_history.active_conversation(WS + "/.", started) == "c1")

sys.exit(0 if all(results) else 1)
