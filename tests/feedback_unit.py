#!/usr/bin/env python3
"""Unit tests for feedback as GitHub issues (feedback.py) and the watcher the
feedback thread runs (dev/feedback-watch), against tests/fake_gh.py: nothing
reaches GitHub. Run: tests/feedback_unit.py"""

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
change(logged_in=False)
code, out, err = watch("--once")
check("…and says when gh can't reach GitHub", code == 1 and "gh auth login" in err, (code, out, err))

print(f"{results.count(True)} passed, {results.count(False)} failed")
sys.exit(0 if all(results) else 1)
