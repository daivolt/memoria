"""
Paper Brain MCP Server — Help Texts

Module-specific help strings for the pb_help tool.
Each module has an overview, tool list, usage guide, and dependencies.
"""

HELP_OVERVIEW = """\
# Paper Brain — External Memory System for LLMs

Inspired by Junko Mizuta's "paper brain" notebook system for navigating life
with 7-second memory. Implements multi-tier memory with criticality tagging,
automatic consolidation, procedural behavioral reinforcement, and graceful
forgetting.

## How It Works

The LLM has "anterograde amnesia" — every session starts from scratch and
compaction loses context. Paper Brain solves this with multiple memory systems
mirroring the brain:

- **Red Ink** (amygdala) — Critical facts that MUST survive compaction
- **Briefing Cards** (pre-mapping) — Task-specific context before starting work
- **Procedural Memory** (basal ganglia) — Learned paths that work, reinforced by success
- **Typed Sections** (cortex organization) — Memory classified by type
- **Nightly Consolidation** (sleep) — Hippocampus → cortex transfer
- **Anchors** (environmental reminders) — Visible proof of recent actions
- **Decay** (Ebbinghaus curve) — Graceful forgetting of unused memories
- **Cost Analytics** — Track token savings from selective injection

## Modules

### 1. Core Memory (PB_MEMORY=true) — always recommended ON
Base memory storage. All other modules depend on entries existing here.

Tools:
- pb_add_memory — Add a memory entry (with optional priority and type)
- pb_get_memory — Get all entries (text only)
- pb_get_full_memory — Get all entries with full metadata
- pb_replace_memory — Replace an entry by substring match
- pb_get_context — Get assembled injection context (what the plugin injects)

### 2. Recall (PB_RECALL=true)
Unified multi-surface recall across sessions, chat messages, and memory entries.
LLM-driven query enrichment for better matches.

Tools:
- pb_recall — Search across sessions, chats, and memory with enrichment

### 3. Red Ink (PB_RED_INK=true) — Phase 1A
Critical facts tagged with priority='critical'. Never compressed, always
injected verbatim. Minimum strength 0.5 (never fully decays).

Tools:
- pb_get_red_ink — List critical entries
- pb_set_priority — Set priority (critical/important/normal) on an entry
- pb_touch_entry — Refresh access timestamp (prevents decay)
- pb_boost_entry — Boost strength by 0.3 (spaced repetition)

### 4. Briefing (PB_BRIEFING=true) — Phase 1B
Task-specific context pre-mapping. Before starting a task, generates a briefing
card with hippocampal recall, topic facts, session search, cultural memory,
procedural steps, and lessons.

Tools:
- pb_briefing — Generate a briefing card for a task description
- pb_get_briefing_anchors — Get environmental anchors (recent tasks, commits, edits, red-ink)

### 5. Procedural (PB_PROCEDURAL=true) — Phase 1C
Behavioral reinforcement — stores proven paths (not documentation). Wrong turns
stay as negative episodic examples. Reinforcement score = accuracy × log(success+1).
Retired automatically when fail_count > success_count × 2 AND fail_count > 5.

Tools:
- pb_list_procedures — List all procedures for a project
- pb_add_procedure — Manually add a procedure
- pb_search_procedures — Search by task description (similarity ≥ 0.7)
- pb_procedure_outcome — Record success/fail/partial outcome
- pb_retire_procedure — Retire a procedure manually

### 6. Typed Sections (PB_TYPED_SECTIONS=true) — Phase 2A
Memory classification: red (critical), concept (architectural), procedural
(how-to), temporal (time-stamped), relation (dependency). Auto-classify via LLM.

Tools:
- pb_classify_memory — Auto-classify all entries via LLM enrichment
- pb_set_type — Manually set type on an entry

### 7. Consolidation (PB_CONSOLIDATION=true) — Phase 2B
Three-tier memory: immediate (last session) → consolidated (7-day insights) →
timeless (promoted from consolidated). Runs every sleep cycle (6h default).

Promotion rules:
- Red-ink entries → immediately promoted to timeless
- Entries with strength ≥ 0.8 (accessed 3+ times) → promoted to timeless
- Entries in ≥3 source sessions → promoted to timeless
- Hippocampal episodes with reward ≥ 0.7 → promoted to cultural memory
- Cross-project overlapping entries → generates cross-project topic proposals

Tools:
- pb_trigger_consolidation — Run the consolidation pipeline now
- pb_get_consolidation — Get entries (optionally by tier)
- pb_consolidation_status — Show tier counts and status

### 8. Anchors (PB_ANCHORS=true) — Phase 3A
Environmental reminders — visible proof of recent actions. Auto-generated from
task state, git commits, file edits, and red-ink reminders.

Tools:
- pb_get_anchors — Get anchors (independent of briefing)

### 9. Decay (PB_DECAY=true) — Phase 4A
Ebbinghaus forgetting curve. Every sleep cycle: strength *= 0.95. Accessing an
entry boosts strength += 0.3 (capped at 1.0). Red-ink entries have minimum
strength = 0.5. Entries below 0.1 strength → archived (still searchable, not
injected by default).

Tools:
- pb_decay_status — Show which entries are fading
- pb_search_archive — Search forgotten/archived entries

### 10. Cost Analytics (PB_COSTS=true) — Phase 4B
Track token costs to prove graceful forgetting saves money without hurting quality.
Measures injection cost, savings from selective injection, savings from forgetting,
and effectiveness (success rate vs context size).

Tools:
- pb_cost_analysis — Full cost report with effectiveness metrics
- pb_cost_trends — Raw per-record trend data for charting
- pb_record_cost — Record a cost observation

## Toggle Any Module

Set PB_<MODULE>=false in environment to disable. Examples:
- Only red ink: PB_MEMORY=true PB_RECALL=true PB_RED_INK=true (rest false)
- Procedural testing: PB_MEMORY=true PB_PROCEDURAL=true PB_CONSOLIDATION=true
- Everything: all PB_*=true (default)"""

