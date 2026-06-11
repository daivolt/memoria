#!/bin/bash
# deploy.sh — full restart wrapper with ci_check.sh gate
#
# Usage:
#   ./deploy.sh          — full deploy (ci_check → restart → verify)
#   ./deploy.sh --force  — skip ci_check, just restart
#   ./deploy.sh --help   — this message
set -euo pipefail
ERR=""

MEMORIA_PORT="${MEMORIA_PORT:-19998}"
CHITCHAT_PORT="${CHITCHAT_PORT:-19999}"
MEMORIA_DIR="/mnt/external-drive/code/memoria"

if [ "${1:-}" = "--help" ]; then
  sed -n '3,10p' "$0"
  exit 0
fi

# ── Gate: ci_check.sh ───────────────────────────────────────

if [ "${1:-}" = "--force" ]; then
  :
else
  if ! bash "$MEMORIA_DIR/ci_check.sh" 2>/dev/null >/dev/null; then
    ERR="${ERR}  ci_check.sh FAILED\n"
  fi
fi

# ── Clear bytecode ──────────────────────────────────────────

find "$MEMORIA_DIR" /home/daivolt/conf/chitchat -name "__pycache__" -prune -exec rm -rf {} + 2>/dev/null || true
find "$MEMORIA_DIR" /home/daivolt/conf/chitchat -name "*.pyc" -delete 2>/dev/null || true

# ── Stop all services ───────────────────────────────────────

for s in mini sage builder researcher orchestrator memoria-worker pilosopher memoria-server chitchat-server; do
  systemctl --user stop "$s" 2>/dev/null || true
done
sleep 2

# ── Start in order ──────────────────────────────────────────

systemctl --user start chitchat-server 2>/dev/null >/dev/null || ERR="${ERR}  chitchat-server failed to start\n"
for i in $(seq 1 10); do
  if curl -s --max-time 2 "http://localhost:$CHITCHAT_PORT/rooms" > /dev/null 2>&1; then
    break
  fi
  sleep 2
done

systemctl --user start memoria-server 2>/dev/null >/dev/null || ERR="${ERR}  memoria-server failed to start\n"
for i in $(seq 1 10); do
  if curl -s --max-time 2 "http://localhost:$MEMORIA_PORT/health" > /dev/null 2>&1; then
    break
  fi
  sleep 2
done

for s in mini memoria-worker orchestrator researcher builder pilosopher sage; do
  systemctl --user start "$s" 2>/dev/null >/dev/null || ERR="${ERR}  $s failed to start\n"
done

# ── Wait for agents to register ─────────────────────────────

AGENTS=0
for i in $(seq 1 15); do
  COUNT=$(curl -s --max-time 3 "http://localhost:$MEMORIA_PORT/agents" \
    | python3 -c "import sys,json; print(len(json.load(sys.stdin).get('agents',[])))" 2>/dev/null || echo "0")
  if [ "$COUNT" -gt 0 ]; then
    AGENTS="$COUNT"
    break
  fi
  sleep 4
done

# ── Result ──────────────────────────────────────────────────

if [ -z "$ERR" ]; then
  echo "deploy OK — ${AGENTS} agents, all services active"
else
  echo -e "deploy FAILED:\n${ERR}"
  exit 1
fi
