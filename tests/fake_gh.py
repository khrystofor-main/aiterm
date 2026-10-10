#!/usr/bin/env python3
"""A stand-in for `gh api -i` (the only gh command feedback.py runs), so
tests never reach GitHub. Its GitHub lives in the JSON file FAKE_GH_STATE:
  login, push, logged_in, issues [...], comments {number: [...]},
  branches [...], files {path: base64}, calls [[method, endpoint]]
Tests read and change that file to play the user or Claude.
Run as AITERM_GH="python3 tests/fake_gh.py"."""

import hashlib
import json
import os
import sys
import urllib.parse

REPO = "khrystofor-main/aiterm"
STATE = os.environ["FAKE_GH_STATE"]


def load():
    try:
        with open(STATE) as f:
            state = json.load(f)
    except (OSError, ValueError):
        state = {}
    for key, value in {"login": "khrystofor-main", "push": True, "logged_in": True, "issues": [],
                       "comments": {}, "branches": ["main"], "files": {}, "calls": [], "clock": 0}.items():
        state.setdefault(key, value)
    return state


def save(state):
    with open(STATE, "w") as f:
        json.dump(state, f, indent=1)


def now(state):
    state["clock"] += 1
    return f"2026-10-10T12:{state['clock'] // 60:02d}:{state['clock'] % 60:02d}Z"


def answer(status, data, etag=None):
    reason = {200: "OK", 201: "Created", 304: "Not Modified", 404: "Not Found", 403: "Forbidden"}.get(status, "")
    print(f"HTTP/2.0 {status} {reason}")
    if etag:
        print(f'Etag: "{etag}"')
    print()
    if data is not None:
        print(json.dumps(data))
    if status >= 300:
        print(f"gh: HTTP {status}", file=sys.stderr)
        sys.exit(1)
    sys.exit(0)


def main(argv):
    if argv[:1] != ["api"]:
        print(f"fake gh: unsupported {argv}", file=sys.stderr)
        sys.exit(2)
    method, endpoint, etag, body = "GET", None, None, None
    args = iter(argv[1:])
    for arg in args:
        if arg == "-X":
            method = next(args)
        elif arg == "-H":
            header = next(args)
            if header.lower().startswith("if-none-match:"):
                etag = header.split(":", 1)[1].strip().strip('"')
        elif arg == "--input":
            next(args)
            body = json.loads(sys.stdin.read())
        elif arg == "-i":
            pass
        else:
            endpoint = arg
    state = load()
    if not state["logged_in"]:
        print("To get started with GitHub CLI, please run:  gh auth login", file=sys.stderr)
        sys.exit(4)
    state["calls"].append([method, endpoint])
    path, _, query = endpoint.partition("?")
    query = dict(urllib.parse.parse_qsl(query))
    prefix = f"repos/{REPO}"
    rest = path[len(prefix):] if path.startswith(prefix) else "/unknown"

    def done(status, data, etag=None):
        save(state)
        answer(status, data, etag)

    if path == "user":
        done(200, {"login": state["login"]})
    if rest == "":
        done(200, {"full_name": REPO, "permissions": {"push": state["push"]}})
    if rest.startswith("/branches/"):
        name = rest.split("/", 2)[2]
        done(200, {"name": name}) if name in state["branches"] else done(404, {"message": "Branch not found"})
    if rest in ("/git/trees", "/git/commits") and method == "POST":
        done(201, {"sha": hashlib.sha1(json.dumps(body).encode()).hexdigest()})
    if rest == "/git/refs" and method == "POST":
        state["branches"].append(body["ref"].removeprefix("refs/heads/"))
        done(201, {"ref": body["ref"]})
    if rest.startswith("/contents/") and method == "PUT":
        if not state["push"]:
            done(403, {"message": "Resource not accessible by integration"})
        if body["branch"] not in state["branches"]:
            done(404, {"message": "Branch not found"})
        state["files"][urllib.parse.unquote(rest.removeprefix("/contents/"))] = body["content"]
        done(201, {"content": {}})
    if rest == "/issues" and method == "POST":
        number = len(state["issues"]) + 1
        stamp = now(state)
        issue = {"number": number, "title": body["title"], "body": body["body"], "state": "open",
                 "labels": [{"name": n} for n in body.get("labels", [])], "user": {"login": state["login"]},
                 "comments": 0, "created_at": stamp, "updated_at": stamp,
                 "html_url": f"https://github.com/{REPO}/issues/{number}"}
        state["issues"].append(issue)
        done(201, issue)
    if rest == "/issues" and method == "GET":
        found = [i for i in reversed(state["issues"])
                 if ("labels" not in query or any(l["name"] == query["labels"] for l in i["labels"]))
                 and query.get("state", "open") in ("all", i["state"])
                 and query.get("creator", i["user"]["login"]) == i["user"]["login"]]
        tag = hashlib.sha1(json.dumps(found).encode()).hexdigest()
        done(304, None, tag) if etag == tag else done(200, found, tag)
    parts = rest.split("/")
    if len(parts) == 4 and parts[1] == "issues" and parts[3] == "comments":
        number = int(parts[2])
        issue = next(i for i in state["issues"] if i["number"] == number)
        comments = state["comments"].setdefault(str(number), [])
        if method == "GET":
            done(200, comments)
        stamp = now(state)
        comment = {"id": 1000 + sum(len(c) for c in state["comments"].values()), "body": body["body"],
                   "user": {"login": state["login"]}, "created_at": stamp,
                   "html_url": f"{issue['html_url']}#issuecomment-{stamp}"}
        comments.append(comment)
        issue["comments"] = len(comments)
        issue["updated_at"] = stamp
        done(201, comment)
    done(404, {"message": f"fake gh: no {method} {endpoint}"})


if __name__ == "__main__":
    main(sys.argv[1:])
