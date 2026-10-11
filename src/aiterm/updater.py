"""Keeps the user's Aiterm up to date while it runs; update.py does the git
work, in a thread. The first check comes shortly after start, then one every
CHECK_SECONDS, and one at once when a feedback is done (FeedbackMonitor), as
that means a change was merged. After an update a notification asks to
restart Aiterm. Preferences → Behavior → Update Automatically turns it off.

Only the real app starts it (application.py): tests run their own app ID."""

import sys
import threading

from gi.repository import Gio, GLib

from aiterm import update
from aiterm.settings import Settings

FIRST_CHECK_SECONDS = 20
CHECK_SECONDS = 3600


class Updater:
    _instance = None

    @classmethod
    def get(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        self.started = False
        self.busy = False
        self.loaded = None  # the commit the running app was started from
        self.notified = None  # the commit the last notification was about
        self.changes = []  # what came in since the app started: on restart

    def start(self):
        if self.started or not update.is_clone():
            return
        self.started = True
        self.loaded = update.head()
        GLib.timeout_add_seconds(FIRST_CHECK_SECONDS, lambda: self.check() or GLib.SOURCE_REMOVE)
        GLib.timeout_add_seconds(CHECK_SECONDS, lambda: self.check() or GLib.SOURCE_CONTINUE)

    @property
    def active(self):
        return self.started and Settings.get().auto_update

    def check(self):
        """Updates now, in the background, if it is on and nothing runs yet."""
        if not self.active or self.busy:
            return
        self.busy = True

        def run():
            try:
                result, error = update.update(), None
            except update.UpdateError as e:
                result, error = None, str(e)
            GLib.idle_add(lambda: self._done(result, error) or GLib.SOURCE_REMOVE)

        threading.Thread(target=run, daemon=True).start()

    def _done(self, result, error):
        self.busy = False
        if error:
            print(f"aiterm: update failed: {error}", file=sys.stderr)
            return
        if result.updated:
            print(f"aiterm: {result.reason}", file=sys.stderr)
            self.changes += result.changes
        # Also when someone else pulled into the clone (`git pull` by hand),
        # but not for a clone moved to another branch to work in
        current = result.updated or result.reason == update.UP_TO_DATE
        if current and result.head not in (self.loaded, self.notified):
            self.notified = result.head
            self.notify()

    def notify(self):
        app = Gio.Application.get_default()
        if not app:
            return
        notification = Gio.Notification.new("Aiterm was updated")
        count = len(self.changes)
        if count:
            more = f" and {count - 1} more change{'s' if count > 2 else ''}" if count > 1 else ""
            notification.set_body(f"{self.changes[-1]}{more}. Restart Aiterm to use the new version")
        else:
            notification.set_body("Restart Aiterm to use the new version")
        app.send_notification("aiterm-update", notification)
