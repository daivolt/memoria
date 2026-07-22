# ADR-0009: Memoria-Pneural Context Bridge Architecture

## Status

Accepted

## Date

2026-07-22

## Context

Two systems serve overlapping but distinct purposes:

- **Memoria** (port 19998, PostgreSQL `memoria` DB) — long-term memory, research engine, and multi-agent operating system. Owns episodic memory (hippocampal episodes), cross-project topics, proposals, federation, social learning, cortex RL auction, chitchat, papers, and safety snapshots.
- **Pneural-Context** (port 8778, PostgreSQL `pneural_context` DB) — contextual retrieval for code generation. Owns the opencode plugin (context injection, compaction preservation, session recording), pgvector hybrid search (RRF), and semantic context dedup (three-zone filter).

Both systems implement the same "Paper Brain" feature set (red ink, 3-tier consolidation, Ebbinghaus decay, procedural memory, typed sections, briefing, anchors, cost tracking) against separate databases. The `PLAN_PAPER_BRAIN.md` was written inside memoria, but the features were implemented as a standalone project (pneural-context) with the same `pb_*` MCP tool names. Memoria then also got partial implementations of the same features.

The existing one-way bridge (`pb_memoria.py`) is minimal (recall + get_sessions only) and broken — it calls `/api/recall` and `/api/sessions` but memoria's routes have no `/api` prefix and no `/sessions` endpoint exists. The bridge is currently disabled (`PNEURAL_MEMORIA_ENABLED=false`, `PNEURAL_MEMORIA_URL=` empty).

Facts added via memoria do not appear in pneural-context's injection and vice versa. The two systems are siloed duplicates.

## Decision

Establish a bidirectional HTTP bridge between memoria and pneural-context. Both systems remain independent with their own databases, but a sync layer ensures memory entries, red ink facts, and consolidated knowledge appear in both stores.

### Bridge Topology

```
memoria (19998)                         pneural-context (8778)
┌─────────────────────┐                ┌─────────────────────┐
│ project_memory     │                │ pb_memory           │
│ consolidated_memory │◄──── bridge ──►│ pb_consolidated     │
│ procedural_memory  │                │ pb_procedural       │
│ topics             │                │                     │
│ hippocampal episodes│               │ pgvector embeddings │
│ cortex RL          │                │ smart dedup         │
│ federation         │                │ opencode plugin     │
└─────────────────────┘                └─────────────────────┘
        ▲                                        ▲
        │              sync layer                │
        └────────────────────────────────────────┘
```

### Configuration

- `PNEURAL_MEMORIA_ENABLED=true`
- `PNEURAL_MEMORIA_URL=http://localhost:19998`
- `PNEURAL_SYNC_INTERVAL_SECONDS=300` (5-minute sync loop)
- Memoria registers pneural-context as a federation peer with `POST /federation/peers`

### What Syncs

| Direction | Data | Trigger |
|-----------|------|---------|
| pneural -> memoria | New `pb_memory` entries | On insert (push) + periodic |
| memoria -> pneural | New `project_memory` entries | Periodic pull from `/memory/{project}/full` |
| pneural -> memoria | Red ink priority changes | On `PATCH /api/memory/{index}/priority` |
| pneural -> memoria | Consolidation results | After `run_consolidation()` completes |
| Both | Recall queries | Real-time (already partially implemented) |

### What Does NOT Sync

- Memoria-only: topics, proposals, cortex RL state, hippocampal episodes, federation peers, social learning, chitchat, safety snapshots
- Pneural-only: pgvector embeddings, smart dedup conversation cache, session recordings, error logs

## Consequences

### Positive

- Eliminates siloed duplication — facts added in either system appear in both
- Each system retains its unique strengths (memoria: AgentOS + research; pneural: neural search + injection)
- No schema changes required in either database
- Graceful degradation — if one system is down, the other continues independently
- Existing opencode plugins and MCP servers work unchanged

### Negative

- Eventual consistency (5-minute sync window) instead of immediate
- Network dependency between the two services — if memoria is down, pneural's push will fail (must be retried/resilient)
- Two PostgreSQL databases to maintain and back up
- Sync conflicts possible if the same fact is added to both systems simultaneously (resolved via text-content hashing dedup)

### Neutral

- The bridge is a new operational component that needs monitoring
- Both systems' MCP servers expose the same `pb_*` tools — consumers must know which to call (pneural for code-gen context, memoria for long-term research)