HELP_MEMORY = """\
# Core Memory (PB_MEMORY=true) — Base Memory Storage

The foundation of Paper Brain. All other modules depend on entries existing here.
Stores text entries with metadata: priority, type, strength, timestamps.

## Tools (5)

### pb_add_memory
Add a memory entry with optional priority and type.
Args: project (str), text (str), priority (str, default "normal": "critical"/"important"/"normal"), memory_type (str, optional: "red"/"concept"/"procedural"/"temporal"/"relation")
Returns: {ok: true, index: <int>}

### pb_get_memory
Get all memory entries for a project (text only, no metadata).
Args: project (str)
Returns: list of entry texts

### pb_get_full_memory
Get all memory entries with full metadata (type, priority, strength, timestamps).
Args: project (str)
Returns: list of entries with all fields

### pb_replace_memory
Replace a memory entry containing a substring with new text.
Args: project (str), old (str), new (str)
Returns: {ok: true, replaced: <int>}

### pb_get_context
Get assembled injection context — the full markdown + typed sections + red ink
that the plugin injects into the system prompt.
Args: project (str)
Returns: formatted context string

## How To Use

1. Add entries: pb_add_memory(project, "Important fact about X")
2. Make critical: pb_add_memory(project, "CRITICAL: never delete this", priority="critical")
3. View all: pb_get_memory(project) for text, pb_get_full_memory(project) for metadata
4. Fix entries: pb_replace_memory(project, "old text", "corrected text")
5. See what gets injected: pb_get_context(project)

## Dependencies
- None — this is the base module"""

HELP_RECALL = """\
# Recall (PB_RECALL=true) — Unified Multi-Surface Search

Search across sessions, chat messages, and memory entries with LLM-driven
query enrichment. The LLM expands your query to find better matches.

## Tools (1)

### pb_recall
Search across sessions, chats, and memory with enrichment.
Args: q (str), project (str, optional), limit (int, default 5), source (str, optional: "sessions"/"chats"), enrich (bool, default true), boost (bool, default true)
Returns: search results from multiple sources

## How To Use

1. Basic search: pb_recall(q="database connection issue")
2. Limit results: pb_recall(q="auth bug", limit=10)
3. Source filter: pb_recall(q="deployment steps", source="sessions")
4. No enrichment: pb_recall(q="exact term", enrich=false)
5. No boost: pb_recall(q="read-only query", boost=false)

## Dependencies
- Requires PB_MEMORY=true (recalls from memory entries)"""

