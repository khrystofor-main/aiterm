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

# Aiterm's own prompt (Preferences → Prompt, see prompt.py): the app keeps
# the PS1 in $AITERM_PROMPT_FILE, read at every prompt with builtins only, so
# a change shows at the next prompt. No file: the prompt from ~/.bashrc
__aiterm_custom_prompt() {
    local custom=
    [[ -n $AITERM_PROMPT_FILE && -r $AITERM_PROMPT_FILE ]] \
        && IFS= read -r -d '' custom < "$AITERM_PROMPT_FILE"
    if [[ -z $custom ]]; then
        if [[ -n $__aiterm_own_prompt ]]; then
            PS1=$__aiterm_user_ps1
            __aiterm_own_prompt=
        fi
        return
    fi
    [[ -z $__aiterm_own_prompt ]] && __aiterm_user_ps1=$PS1
    __aiterm_own_prompt=1
    # Keep the semantic prompt marks VTE's vte.sh put around the user's prompt
    if [[ $__aiterm_user_ps1 == *\]133\;A* ]]; then
        custom='\[\e]133;D;$?\e\\\e]133;A\e\\\]'"$custom"'\[\e]133;B\e\\\]'
    fi
    PS1=$custom
    # The variables its segments show; empty ones drop out
    __aiterm_status=
    (( $1 )) && __aiterm_status=$1
    __aiterm_venv=${VIRTUAL_ENV:+${VIRTUAL_ENV##*/}}
    [[ -z $__aiterm_venv && -n $CONDA_DEFAULT_ENV && $CONDA_DEFAULT_ENV != base ]] \
        && __aiterm_venv=$CONDA_DEFAULT_ENV
    [[ $custom == *__aiterm_git* ]] && __aiterm_git_branch
}

# The branch from .git/HEAD in this folder or above, without starting git
__aiterm_git_branch() {
    local dir=$PWD head=
    __aiterm_git=
    while :; do
        if [[ -f $dir/.git/HEAD ]]; then
            IFS= read -r head < "$dir/.git/HEAD"
            break
        elif [[ -f $dir/.git ]]; then  # a worktree or submodule: "gitdir: <path>"
            IFS= read -r head < "$dir/.git"
            head=${head#gitdir: }
            [[ $head == /* ]] || head=$dir/$head
            if [[ -r $head/HEAD ]]; then IFS= read -r head < "$head/HEAD"; else head=; fi
            break
        fi
        [[ -z $dir ]] && return
        dir=${dir%/*}
    done
    case $head in
        "ref: refs/heads/"*) __aiterm_git=${head#ref: refs/heads/} ;;
        ?*) __aiterm_git=${head:0:7} ;;  # detached: the commit
    esac
}

__aiterm_prompt() {
    local status=$?
    # The number the next command gets in the history; checked in PS0
    __aiterm_histcmd=$HISTCMD
    __aiterm_custom_prompt "$__aiterm_last_status"
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

# First of all: the command's exit code, before other prompt commands reset $?
__aiterm_save_status() {
    __aiterm_last_status=$?
    return $__aiterm_last_status
}

if [[ "$(declare -p PROMPT_COMMAND 2>/dev/null)" == "declare -a"* ]]; then
    PROMPT_COMMAND=(__aiterm_save_status "${PROMPT_COMMAND[@]}" __aiterm_prompt)
else
    PROMPT_COMMAND="__aiterm_save_status; ${PROMPT_COMMAND:+$PROMPT_COMMAND; }__aiterm_prompt"
fi
# In front: vte.sh's PS0 ends in a raw ESC \ that would swallow what follows
PS0='$(__aiterm_command)'"${PS0:-}"
