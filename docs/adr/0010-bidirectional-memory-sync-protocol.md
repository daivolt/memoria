# ADR-0010: Bidirectional Memory Sync Protocol

## Status

Accepted

## Date

2026-07-22

## Context

With the bridge established (ADR-0009), we need a concrete protocol for how memory entries flow between the two systems. Both use different table schemas, different API shapes, and different primary keys. The existing pneural dedup uses SequenceMatcher fuzzy matching (threshold 0.8). Memoria has no dedup. We need to prevent sync loops (A pushes to B, B pushes back to A, repeat).

## Decision

Define a pull-based sync protocol with content-hash dedup and a sync watermark.

### Protocol

1. **Watermark tracking**: Each system stores `last_sync_timestamp` per project per peer. Pneural uses a new `pb_sync_state` table; memoria uses its existing `sync_metadata` table.

2. **Pull cycle** (every `PNEURAL_SYNC_INTERVAL_SECONDS`, default 300):
   - Pneural calls `GET http://localhost:19998/memory/{project}/full` for each project
   - For each entry returned, compute SHA-256 hash of normalized text content
   - Compare against local `pb_memory` entries (also hashed)
   - If hash not found locally, insert via `add_memory_entry` (which applies fuzzy dedup)
   - Update watermark to memoria's server timestamp
   - Memoria calls `GET http://localhost:8778/api/memory/full?project={project}` for each project
   - Same hash + dedup process in reverse
   - Update watermark

3. **Push on write** (best-effort, fire-and-forget):
   - Pneural `add_memory_entry` → after local insert, POST to `http://localhost:19998/memory/{project}` with `{"text": entry, "priority": priority, "memory_type": memory_type}`
   - Memoria `add memory` → after local insert, POST to `http://localhost:8778/api/memory` with `{"project": project, "text": entry, "priority": priority, "memory_type": memory_type}`
   - Failures logged but do not block the local write

4. **Loop prevention**:
   - Each sync-injected entry is tagged with `source_system` in a new `pb_sync_source` column (pneural) or metadata field (memoria)
   - When pulling, entries where `source_system == 'pneural'` are skipped by pneural's pull (and vice versa)
   - This prevents the A→B→A ping-pong

5. **Conflict resolution**:
   - Text-content hash dedup: if an entry with the same normalized text exists, skip (no update)
   - Priority changes: if a red ink entry's priority differs, take the higher priority (critical > important > normal)
   - Strength/decay: not synced — each system maintains its own decay state

### Data Mapping

| Pneural field | Memoria field | Notes |
|---------------|---------------|-------|
| `pb_memory.entry` | `project_memory.entry` | Direct text copy |
| `pb_memory.priority` | `project_memory.priority` | Same enum (critical/important/normal) |
| `pb_memory.memory_type` | `project_memory.memory_type` | Same enum (red/concept/procedural/temporal/relation) |
| `pb_memory.project` | `project_memory.project` | Direct |
| `pb_memory.id` | — | Not preserved (each system has own PK) |
| `pb_memory.strength` | `project_memory.strength` | NOT synced (local decay only) |
| `pb_memory.embedding` | — | NOT synced (each system generates own embeddings) |
| `pb_memory.search_enrichments` | — | NOT synced |

## Consequences

### Positive

- Simple, robust protocol — pull ensures eventual consistency even if pushes fail
- Content-hash dedup prevents duplicates across systems
- `source_system` tagging prevents sync loops
- No lock or consensus needed — both systems can write independently

### Negative

- New `pb_sync_source` column needed in pneural (`ALTER TABLE pb_memory ADD COLUMN pb_sync_source VARCHAR(20) DEFAULT 'local'`)
- Memoria needs equivalent — use existing `sync_metadata` table or add a column to `project_memory`
- 5-minute window where entries exist in only one system
- No conflict resolution for same-text-different-priority beyond "take higher" heuristic

### Neutral

- Sync state tracking adds minor DB overhead (one timestamp per project per peer)