"""aiterm-left and aiterm-run: the terminal API (dbus_api.py) on the command
line, through client.py. bin/aiterm-left and bin/aiterm-run run this. The
agent uses the MCP server (mcp_server.py) instead; these stay for scripts and
debugging.

    python3 -m aiterm.tools left [N|all]
    python3 -m aiterm.tools run [-t SECONDS] 'command' | -w
"""

import argparse
import sys

from aiterm.client import NotInside, TerminalClient, Unreachable, format_command

NOT_INSIDE, USAGE, BUSY, TIMEOUT = 1, 2, 3, 124
FALLBACK_LINES = 200


def left(client, mode):
    if mode == "all":
        folder, text = client.read_screen()
        print(f"[terminal folder: {folder}]")
        print(text)
        return 0
    folder, commands = client.read_commands(int(mode))
    print(f"[terminal folder: {folder}]")
    if commands:
        print("\n".join(format_command(*c[:3]) for c in commands))
    else:
        # Nothing in the command log (no shell integration, a full-screen
        # program): the tail of the screen is the best guess
        _, text = client.read_screen()
        print("\n".join(text.split("\n")[-FALLBACK_LINES:]))
    return 0


def run(client, command, timeout, wait_only):
    result = client.wait(timeout) if wait_only else client.run(command, timeout)
    status, folder, text, output, exit_code = result
    if status == "busy":
        print(f"The terminal is busy: «{text}» is running. Nothing was typed.", file=sys.stderr)
        print("Wait for it (aiterm-run -w) or ask the user to finish the program.", file=sys.stderr)
        return BUSY
    if status == "typing":
        print(f"The user is typing in the terminal: «{text}». Nothing was typed.", file=sys.stderr)
        print("Ask them to finish or clear the line (Ctrl+C), then retry.", file=sys.stderr)
        return BUSY
    print(f"[terminal folder: {folder}]")
    if status == "timeout":
        if output:
            print(output)
        print(f"[the command is still running or waiting for input (now running: {text}). "
              "If it waits for a password or an answer, tell the user what to type in their "
              "terminal, then call aiterm-run -w]")
        return TIMEOUT
    print(format_command(text, output, exit_code))
    return 0


def main(argv):
    try:
        client = TerminalClient()
    except NotInside as error:
        print(error, file=sys.stderr)
        return NOT_INSIDE
    parser = argparse.ArgumentParser(prog="aiterm-tools")
    sub = parser.add_subparsers(dest="tool", required=True)
    p_left = sub.add_parser("left", prog="aiterm-left")
    p_left.add_argument("mode", nargs="?", default="1")
    p_run = sub.add_parser("run", prog="aiterm-run")
    p_run.add_argument("-t", dest="timeout", type=int, default=60)
    p_run.add_argument("-w", dest="wait_only", action="store_true")
    p_run.add_argument("command", nargs="?")
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        return USAGE
    if args.tool == "left":
        if args.mode != "all" and not (args.mode.isdigit() and int(args.mode) > 0):
            print("Usage: aiterm-left [N|all]", file=sys.stderr)
            return USAGE
        return call(left, client, args.mode)
    if not args.wait_only and not args.command or args.timeout < 0:
        print("Usage: aiterm-run [-t SECONDS] 'command'  |  aiterm-run [-t SECONDS] -w", file=sys.stderr)
        return USAGE
    return call(run, client, args.command or "", args.timeout, args.wait_only)


def call(tool, client, *args):
    try:
        return tool(client, *args)
    except Unreachable as error:
        print(error, file=sys.stderr)
        return NOT_INSIDE


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
