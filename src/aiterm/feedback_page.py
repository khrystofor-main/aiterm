"""Preferences → Feedback: tell what you don't like, with screenshots or
recordings; it goes to GitHub as an issue (feedback.py) and Claude makes the
change. Below, the feedback sent so far with what each waits for, and the
conversation with Claude in each, answered from here.

FeedbackMonitor keeps the list for the page and, while any feedback is open,
re-reads it every POLL_SECONDS to notify about Claude's questions and results.
GitHub is reached through `gh` in a thread, never on the main loop."""

import json
import os
import re
import threading

from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk, Pango, Vte

from aiterm import feedback
from aiterm.chat_view import markdown_to_pango
from aiterm.settings import Settings, config_dir

POLL_SECONDS = 120
FILE_FILTER_TYPES = ("image/png", "image/jpeg", "image/gif", "image/webp", "video/mp4", "video/webm")

CSS = """
.feedback-text { padding: 10px 12px; }
.feedback-text text { background: none; }
.feedback-file { padding: 4px 4px 4px 8px; }
.feedback-status { font-size: smaller; padding: 2px 8px; border-radius: 999px;
                   background: alpha(currentColor, 0.1); }
.feedback-status.question { background: @accent_bg_color; color: @accent_fg_color; }
.feedback-you { padding: 8px 12px; }
"""


def in_background(work, done):
    """Runs work() in a thread, then done(result, error) on the main loop."""
    def run():
        try:
            result, error = work(), None
        except Exception as e:  # GhError, or a reply GitHub never sends: shown, not lost in a thread
            result, error = None, str(e) or type(e).__name__
        GLib.idle_add(lambda: done(result, error) or GLib.SOURCE_REMOVE)

    threading.Thread(target=run, daemon=True).start()


def libraries():
    return {
        "GTK": f"{Gtk.get_major_version()}.{Gtk.get_minor_version()}.{Gtk.get_micro_version()}",
        "libadwaita": f"{Adw.get_major_version()}.{Adw.get_minor_version()}.{Adw.get_micro_version()}",
        "VTE": f"{Vte.get_major_version()}.{Vte.get_minor_version()}.{Vte.get_micro_version()}",
    }


def short_time(iso):
    stamp = GLib.DateTime.new_from_iso8601(iso, None)
    return stamp.to_local().format("%-d %b, %H:%M") if stamp else iso


def open_uri(widget, uri):
    Gtk.UriLauncher.new(uri).launch(widget.get_root(), None, None)


class FeedbackMonitor(GObject.Object):
    """The user's feedback issues, shared by every Feedback page, and the
    notifications. "changed" fires after each read."""

    __gsignals__ = {"changed": (GObject.SignalFlags.RUN_FIRST, None, ())}

    _instance = None

    @classmethod
    def get(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        super().__init__()
        self.items = []
        self.error = None
        self.login = None
        self.access_problem = None  # why feedback goes through the browser
        self.checked = False
        self.busy = False
        self.loaded = False
        self.viewers = 0
        self.tracker = None
        self.path = os.path.join(config_dir(), "feedback.json")
        try:
            with open(self.path) as f:
                self.state = json.load(f)
        except (OSError, ValueError):
            self.state = {}
        self.timer = None

    def start(self):
        """Polls from now on, while there is open feedback or a page shows it."""
        if self.timer is None:
            self.timer = GLib.timeout_add_seconds(POLL_SECONDS, self._tick)
        if self.state.get("open"):
            self.refresh()

    def _tick(self):
        if self.state.get("open") or self.viewers:
            self.refresh()
        return GLib.SOURCE_CONTINUE

    def watch(self, showing):
        self.viewers += 1 if showing else -1
        if showing:
            self.refresh()

    @property
    def questions(self):
        return sum(1 for item in self.items if item.status == "question")

    def refresh(self):
        if self.busy:
            return
        self.busy = True

        def work():
            if not self.checked:
                self.login, self.access_problem = feedback.check_access()
                self.checked = True
            if not self.login:
                raise feedback.GhError(self.access_problem)
            if self.tracker is None:
                self.tracker = feedback.Tracker(creator=self.login)
            return self.tracker.poll()

        in_background(work, self._read)

    def _read(self, items, error):
        self.busy = False
        self.loaded = True
        if error is not None:
            self.error = error
            self.emit("changed")
            return
        self.error = None
        self.items = items
        known = self.state.get("seen")
        seen = set(known or [])
        for item in items:
            for message in item.messages:
                if message.author == "claude" and message.id not in seen:
                    seen.add(message.id)
                    # The first read ever only learns what is there
                    if known is not None and message.kind in ("question", "done"):
                        self.notify(item, message)
        self.state = {"seen": sorted(seen), "open": [item.number for item in items if item.state == "open"]}
        self.save()
        self.emit("changed")

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.state, f)
        os.replace(tmp, self.path)

    def sent(self, number):
        self.state.setdefault("seen", [])
        self.state.setdefault("open", []).append(number)
        self.save()
        self.refresh()

    def notify(self, item, message):
        app = Gio.Application.get_default()
        if not app:
            return
        if message.kind == "question":
            notification = Gio.Notification.new("Claude asks about your feedback")
            notification.set_body(" ".join(message.text.split())[:240])
        else:
            notification = Gio.Notification.new("Your feedback is done")
            notification.set_body(f"{item.title}. Restart Aiterm to get the change")
        notification.set_default_action_and_target("app.show-feedback", GLib.Variant("u", item.number))
        app.send_notification(f"feedback-{item.number}", notification)


