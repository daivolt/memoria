# ADR-0011: Memoria as Episodic Source, Pneural as Injection Consumer

## Status

Accepted

## Date

2026-07-22

## Context

The two systems have ~90% code overlap in the "Paper Brain" layer (red ink, consolidation, decay, procedural, briefing, anchors, cost tracking) but serve fundamentally different purposes. Memoria is a multi-agent operating system with 15+ subsystems. Pneural-context is a focused context-injection layer for code generation. Without clear role separation, both systems try to own everything, leading to confusion about which system to use for which operation.

## Decision

Formalize the role boundary:

### Memoria Owns (Episodic Source)

- Episodic memory (hippocampal episodes, session history)
- Cross-project knowledge (topics, proposals)
- Agent orchestration (cortex RL, auction, task board, agent registry)
- Federation (multi-server replication)
- Social/cultural learning
- Grounded cognition (vision)
- Research paper indexing
- Safety (git snapshots)
- Chitchat (agent chat rooms)
- LLM provider management
- The consolidation pipeline (hippocampus -> cortex -> timeless), since it owns the episodic source data

### Pneural-Context Owns (Injection Consumer)

- The opencode plugin (context injection + compaction preservation + session recording)
- Semantic dedup (three-zone filter against live conversation)
- The briefing card assembler
- pgvector hybrid search (RRF)
- Neural embeddings generation and management
- The `pb_memory` table as a derived, project-local cache of consolidated context

### Boundary Contract

- Pneural-context's consolidation reads from memoria's episodic store via the bridge (ADR-0009), rather than maintaining its own independent episodic memory
- Pneural-context's session recording writes to memoria's episodic store via `POST /memory/{project}` (memoria stores it), and pneural caches the summary locally for injection
- Memoria does not call pneural-context for memory operations — it is the source, not the consumer
- Pneural-context calls memoria for recall enrichment (already partially implemented in the bridge)

### Practical Implications

- `pb_memory` entries with `source_system='memoria'` (from ADR-0010) are read-only — pneural does not decay or archive them independently; the source of truth is memoria
- `pb_memory` entries with `source_system='local'` are pneural-owned and subject to local decay/consolidation/archiving
- When pneural's consolidation promotes a `source_system='memoria'` entry to timeless, it notifies memoria to promote the equivalent entry there too

## Consequences

### Positive

- Clear ownership — no confusion about which system to update
- Memoria remains the long-term knowledge authority
- Pneural remains lightweight and focused on injection quality
- Eliminates the "which system do I add this fact to?" question (answer: either, it syncs)
- Eliminates the "which system do I query for recall?" question (answer: pneural for code-gen context, memoria for research)

### Negative

- Pneural's consolidation becomes dependent on memoria's API for source data
- If memoria is down, pneural can still inject cached context but cannot consolidate new memories from episodic source
- Requires careful `source_system` tracking to avoid double-decay or conflicting consolidation

### Neutral

- The `pb_*` MCP tool names remain the same in both systems — they serve different consumers (pneural MCP for code-gen, memoria MCP for research)
- Documentation must clarify when to use which system's API