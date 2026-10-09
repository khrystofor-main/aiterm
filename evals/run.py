#!/usr/bin/env python3
"""Evals: does the agent fix a broken setup through aiterm's tools?

Each scenario in evals/scenarios/<name>/ is a small broken setup:

    scenario.json  title, category, the commands "the user" ran (history)
                   and what they ask the agent (prompt)
    setup.sh       makes the broken state in the project folder
    check.sh       exit 0 when the problem is solved

For each one the runner opens a real Aiterm window whose shell runs in a
bubblewrap sandbox (no network, no sudo, the host's home and session bus
hidden, the system read-only), types the history into it, and runs agy once
(`agy -p --output-format stream-json`) with the aiterm plugin, as if it were
in the agent panel. agy gets its own HOME with only the plugin and the
user's login linked in, so the user's GEMINI.md and settings don't change
what is measured. Then check.sh runs in the same sandbox.

Measured per run: solved, steps (tool calls), commands run in the user's
terminal and how many failed, tries to use agy's own hidden shell instead,
tokens and time. Results go to evals/results/latest.json and, with
--update-readme, into the README.

    evals/run.py                      all scenarios once
    evals/run.py typo-command -n 3    one scenario, three times
    evals/run.py --update-readme
    evals/run.py --from-results --update-readme   redraw the table only

Needs a graphical session, bwrap and a signed-in agy. Uses the agy
subscription's quota: about 100k tokens per scenario.
"""

import argparse
import datetime
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
SCENARIOS = os.path.join(ROOT, "evals", "scenarios")
RESULTS = os.path.join(ROOT, "evals", "results", "latest.json")
README = os.path.join(ROOT, "README.md")
BEGIN, END = "<!-- evals:begin -->", "<!-- evals:end -->"
DEFAULT_MODEL = "gemini-3.8-flash-low"
# Linked from the user's ~/.gemini so agy is signed in; nothing else is
LOGIN_FILES = ("oauth_creds.json", "google_accounts.json", "installation_id", "settings.json")

# check.sh gets these: EVAL_RESPONSE (the agent's answer, a file) and
# EVAL_COMMANDS (the commands run while the agent worked, JSON), and
# commands_ok REGEX: a command matching REGEX ran and succeeded
CHECK_PRELUDE = r"""
set -u
commands_ok() {
  python3 - "$1" <<'PY'
import json, os, re, sys
commands = json.load(open(os.environ["EVAL_COMMANDS"]))
sys.exit(0 if any(re.search(sys.argv[1], c["command"]) and c["exit_code"] == 0 for c in commands) else 1)
PY
}
"""


def sandbox(home, cwd):
    """bwrap argv: the system read-only, a private home at the same path as
    on the host (so the agent's file tools and the terminal agree on
    paths), no network, no D-Bus, no sudo."""
    shell_dir = os.path.join(ROOT, "src", "aiterm", "shell")
    return [
        "bwrap", "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc",
        *[arg for path in ("/tmp", "/home", "/run", "/media", "/mnt") for arg in ("--tmpfs", path)],
        "--bind", home, home, "--ro-bind", shell_dir, shell_dir,
        "--setenv", "HOME", home, "--unsetenv", "DBUS_SESSION_BUS_ADDRESS",
        "--unsetenv", "WAYLAND_DISPLAY", "--unsetenv", "DISPLAY",
        "--unshare-net", "--unshare-pid", "--unshare-ipc", "--unshare-uts", "--hostname", "sandbox",
        "--die-with-parent", "--chdir", cwd, "--",
    ]


def agy_home(model):
    """A HOME for agy with the user's login, the aiterm plugin, and
    permission for the aiterm tools only (so its own shell is refused)."""
    home = tempfile.mkdtemp(prefix="aiterm-eval-agy-")
    gemini = os.path.join(home, ".gemini")
    os.makedirs(os.path.join(gemini, "antigravity-cli"))
    os.makedirs(os.path.join(gemini, "config", "plugins"))
    for name in LOGIN_FILES:
        source = os.path.expanduser(f"~/.gemini/{name}")
        if os.path.exists(source):
            os.symlink(source, os.path.join(gemini, name))
    os.symlink(os.path.join(ROOT, "agy-plugin"), os.path.join(gemini, "config", "plugins", "aiterm"))
    with open(os.path.join(gemini, "antigravity-cli", "settings.json"), "w") as f:
        json.dump({"model": model, "permissions": {"allow": ["mcp(aiterm_terminal/*)"]},
                   "trustedWorkspaces": [tempfile.gettempdir()], "showTips": False}, f, indent=2)
    return home


