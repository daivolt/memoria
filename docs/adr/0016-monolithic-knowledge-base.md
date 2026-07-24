# ADR-0016: Monolithic Knowledge Base; AGENTS.md as Pointers

## Status

Accepted

## Date

2026-07-24

## Context

Knowledge is fragmented across 19 `AGENTS.md` files, 7 `.standards/` files, `pneural-context`, and `memoria`. The same infrastructure facts (machine table, SSH quoting rules, Cloudflare API patterns) are duplicated in root, global, and project `AGENTS.md` files. NotebookLM's `AGENTS.md` alone was 2,365 lines, burying tool-usage knowledge.

## Decision

1. Ingest all `AGENTS.md` and `.standards/` content into `pneural-context` as typed memory entries per project:
   - Critical red-ink rules → `priority: critical`, `memory_type: red`
   - Recurring workflows → `memory_type: procedural`
   - Infrastructure facts → `memory_type: concept`
   - Service/port mappings → `memory_type: relation`
   - Session events → `memory_type: temporal`
2. Author missing tool-usage procedures for browser automation, Sheets routing, and Bloomberg terminal workflows.
3. Rewrite each `AGENTS.md` to a thin pointer (~30 lines): project purpose, run command, red-ink pointer, and a note that the canonical KB lives in `pneural-context` project `<name>`.
4. Back up originals as `AGENTS.full.md` before rewriting.

## Consequences

### Positive

- Single source of truth for machine-readable knowledge.
- Smart context injects only relevant knowledge.
- System prompt size drops significantly.

### Negative

- Humans must query pneural-context dashboard or read `AGENTS.full.md` backups for full history.
- Ingestion script must be re-run when standards change.

### Neutral

- `AGENTS.md` files remain as human-readable entry points and project identity documents.
