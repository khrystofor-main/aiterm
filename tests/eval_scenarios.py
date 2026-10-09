#!/usr/bin/env python3
"""The evals without the agent: every scenario's check must fail on the
broken setup and pass after its known solution (solution.sh, one command
per line, run like the agent's commands; answer.txt as the agent's answer).
A check that passes on the broken setup would count every run as solved.
Also the metrics and the report on a made-up event stream.

Needs bwrap (skipped without it). Run: tests/eval_scenarios.py
"""

import importlib.util
import os
import shlex
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP = 77
spec = importlib.util.spec_from_file_location("evals_run", os.path.join(ROOT, "evals", "run.py"))
evals = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evals)
results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(f"  {'ok  ' if ok else 'FAIL'} {name}" + ("" if ok or not detail else f"\n       {detail}"))


# Metrics, from a stream like agy's
def tool(index, name, **params):
    return {"event": "step_update", "step_update": {"step_index": index, "step_type": "tool", "state": "DONE",
                                                    "tool_name": name, "tool_info": {"parameters": params}}}


events = [
    {"event": "init", "conversation_id": "c"},
    tool(1, "call_mcp_tool", ServerName="aiterm_terminal", ToolName="read_terminal"),
    tool(2, "run_command", CommandLine="ls"),
    tool(3, "call_mcp_tool", ServerName="aiterm_terminal", ToolName="run_command"),
    tool(3, "call_mcp_tool", ServerName="aiterm_terminal", ToolName="run_command"),  # same step again
    {"event": "result", "result": {"status": "SUCCESS", "response": "Done.", "duration_seconds": 12.34,
                                   "usage": {"total_tokens": 5000, "output_tokens": 100}}},
]
metrics = evals.summarize(events)
check("summarize counts steps, terminal tools and hidden-shell tries",
      (metrics["steps"], metrics["terminal_tool_calls"], metrics["hidden_shell_tries"]) == (3, 2, 1), str(metrics))
check("…and takes tokens, time and the answer from the result",
      (metrics["tokens"], metrics["seconds"], metrics["response"]) == (5000, 12.3, "Done."), str(metrics))
check("a run without a result is not a success", evals.summarize([])["status"] == "NO_RESULT")
run = {"scenario": "a", "title": "A", "category": "typo", "solved": True, "steps": 3, "terminal_commands": 2,
       "failed_commands": 1, "hidden_shell_tries": 0, "tokens": 5000, "seconds": 12.3}
summary, table = evals.report([run, {**run, "solved": False, "tokens": 7000}], "m", "1.0")
check("the report counts solved runs", summary.startswith("**1/2 solved** with m (agy 1.0)"), summary)
check("…with one row per scenario", table.count("\n") == 2 and "| A | typo | ◐ 1/2 | 3 | 2 (1) | 0 | 6,000 |" in table,
      table)

if not shutil.which("bwrap") or subprocess.run(["bwrap", "--ro-bind", "/", "/", "true"],
                                               capture_output=True).returncode:
    print("  skip scenario checks: bwrap is missing or cannot make a sandbox here")
    sys.exit(0 if all(results) else 1)

# Every scenario: unsolved at the start, solved by its known solution
for scenario in evals.load_scenarios([]):
    name = scenario["name"]
    source = os.path.join(evals.SCENARIOS, name)
    for required in ("setup.sh", "check.sh", "solution.sh"):
        if not os.path.isfile(os.path.join(source, required)):
            check(f"{name} has {required}", False)
    work, home, project = evals.prepare(name)
    try:
        before = evals.check(name, work, "", [])
        # The user's history, then the solution, in one shell (with job
        # control, as in a terminal); each solution line's exit code recorded
        with open(os.path.join(source, "solution.sh")) as f:
            solution = [line.strip() for line in f if line.strip()]
        script = ["set -m", 'run() { eval "$1"; echo $? >> "$HOME/.codes"; }']
        script += [line for line in scenario.get("history", [])]
        script += [f"run {shlex.quote(line)}" for line in solution]
        subprocess.run(evals.sandbox(home, project) + ["bash", "-c", "\n".join(script)], capture_output=True,
                       text=True, timeout=60)
        codes = open(os.path.join(home, ".codes")).read().split() if solution else []
        commands = [{"command": line, "exit_code": int(code), "output": ""} for line, code in zip(solution, codes)]
        answer = os.path.join(source, "answer.txt")
        response = open(answer).read() if os.path.exists(answer) else ""
        after = evals.check(name, work, response, commands)
        check(f"{name}: unsolved at the start, solved by the solution",
              before.returncode != 0 and after.returncode == 0,
              f"before={before.returncode} after={after.returncode} {after.stderr[-300:]}")
    finally:
        shutil.rmtree(work, ignore_errors=True)

sys.exit(0 if all(results) else 1)