def agy_binary():
    return shutil.which("agy") or os.path.expanduser("~/.local/bin/agy")


def agy_version():
    try:
        return subprocess.run([agy_binary(), "--version"], capture_output=True, text=True,
                              timeout=30).stdout.strip()
    except OSError:
        return "?"


def load_scenarios(names):
    found = sorted(n for n in os.listdir(SCENARIOS) if os.path.isfile(os.path.join(SCENARIOS, n, "scenario.json")))
    unknown = set(names) - set(found)
    if unknown:
        sys.exit(f"Unknown scenario: {', '.join(sorted(unknown))}. Known: {', '.join(found)}")
    scenarios = []
    for name in names or found:
        with open(os.path.join(SCENARIOS, name, "scenario.json")) as f:
            scenarios.append({"name": name, **json.load(f)})
    return scenarios


def prepare(name):
    """A fresh folder for one run: work/home/project, with setup.sh run in
    the sandbox. Returns (work, home, project)."""
    work = tempfile.mkdtemp(prefix=f"aiterm-eval-{name}-")
    home = os.path.join(work, "home")
    project = os.path.join(home, "project")
    os.makedirs(project)
    with open(os.path.join(SCENARIOS, name, "setup.sh")) as f:
        setup = subprocess.run(sandbox(home, project) + ["bash", "-e", "-c", f.read()],
                               capture_output=True, text=True, timeout=60)
    if setup.returncode:
        raise RuntimeError(f"{name}: setup.sh failed: {setup.stderr}")
    return work, home, project


def check(name, work, response, commands):
    """Runs the scenario's check.sh in the sandbox; returns the
    CompletedProcess (returncode 0: solved)."""
    home = os.path.join(work, "home")
    with open(os.path.join(work, "response.txt"), "w") as f:
        f.write(response)
    with open(os.path.join(work, "commands.json"), "w") as f:
        json.dump(commands, f, indent=1)
    with open(os.path.join(SCENARIOS, name, "check.sh")) as f:
        script = f.read()
    argv = sandbox(home, os.path.join(home, "project"))
    for variable, file in (("EVAL_RESPONSE", "response.txt"), ("EVAL_COMMANDS", "commands.json")):
        path = os.path.join(work, file)
        argv[-1:-1] = ["--ro-bind", path, path, "--setenv", variable, path]
    return subprocess.run(argv + ["bash", "-c", CHECK_PRELUDE + script], capture_output=True, text=True,
                          timeout=60)


# Metrics from agy's stream-json events

