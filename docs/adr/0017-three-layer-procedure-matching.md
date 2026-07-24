# ADR-0017: Three-Layer Procedure Matching + Standalone Injection

## Status

Accepted

## Date

2026-07-24

## Context

ADR-0015 added procedure matching to smart context, but two structural limits
remained:

1. **Gated injection** — the opencode plugin only rendered procedures when at
   least one memory entry matched. A conversation matching *only* a procedure
   (the exact hallucination scenario) injected nothing.
2. **No semantic layer** — token overlap fails on paraphrases
   ("automate logging into a website that requires MFA" vs the
   "browser automation, CDP, session manager" procedure shares almost no
   tokens). Additionally, ~88% of memory rows and all seeded procedures had
   NULL embeddings, so no vector search was possible at all.
3. **Noise** — an early RRF-based fusion returned long-tail vector hits
   (score ~0.016) that would have injected wrong procedures.

## Decision

1. **Three-layer matcher** (`_match_procedures`):
   - **Token overlap** (deterministic, no embeddings needed) — primary.
   - **Vector semantic search** gated by `VECTOR_FLOOR = 0.55` cosine
     similarity. Calibrated on 5 probe conversations against nomic-embed-text:
     relevant paraphrases >= 0.58, adjacent noise 0.44-0.53, unrelated <= 0.39.
     Precision over recall because context pollution causes hallucination.
   - **Trigram** as last-resort fallback for short keyword queries.
2. **Standalone injection** — procedures match and render even when zero
   memory entries match, in all endpoint paths (`full`, `full_fallback`,
   `smart_dedup`), including when the embedding service is down.
3. **Embedding backfill** — `reindex_table` truncates source text to 2000
   chars for the embedding context window; full content stays in the row.
   Backfilled to 100% coverage: 354/354 memory, 15/15 procedures.

## Consequences

### Positive

- Procedures surface for paraphrased requests (vector) without polluting
  context (floor), and always inject even with no memory matches.
- The system degrades gracefully: embeddings down → token + trigram still work.
- 100% embedding coverage enables semantic recall across the whole KB.

### Negative

- Vector floor is model-specific; changing embed models requires
  recalibration of `VECTOR_FLOOR`.
- Backfill must re-run for entries created with the embedding client off.

### Neutral

- Truncation applies only to the vector representation; stored content is
  never modified.
