#!/usr/bin/env bash
# Integration tests for aiterm-left and aiterm-run.
# Each test drives a real bash in a throwaway tmux server, so nothing touches
# the user's terminal. Needs tmux. Run: tests/run.sh
set -uo pipefail

ROOT=$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)
LEFT="$ROOT/bin/aiterm-left"
RUN="$ROOT/bin/aiterm-run"
export AITERM_SOCKET="aiterm-test-$$"
t() { tmux -L "$AITERM_SOCKET" -f /dev/null "$@"; }

pass=0 fail=0
check() { # check <name> <expected substring> <actual>
  if [[ $3 == *"$2"* ]]; then pass=$((pass + 1)); echo "  ok   $1"
  else fail=$((fail + 1)); echo "  FAIL $1"; echo "       expected: $2"; echo "       actual:   ${3//$'\n'/⏎}"; fi
}
check_not() {
  if [[ $3 != *"$2"* ]]; then pass=$((pass + 1)); echo "  ok   $1"
  else fail=$((fail + 1)); echo "  FAIL $1 (unexpected: $2)"; fi
}

# A clean bash with the default Ubuntu prompt, independent of the user's rc files
t new-session -d -s test -x 160 -y 50 -c "$HOME" "env PS1='\\u@\\h:\\w\\$ ' bash --norc --noprofile -i"
trap 't kill-server 2>/dev/null' EXIT
export AITERM_LEFT_PANE=$(t display-message -p -t test '#{pane_id}')
sleep 0.5

echo "aiterm-run"
out=$("$RUN" 'cd /tmp')
check "cd changes the terminal folder" "[terminal folder: /tmp]" "$out"
out=$("$RUN" 'echo first')
out=$("$RUN" 'ls /no-such-dir')
check "returns the error output" "No such file or directory" "$out"
check_not "returns only the last command" "first" "$out"
out=$("$RUN" "echo \"double\" 'single' \$HOME")
check "keeps quotes and expands variables" "double single $HOME" "$out"
out=$("$RUN" -t 1 'echo start; sleep 2; echo end'); code=$?
check "times out with code 124" "124" "$code"
check "returns partial output on timeout" "start" "$out"
out=$("$RUN" 'echo x' 2>&1); code=$?
check "refuses while a program runs (code 3)" "3" "$code"
out=$("$RUN" -w)
check "-w waits for the running command" "end" "$out"
t send-keys -t "$AITERM_LEFT_PANE" -l 'git sta'
out=$("$RUN" 'echo x' 2>&1); code=$?
check "refuses while the user is typing (code 3)" "3" "$code"
check "says what the user is typing" "git sta" "$out"
t send-keys -t "$AITERM_LEFT_PANE" C-c
sleep 0.3

echo "aiterm-left"
"$RUN" 'echo one' >/dev/null
"$RUN" 'echo two' >/dev/null
out=$("$LEFT")
check "shows the last command" "echo two" "$out"
check_not "hides older commands" "echo one" "$out"
out=$("$LEFT" 2)
check "N shows the last N commands" "echo one" "$out"
out=$("$LEFT" all)
check "all shows the whole terminal" "cd /tmp" "$out"
out=$("$LEFT" abc 2>&1); code=$?
check "rejects a bad argument (code 2)" "2" "$code"
out=$(AITERM_LEFT_PANE= "$LEFT" 2>&1); code=$?
check "outside aiterm exits with code 1" "1" "$code"

echo
echo "$pass passed, $fail failed"
[ "$fail" = 0 ]
