"""Keyboard shortcuts that win over the terminal.

VTE turns every key it receives into terminal input, so ordinary application
accelerators never fire while a terminal has focus. These shortcuts are caught
in the capture phase, on the way down to the focused widget, before VTE sees
the key.
"""

from gi.repository import Gtk


def add_capture_shortcuts(widget, shortcuts):
    """shortcuts: {trigger string: callback()}. A caught key always counts as
    handled, so it never also reaches the shell."""
    controller = Gtk.ShortcutController(propagation_phase=Gtk.PropagationPhase.CAPTURE)
    for trigger, callback in shortcuts.items():
        controller.add_shortcut(Gtk.Shortcut(
            trigger=Gtk.ShortcutTrigger.parse_string(trigger),
            action=Gtk.CallbackAction.new(_run, callback),
        ))
    widget.add_controller(controller)
    return controller


def _run(_widget, _args, callback):
    callback()
    return True
