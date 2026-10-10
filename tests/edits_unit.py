#!/usr/bin/env python3
"""The agent's file edits on their own (src/aiterm/edits.py): planning a
change, its diff, writing it safely, and reading agy's own file tools. No
window, no app. Run: tests/edits_unit.py
"""

import os
import stat
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from aiterm import edits  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(f"  {'ok  ' if ok else 'FAIL'} {name}" + ("" if ok or not detail else f"\n       {detail}"))


def error(function, *args):
    try:
        function(*args)
    except edits.EditError as e:
        return str(e)
    return None


folder = tempfile.mkdtemp(prefix="aiterm-edits-")
path = os.path.join(folder, "app.py")
with open(path, "w") as f:
    f.write("def total(items):\n    totla = 0\n    return totla\n")
os.chmod(path, 0o750)

check("a relative path is taken from the terminal's folder", edits.resolve("app.py", folder) == path)
check("…and ~ is the home folder", edits.resolve("~/x", folder) == os.path.join(os.path.expanduser("~"), "x"))
link = os.path.join(folder, "link.py")
os.symlink(path, link)
check("a symlink resolves to its target, so the link stays a link", edits.resolve(link, None) == path)

before, after = edits.plan_edit(path, "totla", "total", True)
check("plan_edit replaces every match with replace_all", after.count("total") == 3 and "totla" not in after, after)
message = error(edits.plan_edit, path, "totla", "total")
check("…and refuses several matches without it, saying how many", message and "2 times" in message, message)
message = error(edits.plan_edit, path, "    totla = 0", "    total = 0")
check("a unique match is fine", message is None, message)
message = error(edits.plan_edit, path, "nothing like this", "x")
check("text that is not there is an error that says to read the file again",
      message and "Read the file again" in message, message)
message = error(edits.plan_edit, path, "", "x")
check("empty old_text is an error", message and "empty" in message, message)
message = error(edits.plan_edit, os.path.join(folder, "missing.py"), "a", "b")
check("editing a missing file points to write_file", message and "write_file" in message, message)
message = error(edits.plan_edit, folder, "a", "b")
check("a folder is not a file", message and "folder" in message, message)
binary = os.path.join(folder, "blob")
with open(binary, "wb") as f:
    f.write(b"\xff\xfe\x00")
message = error(edits.read, binary)
check("a file that is not UTF-8 is refused", message and "UTF-8" in message, message)
with open(binary, "wb") as f:
    f.write(b"x" * (edits.MAX_BYTES + 1))
message = error(edits.read, binary)
check("…and so is a huge one", message and "larger than" in message, message)

before, after = edits.plan_edit(path, "    totla = 0\n    return totla", "    total = 0\n    return total")
diff = edits.diff(before, after, path)
check("the diff has the change and the lines around it",
      diff[:2] == ["--- a/app.py", "+++ b/app.py"] and " def total(items):" in diff and "+    total = 0" in diff,
      str(diff))
check("…and counts its lines", edits.counts(diff) == (2, 2), str(edits.counts(diff)))
rows = edits.numbered(diff)
check("line numbers come from the hunk header, one side for added and removed lines",
      rows[0] == (None, None, diff[2]) and (1, 1, " def total(items):") in rows
      and (2, None, "-    totla = 0") in rows and (None, 2, "+    total = 0") in rows, str(rows))
two = edits.numbered(edits.diff("".join(f"{i}\n" for i in range(1, 21)),
                                "".join(f"{i}\n" for i in range(1, 21)).replace("2\n", "two\n", 1)
                                .replace("18\n", "eighteen\n"), path))
check("…counting again from each hunk", (None, 18, "+eighteen") in two and (19, 19, " 19") in two, str(two))
new_diff = edits.diff(None, "a\nb\n", "/x/new.txt")
check("a new file's diff is all additions", new_diff[0] == "--- /dev/null" and edits.counts(new_diff) == (2, 0),
      str(new_diff))

edits.write(path, before, after)
with open(path) as f:
    written = f.read()
