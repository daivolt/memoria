#!/bin/bash
# ci_check.sh — deploy gate for memoria
#
# Runs before every deploy. Blocks push/deploy if any check fails.
# Can be run standalone: ./ci_check.sh
#
# Exit codes:
#   0 — all checks pass
#   1 — one or more checks failed (see output)
#
# Skip Sage E2E test with:  ./ci_check.sh --fast  (for pre-push, skips E2E)
set -euo pipefail

MEMORIA_PORT="${MEMORIA_PORT:-19998}"
CHITCHAT_PORT="${CHITCHAT_PORT:-19999}"
MEMORIA_DIR="/mnt/external-drive/code/memoria"
E2E_MAX_WAIT=60
FAIL=0
STEP=0

next() {
  STEP=$((STEP + 1))
  printf "\n  [%d/%d] %s ... " "$STEP" "$TOTAL" "$1"
}
pass() { printf "PASS\n"; }
fail() { printf "FAIL\n"; FAIL=1; }

# Count total steps
if [ "${1:-}" = "--fast" ]; then
  TOTAL=8
else
  TOTAL=11
fi

echo "═══ ci_check.sh — memoria deploy gate ═══"
echo ""

# ── 1. Python syntax ────────────────────────────────────────

next "Python syntax"
PY_FILES=$(find "$MEMORIA_DIR" -maxdepth 1 -name "*.py" | sort)
ERR=""
for f in $PY_FILES; do
  if ! python3 -m py_compile "$f" 2>/dev/null; then
    ERR="$ERR$(python3 -m py_compile "$f" 2>&1)\n"
  fi
done
for f in /home/daivolt/conf/chitchat/sage.py /home/daivolt/conf/chitchat/memoria_agent.py /home/daivolt/conf/chitchat/pilosopher.py; do
  if [ -f "$f" ] && ! python3 -m py_compile "$f" 2>/dev/null; then
    ERR="$ERR$(python3 -m py_compile "$f" 2>&1)\n"
  fi
done
if [ -n "$ERR" ]; then echo; echo -e "$ERR"; fail; else pass; fi

# ── 2. Dashboard JS bugs ────────────────────────────────────

next "Dashboard JS bugs"
HTML=$(curl -s "http://localhost:$MEMORIA_PORT/" 2>/dev/null || echo "")
if [ -z "$HTML" ]; then
  echo "(memoria down, skipping)"
  pass
else
  echo "(memoria at localhost:$MEMORIA_PORT)"
  ISSUES=""
  if [ "$(printf '%s' "$HTML" | grep -c 'let brainTaskData')" -ne 1 ]; then
    ISSUES="$ISSUES  duplicate let brainTaskData ($(printf '%s' "$HTML" | grep -c 'let brainTaskData') occurrences)\n"
  fi
  if printf '%s' "$HTML" | grep -q 'drawBrainFrame'; then
    ISSUES="$ISSUES  dead drawBrainFrame code\n"
  fi
  if ! printf '%s' "$HTML" | grep -q 'let s = esc(text)'; then
    ISSUES="$ISSUES  renderMarkdown missing esc()\n"
  fi
  if [ "$(printf '%s' "$HTML" | grep -c 'AbortController')" -eq 0 ]; then
    ISSUES="$ISSUES  api() missing timeout — AbortController\n"
  fi
  if [ -n "$ISSUES" ]; then echo; echo -e "$ISSUES" | head -20; fail; else pass; fi
fi

# ── 3. Chitchat health ──────────────────────────────────────

next "Chitchat server :$CHITCHAT_PORT"
if curl -s --max-time 5 "http://localhost:$CHITCHAT_PORT/rooms" > /dev/null 2>&1; then
  ROOM_COUNT=$(curl -s "http://localhost:$CHITCHAT_PORT/rooms" | python3 -c "import sys,json; print(len(json.load(sys.stdin).get('rooms',[])))" 2>/dev/null || echo "0")
  pass
  echo " ($ROOM_COUNT rooms)"
else
  fail
  echo " (connection refused)"
fi

# ── 4. Memoria health ───────────────────────────────────────

