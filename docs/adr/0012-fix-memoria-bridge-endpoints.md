# ADR-0012: Fix MemoriaBridge Endpoint Paths

## Status

Accepted

## Date

2026-07-22

## Context

The existing `MemoriaBridge` class in `pneural_context/pb_memoria.py` (49 lines) has two methods:

1. `recall()` — calls `{self.url}/api/recall` — **WRONG**: memoria has no `/api` prefix; correct path is `/recall`
2. `get_sessions()` — calls `{self.url}/api/sessions` — **WRONG**: memoria has no `/sessions` endpoint at all; session data is under `/review`

The bridge is currently disabled (`PNEURAL_MEMORIA_ENABLED=false`), so these bugs haven't surfaced. With ADR-0009 enabling the bridge, these must be fixed first.

Additionally, the bridge needs new methods to support the bidirectional sync protocol (ADR-0010):
- `add_memory(project, text, priority, memory_type)` -> POST to memoria
- `get_memory_full(project)` -> GET memoria's full memory entries
- `get_red_ink(project, min_strength)` -> GET memoria's red ink
- `get_context(project)` -> GET memoria's injection context
- `trigger_consolidation(project)` -> POST to memoria's consolidation trigger

## Decision

Rewrite `MemoriaBridge` with correct endpoint paths and full method coverage.

### Fixed Endpoint Mapping

| Method | Current (broken) | Fixed |
|--------|-------------------|-------|
| `recall()` | `GET /api/recall` | `GET /recall` |
| `get_sessions()` | `GET /api/sessions` | `GET /review?project={project}` |

### New Methods

| Method | HTTP | Path | Request body |
|--------|------|------|-------------|
| `add_memory()` | POST | `/memory/{project}` | `{"text": str, "priority": str, "memory_type": str}` |
| `get_memory_full()` | GET | `/memory/{project}/full` | — |
| `get_red_ink()` | GET | `/red-ink/{project}?min_strength={f}` | — |
| `get_context()` | GET | `/ctx/{project}` | — |
| `trigger_consolidation()` | POST | `/consolidation/{project}/trigger` | — |
| `register_peer()` | POST | `/federation/peers` | `{"name": "pneural-context", "url": "http://localhost:8778"}` |

### Error Handling

- All methods catch `httpx.HTTPStatusError` and `httpx.RequestError`
- Return `None` or empty list on failure (logged at WARNING level)
- No retry logic in the bridge itself — the sync loop (ADR-0010) provides retry via periodic pull

## Consequences

### Positive

- Bridge becomes functional and useful for the sync protocol
- All memory operations available cross-system
- Clean error handling with no silent failures

### Negative

- Bridge grows from 49 lines to ~150 lines
- New methods must be tested against a running memoria instance (integration tests)
- Memoria's API may evolve — bridge must track changes

### Neutral

- Bridge remains optional (`PNEURAL_MEMORIA_ENABLED=false` still works)
- Existing recall router integration pattern (check `app.state.memoria`, call method, catch exception, log warning) is extended to new methods