HELP_RED_INK = """\
# Red Ink (PB_RED_INK=true) — Phase 1A: Criticality Tagging

Critical facts tagged with priority='critical'. Never compressed, always
injected verbatim. Inspired by Junko Mizuta's red ink highlighting — critical
info visually distinct in her notebooks.

Red-ink entries:
- Never get compressed (always injected verbatim)
- Have minimum strength = 0.5 (never fully decay)
- Are immediately promoted to timeless tier during consolidation
- Are excluded from ancient episode pruning

## Tools (4)

### pb_get_red_ink
Get all critical entries for a project.
Args: project (str), min_strength (float, default 0.0)
Returns: list of entries with id, text, strength

### pb_set_priority
Set priority level on a memory entry.
Args: project (str), index (int), priority (str: "critical"/"important"/"normal")
Returns: {ok: true}

### pb_touch_entry
Refresh access timestamp on an entry (prevents decay).
Args: project (str), index (int)
Returns: {ok: true}

### pb_boost_entry
Boost an entry's strength by 0.3 (spaced repetition). Capped at 1.0.
Args: project (str), idx (int)
Returns: {ok: true}

## How To Use

1. Add a memory entry, then promote it: pb_set_priority(project, index, "critical")
2. Or use pb_add_memory with priority="critical" directly
3. Red-ink entries appear in system prompt as <RED_INK_CTX> block (via plugin)
4. They also appear in anchors as "CRITICAL reminders" (via plugin)
5. They survive compaction — the plugin instructs summarizer to preserve them

## Dependencies
- Requires PB_MEMORY=true (entries must exist)
- Consolidation promotes red-ink to timeless automatically"""

HELP_BRIEFING = """\
# Briefing (PB_BRIEFING=true) — Phase 1B: Task Briefing Cards

Task-specific context pre-mapping. Before starting a task, generate a briefing
card with hippocampal recall, topic facts, session search, cultural memory,
procedural steps, and lessons learned.

## Tools (2)

### pb_briefing
Generate a briefing card for a task description.
Args: task_description (str), project (str), max_tokens (int, default 2000)
Returns: formatted briefing card with context sections

### pb_get_briefing_anchors
Get environmental anchors: active/completed tasks, recent commits,
most-edited files, red-ink reminders.
Args: project (str)
Returns: anchors object with tasks, commits, files, red_ink fields

## How To Use

1. Before starting work: pb_briefing(task_description="Fix the login auth bug", project="myapp")
2. Check environment: pb_get_briefing_anchors(project="myapp")
3. Briefing aggregates: hippocampal recall, topic search, session search,
   cultural memory, procedural steps, and lessons learned

## Dependencies
- Requires PB_MEMORY=true (briefing reads memory entries)
- Benefits from PB_PROCEDURAL=true (includes procedural steps)
- Benefits from PB_ANCHORS=true (includes environmental context)"""

HELP_PROCEDURAL = """\
# Procedural (PB_PROCEDURAL=true) — Phase 1C: Behavioral Reinforcement

Stores proven paths — NOT documentation. Wrong turns stay as negative episodic
examples. Reinforcement score = accuracy × log(success+1).
Retired automatically when fail_count > success_count × 2 AND fail_count > 5.

## Tools (5)

### pb_list_procedures
List all procedures for a project.
Args: project (str)
Returns: list of procedures with id, pattern, steps, reinforcement score

### pb_add_procedure
Manually add a procedure.
Args: project (str), task_pattern (str), steps (list[str]), task_type (str, optional)
Returns: {ok: true, id: <int>}

### pb_search_procedures
Search procedures by task description. Uses similarity threshold ≥ 0.7.
Args: project (str), query (str), limit (int, default 5)
Returns: matching procedures with similarity scores

### pb_procedure_outcome
Record the outcome of applying a procedure. Drives reinforcement score.
Args: project (str), proc_id (int), outcome (str: "success"/"fail"/"partial")
Returns: {ok: true}

### pb_retire_procedure
Retire a procedure. It will no longer appear in briefings/search.
Args: project (str), proc_id (int)
Returns: {ok: true}

## How To Use

1. Before a task: pb_search_procedures(project, query="deploy to staging")
2. After success: pb_procedure_outcome(project, proc_id=5, outcome="success")
3. After failure: pb_procedure_outcome(project, proc_id=5, outcome="fail")
4. Manual add: pb_add_procedure(project, task_pattern="deploy to staging", steps=["git push", "ssh server", "pm2 restart"])
5. Auto-creation happens on task completion (via enrichment)

## Dependencies
- Requires PB_MEMORY=true (procedures reference memory entries)"""

