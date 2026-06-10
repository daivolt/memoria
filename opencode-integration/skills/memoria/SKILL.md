---
name: memoria
description: Persistent memory + multi-agent orchestration for opencode — durable facts, FTS5 session recall, task board, agent registry, git safety snapshots
license: MIT
compatibility: opencode
metadata:
  server: localhost:19998
  services: memoria, chitchat, memoria-tui
---

## What is Memoria

Memoria is a 6-layer persistent memory and agent orchestration system running on this machine (port 19998). It gives opencode sessions durable cross-session memory, a shared task board, agent registry, and git safety snapshots.

### Architecture

```
Safety Layer   — Git snapshots, rollback, commit tagging
Layer 6        — Task Board (cross-agent task delegation)
Layer 5        — Agent Registry (active sessions, file claims, PID heartbeat)
Layer 4        — Active Context (real-time task state, file locks)
Layer 3        — FTS5 Session Recall (SQLite FTS5, searches past sessions)
Layer 2        — Global Topics (cross-project knowledge, §-delimited)
Layer 1        — MEMORY.md (per-project durable facts, §-delimited, 5KB limit)
```

## Automatic Integration (plugin)

The memoria plugin (`~/.config/opencode/plugins/memoria.js`) handles these automatically:

| Action | When | What happens |
|--------|------|-------------|
| Agent registration | Session starts | `POST /agents` → appears on TUI dashboard |
| Heartbeat | Session updates | `PATCH /agents/{id}` → keeps agent active |
| Deregistration | Session ends | `DELETE /agents/{id}` → removed from dashboard |
| Env vars | Shell starts | `MEMORIA_SERVER`, `MEMORIA_PROJECT` injected |

You do NOT need to register manually — the plugin does it. But you should use `!memoria` commands for memory, recall, and tasks.

## Commands

All commands use the `memoria` CLI: `!memoria <command> [args]`

### Server
| Command | Action |
|---------|--------|
| `!memoria init` | Verify server connectivity |
| `!memoria status` | Server health + project memory info |
| `!memoria stop` | No-op (server is a persistent service) |

### Memory (Layer 1 — per-project durable facts)
| Command | Action |
|---------|--------|
| `!memoria add <text>` | Save a durable fact to this project's MEMORY.md |
| `!memoria list` | Show all facts for this project |
| `!memoria replace <old> <new>` | Replace a matching fact |
| `!memoria learnings` | Show accumulated knowledge from recent sessions |

### Recall (Layer 3 — SIRA-enriched session search across all past sessions)
| Command | Action |
|---------|--------|
| `!memoria recall <query>` | Search past sessions (SIRA-enriched: LLM expands query, DF-filtered, weighted similarity) |
| `!memoria review [N]` | Show last N session summaries (default: 3) |
| `!memoria context <query>` | Unified search across ALL surfaces: sessions, topics, memory, chitchat, papers, cortex |

### Topics (Layer 2 — cross-project global knowledge)
| Command | Action |
|---------|--------|
| `!memoria topics` | List all cross-project topics |
| `!memoria topic <name>` | Show facts in a topic |
| `!memoria topic <name> <text>` | Add a fact to a topic |
| `!memoria topic delete <name>` | Delete an entire topic |
| `!memoria topic edit <name> <idx> <text>` | Edit fact at index |
| `!memoria topic remove <name> <idx>` | Remove fact at index |
| `!memoria propose <topic> <text>` | Propose a new cross-project fact |
| `!memoria proposals` | List pending proposals |
| `!memoria proposals clear` | Clear all pending proposals |
| `!memoria accept <id>` | Accept a proposal → becomes topic fact |
| `!memoria reject <id>` | Reject a proposal |

### Orchestration (Layer 5+6 — agents + tasks)
| Command | Action |
|---------|--------|
| `!memoria tasks [project]` | List tasks (optionally filter by project) |
| `!memoria task <project> <title>` | Create a task on the board |
| `!memoria claim <task-id>` | Claim a pending task |
| `!memoria done <task-id> [result]` | Mark task complete |
| `!memoria fail <task-id> [error]` | Mark task failed |
| `!memoria agents [project]` | List active agents |

