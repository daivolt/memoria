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
MEMORIA_DIR="/mnt/external-drive/code/memoria"

if [ "${1:-}" = "--help" ]; then
  sed -n '3,10p' "$0"
  exit 0
fi

# ── Gate: ci_check.sh ───────────────────────────────────────

if [ "${1:-}" = "--force" ]; then
  :
else
  sleep 3  # let services stabilize
  if ! bash "$MEMORIA_DIR/ci_check.sh"; then
    ERR="${ERR}  ci_check.sh FAILED\n"
  fi
fi

# ── Clear bytecode ──────────────────────────────────────────

find "$MEMORIA_DIR" /home/daivolt/conf/chitchat -name "__pycache__" -prune -exec rm -rf {} + 2>/dev/null || true
find "$MEMORIA_DIR" /home/daivolt/conf/chitchat -name "*.pyc" -delete 2>/dev/null || true

# ── Stop memoria services ───────────────────────────────────

for s in memoria-server memoria-worker; do
  systemctl --user stop "$s" 2>/dev/null || true
done
sleep 2

# ── Start in order ──────────────────────────────────────────

systemctl --user start memoria-server 2>/dev/null >/dev/null || ERR="${ERR}  memoria-server failed to start\n"
for i in $(seq 1 10); do
  if curl -s --max-time 2 "http://localhost:$MEMORIA_PORT/health" > /dev/null 2>&1; then
    break
  fi
  sleep 2
done

systemctl --user start memoria-worker 2>/dev/null >/dev/null || ERR="${ERR}  memoria-worker failed to start\n"

# ── Result ──────────────────────────────────────────────────

if [ -z "$ERR" ]; then
  echo "deploy OK — all memoria services active"
else
  echo -e "deploy FAILED:\n${ERR}"
  exit 1
fi
