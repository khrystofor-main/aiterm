"""What agy itself recorded about its conversations, so the chat can show what
was said in the Terminal view and follow the conversation that view is in.

These are agy's internal files, not a documented interface (seen in the
Antigravity CLI of October 2026). Everything that reads them is here, and it
fails soft: a missing file, a line that does not parse, a format that
changed give None or fewer steps, never an error, and the chat falls back to
saying the messages are in the Terminal view.

- `history.jsonl`: one line per prompt typed in agy's own interface (and per
  slash command): {"display", "timestamp" (ms), "workspace" (the folder agy
  runs in), "conversationId", "type": "slash_command" for those}. A prompt
  that starts a conversation has no conversationId yet: its folder in
  `brain/` appears at that moment. A slash command carries the conversation
  it was typed in, before it took effect (/resume, /clear and /new leave it).
- `brain/<conversation id>/.system_generated/logs/transcript.jsonl`: the
  conversation's steps, one per line: {"step_index", "type", "status",
  "content", ...}. USER_INPUT (the text inside <USER_REQUEST>, followed by
  more of agy's own tags), PLANNER_RESPONSE (the model: "content",
  "thinking", "tool_calls": [{"name", "args"}], each argument JSON-encoded
  as a string), GENERIC (a tool's result: "Created At: …", "Completed At: …"
  lines, then its output; status ERROR with "error" when it failed) and
  SYSTEM_MESSAGE.

They live in ~/.gemini/antigravity-cli; AITERM_AGY_DIR points elsewhere
(the tests' fake agy writes there). No GTK.
"""

import json
import os
import re

NEW = ""  # active_conversation: a new conversation, nothing in it yet
STARTS_NEW = ("/clear", "/new")
UNKNOWN_AFTER = ("/resume",)  # which conversation it picked is not recorded
TAG = re.compile(r"<([A-Z][A-Z_]*)>.*?</\1>", re.S)


def data_dir():
    return os.environ.get("AITERM_AGY_DIR") or os.path.expanduser("~/.gemini/antigravity-cli")


def transcript_path(conversation_id):
    return os.path.join(data_dir(), "brain", conversation_id, ".system_generated", "logs", "transcript.jsonl")


def _lines(path):
    """The JSON objects of a JSONL file; lines that are not one are skipped.
    [] when the file cannot be read."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            out.append(value)
    return out


def _same_folder(a, b):
    try:
        return os.path.realpath(a) == os.path.realpath(b)
    except (TypeError, ValueError):
        return False


def active_conversation(workspace, since):
    """The conversation agy's own interface, started in `workspace` at
    `since` (seconds since the epoch), is in now, as far as history.jsonl
    tells: its id, NEW after /clear or /new, or None when it does not tell
    (nothing typed since, or a /resume after the last prompt)."""
    since_ms = since * 1000
    entries = [e for e in _lines(os.path.join(data_dir(), "history.jsonl"))
               if isinstance(e.get("timestamp"), (int, float)) and e["timestamp"] >= since_ms
               and _same_folder(e.get("workspace"), workspace)]
    for entry in sorted(entries, key=lambda e: e["timestamp"], reverse=True):
        conversation = entry.get("conversationId")
        if entry.get("type") == "slash_command":
            command = str(entry.get("display", "")).split(" ")[0]
            if command in STARTS_NEW:
                return NEW
            if command in UNKNOWN_AFTER:
                return None
            if isinstance(conversation, str) and conversation:
                return conversation
            continue
        if isinstance(conversation, str) and conversation:
            return conversation
        return _started_at(entry["timestamp"] / 1000)
    return None


def _started_at(when, slack=30):
    """The conversation whose folder in brain/ appeared at `when`: the one a
    prompt without a conversationId started."""
    brain = os.path.join(data_dir(), "brain")
    try:
        folders = [(abs(os.stat(os.path.join(brain, name)).st_mtime - when), name) for name in os.listdir(brain)]
    except OSError:
        return None
    near = [f for f in folders if f[0] <= slack]
    return min(near)[1] if near else None


def _decoded(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def user_text(content):
    """What the user typed: the text in <USER_REQUEST>, without the tags agy
    adds around it."""
    if not isinstance(content, str):
        return ""
    request = re.search(r"<USER_REQUEST>(.*?)</USER_REQUEST>", content, re.S)
    return (request[1] if request else TAG.sub("", content)).strip()


def tool_output(content):
    """A tool's result without the times agy writes before it."""
    lines = (content if isinstance(content, str) else "").split("\n")
    while lines and re.match(r"(Created|Completed) At: ", lines[0]):
        lines = lines[1:]
    return "\n".join(lines).strip("\n")


def read_steps(conversation_id):
    """The conversation's steps as the chat draws them, in order, or None
    when there is no transcript to read. Each is a dict with "index" (the
    transcript's step_index) and "kind":
      "user"  — "text";
      "agent" — "text" (Markdown);
      "tool"  — "name", "parameters" (decoded), "output", "failed".
    Thinking and system messages are left out."""
    if not conversation_id:
        return None
    path = transcript_path(conversation_id)
    if not os.path.isfile(path):
        return None
    steps, waiting = [], []  # waiting: tool calls whose result has not come yet
    for record in _lines(path):
        index = record.get("step_index")
        if not isinstance(index, int):
            continue
        kind = record.get("type")
        if kind == "USER_INPUT":
            text = user_text(record.get("content"))
            if text:
                steps.append({"index": index, "kind": "user", "text": text})
        elif kind == "PLANNER_RESPONSE":
            text = record.get("content")
            if isinstance(text, str) and text.strip():
                steps.append({"index": index, "kind": "agent", "text": text.strip()})
            for call in record.get("tool_calls") or []:
                if not isinstance(call, dict) or not isinstance(call.get("name"), str):
                    continue
                args = call.get("args") if isinstance(call.get("args"), dict) else {}
                parameters = {key: _decoded(value) for key, value in args.items()
                              if key not in ("toolAction", "toolSummary")}
                if isinstance(parameters.get("Arguments"), str):
                    parameters["Arguments"] = _decoded(parameters["Arguments"])
                step = {"index": index, "kind": "tool", "name": call["name"], "parameters": parameters,
                        "output": "", "failed": False}
                steps.append(step)
                waiting.append(step)
        elif kind == "GENERIC" and waiting:
            step = waiting.pop(0)
            step["index"] = index
            step["failed"] = record.get("status") == "ERROR" or bool(record.get("error"))
            error = record.get("error")
            step["output"] = (str(error) if step["failed"] and error else "") or tool_output(record.get("content"))
    return steps


def last_index(conversation_id):
    """The transcript's last step_index now, or None without a transcript:
    the chat remembers it when it leaves, to show later what came after."""
    if not conversation_id or not os.path.isfile(transcript_path(conversation_id)):
        return None
    indices = [r["step_index"] for r in _lines(transcript_path(conversation_id))
               if isinstance(r.get("step_index"), int)]
    return max(indices, default=-1)
