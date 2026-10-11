"""Updates for an Aiterm that runs from a git clone (no GTK).

install.sh links the commands to the clone it was run from, so a change
merged on GitHub (a feedback the workflow handled, say) reaches the user when
the clone pulls it. update() does that, and only when it is safe: the clone
is on `main`, has no changes of its own, and is only behind origin/main. Then
it fast-forwards and runs install.sh again (new commands, the launcher, the
agy plugin). A clone someone works in, a .deb install or no network is left
as it is, with the reason. The running app keeps the code it loaded: the
update takes effect on restart, and the app says so (updater.py).

Tests use throwaway repositories (tests/update_unit.py).
"""

import os
import subprocess
from dataclasses import dataclass

SRC_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BRANCH = "main"
REMOTE = "origin"
UP_TO_DATE = "up to date"

# Never ask for a password or a passphrase: there is nobody to type it
GIT_ENV = {"GIT_TERMINAL_PROMPT": "0", "GIT_SSH_COMMAND": "ssh -o BatchMode=yes", "LC_ALL": "C"}


class UpdateError(Exception):
    pass


@dataclass
class Result:
    updated: bool
    reason: str  # what happened, or why nothing did
    changes: list  # the subjects of the commits that came in, oldest first
    head: str | None = None  # the clone's commit afterwards


def git(root, *args, timeout=120):
    try:
        done = subprocess.run(["git", "-C", root, *args], capture_output=True, text=True, timeout=timeout,
                              env={**os.environ, **GIT_ENV})
    except FileNotFoundError:
        raise UpdateError("git is not installed") from None
    except subprocess.TimeoutExpired:
        raise UpdateError(f"git {args[0]} took too long") from None
    if done.returncode != 0:
        raise UpdateError(f"git {args[0]}: {(done.stderr or done.stdout).strip() or 'failed'}")
    return done.stdout.strip()


def is_clone(root=SRC_ROOT):
    """A git clone of its own (not the .deb's /usr/lib/aiterm)."""
    return os.path.exists(os.path.join(root, ".git"))


def head(root=SRC_ROOT):
    """The commit the clone is at; None when it is not one."""
    try:
        return git(root, "rev-parse", "HEAD", timeout=10) if is_clone(root) else None
    except UpdateError:
        return None


def update(root=SRC_ROOT, install=True):
    """Brings the clone at `root` up to origin/main if it is safe; a Result.
    UpdateError when git or install.sh fails."""
    result = _update(root, install)
    result.head = head(root)
    return result


def _update(root, install):
    if not is_clone(root):
        return Result(False, "not a git clone", [])
    branch = git(root, "rev-parse", "--abbrev-ref", "HEAD")
    if branch != BRANCH:
        return Result(False, f"on branch {branch}, not {BRANCH}", [])
    if git(root, "status", "--porcelain", "--untracked-files=no"):
        return Result(False, "has local changes", [])
    git(root, "fetch", "--quiet", REMOTE, BRANCH)
    upstream = f"{REMOTE}/{BRANCH}"
    if int(git(root, "rev-list", "--count", f"{upstream}..HEAD")):
        return Result(False, f"has commits that are not on {upstream}", [])
    if not int(git(root, "rev-list", "--count", f"HEAD..{upstream}")):
        return Result(False, UP_TO_DATE, [])
    # The changes themselves, not the "Merge pull request" commits around them
    changes = git(root, "log", "--reverse", "--no-merges", "--format=%s", f"HEAD..{upstream}").splitlines()
    git(root, "merge", "--ff-only", "--quiet", upstream)
    if install:
        script = os.path.join(root, "install.sh")
        try:
            done = subprocess.run([script], cwd=root, capture_output=True, text=True, timeout=300)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise UpdateError(f"install.sh: {error}") from None
        if done.returncode != 0:
            raise UpdateError(f"install.sh: {(done.stderr or done.stdout).strip() or 'failed'}")
    return Result(True, f"updated to {git(root, 'rev-parse', '--short', 'HEAD')}", changes)
