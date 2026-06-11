# memoria v2 — Memory + AgentOS Orchestration

Persistent memory + agent coordination for opencode.
Runs as a REST server on mediserv (`100.126.64.13:19998`).

## Architecture (6 Layers + Safety)

```
┌─────────────────────────────────────────────────────────────┐
│  Safety Layer — Git snapshots, rollback, commit tagging     │
│  /var/tmp/memoria/safety/<project>/snapshots.jsonl          │
│  Pre-work snapshot, rollback anytime, auto-commit tagging   │
├─────────────────────────────────────────────────────────────┤
│  Layer 5 — Agent Registry                                   │
│  /var/tmp/memoria/agents/*.json                             │
│  Tracks active sessions, file claims, PID heartbeat.        │
│  Conflict detection prevents concurrent edits.              │
├─────────────────────────────────────────────────────────────┤
│  Layer 6 — Task Board                                       │
│  /var/tmp/memoria/tasks/*.json                              │
│  Cross-agent task delegation via chitchat notifications.    │
│  Status: pending → assigned → completed/failed              │
├─────────────────────────────────────────────────────────────┤
│  Layer 1 — MEMORY.md (per-project durable facts)            │
│  /var/tmp/memoria/<project>/MEMORY.md  (§-delimited)        │
│  5KB limit. Saved via `!memoria add <fact>`.                │
├─────────────────────────────────────────────────────────────┤
│  Layer 2 — Global Topics (cross-project knowledge)          │
│  /var/tmp/memoria/topics/<name>.md  (§-delimited)           │
│  Proposed via `!memoria propose <topic> <text>`,            │
│  accepted/rejected via `!memoria accept/reject <id>`.       │
├─────────────────────────────────────────────────────────────┤
│  Layer 3 — FTS5 Session Recall (all sessions)               │
│  /var/tmp/memoria/index.db (SQLite FTS5)                    │
│  Polls opencode.db every 30s, extracts structured records.  │
│  Searchable via `!memoria recall <query>`.                  │
├─────────────────────────────────────────────────────────────┤
│  Layer 4 — Active Context (real-time task state)            │
│  /var/tmp/memoria/<project>/context/state.json              │
│  Task description, files in scope, file locks.              │
│  PID-based staleness check (5min timeout).                  │
└─────────────────────────────────────────────────────────────┘
```

## Components

### `memoriad_global.py` — REST Server (port 19998)
FastAPI server with all endpoints. Runs on mediserv.
Background task polls opencode.db every 30s for new sessions.

**Endpoints:**
| Method | Path | Purpose |
|--------|------|---------|
| GET | `/health` | Server status, session count, topics |
| GET | `/recall?q=<query>&limit=N` | FTS5 session search |
| GET | `/review?n=N` | Recent session summaries |
| GET/POST | `/memory/{project}` | Per-project durable facts |
| PUT | `/memory/{project}` | Replace memory entry |
| GET/POST | `/topics/{name}` | Cross-project topic facts |
| GET/POST | `/proposals` | Propose new cross-project facts |
| POST | `/proposals/{id}/accept` | Accept proposal → topic |
| DELETE | `/proposals/{id}` | Reject proposal |
| GET/POST/DELETE | `/context/{project}` | Active task state |
| POST | `/context/{project}/claim/{file}` | File lock claim |
| POST | `/compress` | Phase 1+2 context compression |
| POST | `/agents` | Register agent session |
| GET | `/agents` | List active agents |
| PATCH | `/agents/{id}` | Agent heartbeat |
| DELETE | `/agents/{id}` | Deregister agent |
| POST | `/tasks` | Create task |
| GET | `/tasks` | List tasks (filter by project/status) |
| PATCH | `/tasks/{id}` | Update task (assign, complete, fail) |
| DELETE | `/tasks/{id}` | Delete task |
| POST | `/safety/snapshot/{project}` | Create git snapshot |
| POST | `/safety/rollback/{project}` | Rollback to snapshot |
| GET | `/safety/{project}/snapshots` | List snapshots |
| GET | `/` | HTML dashboard |

### `memoria.py` — CLI (HTTP client)
Proxies all commands to REST server:

**Memory:**
| Command | Action |
|---------|--------|
| `!memoria init` | Verify server connectivity |
| `!memoria add <text>` | Save durable fact to MEMORY.md |
| `!memoria list` | Show all facts |
| `!memoria replace <old> <new>` | Update a matching fact |
| `!memoria recall <query>` | Search past sessions via FTS5 |
| `!memoria review [N]` | Show last N session summaries |
| `!memoria learnings` | Show accumulated project knowledge |
| `!memoria compress` | Read stdin, compress via REST |
| `!memoria status` | Server health + project memory |
| `!memoria topics` | List cross-project topics |
| `!memoria topic <name> [text]` | Show topic facts or add one |
| `!memoria propose <topic> <text>` | Propose cross-project fact |
| `!memoria proposals` | List pending proposals |
| `!memoria accept <id>` | Accept proposal → topic |
| `!memoria reject <id>` | Reject proposal |

**Orchestration:**
| Command | Action |
|---------|--------|
| `!memoria task <project> <title>` | Create task on board |
| `!memoria claim <task-id>` | Claim a pending task |
| `!memoria done <task-id> [result]` | Mark task complete |
| `!memoria fail <task-id> [error]` | Mark task failed |
| `!memoria tasks [project]` | List tasks (filter by project) |
| `!memoria agents [project]` | List active agents |
| `!memoria snap <project> [msg]` | Create git snapshot |
| `!memoria rollback <project> [id]` | Rollback to snapshot |

### `compress.py` — Phase 1 + 2 Engine
Pure stdlib, deterministic, zero LLM cost.