def text_of(view):
    buffer = view.get_buffer()
    return buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)


def text_box(view, height):
    view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
    view.set_accepts_tab(False)
    view.add_css_class("feedback-text")
    scroller = Gtk.ScrolledWindow(child=view, min_content_height=height, max_content_height=height * 3,
                                  propagate_natural_height=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
    scroller.add_css_class("card")
    return scroller


class FileChip(Gtk.Box):
    """An attached file: a thumbnail or an icon, its name, and a remove button."""

    def __init__(self, path, on_remove):
        super().__init__(spacing=8)
        self.path = path
        self.add_css_class("card")
        self.add_css_class("feedback-file")
        kind = feedback.FILE_TYPES.get(os.path.splitext(path)[1].lower())
        if kind == "image":
            picture = Gtk.Picture.new_for_filename(path)
            picture.set_content_fit(Gtk.ContentFit.COVER)
            picture.set_size_request(32, 32)
            self.append(picture)
        else:
            self.append(Gtk.Image(icon_name="video-x-generic-symbolic"))
        self.append(Gtk.Label(label=os.path.basename(path), ellipsize=Pango.EllipsizeMode.MIDDLE,
                              max_width_chars=24))
        remove = Gtk.Button(icon_name="window-close-symbolic", tooltip_text="Remove", valign=Gtk.Align.CENTER)
        remove.add_css_class("flat")
        remove.add_css_class("circular")
        remove.connect("clicked", lambda _b: on_remove(self))
        self.append(remove)


class FeedbackPage(Adw.PreferencesPage):
    def __init__(self):
        super().__init__(title="Feedback", icon_name="mail-send-symbolic")
        self.monitor = FeedbackMonitor.get()
        self.context_items = None
        self.files = []
        self.sending = False

        # Writing one
        self.send_group = Adw.PreferencesGroup(
            title="Send Feedback",
            description="What don't you like in Aiterm, or what is missing? It goes to GitHub as an issue from "
                        "your gh account, and Claude makes the change. Screenshots and recordings help most",
        )
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.text = Gtk.TextView()
        self.text.get_buffer().connect("changed", lambda *_: self._update_send())
        box.append(text_box(self.text, 110))
        self.files_box = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=3,
                                     column_spacing=8, row_spacing=8, visible=False)
        box.append(self.files_box)
        buttons = Gtk.Box(spacing=12)
        self.attach_button = Gtk.Button(child=Adw.ButtonContent(icon_name="mail-attachment-symbolic",
                                                                label="Attach Files…"))
        self.attach_button.connect("clicked", lambda _b: self.choose_files())
        buttons.append(self.attach_button)
        self.progress = Gtk.Label(hexpand=True, xalign=1, ellipsize=Pango.EllipsizeMode.END)
        self.progress.add_css_class("dim-label")
        buttons.append(self.progress)
        self.send_button = Gtk.Button(label="Send", sensitive=False)
        self.send_button.add_css_class("suggested-action")
        self.send_button.add_css_class("pill")
        self.send_button.connect("clicked", lambda _b: self.send())
        buttons.append(self.send_button)
        box.append(buttons)
        self.send_group.add(box)
        self.add(self.send_group)
        # Files dropped anywhere on the page are attached
        drop = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop.connect("drop", lambda _t, files, *_: self.attach([f.get_path() for f in files.get_files()]))
        self.add_controller(drop)

        context = Adw.PreferencesGroup()
        self.context_row = Adw.ExpanderRow(
            title="Sent With It", subtitle="Versions and the preferences you changed. "
                                           "Never your terminal's text or commands")
        context.add(self.context_row)
        self.add(context)

        # Sent so far
        self.list_group = Adw.PreferencesGroup(title="Your Feedback")
        refresh = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Refresh", valign=Gtk.Align.CENTER)
        refresh.add_css_class("flat")
        refresh.connect("clicked", lambda _b: self.monitor.refresh())
        self.list_group.set_header_suffix(refresh)
        self.rows = []
        self.add(self.list_group)

        self.conversation = None
        self.monitor.connect("changed", lambda *_: self.show_items())
        self.connect("map", lambda *_: self.monitor.watch(True))
        self.connect("unmap", lambda *_: self.monitor.watch(False))
        self.show_items()
        in_background(lambda: feedback.context(libraries(), feedback.changed_settings(Settings.get())),
                      self._context_ready)

    def _context_ready(self, items, _error):
        self.context_items = items
        for name, value in items:
            row = Adw.ActionRow(title=name, subtitle=GLib.markup_escape_text(str(value)), subtitle_selectable=True)
            row.add_css_class("property")
            self.context_row.add_row(row)

    def _update_send(self):
        self.send_button.set_sensitive(bool(text_of(self.text).strip()) and not self.sending)

    def toast(self, title):
        dialog = self.get_ancestor(Adw.PreferencesDialog)
        if dialog:
            dialog.add_toast(Adw.Toast(title=title, timeout=4))

    # Files

    def choose_files(self):
        media = Gtk.FileFilter(name="Pictures and Videos")
        for mime in FILE_FILTER_TYPES:
            media.add_mime_type(mime)
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(media)
        dialog = Gtk.FileDialog(title="Attach Files", filters=filters, default_filter=media)
        dialog.open_multiple(self.get_root(), None, self._chosen)

    def _chosen(self, dialog, result):
        try:
            files = dialog.open_multiple_finish(result)
        except GLib.Error:
            return  # cancelled
        self.attach([files.get_item(i).get_path() for i in range(files.get_n_items())])

    def attach(self, paths):
        for path in paths:
            if not path or path in self.files:
                continue
            problem = feedback.check_file(path)
            if problem:
                self.toast(problem)
                continue
            self.files.append(path)
            self.files_box.append(FileChip(path, self._remove))
        self.files_box.set_visible(bool(self.files))
        return True

    def _remove(self, chip):
        self.files.remove(chip.path)
        self.files_box.remove(chip.get_parent())
        self.files_box.set_visible(bool(self.files))

    # Sending

    def send(self):
        text = text_of(self.text).strip()
        if not text or self.sending:
            return
        self.sending = True
        self._update_send()
        self.attach_button.set_sensitive(False)
        files = list(self.files)
        monitor = self.monitor

        def progress(message):
            GLib.idle_add(lambda: self.progress.set_label(message) or GLib.SOURCE_REMOVE)

        def work():
            items = self.context_items or feedback.context(libraries(), feedback.changed_settings(Settings.get()))
            context_md = feedback.context_markdown(items)
            if not monitor.checked:
                monitor.login, monitor.access_problem = feedback.check_access()
                monitor.checked = True
            if monitor.access_problem:
                return "browser", feedback.browser_url(text, context_md)
            return "sent", feedback.send(text, files, context_md, progress)

        in_background(work, self._sent)

    def _sent(self, result, error):
        self.sending = False
        self.attach_button.set_sensitive(True)
        self.progress.set_label("")
        if error is not None:
            self._update_send()
            self.toast(f"Not sent: {error}")
            return
        how, value = result
        self._sent_clear()
        if how == "browser":
            open_uri(self, value)
            self.toast("gh can't send it, so GitHub's form opened in your browser: add the files there")
            return
        number, _url = value
        self.toast(f"Sent as #{number}. Claude takes it from here")
        self.monitor.sent(number)

    def _sent_clear(self):
        self.text.get_buffer().set_text("")
        for path in list(self.files):
            self.files.remove(path)
        self.files_box.remove_all()
        self.files_box.set_visible(False)
        self._update_send()

    # The list

    def show_items(self):
        for row in self.rows:
            self.list_group.remove(row)
        self.rows = []
        monitor = self.monitor
        self.set_icon_name("mail-unread-symbolic" if monitor.questions else "mail-send-symbolic")
        if monitor.error and not monitor.items:
            self._add_row(Adw.ActionRow(title="Can't read your feedback from GitHub",
                                        subtitle=GLib.markup_escape_text(monitor.error)))
        elif not monitor.items:
            row = Adw.ActionRow(title="Loading…" if not monitor.loaded else "Nothing sent yet")
            row.add_css_class("dim-label")
            self._add_row(row)
        for item in monitor.items:
            row = Adw.ActionRow(title=GLib.markup_escape_text(item.title), activatable=True,
                                subtitle=f"#{item.number} · {short_time(item.created)}")
            status = Gtk.Label(label=feedback.STATUS_LABELS[item.status], valign=Gtk.Align.CENTER)
            status.add_css_class("feedback-status")
            status.add_css_class(item.status)
            row.add_suffix(status)
            row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
            row.connect("activated", lambda _r, number=item.number: self.open_conversation(number))
            row.number = item.number
            self._add_row(row)
        if self.conversation:
            self.conversation.update()

    def _add_row(self, row):
        self.rows.append(row)
        self.list_group.add(row)

    def open_conversation(self, number):
        item = next((i for i in self.monitor.items if i.number == number), None)
        dialog = self.get_ancestor(Adw.PreferencesDialog)
        if not item or not dialog:
            return False
        self.conversation = Conversation(self, number)
        self.conversation.connect("hidden", lambda page: setattr(self, "conversation", None)
                                  if self.conversation is page else None)
        dialog.push_subpage(self.conversation)
        return True


