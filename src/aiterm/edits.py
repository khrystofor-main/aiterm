"""The agent's file edits: planning a change, its diff, and writing it.

No GTK here, so the MCP server, the app and the tests share it. The MCP
server plans an edit (edit_file / write_file): it reads the file and works
out the text before and after. The app shows the diff and writes the file
only when the user clicks Apply (dbus_api.py), after checking that the file
still holds the text the diff was made from.

agy's own file tools write without asking; diff_native() turns their
parameters into a diff, so the chat can show what they changed.
"""

import difflib
import json
import os
import tempfile

MAX_BYTES = 1_000_000  # larger files are not something to edit through a diff
CONTEXT_LINES = 3


class EditError(Exception):
    """An edit that cannot be made, in words the model can act on."""


class Changed(EditError):
    """The file is not what the edit was planned from any more."""


def resolve(path, folder):
    """An absolute path: `~` expanded, relative paths taken from `folder`
    (the terminal's), symlinks followed so the link stays a link."""
    path = os.path.expanduser(path)
    if not os.path.isabs(path):
        path = os.path.join(folder or os.path.expanduser("~"), path)
    return os.path.realpath(path)


def read(path):
    """The file's text, or None when there is no such file."""
    if os.path.isdir(path):
        raise EditError(f"{path} is a folder, not a file.")
    try:
        with open(path, "rb") as f:
            data = f.read(MAX_BYTES + 1)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise EditError(f"Cannot read {path}: {error.strerror}.") from None
    if len(data) > MAX_BYTES:
        raise EditError(f"{path} is larger than {MAX_BYTES // 1000} kB; change it with a command instead.")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise EditError(f"{path} is not a UTF-8 text file.") from None


def plan_edit(path, old_text, new_text, replace_all=False):
    """(before, after) for replacing old_text with new_text in the file."""
    before = read(path)
    if before is None:
        raise EditError(f"There is no file {path}. Create it with write_file.")
    if not old_text:
        raise EditError("`old_text` is empty: give the exact text to replace (use write_file for a new file).")
    count = before.count(old_text)
    if count == 0:
        raise EditError(f"`old_text` is not in {path}. Read the file again and copy the text exactly, "
                        "with its indentation and line breaks.")
    if count > 1 and not replace_all:
        raise EditError(f"`old_text` is in {path} {count} times. Add lines around it so it matches once, "
                        "or set replace_all to change every one.")
    return before, before.replace(old_text, new_text)


def plan_write(path, content):
    """(before, after) for writing content to the file; before is None for a
    new file."""
    return read(path), content


def diff(before, after, path):
    """The unified diff of the change, as lines without line endings."""
    name = os.path.basename(path)
    return list(difflib.unified_diff(
        (before or "").splitlines(), after.splitlines(),
        "/dev/null" if before is None else f"a/{name}", f"b/{name}", n=CONTEXT_LINES, lineterm=""))


def counts(lines):
    """(lines added, lines removed) in a diff from diff()."""
    added = sum(1 for line in lines if line.startswith("+") and not line.startswith("+++"))
    removed = sum(1 for line in lines if line.startswith("-") and not line.startswith("---"))
    return added, removed


def write(path, before, after):
    """Writes `after` if the file still holds `before` (None: still absent).
    Atomic: a temporary file next to it, renamed over it, with its mode."""
    current = read(path)
    if current != before:
        raise Changed(f"{path} changed after the edit was planned; nothing was written. "
                        "Read it again and redo the edit.")
    folder = os.path.dirname(path)
    try:
        os.makedirs(folder, exist_ok=True)
        mode = os.stat(path).st_mode & 0o7777 if before is not None else None
        fd, tmp = tempfile.mkstemp(dir=folder, prefix=f".{os.path.basename(path)}.")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
                f.write(after)
            if mode is None:
                umask = os.umask(0)
                os.umask(umask)
                mode = 0o666 & ~umask
            os.chmod(tmp, mode)
            os.replace(tmp, path)
        except BaseException:
            os.unlink(tmp)
            raise
    except OSError as error:
        raise EditError(f"Cannot write {path}: {error.strerror}.") from None


# agy's own file tools: name -> how to read their parameters

def diff_native(name, params):
    """(path, diff lines) for agy's own file tools, from their parameters
    alone: the changed text without the lines around it. None for other tools."""
    params = _decoded(params)
    path = params.get("TargetFile")
    if not isinstance(path, str):
        return None
    if name == "write_to_file":
        content = params.get("CodeContent")
        return (path, diff(None, content, path)) if isinstance(content, str) else None
    if name == "replace_file_content":
        chunks = [params]
    elif name == "multi_replace_file_content":
        chunks = params.get("ReplacementChunks")
        chunks = chunks if isinstance(chunks, list) else []
    else:
        return None
    lines = []
    for chunk in chunks:
        if not isinstance(chunk, dict):
            continue
        old, new = chunk.get("TargetContent"), chunk.get("ReplacementContent")
        if isinstance(old, str) and isinstance(new, str):
            # Line numbers within the chunk mean nothing: the hunk header
            # says where in the file it is, when agy says so
            start = chunk.get("StartLine")
            header = f"@@ line {start} @@" if isinstance(start, int) else "@@"
            lines += [header if line.startswith("@@") else line
                      for line in diff(old, new, path)[2 if lines else 0:]]  # one header for all chunks
    return (path, lines) if lines else None


def _decoded(params):
    """Some of agy's logs carry every parameter JSON-encoded ("\"/path\"",
    "28"): take either form, telling them apart by the path."""
    target = params.get("TargetFile")
    if not (isinstance(target, str) and target.startswith('"')):
        return params
    decoded = {}
    for key, value in params.items():
        try:
            decoded[key] = json.loads(value) if isinstance(value, str) else value
        except ValueError:
            decoded[key] = value
    return decoded
