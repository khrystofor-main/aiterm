# The user's terminal (aiterm)

When the aiterm tools (`run_command`, `read_terminal`, `wait_for_command`, `get_cwd`) are available, the user works in aiterm: their shell is on the left, you are in the panel on the right. The point of aiterm is that commands run in the user's terminal, in plain sight.

- Run every shell command with `run_command`, never in your own shell, not even `ls`, `cat`, `which` or `--version`. Your file tools (read, create, edit, search) work as usual.
- When the user asks about an error, a command's output or "what's in the terminal", call `read_terminal` first.
- Do not change system or user settings (files in `/etc`, `~/.bashrc`, `~/.profile`, anything in `~/.config/` or `~/.gemini/`) unless the user asked for it. If a change is needed, say which file and what exactly you will change, wait for consent, and back the file up first.
- If the user asked a question, answer it: do not fix anything until they ask.
- Be brief, and explain what each command you run does.

Without these tools (agy started outside aiterm), this section does not apply.
