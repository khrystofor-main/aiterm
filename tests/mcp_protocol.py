#!/usr/bin/env python3
"""The MCP server's protocol on its own, without the app: the handshake,
listing tools, and how it answers bad input. tests/gtk_smoke.py calls every
tool against the real app. Run: tests/mcp_protocol.py
"""

import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER = os.path.join(ROOT, "bin", "aiterm-mcp")
results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(f"  {'ok  ' if ok else 'FAIL'} {name}" + ("" if ok or not detail else f"\n       {detail}"))


def session(*messages, **env):
    """Sends the messages to a fresh server; returns its replies by id."""
    environ = {k: v for k, v in os.environ.items() if not k.startswith("AITERM_")} | env
    lines = "".join((m if isinstance(m, str) else json.dumps(m)) + "\n" for m in messages)
    out = subprocess.run([SERVER], input=lines, capture_output=True, text=True, env=environ, timeout=30)
    replies = [json.loads(line) for line in out.stdout.splitlines()]
    return {r.get("id"): r for r in replies}, out


def request(id_, method, params=None):
    return {"jsonrpc": "2.0", "id": id_, "method": method, **({"params": params} if params is not None else {})}


INSIDE = {"AITERM_WINDOW": "1", "AITERM_BUS_NAME": ":1.no-such-app"}

replies, out = session(
    request(1, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                              "clientInfo": {"name": "test", "version": "0"}}),
    {"jsonrpc": "2.0", "method": "notifications/initialized"},
    request(2, "tools/list"), request(3, "ping"), **INSIDE)
init = replies.get(1, {}).get("result", {})
check("initialize answers with the client's protocol version", init.get("protocolVersion") == "2025-06-18",
      str(replies.get(1)))
check("…offers tools and says what they are for",
      "tools" in init.get("capabilities", {}) and "run_command" in init.get("instructions", ""), str(init))
check("notifications get no reply", set(replies) == {1, 2, 3}, str(replies))
tools = {t["name"]: t for t in replies.get(2, {}).get("result", {}).get("tools", [])}
check("tools/list lists the terminal and file tools",
      sorted(tools) == ["edit_file", "get_cwd", "read_terminal", "run_command", "wait_for_command", "write_file"],
      str(sorted(tools)))
check("…each with a description and an object schema",
      all(t["description"] and t["inputSchema"]["type"] == "object" for t in tools.values()))
check("run_command requires a command", tools.get("run_command", {}).get("inputSchema", {}).get("required")
      == ["command"])
check("ping answers", replies.get(3, {}).get("result") == {}, str(replies.get(3)))
check("stdout carries nothing but protocol", all(line.startswith("{") for line in out.stdout.splitlines()))

replies, _ = session(request(1, "initialize", {"protocolVersion": "1999-01-01"}), **INSIDE)
check("an unknown protocol version gets the newest we speak",
      replies[1]["result"]["protocolVersion"] == "2025-11-25", str(replies[1]))

replies, _ = session(request(1, "initialize", {"protocolVersion": "2025-06-18"}), request(2, "tools/list"))
check("outside aiterm there are no tools", replies[2]["result"]["tools"] == [], str(replies[2]))
check("…and no instructions", replies[1]["result"]["instructions"] == "", str(replies[1]))

call = lambda id_, name, arguments=None: request(id_, "tools/call", {"name": name, "arguments": arguments or {}})
replies, _ = session(
    "not json", request(1, "server/discover"), call(2, "no_such_tool"), call(3, "run_command"),
    call(4, "run_command", {"command": "echo hi", "timeout": 0}), call(5, "get_cwd", {"folder": "/"}),
    call(6, "read_terminal", {"commands": "3"}), call(7, "run_command", {"command": "echo hi"}),
    request(8, "tools/call", {"name": "get_cwd", "arguments": [1]}), **INSIDE)
check("bad JSON is a parse error", replies.get(None, {}).get("error", {}).get("code") == -32700, str(replies))
check("an unknown method is an error", replies[1]["error"]["code"] == -32601, str(replies[1]))
check("an unknown tool is an error", replies[2]["error"]["code"] == -32602, str(replies[2]))
text = lambda id_: replies[id_]["result"]["content"][0]["text"] if replies[id_]["result"]["isError"] else None
check("a missing command is a tool error the model can read", "`command` is required" in (text(3) or ""),
      str(replies[3]))
check("…so is a bad timeout", "`timeout` must be" in (text(4) or ""), str(replies[4]))
check("…an unknown argument", "Unexpected arguments for get_cwd: folder" == text(5), str(replies[5]))
check("…a number given as a string", "`commands` must be" in (text(6) or ""), str(replies[6]))
check("…and an app that does not answer", "Cannot reach the Aiterm window" in (text(7) or ""), str(replies[7]))
check("arguments that are not an object are invalid params", replies[8]["error"]["code"] == -32602,
      str(replies[8]))

# The file tools plan the change themselves: errors the model can fix come
# before the app is asked anything
folder = tempfile.mkdtemp(prefix="aiterm-mcp-")
target = os.path.join(folder, "a.txt")
with open(target, "w") as f:
    f.write("one\ntwo\n")
replies, _ = session(
    call(1, "edit_file", {"old_text": "a", "new_text": "b"}),
    call(2, "edit_file", {"path": target, "old_text": "three", "new_text": "3"}),
    call(3, "edit_file", {"path": target, "old_text": "one", "new_text": "1", "replace_all": "yes"}),
    call(4, "write_file", {"path": target, "content": "one\ntwo\n"}),
    call(5, "edit_file", {"path": target, "old_text": "one", "new_text": "1"}),
    call(6, "write_file", {"path": target}), **INSIDE)
check("edit_file needs a path", "`path` is required" in (text(1) or ""), str(replies[1]))
check("…and text that is in the file", "is not in" in (text(2) or ""), str(replies[2]))
check("…and a real true or false", "`replace_all` must be" in (text(3) or ""), str(replies[3]))
check("writing what the file already has changes nothing, and asks nobody",
      not replies[4]["result"]["isError"] and replies[4]["result"]["structuredContent"]["status"] == "unchanged",
      str(replies[4]))
check("a good edit goes to the app for approval", "Cannot reach the Aiterm window" in (text(5) or ""),
      str(replies[5]))
check("write_file needs content", "`content` is required" in (text(6) or ""), str(replies[6]))
check("…and nothing was written meanwhile", open(target).read() == "one\ntwo\n")

sys.exit(0 if all(results) else 1)
