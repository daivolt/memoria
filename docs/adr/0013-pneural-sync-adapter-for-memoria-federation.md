# ADR-0013: Pneural-Context Sync Adapter for Memoria Federation

## Status

Accepted

## Date

2026-07-22

## Context

Memoria has an existing federation system (`federation.py`) for peer-to-peer memory sync between memoria instances. It is file-based — it reads/writes markdown topic files, section-delimited MEMORY.md files, and JSONL session/proposal files. The changelog shape (topics as section-delimited markdown, memory as section-delimited MEMORY.md) does not map directly to pneural-context's PostgreSQL relational schema (`pb_memory` with `id`, `entry`, `priority`, `memory_type`, `strength` columns).

To integrate pneural-context into memoria's federation as a peer, we need an adapter that translates between the two storage formats.

## Decision

Add a pneural-context adapter in memoria's federation system that translates between file-based and relational formats.

### Adapter Location

`memoria/federation.py` — add `PneuralAdapter` class

### Adapter Responsibilities

1. **`get_changelog_for_pneural(since: float) -> dict`** — Convert memoria's file-based changelog into pneural-context's API format:
   - `memory` dict (project -> list of facts) -> list of `{"project": str, "text": str, "priority": str, "memory_type": str}` for POST to `http://localhost:8778/api/memory`
   - `topics` dict -> not synced (pneural has no topics concept)
   - `sessions` -> not synced (pneural has its own session recording)
   - `tasks` -> not synced
   - `proposals` -> not synced

2. **`apply_pneural_changelog(changelog: list[dict]) -> dict`** — Convert pneural-context's API response into memoria's file format:
   - Each entry `{"project": str, "text": str, "priority": str, "memory_type": str}` -> append to memoria's `MEMORY.md` for that project (section-delimited, with priority prefix)
   - Skip entries already present (dedup by normalized text)

3. **Registration**: Memoria's `POST /federation/peers` endpoint accepts pneural-context with `{"name": "pneural-context", "url": "http://localhost:8778", "adapter": "pneural"}`

4. **Sync flow**:
   - `sync_full("pneural-context", types=["memory"])` -> pull from pneural (GET `/api/memory/full?project={p}` for each project) + push to pneural (POST `/api/memory` for each new entry)
   - Only `memory` type is synced (not topics, sessions, tasks, proposals)

### Adapter vs. Direct Bridge

- The direct bridge (ADR-0009, ADR-0010) handles real-time push-on-write and periodic pull
- The federation adapter handles scheduled full-sync cycles and integrates pneural into memoria's existing peer management UI
- Both can coexist — the bridge is the primary path, the federation adapter is the fallback/audit path

## Consequences

### Positive

- Pneural-context appears in memoria's federation peer list — visible in the dashboard
- Reuses memoria's existing sync infrastructure (watermarks, conflict resolution)
- File-based format means memoria's MEMORY.md files get updated from pneural's relational store
- Only `memory` type syncs — clean boundary

### Negative

- Adds adapter complexity to `federation.py` (already a large module)
- Format translation has edge cases (markdown section-delimited vs. relational rows)
- Two sync mechanisms (direct bridge + federation adapter) could conflict — must coordinate via the same watermark

### Neutral

- Federation adapter is optional — the direct bridge (ADR-0009) can work without it
- The adapter makes pneural a first-class peer in memoria's ecosystem, which may be useful for multi-server setups