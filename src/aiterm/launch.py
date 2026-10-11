"""Starting Aiterm quietly: `aiterm` typed in a terminal hands the app to a
detached process and returns the prompt at once, with nothing printed there
then or later. The app's output (GTK and GLib warnings, Python tracebacks)
goes to a log file instead.

The launcher is a plain Gio.Application under the app's ID, and it never
loads GTK: importing Gtk opens the display, and the display's own warnings
(EGL, Vulkan, GDK) would land in the caller's terminal. It checks the command
line, then either forwards it to a running Aiterm over D-Bus (GApplication's
remote mode, which prints the running app's errors here), or starts the app
detached (`--foreground`: in this process) and exits."""

import os
import subprocess
import sys

from gi.repository import Gio, GLib

from aiterm import APP_ID

# Set in the detached process's environment so it runs the app instead of
# detaching again; taken out before any shell starts
DETACHED_ENV = "AITERM_DETACHED"
# The log is moved to aiterm.log.1 when it grows past this at a start
LOG_LIMIT = 512 * 1024


def app_id():
    """The application ID; AITERM_APP_ID lets tests run the whole command
    under their own, never reaching the user's running Aiterm."""
    return os.environ.get("AITERM_APP_ID", APP_ID)


def add_options(app):
    """The command line, the same in the launcher and in the app (which gets
    it forwarded): `aiterm [DIR]`, `aiterm -w DIR`, `aiterm --foreground`."""
    app.add_main_option("working-directory", ord("w"), GLib.OptionFlags.NONE, GLib.OptionArg.FILENAME,
                        "Open the first tab in DIR instead of the current folder", "DIR")
    app.add_main_option("foreground", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                        "Stay in this terminal and print warnings here instead of the log", None)
    app.add_main_option(GLib.OPTION_REMAINING, 0, GLib.OptionFlags.NONE, GLib.OptionArg.FILENAME_ARRAY,
                        "", "[DIR]")


def start_directory(options, cwd):
    """The folder for the window a command line opens: `-w DIR` or `DIR`,
    relative to the caller's folder `cwd`, else that folder itself. Raises
    ValueError for a folder that does not exist."""
    named = options.lookup_value("working-directory", GLib.VariantType("ay"))
    rest = options.lookup_value(GLib.OPTION_REMAINING, GLib.VariantType("aay"))
    rest = [os.fsdecode(path) for path in rest.get_bytestring_array()] if rest else []
    if named and rest or len(rest) > 1:
        raise ValueError("give one folder to open in")
    arg = os.fsdecode(named.get_bytestring()) if named else rest[0] if rest else None
    if arg is None:
        # The caller's folder may be gone (deleted while its shell sat there):
        # then the home folder, as before
        return cwd if cwd and os.path.isdir(cwd) else None
    path = Gio.File.new_for_commandline_arg_and_cwd(arg, cwd or os.path.expanduser("~")).get_path()
    if not path or not os.path.isdir(path):
        raise ValueError(f"{arg}: no such folder")
    return path


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


def caller_cwd():
    try:
        return os.getcwd()
    except FileNotFoundError:
        return None  # the caller's folder is gone: the app opens in ~


class Launcher(Gio.Application):
    def __init__(self, argv):
        super().__init__(application_id=app_id(), flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        add_options(self)
        self.argv = argv
        self.run_here = False  # set when the app itself should run in this process

    def do_handle_local_options(self, options):
        """Runs after GApplication has dealt with --help and bad options
        (printed here, with their exit code)."""
        if os.environ.pop(DETACHED_ENV, None) or options.contains("foreground"):
            self.run_here = True
            return 0
        try:
            start_directory(options, caller_cwd())
        except ValueError as error:
            print(f"aiterm: {error}", file=sys.stderr)
            return 1
        if primary_running(self.get_application_id()):
            return -1  # GApplication forwards the command line to it
        spawn_detached(self.argv[1:], caller_cwd())
        return 0

    def do_command_line(self, command_line):
        # Reached only when the running Aiterm quit between the check above
        # and registering: start a new one, as if none had been running
        spawn_detached(self.argv[1:], caller_cwd())
        return 0


def primary_running(name):
    """Whether an Aiterm already owns the application ID on the session bus."""
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION)
        reply = bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                              "NameHasOwner", GLib.Variant("(s)", (name,)),
                              GLib.VariantType("(b)"), Gio.DBusCallFlags.NONE, -1)
    except GLib.Error:
        return False  # no session bus: the app will run on its own
    return reply.unpack()[0]


def main(argv):
    GLib.set_prgname("aiterm")  # for --help, as argv[0] is __main__.py
    launcher = Launcher(argv)
    code = launcher.run(argv)
    if not launcher.run_here:
        return code
    del launcher  # its ID is the app's: leave it to the real one
    from aiterm.application import main as run_app  # GTK from here on
    return run_app(argv)