- **Phase 1**: Tool output → 1-line summaries (regex: exit code, match count, line count)
- **Phase 2**: Structured turn merging — drop acks, placeholder reasoning, group sequential tools, preserve errors

### `context.py` — Active Context State
Real-time task info per project. PID-based staleness (5min).
File claims prevent concurrent edits.

### `tui/` — Charm TUI (Go)

Terminal UI built with [Charm](https://charm.land/libs/) (Bubble Tea + Lip Gloss + Bubbles + Wish).

**Layout:** Two panes — chat on the left, dashboard on the right.

| Pane | Content |
|------|---------|
| **Chat** (left) | Room tabs, message viewport, text input. Connects to chitchat (port 19999). |
| **Dashboard** (right) | Tabs: Agents, Tasks, Memory, Recall. Connects to memoria (port 19998). |

**Controls:**

| Key | Action |
|-----|--------|
| `←` `→` | Switch chat rooms |
| `h` `l` | Switch dashboard tabs |
| `1`-`4` | Jump to dashboard tab |
| `tab` | Toggle chat input focus |
| `r` | Focus recall search (on Recall tab) |
| `enter` | Send message / submit search / load memory |
| `q` / `ctrl+c` | Quit |

**Modes:**

| Command | Mode |
|---------|------|
| `tui` | Run as local TUI |
| `tui --sshd` | Run as SSH server on port 23234 |
| `tui --help` | Show help |

The TUI is also available as a **Tailscale service** via SSH. From any device on your Tailscale network:

```bash
ssh -p 23234 daivolt@100.126.64.13
```

### systemd — TUI SSH Server

`memoria-tui.service` runs the TUI as an SSH server (port 23234):

```bash
# Service definition: /etc/systemd/system/memoria-tui.service
#   WorkingDirectory: /home/daivolt/memoria/tui
#   ExecStart:        tui --sshd
#   User:             daivolt
#   Restart:          always

sudo systemctl enable --now memoria-tui
```

## OpenCode Integration

Memoria integrates with opencode via two mechanisms:

### Plugin (automatic lifecycle)

`~/.config/opencode/plugins/memoria.js` — hooks into opencode events:

| Event | Action | Failure mode |
|-------|--------|-------------|
| `shell.env` | Injects `MEMORIA_SERVER` + `MEMORIA_PROJECT` | Silent skip |
| `session.created` | `POST /agents` — registers this session | Silent skip |
| `session.updated` | `PATCH /agents/{id}` — heartbeat | Silent skip |
| `session.deleted` | `DELETE /agents/{id}` — deregisters | Silent skip |

All HTTP calls have 3s timeout. All errors are caught silently — memoria being down never affects the session. The plugin never touches opencode.db or modifies any config files.

### Skill (agent instructions)

`~/.config/opencode/skills/memoria/SKILL.md` — tells every opencode agent:
- What memoria is and its 6-layer architecture
- All `!memoria` commands (memory, recall, tasks, agents, safety, topics)
- Session startup/shutdown checklists
- How the plugin handles automatic registration

Reference copies are kept at `opencode-integration/` in this repo.

## Configuration

| Env Var | Default | Purpose |
|---------|---------|---------|
| `MEMORIA_SERVER` | `http://100.126.64.13:19998` | REST server URL |
| `MEMORIA_PROJECT` | `$(basename $(pwd))` | Project name for memory scoping |
| `MEMORIA_PORT` | `19998` | Server port (server-side) |
| `MEMORIA_HOST` | `0.0.0.0` | Server bind address |

## Token Cost

| Component | Tokens | Frequency |
|-----------|--------|-----------|
| SKILL.md | ~800 | Once per session (prefix-cached) |
| `!memoria recall` | 300-500 | On-demand |
| `!memoria compress` | **Negative** | On-demand (saves tokens) |
| Server | 0 | Background, filesystem-only |

## Files

| File | Purpose |
|------|---------|
| `memoria.py` | CLI tool (HTTP client to REST server) |
| `memoriad_global.py` | REST server — memory + agents + tasks + safety + dashboard |
| `memoriad.py` | [DEPRECATED] Old per-project daemon |
| `compress.py` | Phase 1+2 compression engine |
| `context.py` | Active context state management |
| `README.md` | This file |
| `opencode-integration/` | Reference copies of opencode plugin + skill (installed at `~/.config/opencode/`) |

## Deployment

```bash
# On mediserv:
mkdir -p ~/memoria
# rsync or copy files from mediserv
uvicorn memoriad_global:app --host 0.0.0.0 --port 19998

# On any machine with network access to mediserv:
export MEMORIA_SERVER=http://100.126.64.13:19998
!memoria status
```

### systemd Service

Memoria runs as a systemd service for automatic startup:

```bash
# Service definition: /etc/systemd/system/memoria.service
#   WorkingDirectory:  /home/daivolt/memoria
#   ExecStart:         uvicorn memoriad_global:app --host 0.0.0.0 --port 19998
#   User:              daivolt
#   Restart:           always

# Enable (auto-start on boot):
sudo systemctl enable memoria

# Start/stop/status:
sudo systemctl start memoria
sudo systemctl stop memoria
sudo systemctl status memoria
```

Note: `PrivateTmp=true` and `ReadWritePaths=/var/tmp/memoria` are set for security.
The service does NOT use `ProtectHome` or `ProtectSystem` to allow git snapshot/rollback operations on project directories.

## Migration from v1

- v1 used per-project daemons (memoriad.py) that polled opencode.db individually
- v2 uses a single REST server (memoriad_global.py) that handles all projects
- All state still at `/var/tmp/memoria/` — compatible layout
- CLI commands unchanged — `memoria.py` now proxies to REST
- Daemon lifecycle commands (`init`, `stop`) are now no-ops / health checks
# agent-os auto-commit test
# agent-os auto-commit verification
