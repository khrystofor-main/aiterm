# aiterm — notes for Claude

AI terminal for Ubuntu: a GTK app with the user's shell in tabs and the `agy` agent (Antigravity CLI, Gemini on the user's Google subscription) in a panel on the right. See README.md for the architecture and roadmap.

## Talking to the user

- The user speaks Russian: reply in Russian. Code, comments, commit messages and docs in the repo are in English.
- The user is learning Linux and heading for an AI Engineer role: explain decisions briefly, prefer clear designs that read well in a portfolio.

## Layout

- `bin/aiterm`, `src/aiterm/` — the app (Python + PyGObject, GTK 4, libadwaita, VTE 3.91), run from the repo. Plans: `docs/v0.2-plan.md` … `docs/v1.0-plan.md`, one per release.
- `bin/aiterm-left` / `bin/aiterm-run` — the terminal API on the command line (read; run a command and get the output and exit code). The agent uses the MCP server instead; these stay for scripts and debugging.
- Both tools run `src/aiterm/tools.py` on top of `src/aiterm/client.py`, the D-Bus client; `src/aiterm/agent_panel.py` starts agy with `AITERM_WINDOW`, `AITERM_BUS_NAME`, `AITERM_OBJECT_PATH`.
- `bin/aiterm-mcp` — the MCP server (`src/aiterm/mcp_server.py`, stdio, no SDK): `read_terminal`, `run_command`, `wait_for_command`, `get_cwd`, through the same client. agy starts it and it inherits the `AITERM_*` variables. Plan: `docs/v0.4-plan.md`.
- `agy-plugin/` — the agy plugin: `mcp_config.json` starts `aiterm-mcp` (agy names the server `aiterm_terminal`), `rules/AGENTS.md` holds the agent rules. `install.sh` links it to `~/.gemini/config/plugins/aiterm` and removes the old rules block from `~/.gemini/GEMINI.md`.
- `src/aiterm/commands.py` + `src/aiterm/shell/integration.bash` — the command log (text, output, exit code per command). The app starts bash with `--rcfile` on that file; it loads `/etc/bash.bashrc` and `~/.bashrc` itself.
- `src/aiterm/chat.py` + `src/aiterm/chat_view.py` — the Chat view of the agent panel (Preferences → Agent → View): agy with `--input-format/--output-format stream-json`, one process per conversation, resumed with `--conversation` after Stop. Tests use `tests/fake_agy.py` through `AITERM_CHAT_AGENT`. Plan: `docs/v0.5-plan.md`.
- `src/aiterm/approval.py` — the bar above the terminal where the user approves each agent command (Run / Don't Run); `RunCommand` in `dbus_api.py` waits for it while the `approve_agent_commands` preference is on.
- `src/aiterm/dbus_api.py` — the terminal API on the session bus (ReadCommands, ReadScreen, RunCommand, Wait); the agent's tools and later the MCP server are its clients. Plan: `docs/v0.3-plan.md`.
- `evals/run.py` + `evals/scenarios/<name>/` (`scenario.json`, `setup.sh`, `check.sh`, `solution.sh`, `answer.txt`) — the evals: a real Aiterm window with a bwrap-sandboxed shell, one `agy -p` run with its own temp HOME (login files linked, aiterm plugin, only `mcp(aiterm_terminal/*)` allowed), then `check.sh`. Results in `evals/results/latest.json` and the README (`--update-readme`). Plan: `docs/v0.6-plan.md`. They use the user's agy quota: run them only when asked or when measuring a change.
- `bin/aiterm-agent-setup` — connects agy to aiterm for the user (plugin link, permissions, old GEMINI.md block out; `--check`, `--remove`). `install.sh` runs it; with the .deb the agent panel offers it (Connect / Not Now).
- `packaging/build-deb.sh` — builds `dist/aiterm_<version>_all.deb` (the app under `/usr/lib/aiterm`, commands in `/usr/bin`); `packaging/demo.py` records `docs/demo.gif` with the real agy.
- `data/*.desktop.in` — the app's launcher; `install.sh` fills in `@BIN@`.
- `tests/run.sh` — the tools' error handling, the installer in a throwaway HOME, the MCP protocol, the eval scenarios' checks (`tests/eval_scenarios.py`, no agent) (`tests/mcp_protocol.py`), then `tests/gtk_smoke.py` (real window and bash, end to end; skipped without a display).

## Working on it

- Run `tests/run.sh` after any change to `bin/` or `src/`. Add a test for new behaviour.
- GTK tests use their own non-unique application ID, so they never reach the user's running Aiterm; `AITERM_CONFIG_DIR` pointing at a temp folder, so they never touch `~/.config/aiterm`; and `AITERM_AGENT` set to a plain bash, so they never start agy.
- The smoke test opens real windows on the user's desktop; typing on the keyboard while it runs can land in them and fail a check. While the user works, run it off-screen instead: `gtk4-broadwayd :7 &` then `GDK_BACKEND=broadway BROADWAY_DISPLAY=:7 tests/run.sh`.
- Never touch the user's real `~/.gemini` files from tests.
- Things only the user's machine has: `agy` with their login and the GNOME desktop. Cloud sessions can edit code and run the tool checks in `tests/run.sh` (the GTK part is skipped without a display), but checking agy or the GUI needs a thread on the user's computer.
- Don't use the user's subscription token outside the official `agy` client. Integrations go through `agy` itself (MCP, `--output-format stream-json`).
