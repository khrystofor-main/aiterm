"""Feedback from Preferences → Feedback, as GitHub issues (no GTK).

The user's own `gh` does the talking, so Aiterm keeps no token: a feedback
is an issue labelled `feedback` in the Aiterm repository, its files go to the
`feedback-assets` branch (the issues API takes no uploads) and are linked
from it. Claude answers in the issue's comments, through the same account,
and marks each comment with a hidden kind (MARKER), so the app tells its
questions, progress and results from the user's own replies.

Tests swap `gh` for tests/fake_gh.py through AITERM_GH.
"""

import base64
import datetime
import json
import os
import re
import shlex
import subprocess
import urllib.parse
import uuid
from dataclasses import dataclass, field

from aiterm import VERSION

REPO = "khrystofor-main/aiterm"
OWNER = REPO.split("/")[0]
LABEL = "feedback"
ASSETS_BRANCH = "feedback-assets"
# The contents API takes files up to about this size
MAX_FILE_SIZE = 25 * 1024 * 1024
FILE_TYPES = {
    ".png": "image", ".jpg": "image", ".jpeg": "image", ".gif": "image", ".webp": "image",
    ".mp4": "video", ".webm": "video",
}

# <!-- aiterm-feedback: question --> in a comment: Claude wrote it, and asks.
# The issue body carries "context" around the block the app adds
MARKER = re.compile(r"<!--\s*aiterm-feedback:\s*([a-z]+)\s*-->")
KINDS = ("status", "question", "pr", "done")
CONTEXT_BLOCK = re.compile(r"<!--\s*aiterm-feedback:\s*context\s*-->.*?</details>", re.S)

# What a feedback waits for, in order
STATUSES = ("sent", "working", "question", "pr", "done")
STATUS_LABELS = {
    "sent": "Sent", "working": "In Progress", "question": "Question for You", "pr": "Change Ready", "done": "Done",
}

# Preferences that are window state, not choices worth reporting
NOT_PREFERENCES = {
    "window_width", "window_height", "window_maximized", "agent_panel_visible", "agent_panel_width",
    "animations_hint_shown",
}

SRC_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class GhError(Exception):
    pass


@dataclass
class Response:
    status: int
    etag: str | None
    data: object


def gh_command():
    return shlex.split(os.environ.get("AITERM_GH") or "gh")


def api(endpoint, method="GET", body=None, etag=None, timeout=60):
    """One GitHub REST call through `gh api`. A 304 (not changed since `etag`)
    comes back as a Response with no data; errors raise GhError."""
    args = [*gh_command(), "api", "-i", "-X", method, endpoint]
    if etag:
        args += ["-H", f"If-None-Match: {etag}"]
    if body is not None:
        args += ["--input", "-"]
    try:
        done = subprocess.run(args, input=None if body is None else json.dumps(body), capture_output=True,
                              text=True, timeout=timeout)
    except FileNotFoundError:
        raise GhError("gh is not installed") from None
    except subprocess.TimeoutExpired:
        raise GhError("GitHub did not answer in time") from None
    head, _, payload = done.stdout.replace("\r\n", "\n").partition("\n\n")
    status_line = head.split("\n", 1)[0]
    match = re.match(r"HTTP/\S+ (\d{3})", status_line)
    if not match:  # gh never reached GitHub: not logged in, no network
        raise GhError(done.stderr.strip().removeprefix("gh: ") or "gh failed")
    status = int(match[1])
    found = re.search(r"^etag:\s*(.+)$", head, re.I | re.M)
    try:
        data = json.loads(payload) if payload.strip() else None
    except ValueError:
        data = payload
    if status >= 400:
        message = data.get("message") if isinstance(data, dict) else None
        raise GhError(f"GitHub answered {status}" + (f": {message}" if message else ""))
    return Response(status, found[1].strip() if found else None, data)


def check_access():
    """(login, None) when gh can send feedback with files, else (login or
    None, why not): then the browser form is the way."""
    try:
        login = api("user").data["login"]
    except GhError as error:
        return None, str(error)
    try:
        repo = api(f"repos/{REPO}").data
    except GhError as error:
        return login, str(error)
    if not (repo.get("permissions") or {}).get("push"):
        return login, f"{login} cannot add files to {REPO}"
    return login, None


# The context sent with each feedback