def summarize(events):
    tools = {}
    result = {}
    for event in events:
        if event.get("event") == "step_update":
            step = event["step_update"]
            if step.get("step_type") == "tool":
                tools[step.get("step_index")] = step
        elif event.get("event") == "result":
            result = event["result"]

    def is_terminal(step, tool=None):
        params = (step.get("tool_info") or {}).get("parameters") or {}
        return (step.get("tool_name") == "call_mcp_tool" and params.get("ServerName") == "aiterm_terminal"
                and (tool is None or params.get("ToolName") == tool))

    usage = result.get("usage") or {}
    return {
        "status": result.get("status", "NO_RESULT"),
        "response": result.get("response", ""),
        "steps": len(tools),
        "terminal_tool_calls": sum(is_terminal(s) for s in tools.values()),
        "hidden_shell_tries": sum(s.get("tool_name") == "run_command" for s in tools.values()),
        "tokens": usage.get("total_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "seconds": round(result.get("duration_seconds", 0), 1),
    }


class Runner:
    def __init__(self, args):
        self.args = args
        self.agy_home = agy_home(args.model)
        self.results = []

    # GTK; imported here so --help works without a display
    def run(self, scenarios):
        import gi

        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        gi.require_version("Vte", "3.91")
        from gi.repository import Gio, GLib

        from aiterm.application import Application
        from aiterm.settings import Settings

        os.environ["AITERM_CONFIG_DIR"] = tempfile.mkdtemp(prefix="aiterm-eval-config-")
        self.GLib, self.Gio = GLib, Gio
        app = Application(application_id="io.github.khrystofor_main.Aiterm.Evals",
                          flags=Gio.ApplicationFlags.NON_UNIQUE)

        def start(app):
            Settings.get().approve_agent_commands = False  # nobody to click Run
            try:
                for scenario in scenarios:
                    for trial in range(self.args.repeat):
                        self.results.append(self.run_one(app, scenario, trial))
            finally:
                for window in app.get_windows():
                    window.destroy()
                app.quit()

        app.connect("activate", lambda app: GLib.idle_add(lambda: start(app) and False))
        app.run([])
        shutil.rmtree(os.environ["AITERM_CONFIG_DIR"], ignore_errors=True)
        shutil.rmtree(self.agy_home, ignore_errors=True)
        return self.results

    def wait_for(self, predicate, seconds):
        context = self.GLib.MainContext.default()
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if predicate():
                return True
            context.iteration(False)
            time.sleep(0.01)
        return predicate()

    def run_one(self, app, scenario, trial):
        from aiterm.terminal import BASH_INTEGRATION
        from aiterm.window import Window

        name = scenario["name"]
        print(f"▶ {name}" + (f" #{trial + 1}" if self.args.repeat > 1 else ""), flush=True)
        work, home, project = prepare(name)

        # The user's terminal: a sandboxed bash with aiterm's shell integration
        for window in app.get_windows():
            window.destroy()
        window = Window(application=app, cwd=project,
                        argv=sandbox(home, project) + ["bash", "--rcfile", BASH_INTEGRATION])
        window.present()
        terminal = window.current_terminal()
        log = terminal.command_log
        if not self.wait_for(lambda: log._prompt is not None, 20):
            raise RuntimeError(f"{name}: the sandboxed shell shows no prompt")
        for command in scenario.get("history", []):
            count = len(log.commands)
            terminal.feed_child(command.encode() + b"\n")
            self.wait_for(lambda: len(log.commands) > count, 20)
        before = len(log.commands)

        # The agent, once, as the panel would start it
        events = self.run_agent(app, window, project, scenario["prompt"])
        metrics = summarize(events)
        commands = [{"command": c.text, "exit_code": c.exit_code, "output": c.output[-2000:]}
                    for c in log.commands[before:]]
        metrics["terminal_commands"] = len(commands)
        metrics["failed_commands"] = sum(c["exit_code"] != 0 for c in commands)

        with open(os.path.join(work, "events.jsonl"), "w") as f:
            f.writelines(json.dumps(e, ensure_ascii=False) + "\n" for e in events)
        checked = check(name, work, metrics["response"], commands)
        solved = checked.returncode == 0 and metrics["status"] == "SUCCESS"
        window.destroy()
        print(f"  {'solved' if solved else 'NOT solved'} · {metrics['steps']} steps · "
              f"{metrics['terminal_commands']} commands ({metrics['failed_commands']} failed) · "
              f"{metrics['tokens']:,} tokens · {metrics['seconds']} s", flush=True)
        if not self.args.keep:
            shutil.rmtree(work, ignore_errors=True)
        else:
            print(f"  kept: {work}")
        return {"scenario": name, "title": scenario["title"], "category": scenario["category"],
                "trial": trial + 1, "solved": solved, **metrics, "commands": commands,
                "check_output": (checked.stdout + checked.stderr)[-1000:]}

    def run_agent(self, app, window, cwd, prompt):
        Gio, GLib = self.Gio, self.GLib
        launcher = Gio.SubprocessLauncher.new(Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE)
        launcher.set_cwd(cwd)
        for key, value in {
            "HOME": self.agy_home,
            "AITERM_WINDOW": str(window.get_id()),
            "AITERM_BUS_NAME": app.get_dbus_connection().get_unique_name(),
            "AITERM_OBJECT_PATH": app.get_dbus_object_path(),
            "PATH": f"{os.path.join(ROOT, 'bin')}:{os.environ.get('PATH', '')}",
        }.items():
            launcher.setenv(key, value, True)
        process = launcher.spawnv([agy_binary(), "--model", self.args.model, "--output-format", "stream-json",
                                   "-p", prompt])
        stream = Gio.DataInputStream.new(process.get_stdout_pipe())
        events, done = [], []

        def on_line(stream, result):
            try:
                line, _ = stream.read_line_finish_utf8(result)
            except GLib.Error:
                line = None
            if line is None:
                done.append(True)
                return
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                pass
            stream.read_line_async(GLib.PRIORITY_DEFAULT, None, on_line)

        stream.read_line_async(GLib.PRIORITY_DEFAULT, None, on_line)
        if not self.wait_for(lambda: done, self.args.timeout):
            process.force_exit()
            self.wait_for(lambda: done, 10)
        return events


# Reporting

def report(results, model, version):
    lines = ["| Scenario | Category | Solved | Steps | Commands (failed) | Hidden shell | Tokens | Time |",
             "|---|---|---|---|---|---|---|---|"]
    by_name = {}
    for r in results:
        by_name.setdefault(r["scenario"], []).append(r)
    for name, runs in by_name.items():
        solved = sum(r["solved"] for r in runs)
        median = lambda key: statistics.median(r[key] for r in runs)
        solved_text = ("✅" if solved == len(runs) else "❌" if not solved else "◐") + (
            f" {solved}/{len(runs)}" if len(runs) > 1 else "")
        lines.append(
            f"| {runs[0]['title']} | {runs[0]['category']} | {solved_text} | {median('steps'):g} | "
            f"{median('terminal_commands'):g} ({median('failed_commands'):g}) | "
            f"{sum(r['hidden_shell_tries'] for r in runs)} | {median('tokens'):,.0f} | {median('seconds'):.0f} s |")
    solved = sum(r["solved"] for r in results)
    summary = (f"**{solved}/{len(results)} solved** with {model} (agy {version}). "
               f"Median per run: {statistics.median(r['tokens'] for r in results):,.0f} tokens, "
               f"{statistics.median(r['steps'] for r in results):g} steps, "
               f"{statistics.median(r['seconds'] for r in results):.0f} s. "
               f"Tries to use agy's own shell instead of the user's terminal: "
               f"{sum(r['hidden_shell_tries'] for r in results)}.")
    return summary, "\n".join(lines)


def update_readme(summary, table, date):
    with open(README) as f:
        text = f.read()
    if BEGIN not in text or END not in text:
        sys.exit(f"README.md has no {BEGIN} … {END} section")
    head, rest = text.split(BEGIN, 1)
    _, tail = rest.split(END, 1)
    block = f"{BEGIN}\n{summary} Run on {date}.\n\n{table}\n{END}"
    with open(README, "w") as f:
        f.write(head + block + tail)


def main():
    parser = argparse.ArgumentParser(description="Run aiterm's evals with the real agy.")
    parser.add_argument("scenarios", nargs="*", help="scenario names (default: all)")
    parser.add_argument("-n", "--repeat", type=int, default=1, help="runs per scenario")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"agy model (default {DEFAULT_MODEL})")
    parser.add_argument("--timeout", type=int, default=300, help="seconds per run")
    parser.add_argument("--keep", action="store_true", help="keep each run's folder")
    parser.add_argument("--update-readme", action="store_true", help="write the results table into README.md")
    parser.add_argument("--from-results", action="store_true",
                        help=f"don't run: report {os.path.relpath(RESULTS, ROOT)} again")
    args = parser.parse_args()

    if args.from_results:  # only redraw the table from the last run
        with open(RESULTS) as f:
            saved = json.load(f)
        summary, table = report(saved["runs"], saved["model"], saved["agy"])
        print(summary)
        print(table)
        if args.update_readme:
            update_readme(summary, table, saved["date"])
        return

    if not (os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY")):
        sys.exit("The evals open Aiterm windows: run them in a graphical session.")
    for tool, why in (("bwrap", "the sandbox (sudo apt install bubblewrap)"), (agy_binary(), "the agent")):
        if not shutil.which(tool):
            sys.exit(f"Missing {tool}: needed for {why}.")
    scenarios = load_scenarios(args.scenarios)
    version = agy_version()
    results = Runner(args).run(scenarios)
    if not results:
        sys.exit(1)
    date = datetime.date.today().isoformat()
    os.makedirs(os.path.dirname(RESULTS), exist_ok=True)
    with open(RESULTS, "w") as f:
        json.dump({"date": date, "model": args.model, "agy": version, "runs": results}, f, indent=1,
                  ensure_ascii=False)
        f.write("\n")
    summary, table = report(results, args.model, version)
    print()
    print(summary)
    print(table)
    if args.update_readme:
        update_readme(summary, table, date)
        print(f"\nREADME.md updated; full results in {os.path.relpath(RESULTS, ROOT)}")


if __name__ == "__main__":
    main()