check("write writes the new text", written == after, written)
check("…keeps the file's mode", stat.S_IMODE(os.stat(path).st_mode) == 0o750, oct(os.stat(path).st_mode))
check("…and leaves no temporary file", sorted(os.listdir(folder)) == ["app.py", "blob", "link.py"],
      str(os.listdir(folder)))
message = error(edits.write, path, before, after)
check("writing over a file that changed since the plan is refused",
      message and "changed after" in message, message)
try:
    edits.write(path, before, after)
    changed = False
except edits.Changed:
    changed = True
check("…as edits.Changed, so the app can tell the model to read it again", changed)
check("…and the file is left as it was", open(path).read() == after)

new = os.path.join(folder, "sub", "dir", "notes.txt")
before, after = edits.plan_write(new, "hello\n")
check("plan_write on a new file has no text before", before is None and after == "hello\n")
edits.write(new, before, after)
check("write creates the file and its folders", open(new).read() == "hello\n")
umask = os.umask(0)
os.umask(umask)
check("…with the usual mode for a new file", stat.S_IMODE(os.stat(new).st_mode) == 0o666 & ~umask,
      oct(os.stat(new).st_mode))
message = error(edits.write, new, None, "again\n")
check("creating a file that appeared meanwhile is refused", message and "changed after" in message, message)
crlf = os.path.join(folder, "win.txt")
with open(crlf, "wb") as f:
    f.write(b"a\r\nb\r\n")
before, after = edits.plan_edit(crlf, "b", "c")
edits.write(crlf, before, after)
check("line endings stay as they were", open(crlf, "rb").read() == b"a\r\nc\r\n", repr(open(crlf, "rb").read()))

# Undo: the change applied, then taken back
edits.undo(crlf, before, after)
check("undo puts the text back", open(crlf, "rb").read() == b"a\r\nb\r\n", repr(open(crlf, "rb").read()))
message = error(edits.undo, crlf, before, after)
check("undoing a file that changed since is refused", message and "not undone" in message, message)
check("…and the file is left as it was", open(crlf, "rb").read() == b"a\r\nb\r\n")
edits.undo(new, None, "hello\n")
check("undoing a new file removes it", not os.path.exists(new))

# agy's own file tools, from their parameters
native = edits.diff_native("replace_file_content", {
    "TargetFile": "/p/app.py", "TargetContent": "a\nb", "ReplacementContent": "a\nc", "StartLine": 7})
check("replace_file_content becomes a diff that says where",
      native == ("/p/app.py", ["--- a/app.py", "+++ b/app.py", "@@ line 7 @@", " a", "-b", "+c"]), str(native))
native = edits.diff_native("multi_replace_file_content", {"TargetFile": "/p/x", "ReplacementChunks": [
    {"TargetContent": "1", "ReplacementContent": "2"}, {"TargetContent": "3", "ReplacementContent": "4"}]})
check("multi_replace_file_content: one diff, one hunk per chunk",
      native and native[1].count("@@") == 2 and native[1].count("--- a/x") == 1, str(native))
native = edits.diff_native("write_to_file", {"TargetFile": "\"/p/new.txt\"", "CodeContent": "\"hi\\n\""})
check("write_to_file, with parameters JSON-encoded as in some of agy's logs",
      native == ("/p/new.txt", ["--- /dev/null", "+++ b/new.txt", "@@ -0,0 +1 @@", "+hi"]), str(native))
check("…and its lines are numbered from where agy says the change starts",
      edits.numbered(native[1])[1:] == [(10, 10, " a"), (11, None, "-b"), (None, 11, "+c")]
      if (native := edits.diff_native("replace_file_content", {"TargetFile": "/p", "TargetContent": "a\nb",
          "ReplacementContent": "a\nc", "StartLine": 10})) else False, str(native))
check("…or not at all when it does not say",
      all(old is None and new is None for old, new, _ in edits.numbered(["@@", "-b", "+c"])))
check("other tools are not edits", edits.diff_native("view_file", {"AbsolutePath": "/etc/hostname"}) is None)

sys.exit(0 if all(results) else 1)