### Safety (Git snapshots)
| Command | Action |
|---------|--------|
| `!memoria snap <project> [msg]` | Create a git snapshot (stages + commits all changes) |
| `!memoria rollback <project> [id]` | Rollback to a previous snapshot |

### Chitchat (episodic chat memory)
| Command | Action |
|---------|--------|
| `!memoria chitchat rooms` | List tracked chat rooms |
| `!memoria chitchat history <room>` | Show recent chat messages |
| `!memoria chitchat consolidate` | Trigger hippocampal replay → topics proposals |

### Compression
| Command | Action |
|---------|--------|
| `!memoria compress` | Compress stdin tool output (saves tokens) — pipe tool output to this |

## Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `MEMORIA_SERVER` | `http://localhost:19998` | Memoria REST API URL |
| `MEMORIA_PROJECT` | `basename $(pwd)` | Project name for memory scoping |

These are injected automatically by the plugin — you generally don't need to set them.

## Session Checklist

### On start:
1. `!memoria status` — verify connectivity + check project memory
2. `!memoria list` — load this project's durable facts
3. `!memoria context <topic>` — search ALL surfaces (sessions, topics, papers, memory, chats) for relevant context (SIRA-powered)
4. `!memoria tasks` — check for pending tasks assigned to this project

### During session:
1. `!memoria add <fact>` — save any important findings or decisions
2. `!memoria recall <query>` — look up past work when needed (SIRA-enriched)
3. `!memoria context <query>` — multi-surface retrieval when you need broad context

### On end (before finishing):
1. `!memoria add <fact>` — save final facts or decisions
2. `!memoria done <task-id> <result>` — mark task complete with result summary
3. `!memoria fail <task-id> <error>` — mark task failed if blocked
4. `!memoria snap <project> "summary of what was done"` — create a safety snapshot

## REST API Quick Reference

For direct HTTP access (e.g., from plugin or scripts):

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/health` | Server status, session count, topics |
| `GET` | `/recall?q=<query>` | SIRA-enriched session + chat search |
| `POST` | `/context` | Unified multi-surface search (sessions, topics, memory, papers, cortex) |
| `GET` | `/review?n=N` | Recent session summaries |
| `GET/POST` | `/memory/{project}` | Read/add per-project facts |
| `PUT` | `/memory/{project}` | Replace a fact |
| `GET` | `/topics/search?q=<query>` | SIRA-enriched topic search |
| `GET/POST` | `/topics/{name}` | Read/add cross-project topic facts |
| `GET/POST` | `/proposals` | List/propose new facts |
| `POST` | `/proposals/{id}/accept` | Accept proposal |
| `DELETE` | `/proposals/{id}` | Reject proposal |
| `GET/POST/DELETE` | `/context/{project}` | Active task state |
| `POST` | `/agents` | Register agent session |
| `PATCH` | `/agents/{id}` | Agent heartbeat |
| `DELETE` | `/agents/{id}` | Deregister agent |
| `POST` | `/tasks` | Create task |
| `GET` | `/tasks` | List tasks |
| `PATCH` | `/tasks/{id}` | Update task status |
| `POST` | `/safety/snapshot/{project}` | Create git snapshot |
| `POST` | `/safety/rollback/{project}` | Rollback |
| `GET` | `/compress` | Compress tool output |
| `GET` | `/enrichment/stats` | Enrichment queue status (pending, done, errors) |
| `POST` | `/enrichment/reindex` | Re-enqueue all records for LLM enrichment |
| `POST` | `/papers/rescan` | Force rescan of papers/ directory |

## TUI Dashboard

An SSH-accessible terminal UI is available on port 23234:

```bash
ssh -p 23234 daivolt@100.126.64.13
```

Shows chat (left pane) and agents/tasks/memory/recall (right pane). Also runs locally:

```bash
memoria-tui          # local TUI
memoria-tui --sshd   # SSH server mode
```
