"""aiterm-left and aiterm-run for the GTK app: clients of the terminal API
(dbus_api.py). bin/aiterm-left and bin/aiterm-run run this when the agent was
started by Aiterm (AITERM_WINDOW is set); the arguments, output and exit
codes are the same as the tmux versions'.

    python3 -m aiterm.tools left [N|all]
    python3 -m aiterm.tools run [-t SECONDS] 'command' | -w
"""

import argparse
import os
import sys

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from aiterm import APP_ID, OBJECT_PATH, TERMINAL_INTERFACE  # noqa: E402
NOT_INSIDE, USAGE, BUSY, TIMEOUT = 1, 2, 3, 124
FALLBACK_LINES = 200


def call(method, args, signature, reply_type, seconds=30):
    bus = Gio.bus_get_sync(Gio.BusType.SESSION)
    name = os.environ.get("AITERM_BUS_NAME", APP_ID)
    window = int(os.environ["AITERM_WINDOW"])
    try:
        result = bus.call_sync(
            name, os.environ.get("AITERM_OBJECT_PATH", OBJECT_PATH), TERMINAL_INTERFACE, method,
            GLib.Variant(signature, (window, *args)), GLib.VariantType(reply_type),
            Gio.DBusCallFlags.NONE, seconds * 1000, None)
    except GLib.Error as error:
        print(f"Cannot reach the Aiterm window: {error.message}", file=sys.stderr)
        sys.exit(NOT_INSIDE)
    return result.unpack()


def format_command(command, output, exit_code):
    lines = [f"$ {command}"]
    if output:
        lines.append(output)
    if exit_code:
        lines.append(f"[exit code {exit_code}]")
    return "\n".join(lines)


def left(mode):
    if mode == "all":
        folder, text = call("ReadScreen", (), "(u)", "(ss)")
        print(f"[terminal folder: {folder}]")
        print(text)
        return 0
    folder, commands = call("ReadCommands", (int(mode),), "(ui)", "(sa(ssid))")
    print(f"[terminal folder: {folder}]")
    if commands:
        print("\n".join(format_command(c, o, e) for c, o, e, _ in commands))
    else:
        # Nothing in the command log (no shell integration, a full-screen
        # program): the tail of the screen is the best guess
        _, text = call("ReadScreen", (), "(u)", "(ss)")
        print("\n".join(text.split("\n")[-FALLBACK_LINES:]))
    return 0


def run(command, timeout, wait_only):
    if wait_only:
        result = call("Wait", (timeout,), "(uu)", "(ssssi)", timeout + 10)
    else:
        result = call("RunCommand", (command, timeout), "(usu)", "(ssssi)", timeout + 10)
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
    if not os.environ.get("AITERM_WINDOW", "").isdigit():
        print("agy is not running inside aiterm: there is no user terminal.", file=sys.stderr)
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
        return left(args.mode)
    if not args.wait_only and not args.command or args.timeout < 0:
        print("Usage: aiterm-run [-t SECONDS] 'command'  |  aiterm-run [-t SECONDS] -w", file=sys.stderr)
        return USAGE
    return run(args.command or "", args.timeout, args.wait_only)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
