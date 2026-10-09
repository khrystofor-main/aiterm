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
                   │ D-Bus: ReadCommands / ReadScreen /    │ MCP over stdio
                   │        RunCommand / Wait              ▼
                   │                         ┌───────────────────────────┐
                   └─────────────────────────│ aiterm-mcp: read_terminal,│
                                             │ run_command, get_cwd, …   │
                                             └───────────────────────────┘
                                         agy plugin: MCP server + rules
```

- **The app** is a GNOME terminal written in Python with GTK 4, libadwaita and VTE (the terminal engine of GNOME Terminal and Ptyxis). The agent runs in a panel on the right, in its own terminal.
- **The command log.** Bash starts with a small integration file (`src/aiterm/shell/integration.bash`, which loads your `~/.bashrc` first). It marks where each prompt ends and sends each command's text, and Ubuntu's own VTE integration reports when a command starts, ends and with which exit code. From that the app keeps an exact list of commands: text, output, exit code, duration. Nothing guesses command boundaries from the prompt text, so a custom `PS1` does not matter.
- **The terminal API.** The app exports `io.github.khrystofor_main.Aiterm.Terminal` on the session bus (`src/aiterm/dbus_api.py`). Only the same user can reach it, and there is no port or socket to manage.
- **The MCP server.** `aiterm-mcp` (`src/aiterm/mcp_server.py`) gives the agent the terminal as tools over the [Model Context Protocol](https://modelcontextprotocol.io): agy starts it as a child process and talks JSON-RPC over stdin/stdout. It is a small hand-written server with no SDK, and a client of the D-Bus API like any other.

  | Tool | What it does |
  |---|---|
  | `read_terminal` | Your last command, its output and exit code, and the folder. Widens to the last *N* commands or the whole scrollback, so the agent doesn't burn context on old noise |
  | `run_command` | Types a command into your shell, waits for the command log to report it, returns the output, exit code and folder. Refuses to type while you are typing or a program is running, handles timeouts and commands that wait for input, trims huge outputs |
  | `wait_for_command` | Waits for the command that is already running (after a timeout, or one you started) |
  | `get_cwd` | The folder of your terminal |

  How to use each tool well (check the exit code, no pagers, you type `sudo` passwords) is in the tool descriptions, which the model reads with the tools.
- **The agy plugin** (`agy-plugin/`) bundles the server with a few rules (`rules/AGENTS.md`): run shell commands with `run_command`, not the agent's own hidden shell, and don't change your settings unasked. The rules are on only while the plugin is, and your own `~/.gemini/GEMINI.md` stays yours.
- **`aiterm-left` / `aiterm-run`** are the same API on the command line, for scripts and debugging.

## Safety model

| Risk | What aiterm does |
|---|---|
| Agent runs a command you didn't want | Each command waits for **Run** in a bar above your terminal (**Don't Run** declines). The check is in the app, so no prompt or agent setting can skip it; turn it off in Preferences → Agent for hands-free use |
| Agent types over your half-written command | `run_command` checks that the prompt line is empty and no program is running; otherwise it refuses and tells the agent why |
| Agent runs `sudo` behind your back | Before any agent command with `sudo`, cached credentials are dropped (`sudo -K`), so **every** such command needs your password, typed in your terminal |
| Agent hangs on `less`, `vim`, `[Y/n]` | Timeouts with partial output; the tool descriptions ban pagers and full-screen programs |
| Agent says "done" after a failure | Every result carries the command's exit code, and the tool description makes the agent check it |
| Agent edits your configs "to help" | The plugin's rules forbid changing system/user settings without explicit consent |
| You don't see what the agent does | Every shell command runs in your terminal, visible and in your history |

Rules and tool descriptions are instructions to a model, not hard guarantees; the approval bar, the `sudo` guard and the busy-terminal checks are enforced in code.

## Install

Requirements: Ubuntu (or another Linux with GNOME and bash), Python 3 with GTK 4, libadwaita and VTE for GTK 4, `jq`, and [`agy`](https://antigravity.google/docs/cli/install) signed in.

```bash
sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-vte-3.91 jq
git clone https://github.com/khrystofor-main/aiterm.git
cd aiterm
./install.sh
```

The installer (no sudo) links the commands into `~/.local/bin`, adds **Aiterm** to the applications menu, links the agy plugin into `~/.gemini/config/plugins/aiterm`, and lets `agy` call the terminal tools without its own prompt (the app asks before each command instead). Run it again after `git pull` to update (it also moves the rules of older versions out of `~/.gemini/GEMINI.md`); `./uninstall.sh` removes everything it added. There is no build step: the app runs from the repository.

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

Before each command the agent runs, a bar above your terminal shows it with **Run** and **Don't Run**. For fully hands-free use, turn off **Ask Before the Agent Runs a Command** in Preferences → Agent; the `sudo` guard keeps working.

## Tests

```bash
tests/run.sh
```

The command-line tools' error handling, the installer (in a throwaway `HOME`) and the MCP server's protocol are checked on their own. Then `tests/gtk_smoke.py` opens the app with a real bash, under its own application ID, a temporary settings folder, and a plain bash standing in for the agent. It drives everything through the same paths a user and the agent use: commands and the command log, tabs, windows, clipboard, search, links, preferences, safe closing, notifications, the D-Bus API, every MCP tool through a real `aiterm-mcp` process, and `aiterm-run` typed in the agent panel running in the user's terminal. The smoke test needs a graphical session and is skipped without one.

## Limitations

- The command log needs bash. Other shells work as plain terminals, and `read_terminal` falls back to the last 200 lines of the screen.
- One agent per window; it works on the window's selected tab.
- Built and tested on Ubuntu 26.04 with GNOME / Wayland.

## Roadmap

- [x] **v0.1 — tmux prototype.** Read and run tools for the agent, agent rules, sudo guard, installer, integration tests.
- [x] **v0.2 — the terminal itself.** A native terminal app on Python + GTK4 + libadwaita + VTE, no AI yet: GNOME-style window with a header bar and tabs, themes and palettes, fonts, a settings window, shortcuts, copy/paste, search in output, clickable links, an app launcher. *Done when it replaces the default terminal for daily use.* ([plan](docs/v0.2-plan.md))
- [x] **v0.3 — agent panel.** A side panel with `agy` inside the same window (toggle, resizable). No more tmux: the app itself reads the terminal and types commands, and `aiterm-left` / `aiterm-run` talk to the app. *Done when everything v0.1 does works in one window.* ([plan](docs/v0.3-plan.md))
- [x] **v0.4 — own MCP server.** Terminal tools (`read_terminal`, `run_command`, `get_cwd`, …) exposed to the agent over MCP instead of shell scripts and prompt rules. Shell integration gives exact command boundaries and exit codes, so a custom `PS1` no longer matters. *Done when the agent uses the tools without instructions in `GEMINI.md`.* ([plan](docs/v0.4-plan.md))
- [ ] **v0.5 — native chat UI** *(optional)*. The agent panel drawn by the app instead of agy's TUI: messages, collapsible command blocks, approval buttons, driven through `agy --output-format stream-json`.
- [ ] **v0.6 — evals.** A suite of broken-system scenarios (missing package, typo, broken config, missing permissions) run automatically in an isolated environment. Metrics: solved or not, steps, tokens. Results published in this README.
- [ ] **v1.0 — release.** A `.deb` or Flatpak package, demo GIF, CI on GitHub Actions running the tests, a tagged release.

## License

[MIT](LICENSE)