next "Memoria server :$MEMORIA_PORT"
HEALTH=$(curl -s --max-time 5 "http://localhost:$MEMORIA_PORT/health" 2>/dev/null || echo "")
if [ -n "$HEALTH" ]; then
  VER=$(echo "$HEALTH" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('memoria_version',''))" 2>/dev/null)
  SESS=$(echo "$HEALTH" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('sessions_indexed',0))" 2>/dev/null)
  pass
  echo " (v$VER, $SESS sessions)"
else
  fail
  echo " (no response)"
fi

# ── 5. Agent count ──────────────────────────────────────────

next "Agents registered"
AGENTS=$(curl -s --max-time 5 "http://localhost:$MEMORIA_PORT/agents" | python3 -c "import sys,json; print(len(json.load(sys.stdin).get('agents',[])))" 2>/dev/null || echo "0")
if [ "$AGENTS" -gt 0 ]; then
  pass
  echo " ($AGENTS agents)"
else
  fail
  echo " (0 agents — nobody registered)"
fi

# ── 6. Sage E2E test (skipped with --fast) ──────────────────

if [ "${1:-}" = "--fast" ]; then
  echo "  [6/8] Sage E2E ....................... SKIP (--fast mode)"
  echo "  [7/8] Task verification ............... SKIP (--fast mode)"
  echo "  [8/8] Service error log sweep ........ SKIP (--fast mode)"
else

next "Sage E2E: create + complete task"
TASK_ID=$(curl -s -X POST "http://localhost:$MEMORIA_PORT/tasks" \
  -H "Content-Type: application/json" \
  -d '{"project":"memoria","title":"deploy_gate_test","description":"ci_check.sh e2e","test_command":"exit 0","lint_command":"exit 0","rubric":["passes"]}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin).get('task_id',''))" 2>/dev/null || echo "")
if [ -z "$TASK_ID" ]; then
  fail
  echo " (could not create task)"
else
  curl -s -X PATCH "http://localhost:$MEMORIA_PORT/tasks/$TASK_ID" \
    -H "Content-Type: application/json" \
    -d '{"status":"completed","result":"ci_check e2e"}' > /dev/null 2>&1
  echo " ($TASK_ID)"
fi

# ── 7. Wait for Sage verification ───────────────────────────

next "Sage verification (max ${E2E_MAX_WAIT}s)"
SCORE="null"
for i in $(seq 1 $((E2E_MAX_WAIT / 5))); do
  SCORE=$(curl -s --max-time 3 "http://localhost:$MEMORIA_PORT/tasks/$TASK_ID" \
    | python3 -c "import sys,json; t=json.load(sys.stdin); v=t.get('verification') or {}; print(v.get('score','null'))" 2>/dev/null || echo "null")
  if [ "$SCORE" != "null" ]; then break; fi
  echo -n "."
  sleep 5
done
echo ""
if [ "$SCORE" = "null" ]; then
  fail
  echo " (Sage did not verify within ${E2E_MAX_WAIT}s)"
elif [ "$(echo "$SCORE < 0.8" | bc -l 2>/dev/null || echo 1)" = "1" ]; then
  fail
  echo " (score=$SCORE below threshold 0.8)"
else
  pass
  echo " (score=$SCORE)"
fi

# ── 8. Journalctl ERROR sweep ───────────────────────────────

next "Service error log sweep (last 60s)"
ERRORS=$(journalctl --user -u sage -u memoria-server -u chitchat-server --since "60 seconds ago" --no-pager 2>/dev/null \
  | grep -i "error\|exception\|traceback" \
  | grep -v "opencode\|address already in use" \
  | head -5 || true)
if [ -n "$ERRORS" ]; then
  echo
  echo "$ERRORS"
  fail
else
  pass
fi

fi # end --fast guard

# ── Summary ──────────────────────────────────────────────────

echo ""
echo "═══ RESULT ═══"
if [ "$FAIL" -eq 0 ]; then
  echo "  ✅ All $STEP checks passed"
  exit 0
else
  echo "  ❌ $FAIL check(s) failed — deploy blocked"
  echo "  Fix issues above, then re-run: ./ci_check.sh"
  exit 1
fi
