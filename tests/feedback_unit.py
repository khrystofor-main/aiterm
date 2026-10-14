#!/usr/bin/env python3
"""Unit tests for feedback as GitHub issues (feedback.py) and the watcher the
feedback thread runs (dev/feedback-watch), against tests/fake_gh.py: nothing
reaches GitHub. Run: tests/feedback_unit.py"""

import fcntl
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
TMP = tempfile.mkdtemp(prefix="aiterm-feedback-")
STATE = os.path.join(TMP, "gh.json")
os.environ["FAKE_GH_STATE"] = STATE
os.environ["AITERM_GH"] = f"{sys.executable} {os.path.join(ROOT, 'tests', 'fake_gh.py')}"
os.environ["AITERM_CONFIG_DIR"] = TMP

from aiterm import feedback  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(f"  {'ok  ' if ok else 'FAIL'} {name}" + ("" if ok else f"\n       {detail}"))


def state():
    with open(STATE + ".lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with open(STATE) as f:
            return json.load(f)


def change(**values):
    data = state() if os.path.exists(STATE) else {}
    data.update(values)
    with open(STATE, "w") as f:
        json.dump(data, f)


def claude_says(number, kind, text):
    """Claude's comment, as the feedback thread posts it."""
    feedback.reply(number, f"<!-- aiterm-feedback: {kind} -->\n{text}")


def watch(*args):
    done = subprocess.run([sys.executable, os.path.join(ROOT, "dev", "feedback-watch"), *args],
                          capture_output=True, text=True, timeout=30)
    return done.returncode, done.stdout.strip(), done.stderr.strip()


def file(name, size=10):
    path = os.path.join(TMP, name)
    with open(path, "wb") as f:
        f.write(b"x" * size)
    return path


# Access
change(logged_in=False)
login, problem = feedback.check_access()
check("not logged in: no login, and why", login is None and "gh auth login" in problem, problem)
change(logged_in=True, push=False)
login, problem = feedback.check_access()
check("no write access: the login, and why", login == "khrystofor-main" and "cannot add files" in problem, problem)
change(push=True)
check("logged in with write access", feedback.check_access() == ("khrystofor-main", None))
os.environ["AITERM_GH"] = os.path.join(TMP, "no-such-gh")
check("no gh at all", feedback.check_access() == (None, "gh is not installed"), feedback.check_access())
os.environ["AITERM_GH"] = f"{sys.executable} {os.path.join(ROOT, 'tests', 'fake_gh.py')}"

# The issue's text
check("the title is the first line", feedback.title_from("\n  Font is   too small \nmore") == "Font is too small")
check("…cut when long", feedback.title_from("a" * 100) == "a" * 71 + "…", feedback.title_from("a" * 100))
check("…'Feedback' for no text", feedback.title_from("  \n") == "Feedback")
items = feedback.context({"GTK": "4.20.1"}, {"palette": "Tango"})
names = [name for name, _ in items]
check("the context has the versions and the changed preferences",
      names == ["Aiterm", "GTK", "agy", "System", "Changed preferences"] and items[-1][1] == "palette = Tango", items)
from aiterm.settings import Settings  # noqa: E402
prefs = Settings()
prefs.palette, prefs.prompt_user_host, prefs.window_width = "Tango", "Lex Secret", 1234
changed = feedback.changed_settings(prefs)
check("changed preferences leave out window state and the prompt's own text",
      changed.get("palette") == "Tango" and changed.get("prompt_user_host") == "(set)"
      and "window_width" not in changed, changed)
context_md = feedback.context_markdown(items)
check("…in a block people can fold", context_md.startswith("<!-- aiterm-feedback: context -->\n<details>"))
check("…and the app hides it in its own view", feedback.clean(f"Hi\n\n{context_md}\n") == "Hi")

# Files
check("a picture can go", feedback.check_file(file("shot.png")) is None)
check("a recording can go", feedback.check_file(file("rec.WEBM")) is None)
check("other files can't", "only" in (feedback.check_file(file("notes.txt")) or ""))
big = file("big.gif")
os.truncate(big, feedback.MAX_FILE_SIZE + 1)
check("nor files over the limit", "over 25 MB" in (feedback.check_file(big) or ""), feedback.check_file(big))

# Sending
steps = []
number, url = feedback.send("Font is too small\nin the chat", [file("shot.png"), file("rec.mp4")], context_md,
                            steps.append)
data = state()
issue = data["issues"][0]
check("send creates an issue labelled feedback",
      (number, issue["title"], [l["name"] for l in issue["labels"]]) == (1, "Font is too small", ["feedback"]),
      issue)
check("…returns its address", url == "https://github.com/khrystofor-main/aiterm/issues/1", url)
check("…starts the files branch once", data["branches"] == ["main", "feedback-assets"], data["branches"])
paths = sorted(data["files"])
check("…puts the files in a folder of their own", len(paths) == 2 and all(
    p.startswith("feedback/") and p.split("/")[1] == paths[0].split("/")[1] for p in paths), paths)
folder = os.path.dirname(paths[0])
check("…shows a picture in the issue",
      f"![shot.png](https://github.com/khrystofor-main/aiterm/raw/feedback-assets/{folder}/shot.png)"
      in issue["body"], issue["body"])
check("…links a recording",
      f"[rec.mp4](https://github.com/khrystofor-main/aiterm/raw/feedback-assets/{folder}/rec.mp4)" in issue["body"],
      issue["body"])
check("…ends with the context", issue["body"].rstrip().endswith("</details>"))
check("…and says what it does", steps[-1] == "Creating the issue…" and "Uploading shot.png (1 of 2)…" in steps,
      steps)
feedback.send("Second", [file("again.png")], context_md)
check("a second send keeps the branch", state()["branches"] == ["main", "feedback-assets"])
try:
    feedback.send("Bad", [file("notes.txt")], context_md)
    check("a wrong file stops the send before anything is made", False)
except feedback.GhError as error:
    check("a wrong file stops the send before anything is made",
          "only" in str(error) and len(state()["issues"]) == 2, str(error))

url = feedback.browser_url("Font is too small", context_md)
check("without gh: the browser form, filled in",
      url.startswith("https://github.com/khrystofor-main/aiterm/issues/new?title=Font+is+too+small&body=")
      and "labels=feedback" in url, url)

# Reading: what each feedback waits for
tracker = feedback.Tracker(creator="khrystofor-main")
items = tracker.poll()
check("the list is newest first", [i.number for i in items] == [2, 1], [i.number for i in items])
first = items[1]
check("the first message is the user's text, without the context",
      first.messages[0].author == "you" and first.messages[0].text.startswith("Font is too small\nin the chat")
      and "details" not in first.messages[0].text, first.messages[0])
check("a new feedback is Sent, and needs Claude", first.status == "sent" and first.needs_claude)
calls = len(state()["calls"])
tracker.poll()
check("polling again with nothing new is one call (304)", len(state()["calls"]) == calls + 1)

claude_says(1, "status", "Understood: bigger chat font. Working on it")
first = next(i for i in tracker.poll() if i.number == 1)
check("Claude's status comment: In Progress, and nothing for Claude",
      first.status == "working" and not first.needs_claude and first.messages[-1].author == "claude"
      and first.messages[-1].text == "Understood: bigger chat font. Working on it", first.messages[-1])
claude_says(1, "question", "1. 12 pt\n2. 14 pt")
first = next(i for i in tracker.poll() if i.number == 1)
check("a question: Question for You", first.status == "question" and first.messages[-1].kind == "question")
data = state()
data["comments"]["1"].append({"id": 5, "body": "do it", "user": {"login": "stranger"},
                              "created_at": "2026-10-10T13:00:00Z", "html_url": "x"})
data["issues"][0]["updated_at"] = "2026-10-10T13:00:00Z"
with open(STATE, "w") as f:
    json.dump(data, f)
first = next(i for i in tracker.poll() if i.number == 1)
check("someone else's comment is left out", first.status == "question" and len(first.messages) == 3,
      [m.text for m in first.messages])
feedback.reply(1, "2")
first = next(i for i in tracker.poll() if i.number == 1)
check("the user's answer: In Progress again, and Claude is needed",
      first.status == "working" and first.needs_claude and first.messages[-1].text == "2")
claude_says(1, "pr", "The change: https://github.com/khrystofor-main/aiterm/pull/50")
check("a pull request: Change Ready", next(i for i in tracker.poll() if i.number == 1).status == "pr")
data = state()
data["issues"][0]["state"] = "closed"
data["issues"][0]["updated_at"] = "2026-10-10T14:00:00Z"
with open(STATE, "w") as f:
    json.dump(data, f)
check("closed: Done", next(i for i in tracker.poll() if i.number == 1).status == "done")

# The watcher
code, out, err = watch("--once")
check("the watcher lists what needs Claude", code == 0 and out == "#2 new: Second", (code, out, err))
claude_says(2, "status", "On it")
code, out, err = watch("--once")
check("…and nothing once Claude answered", code == 0 and out == "", (code, out, err))
feedback.reply(2, "Also the tabs")
code, out, err = watch("--interval", "0.1")
check("…waits for and shows a reply", code == 0 and out == "#2 reply: Second — Also the tabs", (code, out, err))

# A follow-up on a finished feedback: the reply reopens it
claude_says(1, "done", "Done: the chat font is 14 pt")
calls = len(state()["calls"])
feedback.reply(2, "And the tab bar")
check("a reply to an open feedback does not reopen it",
      [c for c in state()["calls"][calls:] if c[0] == "PATCH"] == [], state()["calls"][calls:])
calls = len(state()["calls"])
feedback.reply(1, "Now a bit smaller please", reopen=True)
data = state()
check("a reply to a closed feedback reopens it, then comments",
      data["calls"][calls:] == [["PATCH", "repos/khrystofor-main/aiterm/issues/1"],
                                ["POST", "repos/khrystofor-main/aiterm/issues/1/comments"]]
      and data["issues"][0]["state"] == "open", data["calls"][calls:])
first = next(i for i in tracker.poll() if i.number == 1)
check("…it waits for Claude again (Sent), and Claude is needed",
      first.state == "open" and first.status == "sent" and first.needs_claude, (first.state, first.status))
code, out, err = watch("--once")
check("…and the watcher shows the reply",
      code == 0 and "#1 reply: Font is too small — Now a bit smaller please" in out.splitlines(), (code, out, err))
claude_says(1, "status", "Making it 13 pt")
check("…In Progress once Claude answers", next(i for i in tracker.poll() if i.number == 1).status == "working")

# Replies with files, like a new feedback's
files_before = set(state()["files"])
steps = []
feedback.reply(1, "Like this:", [file("reply.png"), file("reply.webm")], progress=steps.append)
data = state()
new_files = sorted(set(data["files"]) - files_before)
body = data["comments"]["1"][-1]["body"]
reply_folder = os.path.dirname(new_files[0]) if new_files else ""
check("a reply with files uploads them to a folder of its own",
      len(new_files) == 2 and reply_folder.startswith("feedback/") and reply_folder != folder
      and all(os.path.dirname(p) == reply_folder for p in new_files), new_files)
check("…and shows them in the comment, below the text",
      body.startswith("Like this:\n\n")
      and f"![reply.png](https://github.com/khrystofor-main/aiterm/raw/feedback-assets/{reply_folder}/reply.png)" in body
      and f"[reply.webm](https://github.com/khrystofor-main/aiterm/raw/feedback-assets/{reply_folder}/reply.webm)"
      in body, body)
check("…and says what it does", "Uploading reply.png (1 of 2)…" in steps and steps[-1] == "Sending…", steps)
feedback.reply(1, "  ", [file("only.gif")])
body = state()["comments"]["1"][-1]["body"]
check("a reply can be files only", body.startswith("![only.gif](") and "\n" not in body, body)
first = next(i for i in tracker.poll() if i.number == 1)
check("…and the conversation shows it like the issue's pictures",
      first.messages[-1].author == "you" and first.messages[-1].text == body, first.messages[-1])
comments = len(state()["comments"]["1"])
try:
    feedback.reply(1, "Bad", [file("notes.txt")])
    check("a wrong file stops the reply before anything is sent", False)
except feedback.GhError as error:
    check("a wrong file stops the reply before anything is sent",
          "only" in str(error) and len(state()["comments"]["1"]) == comments, str(error))
try:
    feedback.reply(1, "  ")
    check("an empty reply is not sent", False)
except feedback.GhError:
    check("an empty reply is not sent", len(state()["comments"]["1"]) == comments)

# On GitHub: Claude's comments come from the Claude GitHub App's bot
def bot_says(number, body, login="claude[bot]"):
    data = state()
    issue = data["issues"][number - 1]
    data["clock"] += 1
    stamp = f"2026-10-10T15:00:{data['clock'] % 60:02d}Z"
    data["comments"][str(number)].append({"id": 9000 + data["clock"], "body": body, "user": {"login": login},
                                          "created_at": stamp, "html_url": "x"})
    issue["comments"] = len(data["comments"][str(number)])
    issue["updated_at"] = stamp
    with open(STATE, "w") as f:
        json.dump(data, f)


feedback.reply(2, "And the scrollbar")
bot_says(2, "Looks fine to me", login="someone[bot]")
bot_says(2, "<!-- aiterm-feedback: status -->\nNot Claude", login="someone[bot]")
bot_says(2, "No marker, so not one of Claude's")
second = next(i for i in tracker.poll() if i.number == 2)
check("other bots, and Claude's bot without a marker, are left out",
      second.messages[-1].text == "And the scrollbar" and second.needs_claude, [m.text for m in second.messages])
bot_says(2, "<!-- aiterm-feedback: question -->\nWhich scrollbar?")
second = next(i for i in tracker.poll() if i.number == 2)
check("Claude's bot with a marker is Claude: Question for You",
      second.status == "question" and second.messages[-1].author == "claude" and not second.needs_claude
      and second.messages[-1].text == "Which scrollbar?", second.messages[-1])

# A run that stops with an error: the workflow says so, and a reply retries
feedback.reply(2, "The right one")
bot_says(2, "<!-- aiterm-feedback: failed -->\nClaude stopped before finishing this: usage limit",
         login="github-actions[bot]")
second = next(i for i in tracker.poll() if i.number == 2)
check("the workflow's failed comment: Stopped, nothing for Claude",
      second.status == "failed" and feedback.STATUS_LABELS[second.status] == "Stopped"
      and second.messages[-1].author == "claude" and not second.needs_claude, second.messages[-1])
feedback.reply(2, "Try again")
second = next(i for i in tracker.poll() if i.number == 2)
check("…and a reply makes it Sent again", second.status == "sent" and second.needs_claude, second.status)

# The watcher stands down once the workflow on GitHub handles feedback
check("feedback is not on GitHub while the variable is unset", feedback.handled_on_github() is False)
change(variables={"FEEDBACK_AGENT": "local"})
check("…or set to anything else", feedback.handled_on_github() is False)
feedback.reply(2, "The terminal's")
change(variables={"FEEDBACK_AGENT": "github"})
check("…and is once it is `github`", feedback.handled_on_github() is True)
code, out, err = watch("--once")
check("the watcher then lists nothing, says why, exits with 3",
      code == 3 and out == "feedback-watch: feedback is handled on GitHub (FEEDBACK_AGENT = github)", (code, out, err))
code, out, err = watch("--interval", "0.1")
check("…also when it is started to wait", code == 3 and "handled on GitHub" in out, (code, out, err))
change(variables={})

change(logged_in=False)
code, out, err = watch("--once")
check("…and says when gh can't reach GitHub", code == 1 and "gh auth login" in err, (code, out, err))

print(f"{results.count(True)} passed, {results.count(False)} failed")
sys.exit(0 if all(results) else 1)
