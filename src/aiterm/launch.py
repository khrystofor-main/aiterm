"""Starting Aiterm quietly: `aiterm` typed in a terminal hands the app to a
detached process and returns the prompt at once, with nothing printed there
later. The app's output (GTK and GLib warnings, Python tracebacks) goes to a
log file instead. No GTK here."""

import os
import subprocess
import sys

# Set in the detached process's environment so it runs the app instead of
# detaching again; the app takes it out before starting any shell
DETACHED_ENV = "AITERM_DETACHED"
# The log is moved to aiterm.log.1 when it grows past this at a start
LOG_LIMIT = 512 * 1024


def log_path():
    """$XDG_STATE_HOME/aiterm/aiterm.log, ~/.local/state/aiterm/aiterm.log by default."""
    state = os.environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state")
    return os.path.join(state, "aiterm", "aiterm.log")


def open_log():
    """Opens the log for appending, after moving a big one to aiterm.log.1
    (the one before it is dropped): two runs' worth at most."""
    path = log_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        if os.path.getsize(path) > LOG_LIMIT:
            os.replace(path, path + ".1")
    except OSError:
        pass
    return open(path, "ab", buffering=0)


def spawn_detached(args, cwd):
    """Starts `python3 -m aiterm ARGS` in its own session, away from the
    caller's terminal: no controlling terminal, stdin from /dev/null,
    stdout and stderr to the log. Closing that terminal leaves it running."""
    env = dict(os.environ, **{DETACHED_ENV: "1"})
    # The package's own folder first, however this process found it
    package_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [package_root, env.get("PYTHONPATH")]))
    with open_log() as log:
        subprocess.Popen([sys.executable, "-m", "aiterm", *args], cwd=cwd, env=env,
                         stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                         start_new_session=True)