HELP_TYPED_SECTIONS = """\
# Typed Sections (PB_TYPED_SECTIONS=true) — Phase 2A: Memory Classification

Memory classification into types:
- **red** — Critical facts (equivalent to priority=critical)
- **concept** — Architectural understanding (how things fit together)
- **procedural** — How-to steps (references procedural memory)
- **temporal** — Time-stamped events (when things happened)
- **relation** — Dependencies and connections (X requires Y)

Auto-classification uses LLM enrichment to assign types based on content.

## Tools (2)

### pb_classify_memory
Auto-classify all memory entries into types via LLM enrichment.
Args: project (str)
Returns: {ok: true, classified: <int>, types: {<type>: <count>}}

### pb_set_type
Manually set the type of a memory entry.
Args: project (str), index (int), memory_type (str: "red"/"concept"/"procedural"/"temporal"/"relation")
Returns: {ok: true}

## How To Use

1. Auto-classify: pb_classify_memory(project="myapp")
2. Manual override: pb_set_type(project="myapp", index=3, memory_type="concept")
3. Types affect injection — typed sections are organized by type in context

## Dependencies
- Requires PB_MEMORY=true (classifies memory entries)"""

HELP_CONSOLIDATION = """\
# Consolidation (PB_CONSOLIDATION=true) — Phase 2B: Nightly Consolidation

Three-tier memory architecture:
- **immediate** — Last session's entries (fresh, volatile)
- **consolidated** — 7-day insights merged from multiple sessions
- **timeless** — Promoted from consolidated (permanent, never pruned)

Promotion rules:
- Red-ink entries → immediately promoted to timeless
- Entries with strength ≥ 0.8 (accessed 3+ times) → promoted to timeless
- Entries in ≥3 source sessions → promoted to timeless
- Hippocampal episodes with reward ≥ 0.7 → promoted to cultural memory
- Cross-project overlapping entries → generates cross-project topic proposals

## Tools (3)

### pb_trigger_consolidation
Run the consolidation pipeline now. Creates immediate tier, extracts insights,
promotes entries, archives old temporal entries.
Args: project (str)
Returns: consolidation report with counts

### pb_get_consolidation
Get consolidated memory entries, optionally filtered by tier.
Args: project (str), tier (str, optional: "immediate"/"consolidated"/"timeless")
Returns: list of entries with tier info

### pb_consolidation_status
Show consolidation status: per-tier counts.
Args: project (str)
Returns: {immediate: <count>, consolidated: <count>, timeless: <count>}

## How To Use

1. Check status: pb_consolidation_status(project="myapp")
2. Run consolidation: pb_trigger_consolidation(project="myapp")
3. View results: pb_get_consolidation(project="myapp", tier="timeless")

## Dependencies
- Requires PB_MEMORY=true (consolidates memory entries)
- Benefits from PB_RED_INK=true (promotes critical entries)
- Benefits from PB_PROCEDURAL=true (procedural knowledge is consolidated)"""

