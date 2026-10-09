# aiterm

**An AI terminal for Ubuntu: your shell on the left, an AI agent on the right that sees your terminal and runs commands in it — in plain sight.**

<!-- TODO: demo GIF -->

Ask *"why did this fail?"* and the agent reads the command you just ran. Ask *"install docker"* and you watch the commands appear in your own shell, one by one. When something needs `sudo`, the agent stops and you type the password yourself.

The agent is [Antigravity CLI](https://antigravity.google/docs/cli/install) (`agy`) running on your own Google subscription. aiterm is the glue that gives it eyes and hands in your terminal.

## How it works

```
┌────────────── tmux (own server, own config) ──────────────┐
│                                 │                         │
│   Your shell (bash)             │   agy (Gemini)          │
│                                 │                         │
└───────────────▲─────────────────┴────────────┬────────────┘
                │ capture-pane / send-keys     │ shell tool calls
                │                              ▼
                │                ┌──────────────────────────┐
                └────────────────│ aiterm-left  — read      │
                                 │ aiterm-run   — execute   │
                                 └──────────────────────────┘
                                   + rules in ~/.gemini/GEMINI.md
```

- **`aiterm`** starts a dedicated tmux server with two panes and tells the agent which pane is yours (`AITERM_LEFT_PANE`).
- **`aiterm-left`** gives the agent *just enough* context: by default only your last command and its output, split on the shell prompt. It widens to the last *N* commands or the whole scrollback when needed, so the agent doesn't burn context on old noise.
- **`aiterm-run`** types a command into your shell, waits for the prompt to come back and returns the output. It refuses to type while you are typing or a program is running, handles timeouts and commands that wait for input, and trims huge outputs.
- **Rules** (`rules/aiterm.md`) teach the agent to use these tools instead of its own hidden shell, how to widen context, and what not to touch.

## Safety model

| Risk | What aiterm does |
|---|---|
| Agent types over your half-written command | `aiterm-run` checks that the prompt is empty and only bash is running; otherwise it refuses (exit code 3) |
| Agent runs `sudo` behind your back | Before any agent command with `sudo`, cached credentials are dropped (`sudo -K`), so **every** such command needs your password, typed in your pane |
| Agent hangs on `less`, `vim`, `[Y/n]` | Timeouts with partial output; the rules ban pagers and full-screen programs |
| Agent edits your configs "to help" | Rules forbid changing system/user settings without explicit consent |
| You don't see what the agent does | Every shell command runs in your terminal, visible and in your history |

The rules are instructions to a model, not hard guarantees; the `sudo` guard and the busy-terminal checks are enforced in code.

## Install

Requirements: Ubuntu (or another Linux with bash), `tmux`, `jq`, and [`agy`](https://antigravity.google/docs/cli/install) signed in.

```bash
sudo apt install tmux jq
git clone https://github.com/khrystofor-main/aiterm.git
cd aiterm
./install.sh
```

The installer (no sudo) links the commands into `~/.local/bin`, adds an **AI Terminal** launcher to the applications menu, adds the agent rules to `~/.gemini/GEMINI.md` between `aiterm` markers, and allows `agy` to run `aiterm-left` / `aiterm-run`. Run it again to update; `./uninstall.sh` removes everything it added.

## Usage

Run `aiterm` (or open **AI Terminal** from the menu). Work on the left, talk to the agent on the right.

| Keys | Action |
|---|---|
| `Alt+Enter` | Switch between your shell and the agent |
| Mouse | Click to focus, drag the border to resize, wheel to scroll |
| `Shift` + mouse | Native text selection |

Tip: `agy` asks before every `aiterm-run 'command'`, because its allow rules only match simple commands. To make it fully hands-free, set **Tool Permission → always-proceed** in `agy`'s `/config`; the `sudo` guard keeps working.

## Native app (v0.2 preview)

aiterm is moving from tmux to its own GTK 4 window. The first step is a plain terminal built on Python, libadwaita and VTE, meant to replace Ptyxis day to day; the agent comes back into it later. It follows the system light/dark theme and font.

```bash
sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-vte-3.91
./install.sh   # adds "Aiterm" to the applications menu
```

Or run it straight from the repo: `bin/aiterm-gtk`. Preferences (Ctrl+,) are saved to `~/.config/aiterm/settings.json`. Bash starts with a small integration file (`src/aiterm/shell/integration.bash`, it loads your `~/.bashrc` first) that lets the app see each command, its output and exit code: Ctrl+Shift+Up/Down jumps between prompts, and right-click → Copy Output copies one command's output. The plan and feature list are in [docs/v0.2-plan.md](docs/v0.2-plan.md).

## Tests

```bash
tests/run.sh
```

Integration tests drive a real bash in a throwaway tmux server: reading the last / last N / all commands, folder tracking, quoting, timeouts, waiting for a running command, and refusing to type while the user is typing or a program runs. A smoke test then opens the GTK app with a real bash and checks commands, the title and the theme switch (skipped without a display).

## Limitations

- Command boundaries are found by the default bash prompt (`user@host:path$ `). With a custom `PS1`, `aiterm-left` falls back to the last 200 lines.
- `aiterm-run` doesn't return the command's exit code; the agent judges by the output.
- Built and tested on Ubuntu 26.04 with GNOME / Wayland.

## Roadmap

- [x] **v0.1** — tmux-based prototype: read and run tools, rules, sudo guard, installer, tests
- [ ] **v0.2** — native GTK4 + VTE app: one window, terminal and agent panel, no tmux ([plan](docs/v0.2-plan.md))
- [ ] **v0.3** — own MCP server: terminal tools exposed to the agent over MCP instead of shell scripts
- [ ] **v0.4** — evals: a suite of broken-system scenarios to measure how well the agent diagnoses and fixes them

## License

[MIT](LICENSE)
