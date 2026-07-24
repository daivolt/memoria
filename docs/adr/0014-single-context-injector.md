# ADR-0014: Single Context Injector

## Status

Accepted

## Date

2026-07-24

## Context

Two opencode plugins currently inject context into the system prompt:

- `pneural-context-plugin` injects a `PNEURAL_CTX` block from port 8778.
- `memoria-plugin` injects a `MEMORIA_CTX` block from port 19998.

This duplicates the attention budget, creates conflicting memory systems, and makes it impossible to guarantee a single coherent context ordering. The LLM must juggle two markers, two caching layers, and two sources of truth.

## Decision

`pneural-context-plugin` becomes the sole system-prompt injector. `memoria-plugin` retains its non-injection responsibilities:

- Agent registry/heartbeat via `/agents` endpoints.
- Active session bridge file (`/var/tmp/memoria/bridge_active_session.json`).
- `experimental.session.compacting` preservation hook (marker no longer emitted).

The `/ctx/:project` endpoint in memoria remains available for other consumers, but opencode no longer reads it directly.

## Consequences

### Positive

- One canonical context assembly path.
- `pneural-context` can merge memoria recall hits into its briefing instead of duplicating them.
- Easier debugging: a single marker, a single cache.

### Negative

- Temporary coupling: `pneural-context` must query memoria for session search.
- Any external tools depending on `MEMORIA_CTX` marker must migrate.

### Neutral

- `memoria` continues to index sessions and provide recall; only the client-side injection is removed.