def app_commit():
    try:
        done = subprocess.run(["git", "-C", SRC_ROOT, "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout.strip() if done.returncode == 0 else None


def agy_version():
    try:
        done = subprocess.run(["agy", "--version"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return (done.stdout.strip() or done.stderr.strip()).splitlines()[0] if done.returncode == 0 else None


def distro(path="/etc/os-release"):
    try:
        with open(path) as f:
            for line in f:
                if line.startswith("PRETTY_NAME="):
                    return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return None


def changed_settings(settings):
    """The preferences the user changed from their defaults: {key: value}."""
    out = {}
    for spec in settings.list_properties():
        key = spec.name.replace("-", "_")
        value = settings.get_property(key)
        if key not in NOT_PREFERENCES and value != spec.get_default_value():
            out[key] = value
    return out


def context(libraries, changed):
    """[(name, value)]: Aiterm, the libraries (GTK side passes their
    versions), agy, the system and the changed preferences."""
    commit = app_commit()
    items = [("Aiterm", f"{VERSION} ({commit})" if commit else VERSION)]
    items += list(libraries.items())
    items += [("agy", agy_version() or "not found"), ("System", distro() or "unknown")]
    items.append(("Changed preferences", ", ".join(f"{k} = {v}" for k, v in changed.items()) or "none"))
    return items


def context_markdown(items):
    rows = "\n".join(f"| {name} | {str(value).replace('|', '/')} |" for name, value in items)
    return ("<!-- aiterm-feedback: context -->\n<details><summary>Sent from Aiterm</summary>\n\n"
            f"| | |\n|---|---|\n{rows}\n\n</details>")


# Sending

def title_from(text, limit=72):
    line = next((line.strip() for line in text.splitlines() if line.strip()), "")
    line = re.sub(r"\s+", " ", line)
    if not line:
        return "Feedback"
    return line if len(line) <= limit else line[:limit - 1].rstrip() + "…"


def check_file(path):
    """None when the file can go with a feedback, else why not."""
    kind = FILE_TYPES.get(os.path.splitext(path)[1].lower())
    if not kind:
        return f"{os.path.basename(path)}: only {', '.join(sorted(FILE_TYPES))} files"
    try:
        size = os.path.getsize(path)
    except OSError as error:
        return f"{os.path.basename(path)}: {error.strerror}"
    if size > MAX_FILE_SIZE:
        return f"{os.path.basename(path)} is over {MAX_FILE_SIZE // 1024 // 1024} MB"
    return None


def ensure_assets_branch():
    """The branch for feedback files: its own history, nothing of the app's."""
    try:
        api(f"repos/{REPO}/branches/{ASSETS_BRANCH}")
        return
    except GhError as error:
        if "404" not in str(error):
            raise
    readme = ("# Feedback files\n\nScreenshots and recordings sent from Aiterm's Preferences → Feedback, "
              "linked from the `feedback` issues. Not part of the app.\n")
    tree = api(f"repos/{REPO}/git/trees", "POST", {
        "tree": [{"path": "README.md", "mode": "100644", "type": "blob", "content": readme}]}).data
    commit = api(f"repos/{REPO}/git/commits", "POST", {
        "message": "Start the feedback files branch", "tree": tree["sha"], "parents": []}).data
    api(f"repos/{REPO}/git/refs", "POST", {"ref": f"refs/heads/{ASSETS_BRANCH}", "sha": commit["sha"]})


def file_markdown(path, folder):
    name = os.path.basename(path)
    quoted = urllib.parse.quote(f"{folder}/{name}")
    raw = f"https://github.com/{REPO}/raw/{ASSETS_BRANCH}/{quoted}"
    if FILE_TYPES[os.path.splitext(path)[1].lower()] == "image":
        return f"![{name}]({raw})"
    return f"🎞️ [{name}]({raw})"


def upload(path, folder):
    """Commits one file to the assets branch; returns the Markdown that shows it."""
    name = os.path.basename(path)
    with open(path, "rb") as f:
        content = base64.b64encode(f.read()).decode()
    api(f"repos/{REPO}/contents/{urllib.parse.quote(f'{folder}/{name}')}", "PUT", {
        "message": f"Feedback file {folder}/{name}", "content": content, "branch": ASSETS_BRANCH,
    }, timeout=300)
    return file_markdown(path, folder)


def issue_body(text, files_markdown, context_md):
    parts = [text.strip()]
    if files_markdown:
        parts.append("\n\n".join(files_markdown))
    parts.append(context_md)
    return "\n\n".join(parts) + "\n"


def send(text, paths, context_md, progress=lambda message: None):
    """Creates the issue with its files; returns (number, url)."""
    for path in paths:
        problem = check_file(path)
        if problem:
            raise GhError(problem)
    files = []
    if paths:
        progress("Uploading files…")
        ensure_assets_branch()
        folder = f"feedback/{datetime.date.today().isoformat()}-{uuid.uuid4().hex[:6]}"
        for i, path in enumerate(paths, 1):
            progress(f"Uploading {os.path.basename(path)} ({i} of {len(paths)})…")
            files.append(upload(path, folder))
    progress("Creating the issue…")
    issue = api(f"repos/{REPO}/issues", "POST", {
        "title": title_from(text), "body": issue_body(text, files, context_md), "labels": [LABEL],
    }).data
    return issue["number"], issue["html_url"]


def browser_url(text, context_md, limit=6000):
    """The new-issue form with everything but the files filled in."""
    body = issue_body(text, [], context_md)
    if len(body) > limit:
        body = body[:limit] + "\n…"
    query = urllib.parse.urlencode({"title": title_from(text), "body": body, "labels": LABEL})
    return f"https://github.com/{REPO}/issues/new?{query}"


def reply(number, text):
    api(f"repos/{REPO}/issues/{number}/comments", "POST", {"body": text.strip()})


# Reading

@dataclass
class Message:
    id: int  # 0 for the issue itself
    author: str  # "you" or "claude"
    kind: str | None  # a KINDS entry for Claude's comments
    text: str
    created: str
    url: str


@dataclass
class Feedback:
    number: int
    title: str
    url: str
    state: str  # "open" or "closed"
    created: str
    updated: str
    messages: list = field(default_factory=list)

    @property
    def status(self):
        return status_of(self.state, self.messages)

    @property
    def needs_claude(self):
        """Open, and the user wrote last: a new feedback or a reply."""
        return self.state == "open" and bool(self.messages) and self.messages[-1].author == "you"


def clean(text):
    """A message as people read it: no markers, no context block."""
    text = CONTEXT_BLOCK.sub("", text or "")
    return MARKER.sub("", text).strip()


def parse_messages(issue, comments):
    """The issue and the comments of its author: Claude's carry a marker,
    the rest are the user's. Comments of anyone else are left out."""
    author = issue["user"]["login"]
    out = [Message(0, "you", None, clean(issue.get("body")), issue["created_at"], issue["html_url"])]
    for comment in comments:
        if comment["user"]["login"] != author:
            continue
        marker = MARKER.search(comment.get("body") or "")
        kind = marker[1] if marker and marker[1] in KINDS else None
        out.append(Message(comment["id"], "claude" if marker else "you", kind, clean(comment.get("body")),
                           comment["created_at"], comment["html_url"]))
    return out


def status_of(state, messages):
    if state == "closed":
        return "done"
    last_claude = next((i for i in range(len(messages) - 1, -1, -1) if messages[i].author == "claude"), None)
    if last_claude is None:
        return "sent"
    kind = messages[last_claude].kind or "status"
    if kind == "question" and last_claude < len(messages) - 1:
        return "working"  # answered
    return {"status": "working", "question": "question", "pr": "pr", "done": "done"}[kind]


class Tracker:
    """The user's feedback issues, re-read cheaply: the list with an ETag,
    the comments only of issues that changed."""

    def __init__(self, creator=None, open_only=False):
        self.creator = creator
        self.open_only = open_only
        self.etag = None
        self.issues = []
        self.comments = {}  # number -> (updated_at, [comment])

    def endpoint(self):
        query = {"labels": LABEL, "state": "open" if self.open_only else "all", "per_page": 50,
                 "sort": "created", "direction": "desc"}
        if self.creator:
            query["creator"] = self.creator
        return f"repos/{REPO}/issues?{urllib.parse.urlencode(query)}"

    def poll(self):
        """[Feedback], newest first; GhError when GitHub can't be reached."""
        response = api(self.endpoint(), etag=self.etag)
        if response.status != 304:
            self.etag = response.etag
            self.issues = [i for i in response.data if "pull_request" not in i]
        out = []
        for issue in self.issues:
            number = issue["number"]
            cached = self.comments.get(number)
            if not issue.get("comments"):
                comments = []
            elif cached and cached[0] == issue["updated_at"]:
                comments = cached[1]
            else:
                comments = api(f"repos/{REPO}/issues/{number}/comments?per_page=100").data
            self.comments[number] = (issue["updated_at"], comments)
            out.append(Feedback(number, issue["title"], issue["html_url"], issue["state"], issue["created_at"],
                                issue["updated_at"], parse_messages(issue, comments)))
        return out
