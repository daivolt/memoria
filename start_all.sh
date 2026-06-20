#!/bin/bash
# Start all memoria + chitchat services
# Run this after boot or systemd user service failure

set -e

echo "== Starting memoria stack =="

# Reload user daemon
systemctl --user daemon-reload

# Start chitchat server first (memoria depends on it)
echo "→ chitchat-server"
systemctl --user start chitchat-server

# Wait for chitchat to be ready
sleep 3

# Start memoria server
echo "→ memoria-server"
systemctl --user start memoria-server

# Wait for memoria to accept connections
for i in $(seq 1 10); do
  if curl -s http://localhost:19998/health > /dev/null 2>&1; then
    break
  fi
  echo "  waiting for memoria... ($i)"
  sleep 2
done

# Start warden session manager (gNotes depends on it)
echo "→ xvfb + warden-session-manager"
systemctl --user start xvfb warden-session-manager 2>/dev/null || echo "  (warden not installed, skipping)"

# Start all agents
echo "→ agents (worker, orchestrator, researcher, builder, pilosopher, gnotes)"
systemctl --user start memoria-worker orchestrator researcher builder pilosopher gnotes

sleep 2

# Verify
echo ""
echo "== Status =="
systemctl --user list-units --type=service 2>/dev/null | grep -E "memoria|chitchat|pilosopher|orchestrator|researcher|builder|gnotes" | grep loaded
echo ""
echo "== Memoria health =="
curl -s http://localhost:19998/health | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'  version={d.get(\"memoria_version\")} rooms={d.get(\"chitchat_rooms\")}')"
echo "== Agents =="
curl -s http://localhost:19998/agents | python3 -c "
import sys,json
for a in json.load(sys.stdin).get('agents',[]):
    print(f'  {a[\"id\"][:10]} name={a.get(\"chitchat_name\",\"?\")[:15]} status={a[\"status\"]}')
" 2>/dev/null
echo ""
echo "All services started."