HELP_ANCHORS = """\
# Anchors (PB_ANCHORS=true) — Phase 3A: Environmental Reminders

Environmental reminders — visible proof of recent actions. Auto-generated from
task state, git commits, file edits, and red-ink reminders.

Anchors include:
- Active and completed tasks
- Recent git commits
- Most-edited files (last 24h)
- Red-ink reminders (top 5 critical entries)

## Tools (1)

### pb_get_anchors
Get environmental anchors: active tasks, completed tasks, commit log,
most-edited files, red-ink reminders.
Args: project (str)
Returns: anchors object with tasks, commits, files, red_ink fields

## How To Use

1. Get full anchors: pb_get_anchors(project="myapp")
2. Anchors are also injected in briefing cards (via PB_BRIEFING)
3. The plugin injects anchors as ANCHOR_CTX in system prompt

## Dependencies
- Requires PB_MEMORY=true (red-ink reminders come from memory entries)"""

HELP_DECAY = """\
# Decay (PB_DECAY=true) — Phase 4A: Ebbinghaus Forgetting Curve

Every sleep cycle: strength *= 0.95. Accessing an entry boosts strength += 0.3
(capped at 1.0). Red-ink entries have minimum strength = 0.5.

Entries below 0.1 strength → archived (still searchable, not injected by default).
Archived entries can be searched and restored.

## Tools (2)

### pb_decay_status
Show decay status for all entries: current strength, half-life, last-access time.
Args: project (str)
Returns: list of entries with decay metrics

### pb_search_archive
Search archived (forgotten) entries. Below 0.1 strength. Still searchable.
Args: project (str), q (str, default ""), limit (int, default 20)
Returns: matching archived entries

## How To Use

1. Check what's fading: pb_decay_status(project="myapp")
2. Find forgotten info: pb_search_archive(project="myapp", q="old config")
3. Restore by boosting: pb_boost_entry(project="myapp", idx=3)
4. Restore by touching: pb_touch_entry(project="myapp", index=3)

## Dependencies
- Requires PB_MEMORY=true (decay operates on memory entries)
- Requires PB_RED_INK=true (red-ink entries have minimum strength 0.5)"""

HELP_COSTS = """\
# Cost Analytics (PB_COSTS=true) — Phase 4B: Token Cost Tracking

Track token costs to prove graceful forgetting saves money without hurting quality.
Measures: injection cost, savings from selective injection, savings from forgetting,
and effectiveness (success rate vs context size).

## Tools (3)

### pb_cost_analysis
Full cost analysis report with effectiveness metrics.
Args: project (str), days (int, default 30)
Returns: analysis with totals, averages, effectiveness scores

### pb_cost_trends
Raw per-record cost trend data for charting over time.
Args: project (str), days (int, default 90)
Returns: list of cost records with timestamps

### pb_record_cost
Record a cost observation. Called after context injection and on task completion.
Args: project (str), session_id (str), tokens_injected (int), tokens_saved_injection (int), tokens_saved_forgetting (int), context_type (str), task_outcome (str), breakdown (dict, optional)
Returns: {ok: true}

## How To Use

1. After injecting context: pb_record_cost(project, session_id, tokens_injected, ...)
2. Periodic review: pb_cost_analysis(project="myapp", days=7)
3. Chart trends: pb_cost_trends(project="myapp", days=90)

## Dependencies
- Independent module (no PB_MEMORY requirement)"""

HELPS = {
    "": HELP_OVERVIEW,
    "memory": HELP_MEMORY,
    "recall": HELP_RECALL,
    "red_ink": HELP_RED_INK,
    "briefing": HELP_BRIEFING,
    "procedural": HELP_PROCEDURAL,
    "typed_sections": HELP_TYPED_SECTIONS,
    "consolidation": HELP_CONSOLIDATION,
    "anchors": HELP_ANCHORS,
    "decay": HELP_DECAY,
    "costs": HELP_COSTS,
}


def get_help(
    module: str = "", fmt: str = "markdown", features: dict | None = None
) -> str:
    text = HELPS.get(module, HELPS[""])
    if fmt == "compact":
        lines = []
        for line in text.split("\n"):
            if line.startswith("- "):
                lines.append(line)
        return "\n".join(lines) if lines else text
    if features is not None:
        status_lines = []
        for feature, enabled in features.items():
            status_lines.append(
                f"  PB_{feature.upper()}: {'ENABLED' if enabled else 'DISABLED'}"
            )
        status_block = "\n\n## Current Feature Status\n\n" + "\n".join(status_lines)
        return text + status_block
    return text
