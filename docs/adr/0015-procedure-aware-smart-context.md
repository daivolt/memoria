# ADR-0015: Procedure-Aware Smart Context

## Status

Accepted

## Date

2026-07-24

## Context

`/api/context/smart` currently deduplicates memory entries based on conversation text. It does not surface `procedures`, which capture recurring task patterns (e.g., "use `sheets-reader` for Google Sheets reads"). Tool-usage instructions are therefore either absent or buried in large `AGENTS.md` files, causing the LLM to hallucinate tool selection.

## Decision

Extend `/api/context/smart` to match the conversation against stored procedures using the existing `search_procedures` function (similarity threshold ≥ 0.7). Include the top 3 matched procedures in the returned context under a dedicated `## PROCEDURES (matched)` section.

Implementation notes:

- Procedure text is rendered as an ordered step list.
- If fewer than 3 procedures match, return only those above threshold.
- The endpoint response shape gains a `procedures` field parallel to `entries`.
- The plugin renders the procedure section after red ink and before concepts.

## Consequences

### Positive

- Tool-usage knowledge surfaces exactly when the conversation needs it.
- Reduces static prompt size (no need to inject all procedures).
- Works with the existing decay/consolidation pipeline.

### Negative

- Adds one more DB query per context fetch.
- Procedure quality becomes critical; poorly written procedures will mislead.

### Neutral

- `/api/context` (non-smart) remains unchanged for backward compatibility.
