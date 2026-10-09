# aiterm — notes for Claude

AI terminal for Ubuntu: a GTK app with the user's shell in tabs and the `agy` agent (Antigravity CLI, Gemini on the user's Google subscription) in a panel on the right. See README.md for the architecture and roadmap.

## Talking to the user

- The user speaks Russian: reply in Russian. Code, comments, commit messages and docs in the repo are in English.
- The user is learning Linux and heading for an AI Engineer role: explain decisions briefly, prefer clear designs that read well in a portfolio.

## Layout

- `bin/aiterm`, `src/aiterm/` — the app (Python + PyGObject, GTK 4, libadwaita, VTE 3.91), run from the repo. Plans: `docs/v0.2-plan.md` (terminal), `docs/v0.3-plan.md` (agent).
- `bin/aiterm-left` — the agent reads the user's terminal (last command / N / all).
- `bin/aiterm-run` — the agent runs a command in the user's terminal and gets the output and exit code.
- Both tools run `src/aiterm/tools.py`, a D-Bus client; `src/aiterm/agent_panel.py` starts agy with `AITERM_WINDOW`, `AITERM_BUS_NAME`, `AITERM_OBJECT_PATH`.
- `rules/aiterm.md` — agent rules; `install.sh` puts them into `~/.gemini/GEMINI.md` between `<!-- aiterm:begin -->` / `<!-- aiterm:end -->`.
- `src/aiterm/commands.py` + `src/aiterm/shell/integration.bash` — the command log (text, output, exit code per command). The app starts bash with `--rcfile` on that file; it loads `/etc/bash.bashrc` and `~/.bashrc` itself.
- `src/aiterm/dbus_api.py` — the terminal API on the session bus (ReadCommands, ReadScreen, RunCommand, Wait); the agent's tools and later the MCP server are its clients. Plan: `docs/v0.3-plan.md`.
- `data/*.desktop.in` — the app's launcher; `install.sh` fills in `@BIN@`.
- `tests/run.sh` — the tools' error handling, then `tests/gtk_smoke.py` (real window and bash, end to end; skipped without a display).

## Working on it

- Run `tests/run.sh` after any change to `bin/` or `src/`. Add a test for new behaviour.
- GTK tests use their own non-unique application ID, so they never reach the user's running Aiterm; `AITERM_CONFIG_DIR` pointing at a temp folder, so they never touch `~/.config/aiterm`; and `AITERM_AGENT` set to a plain bash, so they never start agy.
- The smoke test opens real windows on the user's desktop; typing on the keyboard while it runs can land in them and fail a check.
- Never touch the user's real `~/.gemini` files from tests.
- Things only the user's machine has: `agy` with their login and the GNOME desktop. Cloud sessions can edit code and run the tool checks in `tests/run.sh` (the GTK part is skipped without a display), but checking agy or the GUI needs a thread on the user's computer.
- Don't use the user's subscription token outside the official `agy` client. Integrations go through `agy` itself (MCP, `--output-format stream-json`).
