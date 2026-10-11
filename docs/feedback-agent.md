# Handling feedback — rules for Claude

Feedback written in Aiterm's Preferences → Feedback becomes a GitHub issue labelled `feedback` in this repository (`src/aiterm/feedback.py`). Claude turns each one into a change, in one of two places, picked by the repository variable `FEEDBACK_AGENT`:

- **`github`: on GitHub Actions.** `.github/workflows/feedback.yml` runs Claude ([claude-code-action](https://github.com/anthropics/claude-code-action), on the maintainer's Claude subscription) for every new feedback and every reply the owner writes in one. See "On GitHub" below.
- **Not set: on the maintainer's computer.** One long-running Claude thread, "Фидбек", watches GitHub with `dev/feedback-watch`. See "On the maintainer's computer" below. Once the variable is `github`, the watcher says so and exits with code 3, so the two never take the same issue.

The rules for each feedback are the same in both places. Only issues and comments by the repository owner count (the repository is public); anyone else's are ignored by both and hidden in the app.

## Each feedback

Read the issue with `gh issue view <n> --json title,body,comments` (`--comments` fails while GitHub retires classic Projects). The body has the user's text, links to their screenshots and recordings (on the `feedback-assets` branch: download and look at them) and a folded "Sent from Aiterm" table: versions and the preferences they changed. The user's replies can carry files too, linked the same way below their text (or alone): look at those as well.

- **Clear** ("the font is small", "the button is in the wrong place"): comment how you understood it (`status`), make the change, and take it to merge without asking.
- **Ambiguous, or it touches the architecture**: comment a `question` with numbered options and your recommendation, and wait. The app shows the question as a notification and the user answers from Preferences → Feedback; the answer wakes Claude again.

Each change is its own branch from `main` (`feedback/<n>-<short-name>`) and its own PR that says `Closes #<n>`: change → tests → PR → CI green → merge → `done`. How to run the tests and what follows the merge depends on the place (below). The maintainer's Aiterm pulls merged changes by itself (`src/aiterm/update.py`) and asks for a restart.

### Follow-ups

The conversation stays open in the app after `done`: when the user writes in a finished feedback, the app reopens the issue, then posts the reply, which wakes Claude (a new run on GitHub, `#n reply: …` from the watcher). A reply on a reopened feedback is a **follow-up**: handle it like a new change, in the same issue. Comment a `status` first (how you understood the follow-up), then a new branch from `main`, a new PR that says `Closes #<n>`, merge, and a new `done` comment; the issue closes again. A reply that comes while the feedback's PR is still open goes into that PR instead (its branch). Ask a `question` instead when the follow-up is ambiguous, as with any feedback.

## Comments the app understands

Every comment Claude writes starts with a hidden marker. Without one, the app takes the comment for the user's own reply.

| Marker | When | In the app |
|---|---|---|
| `<!-- aiterm-feedback: status -->` | "Understood as …, working on it"; progress | In Progress |
| `<!-- aiterm-feedback: question -->` | A question with numbered options | Question for You, with a notification |
| `<!-- aiterm-feedback: pr -->` | The PR link, once it is open | Change Ready |
| `<!-- aiterm-feedback: done -->` | After the merge: what changed | Done, with a notification; Aiterm then updates itself and asks for a restart |

Post them with `gh issue comment <n> --body-file -`. Write them for the user, in the language they wrote in: plain language, what they will see, no internal ids. After the `done` comment, close the issue (the PR's `Closes #<n>` usually has already). The user can still answer there: that reopens it (Follow-ups above).

## On GitHub

The workflow starts one run per event (a new feedback, or a reply of the owner's) on a fresh `ubuntu-latest` runner with this repository checked out. You act as the Claude GitHub App: `gh` and `git push` work (`GH_TOKEN` is set), and your comments come from `claude[bot]`; the app counts them as Claude's when they carry a marker. Runs for the same issue wait for each other; different issues run in parallel.

1. Read the issue with `gh issue view <n> --json title,body,comments,state`, and its files (download the links on the `feedback-assets` branch with `curl -L` and look at them). Work out what is new since Claude's last comment. If Claude's comment is already the last one, stop.
2. Comment a `status` (or a `question`, and stop there; the answer starts a new run).
3. Branch from `origin/main`, or check out the open PR's branch for a follow-up (`gh pr list --state open --search "Closes #<n>"`). Make the change, with a test for new behaviour.
4. Test with `dev/ci-tests`: the runner's Ubuntu is older than Aiterm needs, so it runs `tests/run.sh` in docker on Ubuntu 26.04, GTK under Xvfb, as CI does (a few minutes; the Bash tool's 10-minute limit is enough).
5. Commit, push, `gh pr create` (body: what changed and `Closes #<n>`), then comment `pr` with its link.
6. Wait for CI: `gh pr checks <pr> --watch` (again if it is still running). Red: `gh run view <run> --log-failed`, fix, test, push, wait again. After three red rounds in a row, comment a `status` that says what fails, and stop: the maintainer takes it from there.
7. `gh pr merge <pr> --merge --delete-branch`. When `main` moved on and the PR no longer merges, merge `origin/main` into the branch, test, push and wait for CI again.
8. Comment `done` and close the issue if the merge did not (`gh issue close <n>`). There is no install step: the maintainer's Aiterm updates itself.

The Claude GitHub App cannot change `.github/workflows/`. When a feedback needs that, do the rest, and say in the `done` comment (or a `question`) exactly what the maintainer has to change there.

### Setting it up (the maintainer, once)

1. `claude setup-token` on your computer, then `gh secret set CLAUDE_CODE_OAUTH_TOKEN` and paste the token.
2. The [Claude GitHub App](https://github.com/apps/claude) installed on this repository.
3. `gh variable set FEEDBACK_AGENT --body github` switches feedback to GitHub; `gh variable delete FEEDBACK_AGENT` switches it back to the thread on the computer.

## On the maintainer's computer

The thread "Фидбек" handles feedback while `FEEDBACK_AGENT` is not `github`.

### Never on the desktop

`tests/run.sh` opens real windows when a display is set, and on the maintainer's computer it always is. Every run, by the thread or by a subagent, goes off-screen: start `gtk4-broadwayd :<n> &` and run `GDK_BACKEND=broadway BROADWAY_DISPLAY=:<n> tests/run.sh`, with a display number of its own, then stop that broadwayd; or use `tests/headless.sh`. Put this rule in every subagent's brief.

### The loop

1. Run `dev/feedback-watch` in the background. It checks GitHub every 30 seconds through the maintainer's `gh` (an ETag makes most checks free), prints one line per feedback that needs Claude, and exits:
   - `#12 new: Font is too small` — a new issue;
   - `#12 reply: Font is too small — 14 pt please` — the maintainer answered after Claude's last comment, or wrote more in a finished feedback (see "Follow-ups" above).
2. When it exits, handle each issue it printed, then start it again. Exit code 1 means `gh` could not reach GitHub for about ten minutes: say so in the thread, then start it again. Exit code 3 means feedback is handled on GitHub now: say so in the thread and stop watching.
3. On start, run `dev/feedback-watch --once` first: anything that came in while the thread was away is listed at once.

The watcher keeps no state. A feedback "needs Claude" while it is open and its author wrote last; Claude's first comment takes it off the list. So **comment first, then work** (the `status` comment), or the next watch prints the same issue again.

### Each change here

Run `tests/run.sh` with the GUI tests on Broadway or `tests/headless.sh`, never on the desktop (above), or `dev/ci-tests` in docker. After the merge, `git pull && ./install.sh` in `~/Claude/aiterm` (the app would pull it within the hour anyway), then the `done` comment.

### Several at once

Feedback is handled in parallel. When the watcher prints several issues, or a new one comes while another is in progress:

- **One subagent per issue, each in its own git worktree** (the Agent tool's `isolation: "worktree"`), on its own branch and PR. The thread itself only comments, starts agents and merges, so its context stays small.
- **Its own Broadway display per agent** for the GUI tests (`:7`, `:8`, `:9` …), so their windows never mix and none reach the desktop.
- **Issues that touch the same code run one after another**, not in parallel: the second starts from `main` after the first is merged.
- **The tail is serial.** Merge one PR at a time; before merging the next, rebase it on the new `main` and wait for CI again. Run `git pull && ./install.sh` once after the last merge, then post the `done` comments.
