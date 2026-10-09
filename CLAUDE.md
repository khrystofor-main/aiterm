# aiterm — notes for Claude

AI terminal for Ubuntu: the user's shell on the left, the `agy` agent (Antigravity CLI, Gemini on the user's Google subscription) on the right. See README.md for the architecture and roadmap.

## Talking to the user

- The user speaks Russian: reply in Russian. Code, comments, commit messages and docs in the repo are in English.
- The user is learning Linux and heading for an AI Engineer role: explain decisions briefly, prefer clear designs that read well in a portfolio.

## Layout

- `bin/aiterm` — starts a dedicated tmux server (`-L aiterm`, config `config/tmux.conf`) with the shell and the agent panes.
- `bin/aiterm-left` — the agent reads the user's terminal (last command / N / all).
- `bin/aiterm-run` — the agent runs a command in the user's terminal and gets the output.
- `rules/aiterm.md` — agent rules; `install.sh` puts them into `~/.gemini/GEMINI.md` between `<!-- aiterm:begin -->` / `<!-- aiterm:end -->`.
- `bin/aiterm-gtk`, `src/aiterm/` — the native GTK 4 app (v0.2, Python + PyGObject, libadwaita, VTE 3.91). Plan: `docs/v0.2-plan.md`.
- `src/aiterm/commands.py` + `src/aiterm/shell/integration.bash` — the command log (text, output, exit code per command). The app starts bash with `--rcfile` on that file; it loads `/etc/bash.bashrc` and `~/.bashrc` itself.
- `src/aiterm/dbus_api.py` — the terminal API on the session bus (ReadCommands, ReadScreen, RunCommand, Wait); the agent's tools and later the MCP server are its clients. Plan: `docs/v0.3-plan.md`.
- `data/*.desktop.in` — the app's launcher; `install.sh` fills in `@BIN@`.
- `tests/run.sh` — integration tests on a throwaway tmux server, then `tests/gtk_smoke.py` (real window, skipped without a display).

## Working on it

- Run `tests/run.sh` after any change to `bin/` or `src/`. Add a test for new behaviour.
- GTK tests use their own non-unique application ID, so they never reach the user's running Aiterm, and `AITERM_CONFIG_DIR` pointing at a temp folder, so they never touch `~/.config/aiterm`.
- The tools find the tmux server through `AITERM_SOCKET` and the user's pane through `AITERM_LEFT_PANE`; keep both working.
- Never touch the user's live session (`tmux -L aiterm`) or their real `~/.gemini` files from tests.
- Things only the user's machine has: `agy` with their login, the GNOME desktop, their tmux session. Cloud sessions can edit code and run `tests/run.sh` (needs tmux), but checking agy or the GUI needs a thread on the user's computer.
- Don't use the user's subscription token outside the official `agy` client. Integrations go through `agy` itself (MCP, `--output-format stream-json`).
