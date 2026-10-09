# aiterm

**An AI terminal for Ubuntu: your shell on the left, an AI agent on the right that sees your terminal and runs commands in it — in plain sight.**

<!-- TODO: demo GIF -->

Ask *"why did this fail?"* and the agent reads the command you just ran. Ask *"install docker"* and you watch the commands appear in your own shell, one by one. When something needs `sudo`, the agent stops and you type the password yourself.

The agent is [Antigravity CLI](https://antigravity.google/docs/cli/install) (`agy`) running on your own Google subscription. aiterm is the glue that gives it eyes and hands in your terminal.

## How it works

```
┌───────────────────────── Aiterm (GTK 4 + VTE) ──────────────────────────┐
│  your shell, in tabs                     │  agent panel: agy (Gemini)   │
│  bash + shell integration → command log  │                              │
└──────────────────▲───────────────────────┴───────────────┬──────────────┘
                   │ D-Bus: ReadCommands / ReadScreen /    │ shell tool calls
                   │        RunCommand / Wait              ▼
                   │                         ┌───────────────────────────┐
                   └─────────────────────────│ aiterm-left  — read       │
                                             │ aiterm-run   — execute    │
                                             └───────────────────────────┘
                                               + rules in ~/.gemini/GEMINI.md
```

- **The app** is a GNOME terminal written in Python with GTK 4, libadwaita and VTE (the terminal engine of GNOME Terminal and Ptyxis). The agent runs in a panel on the right, in its own terminal.
- **The command log.** Bash starts with a small integration file (`src/aiterm/shell/integration.bash`, which loads your `~/.bashrc` first). It marks where each prompt ends and sends each command's text, and Ubuntu's own VTE integration reports when a command starts, ends and with which exit code. From that the app keeps an exact list of commands: text, output, exit code, duration. Nothing guesses command boundaries from the prompt text.
- **The terminal API.** The app exports `io.github.khrystofor_main.Aiterm.Terminal` on the session bus (`src/aiterm/dbus_api.py`). Only the same user can reach it, and there is no port or socket to manage.
- **`aiterm-left`** gives the agent *just enough* context: by default only your last command, its output and exit code. It widens to the last *N* commands or the whole scrollback when needed, so the agent doesn't burn context on old noise.
- **`aiterm-run`** types a command into your shell, waits for the command log to report it and returns the output and exit code. It refuses to type while you are typing or a program is running, handles timeouts and commands that wait for input, and trims huge outputs.
- **Rules** (`rules/aiterm.md`) teach the agent to use these tools instead of its own hidden shell, how to widen context, and what not to touch.

## Safety model

| Risk | What aiterm does |
|---|---|
| Agent types over your half-written command | `aiterm-run` checks that the prompt line is empty and no program is running; otherwise it refuses (exit code 3) |
| Agent runs `sudo` behind your back | Before any agent command with `sudo`, cached credentials are dropped (`sudo -K`), so **every** such command needs your password, typed in your terminal |
| Agent hangs on `less`, `vim`, `[Y/n]` | Timeouts with partial output; the rules ban pagers and full-screen programs |
| Agent says "done" after a failure | Every result carries the command's exit code, and the rules make the agent check it |
| Agent edits your configs "to help" | Rules forbid changing system/user settings without explicit consent |
| You don't see what the agent does | Every shell command runs in your terminal, visible and in your history |

The rules are instructions to a model, not hard guarantees; the `sudo` guard and the busy-terminal checks are enforced in code.

## Install

Requirements: Ubuntu (or another Linux with GNOME and bash), Python 3 with GTK 4, libadwaita and VTE for GTK 4, `jq`, and [`agy`](https://antigravity.google/docs/cli/install) signed in.

```bash
sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-vte-3.91 jq
git clone https://github.com/khrystofor-main/aiterm.git
cd aiterm
./install.sh
```

The installer (no sudo) links the commands into `~/.local/bin`, adds **Aiterm** to the applications menu, adds the agent rules to `~/.gemini/GEMINI.md` between `aiterm` markers, and allows `agy` to run `aiterm-left` / `aiterm-run`. Run it again after `git pull` to update; `./uninstall.sh` removes everything it added. There is no build step: the app runs from the repository.

## Usage

Open **Aiterm** from the menu (or run `aiterm`). Work in your shell; press **Alt+Enter** to talk to the agent and again to get back.

| Keys | Action |
|---|---|
| `Alt+Enter` | Switch between your shell and the agent (opens the panel) |
| `Ctrl+Shift+T` / `Ctrl+Shift+W` | New tab (in the same folder) / close tab |
| `Ctrl+PgUp` / `Ctrl+PgDn`, `Alt+1…9` | Switch tabs |
| `Ctrl+Shift+N` | New window |
| `Ctrl+Shift+C` / `Ctrl+Shift+V` | Copy / paste |
| `Ctrl+Shift+F` | Find in the scrollback |
| `Ctrl+Shift+Up` / `Ctrl+Shift+Down` | Jump to the previous / next prompt |
| `Ctrl+plus` / `Ctrl+minus` / `Ctrl+0` | Zoom |
| `Ctrl+,` | Preferences: palette, font, scrollback, cursor, notifications |

Also: Ctrl+click opens links, right-click on a command's output → **Copy Output**, dropped files are typed as quoted paths, and a desktop notification tells you when a long command finishes in a background tab. Preferences are saved in `~/.config/aiterm/settings.json`.

Tip: `agy` asks before every `aiterm-run 'command'`, because its allow rules only match simple commands. To make it fully hands-free, set **Tool Permission → always-proceed** in `agy`'s `/config`; the `sudo` guard keeps working.

## Tests

```bash
tests/run.sh
```

The tools' argument and error handling are checked on their own. Then `tests/gtk_smoke.py` opens the app with a real bash, under its own application ID, a temporary settings folder, and a plain bash standing in for the agent. It drives everything through the same paths a user and the agent use: commands and the command log, tabs, windows, clipboard, search, links, preferences, safe closing, notifications, the D-Bus API, and `aiterm-run` typed in the agent panel running in the user's terminal. The smoke test needs a graphical session and is skipped without one.

## Limitations

- The command log needs bash. Other shells work as plain terminals, and `aiterm-left` falls back to the last 200 lines of the screen.
- One agent per window; it works on the window's selected tab.
- Built and tested on Ubuntu 26.04 with GNOME / Wayland.

## Roadmap

- [x] **v0.1** — tmux-based prototype: read and run tools, rules, sudo guard, installer, tests
- [x] **v0.2** — native GTK4 + VTE terminal: tabs, search, preferences, command log, an empty agent panel ([plan](docs/v0.2-plan.md))
- [x] **v0.3** — the agent (agy) in the panel; the tools talk to the app over D-Bus instead of tmux ([plan](docs/v0.3-plan.md))
- [ ] **v0.4** — own MCP server: terminal tools exposed to the agent over MCP instead of shell scripts
- [ ] **v0.5** — evals: a suite of broken-system scenarios to measure how well the agent diagnoses and fixes them

## License

[MIT](LICENSE)
