#!/usr/bin/env python3
"""Unit tests for the updates of an Aiterm git clone (update.py), on
throwaway repositories: an "origin" and the user's clone of it. Nothing
reaches GitHub or the clone the tests run from. Run: tests/update_unit.py"""

import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from aiterm import update  # noqa: E402

TMP = tempfile.mkdtemp(prefix="aiterm-update-")
ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
       "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
os.environ.update(ENV)
results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(f"  {'ok  ' if ok else 'FAIL'} {name}" + ("" if ok else f"\n       {detail}"))


def git(repo, *args):
    return subprocess.run(["git", "-C", repo, *args], check=True, capture_output=True, text=True,
                          env=ENV).stdout.strip()


def commit(repo, name, text, message):
    path = os.path.join(repo, name)
    with open(path, "w") as f:
        f.write(text)
    if name.endswith(".sh"):
        os.chmod(path, 0o755)
    git(repo, "add", name)
    git(repo, "commit", "-q", "-m", message)


# The developer's side: a repository with an install.sh that leaves a mark
dev = os.path.join(TMP, "dev")
os.makedirs(dev)
git(dev, "init", "-q", "-b", "main")
commit(dev, "install.sh", "#!/bin/sh\necho installed >> \"$(dirname \"$0\")/../installs\"\n", "Installer")
origin = os.path.join(TMP, "origin.git")
subprocess.run(["git", "clone", "-q", "--bare", dev, origin], check=True, env=ENV)
git(dev, "remote", "add", "origin", origin)
user = os.path.join(TMP, "user", "aiterm")
subprocess.run(["git", "clone", "-q", origin, user], check=True, env=ENV)
installs = os.path.join(TMP, "user", "installs")


def installed():
    try:
        with open(installs) as f:
            return len(f.readlines())
    except OSError:
        return 0


def publish(name, message):
    """A change merged on GitHub: a commit and a merge commit, as a PR's."""
    git(dev, "checkout", "-q", "-b", "change")
    commit(dev, name, message, message)
    git(dev, "checkout", "-q", "main")
    git(dev, "merge", "-q", "--no-ff", "-m", "Merge pull request #1", "change")
    git(dev, "branch", "-q", "-d", "change")
    git(dev, "push", "-q", "origin", "main")


check("a folder that is no clone is left alone", update.update(TMP).reason == "not a git clone")
check("…and has no commit", update.head(TMP) is None)
result = update.update(user)
check("nothing new: up to date", not result.updated and result.reason == update.UP_TO_DATE, result)
check("…runs no install", installed() == 0)

publish("a.txt", "Bigger chat font")
publish("b.txt", "Quieter launch")
before = update.head(user)
result = update.update(user)
check("new commits on main: fast-forwarded", result.updated and update.head(user) == git(dev, "rev-parse", "HEAD"),
      result)
check("…lists the changes, oldest first, without the merge commits",
      result.changes == ["Bigger chat font", "Quieter launch"], result.changes)
check("…runs install.sh once", installed() == 1, installed())
check("…and reports the new commit", result.head == update.head(user) != before, result)

publish("c.txt", "Another change")
with open(os.path.join(user, "a.txt"), "w") as f:
    f.write("my edit")
result = update.update(user)
check("local changes: left alone", not result.updated and result.reason == "has local changes", result)
git(user, "checkout", "-q", "--", "a.txt")

git(user, "checkout", "-q", "-b", "work")
result = update.update(user)
check("on another branch: left alone", not result.updated and result.reason == "on branch work, not main", result)
git(user, "checkout", "-q", "main")

commit(user, "mine.txt", "x", "My own commit")
result = update.update(user)
check("commits of its own: left alone, never merged",
      not result.updated and "not on origin/main" in result.reason and installed() == 1, result)
git(user, "reset", "-q", "--hard", "HEAD~1")

with open(os.path.join(user, "untracked.txt"), "w") as f:
    f.write("new file")
result = update.update(user)
check("untracked files don't stop it", result.updated and result.changes == ["Another change"], result)

git(user, "remote", "set-url", "origin", os.path.join(TMP, "gone.git"))
try:
    update.update(user)
    check("an unreachable origin is an UpdateError", False)
except update.UpdateError as error:
    check("an unreachable origin is an UpdateError", "git fetch" in str(error), error)
git(user, "remote", "set-url", "origin", origin)

publish("d.txt", "Broken installer")
commit(dev, "install.sh", "#!/bin/sh\necho no >&2\nexit 1\n", "Break the installer")
git(dev, "push", "-q", "origin", "main")
try:
    update.update(user)
    check("a failing install.sh is an UpdateError", False)
except update.UpdateError as error:
    check("a failing install.sh is an UpdateError, with what it said", str(error) == "install.sh: no", error)

print(f"{sum(results)} of {len(results)} passed")
sys.exit(0 if all(results) else 1)
