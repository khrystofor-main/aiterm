# Handling feedback — rules for Claude

Feedback written in Aiterm's Preferences → Feedback becomes a GitHub issue labelled `feedback` in this repository (`src/aiterm/feedback.py`). One long-running Claude thread on the maintainer's computer, "Фидбек", turns each one into a change. These are its rules.

## The loop

1. Run `dev/feedback-watch` in the background. It checks GitHub every 30 seconds through the maintainer's `gh` (an ETag makes most checks free), prints one line per feedback that needs Claude, and exits:
   - `#12 new: Font is too small` — a new issue;
   - `#12 reply: Font is too small — 14 pt please` — the maintainer answered after Claude's last comment.
2. When it exits, handle each issue it printed, then start it again. Exit code 1 means `gh` could not reach GitHub for about ten minutes: say so in the thread, then start it again.
3. On start, run `dev/feedback-watch --once` first: anything that came in while the thread was away is listed at once.

The watcher keeps no state. A feedback "needs Claude" while it is open and its author wrote last; Claude's first comment takes it off the list. So **comment first, then work** (the `status` comment below), or the next watch prints the same issue again.

Only issues and comments by the repository owner count; anyone else's are ignored by the watcher and hidden in the app.

## Each feedback

Read the issue with `gh issue view <n> --comments`. The body has the user's text, links to their screenshots and recordings (on the `feedback-assets` branch: download and look at them) and a folded "Sent from Aiterm" table: versions and the preferences they changed.

- **Clear** ("the font is small", "the button is in the wrong place"): comment how you understood it (`status`), make the change, and take it to merge without asking.
- **Ambiguous, or it touches the architecture**: comment a `question` with numbered options and your recommendation, and wait. The app shows the question as a notification and the user answers from Preferences → Feedback; the answer wakes the watcher.

Each change is its own branch from `main` and its own agent if several come at once (separate worktrees when they don't overlap): change → `tests/run.sh` (GUI tests on Broadway or `tests/headless.sh`, never on the desktop) → PR that says `Closes #<n>` → CI green → merge → `git pull && ./install.sh` in `~/Claude/aiterm`.

## Comments the app understands

Every comment Claude writes starts with a hidden marker. Without one, the app takes the comment for the user's own reply.

| Marker | When | In the app |
|---|---|---|
| `<!-- aiterm-feedback: status -->` | "Understood as …, working on it"; progress | In Progress |
| `<!-- aiterm-feedback: question -->` | A question with numbered options | Question for You, with a notification |
| `<!-- aiterm-feedback: pr -->` | The PR link, once it is open | Change Ready |
| `<!-- aiterm-feedback: done -->` | After the merge and `./install.sh`: what changed | Done, with a notification "restart Aiterm" |

Post them with `gh issue comment <n> --body-file -`. Write them for the user: plain language, what they will see, no internal ids. After the `done` comment, close the issue (the PR's `Closes #<n>` usually has already).
