#!/bin/bash
# deploy.sh — full restart wrapper with ci_check.sh gate
#
# Usage:
#   ./deploy.sh          — full deploy (ci_check → restart → verify)
#   ./deploy.sh --force  — skip ci_check, just restart
#   ./deploy.sh --help   — this message
set -euo pipefail

MEMORIA_PORT="${MEMORIA_PORT:-19998}"
CHITCHAT_PORT="${CHITCHAT_PORT:-19999}"
MEMORIA_DIR="/mnt/external-drive/code/memoria"

if [ "${1:-}" = "--help" ]; then
  sed -n '3,10p' "$0"
  exit 0
fi

echo "═══ deploy.sh — memoria stack deploy ═══"
echo ""

# ── Gate: ci_check.sh ───────────────────────────────────────

if [ "${1:-}" = "--force" ]; then
  echo "  ⚠️  --force: skipping ci_check.sh gate"
  echo ""
else
  echo "  Running ci_check.sh (gate)..."
  echo ""
  if ! bash "$MEMORIA_DIR/ci_check.sh"; then
    echo ""
    echo "  ❌ ci_check.sh FAILED — deploy blocked"
    echo "     Fix issues and re-run: ./deploy.sh"
    echo "     Bypass with:          ./deploy.sh --force"
    exit 1
  fi
  echo ""
fi

# ── Clear bytecode ──────────────────────────────────────────

echo "  [1/6] Clearing stale bytecode..."
find "$MEMORIA_DIR" -name "__pycache__" -prune -exec rm -rf {} + 2>/dev/null || true
find /home/daivolt/conf/chitchat -name "__pycache__" -prune -exec rm -rf {} + 2>/dev/null || true
find "$MEMORIA_DIR" -name "*.pyc" -delete 2>/dev/null || true
find /home/daivolt/conf/chitchat -name "*.pyc" -delete 2>/dev/null || true
echo "  done"

# ── Stop all services ───────────────────────────────────────

echo "  [2/6] Stopping all services..."
for s in sage builder researcher orchestrator memoria-worker pilosopher memoria-server chitchat-server; do
  systemctl --user stop "$s" 2>/dev/null || true
done
sleep 2
echo "  done"

# ── Start in order ──────────────────────────────────────────

echo "  [3/6] Starting chitchat-server..."
systemctl --user start chitchat-server 2>&1
for i in $(seq 1 10); do
  if curl -s --max-time 2 "http://localhost:$CHITCHAT_PORT/rooms" > /dev/null 2>&1; then
    echo "  chitchat ready (port $CHITCHAT_PORT)"
    break
  fi
  echo -n "."
  sleep 2
done
echo ""

echo "  [4/6] Starting memoria-server..."
systemctl --user start memoria-server 2>&1
for i in $(seq 1 10); do
  if curl -s --max-time 2 "http://localhost:$MEMORIA_PORT/health" > /dev/null 2>&1; then
    echo "  memoria ready (port $MEMORIA_PORT)"
    break
  fi
  echo -n "."
  sleep 2
done
echo ""

echo "  [5/6] Starting agents..."
for s in memoria-worker orchestrator researcher builder pilosopher sage; do
  systemctl --user start "$s" 2>&1
  echo "  started $s"
done

# ── Wait for agents to register ─────────────────────────────

echo "  [6/6] Waiting for agent registration..."
for i in $(seq 1 15); do
  COUNT=$(curl -s --max-time 3 "http://localhost:$MEMORIA_PORT/agents" \
    | python3 -c "import sys,json; print(len(json.load(sys.stdin).get('agents',[])))" 2>/dev/null || echo "0")
  if [ "$COUNT" -gt 0 ]; then
    echo "  $COUNT agents registered"
    break
  fi
  echo -n "."
  sleep 4
done
echo ""

# ── Summary ─────────────────────────────────────────────────

echo "═══ DEPLOY COMPLETE ═══"
echo ""
systemctl --user list-units --type=service 2>/dev/null \
  | grep -E "memoria|chitchat|sage|pilosopher|orchestrator|researcher|builder" \
  | grep loaded \
  | awk '{printf "  %-25s %s\n", $1, $3}'
echo ""
echo "  Dashboard: http://localhost:$MEMORIA_PORT/"
echo "  CI check:  ./ci_check.sh"
