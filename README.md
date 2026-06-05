# memoria — OpenCode Session Memory & Pattern Learning

Persistent memory system for opencode that learns from past sessions.

## Architecture

```
┌─────────────┐     polls every 30s     ┌──────────────────┐
│  opencode    │ ←────────────────────── │   memoriad.py    │
│  SQLite DB   │                         │  (background daemon)│
│  9.2GB       │ ──────────────────────→ │  extracts sessions│
│  946 sess.   │                         │  builds FTS5 index│
└─────────────┘                          └────────┬─────────┘
                                                  │ writes
                                                  ↓
┌──────────────────────────────────────────────────┐
│           /var/tmp/memoria-<PROJECT>/             │
│  ┌────────────┐  ┌──────────┐  ┌──────────┐     │
│  │ MEMORY.md  │  │session.  │  │index.db  │     │
│  │ durable    │  │ jsonl    │  │ FTS5     │     │
│  │ facts      │  │records   │  │ search   │     │
│  └────────────┘  └──────────┘  └──────────┘     │
└──────────────────────────────────────────────────┘
         ↑ read/write via CLI      ↑ read via CLI
         └──────── memoria.py ─────┘
```

**Two processes:**

1. **`memoriad.py`** — Background daemon. Polls opencode's SQLite DB every 30s
   for newly archived sessions. Extracts: user task → tools → outcome.
   Writes structured records to `sessions.jsonl` + FTS5 index.

2. **`memoria.py`** — CLI invoked by the LLM via bash. Commands:
   - `memoria add`, `list`, `replace` — manage MEMORY.md durable facts
   - `memoria recall <query>` — search past sessions via FTS5
   - `memoria review [N]` — show last N session summaries
   - `memoria learnings` — accumulated project knowledge
   - `memoria compress` — strip verbose tool output to 1-liners (stdin)
   - `memoria init | stop | status` — manage daemon lifecycle

## Learning Mechanism (How It "Learns")

Memoria does NOT modify model weights. It uses 4 Hermes-inspired techniques:

### 1. Durable Fact Storage (MEMORY.md)
The LLM saves facts via `!memoria add` when it discovers:
- User preferences ("daivolt prefers concise responses")
- Project conventions ("all router endpoints need _audit()")
- Environment quirks ("VPN causes 2s delay on first API call")

Facts are declarative ("User prefers X"), not imperative ("Always do X").
5KB character limit forces curation — LLM must replace/remove when full.

### 2. Cross-Session Recall (FTS5)
When starting a complex task, the LLM runs `!memoria recall <task>`.
FTS5 searches all past session summaries, returns relevant matches.
The LLM reads these before starting work — informed context.

### 3. Context Compression
When sessions grow long (tool outputs accumulate), `!memoria compress`
replaces verbose blocks with 1-line summaries:
```
[grep] ran `grep -r "timeout" src/` → exit 0, 23 matches
```

Phase 1 is zero-LLM-cost regex. The compressed version replaces the
original, saving thousands of tokens.

### 4. Auto-Indexing (Daemon)
Every 30s the daemon checks for new archived sessions, extracts
structured records, updates FTS5. No LLM cost — pure SQL.

## Token Cost

| Component | Tokens | Frequency |
|-----------|--------|-----------|
| SKILL.md | ~600 | Once per session (prefix-cached) |
| `!memoria recall` | 300-500 | On-demand |
| `!memoria compress` | **Negative** | On-demand (saves tokens) |
| Daemon | 0 | Always, filesystem-only |

## Files

| File | Purpose |
|------|---------|
| `memoria.py` | CLI tool invoked by LLM |
| `memoriad.py` | Background daemon |
| `README.md` | This file |

## Hermes Import

If Hermes memories exist at `~/.hermes/memories/MEMORY.md`, first
`!memoria init` imports them tagged with `[hermes]` prefix — zero
context cost until needed.

## Requirements

- Python 3.10+
- No external dependencies (stdlib only: sqlite3, json, asyncio)
- Read access to opencode SQLite DB at `~/.local/share/opencode/opencode.db`
