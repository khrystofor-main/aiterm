# Aiterm's shell integration for bash.
#
# Aiterm starts bash with `--rcfile` pointing here. With --rcfile bash skips
# its usual startup files, so this first loads them in bash's own order, then
# adds two marks that let Aiterm cut the terminal into commands exactly:
#
#   * every prompt starts on a fresh line (OSC 133;L, a conditional newline),
#     and ends with the mark vte.ext.aiterm.prompt=<lines in the prompt - 1>;
#     the cursor at that mark is where the user's command begins;
#   * when a command starts, vte.ext.aiterm.command=<base64 command text>.
#
# Marks travel as VTE terminal properties (OSC 666), the same channel Ubuntu's
# /etc/profile.d/vte-2.91.sh uses for preexec/precmd. Nothing is printed.

[ -r /etc/bash.bashrc ] && . /etc/bash.bashrc
[ -r ~/.bashrc ] && . ~/.bashrc

__aiterm_prompt() {
    local status=$?
    # The number the next command gets in the history; checked in PS0
    __aiterm_histcmd=$HISTCMD
    # Prompt frameworks rebuild PS1, so add the marks back when they are gone
    if [[ $PS1 != *vte.ext.aiterm.prompt* ]]; then
        PS1='\[\e]133;L\e\\\]'"$PS1"'\[\e]666;vte.ext.aiterm.prompt=${__aiterm_prompt_lines}\e\\\]'
    fi
    local expanded=${PS1@P} newlines
    newlines=${expanded//[!$'\n']/}
    __aiterm_prompt_lines=${#newlines}
    return $status
}

__aiterm_command() {
    # Runs in PS0's command substitution, after bash saved the command to the
    # history. HISTCONTROL=ignorespace/ignoredups can keep it out; then the
    # newest entry is an older command, and Aiterm reads the screen instead
    local entry
    entry=$(HISTTIMEFORMAT= builtin history 1)
    if [[ $entry =~ ^\ *([0-9]+)\*?\ \ (.*)$ ]] && (( BASH_REMATCH[1] == __aiterm_histcmd )); then
        printf '\e]666;vte.ext.aiterm.command=%s\e\\' "$(printf '%s' "${BASH_REMATCH[2]}" | base64 -w0)"
    fi
}

if [[ "$(declare -p PROMPT_COMMAND 2>/dev/null)" == "declare -a"* ]]; then
    PROMPT_COMMAND+=(__aiterm_prompt)
else
    PROMPT_COMMAND="${PROMPT_COMMAND:+$PROMPT_COMMAND; }__aiterm_prompt"
fi
# In front: vte.sh's PS0 ends in a raw ESC \ that would swallow what follows
PS0='$(__aiterm_command)'"${PS0:-}"