def message_markup(text):
    # Pictures show as links: a label can't hold them
    return markdown_to_pango(re.sub(r"!\[([^\]\n]*)\]\(", r"[🖼 \1](", text)) or " "


class Conversation(Adw.NavigationPage):
    """One feedback: the user's text and replies on the right, Claude's on the
    left, and a box to answer while it is open."""

    def __init__(self, page, number):
        super().__init__(title=f"#{number}")
        self.page = page
        self.number = number
        header = Adw.HeaderBar()
        self.github = Gtk.Button(icon_name="web-browser-symbolic", tooltip_text="Open on GitHub")
        self.github.connect("clicked", lambda _b: open_uri(self, self.item().url))
        header.pack_end(self.github)
        self.messages = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14,
                                margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        self.scroller = Gtk.ScrolledWindow(child=Adw.Clamp(child=self.messages, maximum_size=640), vexpand=True,
                                           hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.answer = Gtk.TextView()
        self.answer.get_buffer().connect(
            "changed", lambda *_: self.send_button.set_sensitive(bool(text_of(self.answer).strip())))
        self.send_button = Gtk.Button(icon_name="mail-send-symbolic", tooltip_text="Answer", sensitive=False,
                                      valign=Gtk.Align.END)
        self.send_button.add_css_class("circular")
        self.send_button.add_css_class("suggested-action")
        self.send_button.connect("clicked", lambda _b: self.send())
        self.answer_bar = Gtk.Box(spacing=8, margin_top=6, margin_bottom=12, margin_start=12, margin_end=12)
        answer_box = text_box(self.answer, 24)
        answer_box.set_hexpand(True)
        self.answer_bar.append(answer_box)
        self.answer_bar.append(self.send_button)
        view = Adw.ToolbarView(content=self.scroller)
        view.add_top_bar(header)
        view.add_bottom_bar(Adw.Clamp(child=self.answer_bar, maximum_size=640))
        self.set_child(view)
        self.connect("shown", lambda *_: self._focus())
        self.update()

    def _focus(self):
        (self.answer if self.answer_bar.get_visible() else self.github).grab_focus()
        # A message's label that had the focus first keeps all its text selected
        for label in self.labels:
            label.select_region(0, 0)

    def item(self):
        return next((i for i in self.page.monitor.items if i.number == self.number), None)

    def update(self):
        item = self.item()
        if not item:
            return
        self.set_title(f"#{item.number} {item.title}")
        while child := self.messages.get_first_child():
            self.messages.remove(child)
        self.labels = []
        for message in item.messages:
            self.messages.append(self._bubble(message))
        self.answer_bar.set_visible(item.state == "open")
        GLib.idle_add(self._scroll_down)

    def _scroll_down(self):
        adjustment = self.scroller.get_vadjustment()
        adjustment.set_value(adjustment.get_upper())
        return GLib.SOURCE_REMOVE

    def _bubble(self, message):
        label = Gtk.Label(xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, selectable=True)
        label.set_markup(message_markup(message.text))
        self.labels.append(label)
        meta = Gtk.Label(xalign=0, label=short_time(message.created))
        meta.add_css_class("caption")
        meta.add_css_class("dim-label")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        if message.author == "you":
            box.set_halign(Gtk.Align.END)
            box.set_margin_start(48)
            card = Gtk.Box()
            card.add_css_class("card")
            card.add_css_class("feedback-you")
            card.append(label)
            box.append(card)
            meta.set_xalign(1)
        else:
            box.set_margin_end(48)
            who = "Claude" + (" asks" if message.kind == "question" else "")
            meta.set_label(f"{who} · {short_time(message.created)}")
            box.append(meta)
            box.append(label)
            return box
        box.append(meta)
        return box

    def send(self):
        text = text_of(self.answer).strip()
        if not text:
            return
        self.send_button.set_sensitive(False)
        self.answer.set_sensitive(False)
        in_background(lambda: feedback.reply(self.number, text), self._sent)

    def _sent(self, _result, error):
        self.answer.set_sensitive(True)
        if error is not None:
            self.send_button.set_sensitive(True)
            self.page.toast(f"Not sent: {error}")
            return
        self.answer.get_buffer().set_text("")
        self.page.monitor.refresh()
