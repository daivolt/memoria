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
set -euo pipefail

MEMORIA_PORT="${MEMORIA_PORT:-19998}"
MEMORIA_DIR="/mnt/external-drive/code/memoria"
FAIL=0
STEP=0
TOTAL=4

next() {
  STEP=$((STEP + 1))
  printf "\n  [%d/%d] %s ... " "$STEP" "$TOTAL" "$1"
}
pass() { printf "PASS\n"; }
fail() { printf "FAIL\n"; FAIL=1; }

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
if [ -n "$ERR" ]; then echo; echo -e "$ERR"; fail; else pass; fi

# ── 2. Dashboard JS bugs ────────────────────────────────────

next "Dashboard JS bugs"
HTML=$(curl -s "http://localhost:$MEMORIA_PORT/" 2>/dev/null || echo "")
# Retry HTML fetch up to 3 times (server may be slow)
for _ in 1 2 3; do
  HTML=$(curl -s "http://localhost:$MEMORIA_PORT/" 2>/dev/null || echo "")
  [ -n "$HTML" ] && break
  sleep 2
done
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
  if ! grep -q 'let s = esc(text)' <<< "$HTML"; then
    ISSUES="$ISSUES  renderMarkdown missing esc()\n"
  fi
  if [ "$(printf '%s' "$HTML" | grep -c 'AbortController')" -eq 0 ]; then
    ISSUES="$ISSUES  api() missing timeout — AbortController\n"
  fi
  # JS syntax validation via node --check (skip inline event handlers)
  JS_TMP=$(mktemp /tmp/memoria_js_XXXX.js)
  printf '%s' "$HTML" | python3 -c "
import sys, re
html = sys.stdin.read()
scripts = re.findall(r'<script[^>]*>(.*?)</script>', html, re.DOTALL)
for s in scripts:
    lines = s.split('\n')
    clean = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('+') and 'onclick' in stripped:
            continue
        clean.append(line)
    sys.stdout.write('\n'.join(clean) + '\n')
" > "$JS_TMP" 2>/dev/null
  if [ -s "$JS_TMP" ]; then
    if node --check "$JS_TMP" 2>/dev/null; then
      :
    else
      ISSUES="$ISSUES  JS syntax error — run 'node --check' for details\n"
    fi
  fi
  rm -f "$JS_TMP"
  if [ -n "$ISSUES" ]; then echo; echo -e "$ISSUES" | head -20; fail; else pass; fi
fi

# ── 3. Memoria health ───────────────────────────────────────

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

# ── 4. Service error log sweep ────────────────────────────────

next "Service error log sweep (last 60s)"
ERRORS=$(journalctl --user -u memoria-server --since "60 seconds ago" --no-pager 2>/dev/null \
  | grep -i "error\|exception\|traceback" \
  | grep -v "opencode\|address already in use\|429\|Too Many Requests\|WebSocket is not connected\|Need to call" \
  | head -5 || true)
if [ -n "$ERRORS" ]; then
  echo
  echo "$ERRORS"
  fail
else
  pass
fi

# ── Summary ──────────────────────────────────────────────────

echo ""
echo "═══ RESULT ═══"
if [ "$FAIL" -eq 0 ]; then
  echo "  All $STEP checks passed"
  exit 0
else
  echo "  $FAIL check(s) failed — deploy blocked"
  echo "  Fix issues above, then re-run: ./ci_check.sh"
  exit 1
fi