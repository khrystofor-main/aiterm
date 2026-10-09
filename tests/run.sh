#!/usr/bin/env bash
# Tests: the command-line tools and the MCP server's protocol on their own,
# then tests/gtk_smoke.py, which opens the app with a real bash and drives the
# terminal, the D-Bus API, the tools and the MCP server end to end (skipped
# without a display). Run: tests/run.sh
set -uo pipefail

ROOT=$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)
LEFT="$ROOT/bin/aiterm-left"
RUN="$ROOT/bin/aiterm-run"

pass=0 fail=0
check() { # check <name> <expected substring> <actual>
  if [[ $3 == *"$2"* ]]; then pass=$((pass + 1)); echo "  ok   $1"
  else fail=$((fail + 1)); echo "  FAIL $1"; echo "       expected: $2"; echo "       actual:   ${3//$'\n'/⏎}"; fi
}

echo "tools"
out=$(env -u AITERM_WINDOW "$LEFT" 2>&1); code=$?
check "aiterm-left outside aiterm exits with code 1" "1" "$code"
check "…and says why" "not running inside aiterm" "$out"
out=$(env -u AITERM_WINDOW "$RUN" 'echo hi' 2>&1); code=$?
check "aiterm-run outside aiterm exits with code 1" "1" "$code"
out=$(AITERM_WINDOW=1 "$LEFT" abc 2>&1); code=$?
check "aiterm-left rejects a bad argument (code 2)" "2" "$code"
out=$(AITERM_WINDOW=1 "$RUN" 2>&1); code=$?
check "aiterm-run without a command (code 2)" "2" "$code"
out=$(AITERM_WINDOW=1 AITERM_BUS_NAME=:1.no-such-app "$RUN" 'echo hi' 2>&1); code=$?
check "aiterm-run with no app to talk to (code 1)" "1" "$code"
check "…and says why" "Cannot reach the Aiterm window" "$out"

echo "MCP server"
"$ROOT/tests/mcp_protocol.py"; code=$?
if [ $code = 0 ]; then pass=$((pass + 1)); else fail=$((fail + 1)); fi

echo "GTK app"
"$ROOT/tests/gtk_smoke.py" 2> >(grep -v -e VK_SUBOPTIMAL -e '^$' >&2); code=$?
case $code in
  0) pass=$((pass + 1)) ;;
  77) ;;  # skipped: no display or no VTE for GTK 4
  *) fail=$((fail + 1)) ;;
esac

echo
echo "$pass passed, $fail failed"
[ "$fail" = 0 ]
