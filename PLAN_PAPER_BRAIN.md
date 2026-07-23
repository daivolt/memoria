# Paper Brain — Memoria External Memory System Plan

> Inspired by Junko Mizuta's "paper brain" notebook system for navigating life with 7-second memory.
> Also informed by Clive Wearing's condition and neuroscience of memory consolidation.

## Core Insight

LLMs have "anterograde amnesia" — every session starts from scratch, and compaction loses context like Mizuta's 7-second memory. The brain solves this with multiple memory systems (cortex, hippocampus, basal ganglia) and automatic consolidation during sleep. Memoria currently has hippocampal storage (sessions, episodes) and some cortical storage (topics), but **no automatic consolidation** between them, and no procedural memory at all.

## Brain-Memoria Mapping

| Brain Region | Function | Memoria Equivalent | Current Status |
|---|---|---|---|
| **Cerebral cortex** (permanent) | Pre-illness memories, identity, language | Topics + Cultural Memory + Lessons | ✅ Exists, survives sessions |
| **Hippocampus** (temporary buffer) | New experiences → consolidate to cortex | Sessions (FTS5) + HippocampalMemory + Chitchat | ✅ Exists, but NO consolidation to cortex |
| **Basal ganglia** (procedural) | Motor skills, learned routes | **Procedural Memory** | ❌ Missing entirely |
| **Amygdala** (emotional) | Emotional bonds, importance tagging | **Red Ink** (priority tagging) | ❌ Missing |
| PFC-BG gating (action selection) | Choose what to do next | PFCBGGating (cortex.py) | ✅ Exists |
| Sleep consolidation | Hippocampus → cortex transfer | **Nightly Consolidation** | ❌ Missing (only manual propose→accept) |

## What Mizuta Got Right

1. **Real-time logging** — Write before forgetting → We have sessions
2. **Timestamp-first scanning** — Time-indexed for retrieval → FTS5 search exists
3. **Red ink highlighting** — Critical info visually distinct → **NEW: Red Ink System**
4. **Nightly consolidation** — Rewrite key points into master notebook → **NEW: Nightly Consolidation**
5. **Pre-mapping** — Briefing cards before interactions → **NEW: Briefing Cards**
6. **Procedural learning** — Repeated actions become automatic → **NEW: Procedural Memory**

## What Mizuta Got Wrong (and We Avoid)

1. **Notebooks didn't restore memory** — Reading facts ≠ experiencing them → Briefing cards provide CONTEXT, not just facts
2. **Volume became unmanageable** → **Graceful forgetting** (Ebbinghaus decay)
3. **Single point of failure** (losing a notebook = despair) → Redundant storage (PG + flat files)
4. **No emotional memory** → Priority tagging (red ink) marks importance
5. **She stopped taking notes** → **Acceptance model**: not everything needs to be remembered

---

## Phase 1 — Foundation (P0)

### 1A: Red Ink System (Criticality Tagging)

**Mizuta parallel:** Red ink for critical info that must survive compaction.

**Problem:** All memory entries are equal during compaction. No way to say "this MUST survive."

**Implementation:**

#### DB Changes (`pg.py`)
- Add `priority` column to `project_memory`: values `critical`, `important`, `normal` (default `normal`)
- Migration: `ALTER TABLE project_memory ADD COLUMN priority VARCHAR(10) DEFAULT 'normal'`
- New query: `get_red_ink(project)` → returns all `priority=critical` entries
- New query: `update_priority(project, index, priority)` → change priority of entry
- New query: `add_memory_entry` accepts optional `priority` param

#### REST API (`memoriad_global.py`)
- `POST /memory/{project}` — add `priority` param
- `GET /red-ink/{project}` — return only `priority=critical` entries
- `PUT /memory/{project}/priority` — change priority: `{"index": 0, "priority": "critical"}`
- `GET /ctx/{project}` — now includes `RED_INK_CTX` block alongside `MEMORIA_CTX`

#### CLI (`memoria.py`)
- `!memoria add --red "..."` → shortcut for `priority=critical`
- `!memoria add --important "..."` → shortcut for `priority=important`
- `!memoria red-ink` → list all critical entries
- `!memoria promote <idx>` → promote entry to critical
- `!memoria demote <idx>` → demote entry to normal

#### Plugin (`memoria.mjs`)
- Inject `RED_INK_CTX` block in system prompt transform:
```
<RED_INK_CTX>
[CRITICAL — these facts MUST be preserved verbatim in any compaction or summarization]
- fact 1
- fact 2
</RED_INK_CTX>
```
- Compaction protection: `experimental.chat.system.transform` prepends instruction to preserve RED_INK_CTX

#### Compression Policy
- `red` (critical): Never compressed, always injected verbatim
- `important`: Compressed only when >5 similar entries exist
- `normal`: Standard compression behavior

---

### 1B: Briefing Cards (Task-Specific Context Pre-Mapping)

**Mizuta parallel:** Before going somewhere, she wrote briefing cards: "Who am I meeting? What do I talk about?"

**Problem:** New sessions start with generic context. No task-specific targeting.

**Implementation:**

#### New Endpoint: `POST /briefing`
```json
{
  "task_description": "Deploy memoria server to production",
  "project": "memoria",
  "max_tokens": 2000
}
```

**Briefing assembly pipeline:**
1. **Hippocampal recall** → `HippocampalMemory.context_for_task(task_description)` — find similar past tasks
2. **Topic search** → `search_topics_enriched(task_description)` — find relevant cross-project facts
3. **Session search** → `search_sessions_enriched(task_description)` — find relevant past sessions
4. **Cultural memory** → `CulturalMemory.inherit()` — top-20 inherited facts
5. **Procedural memory** → search for matching task patterns with high reinforcement_score
6. **Lessons** → `find_lessons_for_topic(inferred_topic)` — relevant teaching units

**Returns:**
```json
{
  "briefing": "...assembled markdown context...",
  "sources": {
    "hippocampal_episodes": 3,
    "topic_facts": 2,
    "relevant_sessions": 1,
    "procedural_steps": 1,
    "cultural_facts": 5,
    "lessons": 1
  },
  "token_estimate": 1800
}
```

#### CLI
- `!memoria briefing <task description>` → print assembled briefing card

#### Plugin Integration
- On `session.created`, call `/briefing` with session title
- Inject result as `BRIEFING_CTX` block in system prompt

---

### 1C: Procedural Memory (Behavioral Reinforcement)

**Mizuta parallel:** She learned the supermarket route through repetition — basal ganglia, not hippocampus. The route was "what works" — never forgotten.

**Key insight:** Procedural memory isn't documentation, it's **behavioral reinforcement**. It stores proven paths (success) and avoids proven failures. Wrong turns are NOT stored as procedural — they stay as negative episodic examples.

#### DB Changes
New table `procedural_memory`:
```sql
CREATE TABLE procedural_memory (
    id SERIAL PRIMARY KEY,
    project VARCHAR(200) NOT NULL,
    task_pattern VARCHAR(500) NOT NULL,   -- e.g., "deploy to production"
    task_type VARCHAR(100),               -- e.g., "deployment", "bugfix"
    steps JSONB NOT NULL,                 -- ordered list of step descriptions
    success_count INT DEFAULT 1,
    fail_count INT DEFAULT 0,
    reinforcement_score FLOAT DEFAULT 0.5,
    last_success_at FLOAT,
    proven_by TEXT[] DEFAULT '{}',        -- session IDs that proved this
    created_at FLOAT DEFAULT EXTRACT(EPOCH FROM NOW()),
    retired BOOLEAN DEFAULT FALSE,
    search_enrichments TEXT[] DEFAULT '{}'
);
CREATE INDEX idx_proc_project ON procedural_memory(project);
CREATE INDEX idx_proc_pattern ON procedural_memory USING gin(to_tsvector('english', task_pattern));
CREATE INDEX idx_proc_score ON procedural_memory(reinforcement_score DESC);
```

#### Reinforcement Score Formula
```
reinforcement_score = (success_count / (success_count + fail_count)) * log(success_count + 1)
```
Rewards both success rate AND repetition count (the "100+ trips" effect).

#### Formation (Automatic)
1. When `PATCH /tasks/{id}` with `status=completed`:
   - Extract task pattern from title
   - Extract steps from session (tool calls, file edits in order)
   - Search for matching procedural memory (embedding similarity ≥ 0.7)
   - If match: increment `success_count`, update `reinforcement_score`, append session ID
   - If new: create with `success_count=1`
2. When task FAILS: if matching procedure exists, increment `fail_count`
3. **Retirement rule:** If `fail_count > success_count * 2` AND `fail_count > 5`, mark `retired=true`

#### Injection (Full Fidelity)
- When briefing card is assembled, search procedural memory for matching patterns
- Matching procedures (similarity ≥ 0.7, `retired=false`) are injected as `PROCEDURAL_CTX`
- **Never compressed.** Always full fidelity.
- Ranked by `reinforcement_score`

#### REST API
- `GET /procedural/{project}` — list procedures for project
- `POST /procedural/{project}` — manually add a procedure
- `POST /procedural/{project}/search` — find procedures matching a task description
- `PATCH /procedural/{project}/{id}` — update procedure (success/fail)
- `POST /procedural/{project}/retire/{id}` — retire a procedure

#### CLI
- `!memoria procedure list` — list procedures for current project
- `!memoria procedure add <task_pattern> <step1> <step2> ...` — manually add
- `!memoria procedure search <task>` — find matching procedures

---

## Phase 2 — Smart Memory (P1)

### 2A: Notebook Organization (Typed Sections + Auto-Classify Migration)

**Mizuta parallel:** Three tiers: pocket notebooks (raw) → daily notebooks (consolidated) → archive (historical)

#### Memory Types

| Type | Brain Region | Compaction Policy | Max Lifetime |
|---|---|---|---|
| `red` | Cortex (permanent) | Never compressed, always verbatim | Permanent |
| `concept` | Cortex (semantic) | Compress if >5 similar concepts | Permanent |
| `procedural` | Basal ganglia | Never compressed, full fidelity | Permanent (or retired) |
| `temporal` | Hippocampus (episodic) | Compress after 7 days, archive after 30 | Archive after 30 |
| `relation` | Cortex (semantic) | Preserve until contradicted | Until contradicted |

#### DB Changes
- Add `memory_type` column to `project_memory`: `VARCHAR(20) DEFAULT 'temporal'`
- Add `strength` column: `FLOAT DEFAULT 1.0`
- Add `last_accessed` column: `FLOAT DEFAULT EXTRACT(EPOCH FROM NOW())`
- Migration: `ALTER TABLE project_memory ADD COLUMN memory_type VARCHAR(20) DEFAULT 'temporal'`

#### Auto-Classify Migration
On migration, run LLM enrichment to classify existing entries:
```
Prompt: "Classify this memory entry into one of: red (critical, must never be forgotten),
concept (architectural decision or key insight), procedural (step-by-step how-to),
temporal (time-stamped event), relation (dependency or connection between things).

Entry: '{entry_text}'

Output only the type name."
```

Existing entries that are currently **pinned** → classify as `red`
Entries containing dates/timestamps → `temporal`
Entries with "how to" / imperative verbs → `procedural`
Entries with "depends on" / "uses" / "requires" → `relation`
Short factual entries → `concept`

#### CLI
- `!memoria add --type red "..."` → add with specific type
- `!memoria add --type procedural "step 1: ... step 2: ..."` 
- `!memoria type <idx> <type>` → reclassify an entry
- `!memoria list` → now shows type icons: 🔴 red, 💡 concept, 🔧 procedural, 🕐 temporal, 🔗 relation

---

### 2B: Nightly Consolidation (3-Tier Memory)

**Mizuta parallel:** Every night she re-read raw notes and rewrote key points into a consolidated "master" notebook. This is exactly hippocampal→cortical consolidation during sleep.

#### Three Tiers

| Tier | Brain Region | Content | Lifetime | Injection Priority |
|---|---|---|---|---|
| `immediate` | Hippocampus | Last session's raw summary | Until next consolidation | Highest (current context) |
| `consolidated` | Cortex (recent) | Merged insights from 7 days | 30 days | Medium (recent patterns) |
| `timeless` | Cortex (permanent) | Promoted from consolidated | Permanent | High (always available) |

#### New Table: `consolidated_memory`
```sql
CREATE TABLE consolidated_memory (
    id SERIAL PRIMARY KEY,
    project VARCHAR(200) NOT NULL,
    tier VARCHAR(20) NOT NULL,  -- immediate, consolidated, timeless
    content TEXT NOT NULL,
    source_sessions TEXT[] DEFAULT '{}',
    source_episode_ids TEXT[] DEFAULT '{}',
    memory_type VARCHAR(20) DEFAULT 'concept',
    priority VARCHAR(10) DEFAULT 'normal',
    strength FLOAT DEFAULT 1.0,
    created_at FLOAT DEFAULT EXTRACT(EPOCH FROM NOW()),
    last_accessed FLOAT DEFAULT EXTRACT(EPOCH FROM NOW()),
    search_enrichments TEXT[] DEFAULT '{}'
);
CREATE INDEX idx_consolidated_project_tier ON consolidated_memory(project, tier);
```

#### Consolidation Pipeline (runs every sleep cycle, default 6h)

**Immediate → Consolidated:**
1. Gather all sessions from last 7 days
2. Use LLM enrichment to extract cross-session insights:
   ```
   Prompt: "Given these session summaries, extract 3-5 key insights that
   span multiple sessions. Focus on patterns, decisions, and outcomes
   that repeat or build on each other."
   ```
3. Merge overlapping insights, deduplicate
4. Store in `consolidated_memory` with `tier=consolidated`
5. Archive original immediate entries older than 7 days

**Consolidated → Timeless (promotion):**
1. Entries accessed ≥3 times in 7 days → promote to `timeless`
2. Entries that appear in ≥3 sessions → auto-propose as topic proposal
3. Hippocampal episodes with reward ≥0.7 → promote to cultural memory
4. Red-ink entries → always promoted to timeless immediately

#### REST API
- `GET /consolidation/{project}?tier=<tier>` — get entries by tier
- `POST /consolidation/{project}/trigger` — manually trigger consolidation
- `GET /consolidation/{project}/status` — show consolidation status

#### CLI
- `!memoria consolidate` — manually trigger consolidation
- `!memoria consolidation status` — show tiers and counts

---

## Phase 3 — Continuity (P2)

### 3A: Environmental Anchors (Visible Reminders)

**Mizuta parallel:** She left dirty dishes out as proof she'd eaten. Physical receipts of recent actions.

#### Auto-Generated Anchors
Injected as `ANCHOR_CTX` block:
- Last 3 git commits in the project
- Last 3 completed tasks
- Current task state (if any)
- Top 3 most-edited files in last 24h
- Recent red-ink reminders

#### Implementation
- Generated during heartbeat (`PATCH /agents/{id}`)
- Stored in `/var/tmp/memoria/<project>/anchors.json`
- Injected by plugin as `ANCHOR_CTX`

#### REST API
- `GET /anchors/{project}` — get current anchors
- Regenerated automatically every heartbeat

#### CLI
- `!memoria anchors` — show current environmental anchors

---

### 3B: Hippocampal Replay Enhancement

**Mizuta parallel:** Her nightly review ritual — re-reading notes and rewriting key points.

#### Enhancement to `HippocampalReplay.replay_step()`

Add three new phases after Q-learning update:

1. **Consolidate Phase:**
   - Take top-K high-reward episodes (reward ≥ 0.7)
   - Generate topic proposals from them via LLM enrichment
   - Auto-propose to topics system (not auto-accept — still needs approval)

2. **Prune Phase:**
   - Episodes older than 30 days with reward < 0.3 → archive
   - Episodes older than 90 days regardless → archive (unless red-ink)

3. **Bridge Phase:**
   - When `context_for_task()` returns results from different projects
   - Create cross-project topic proposal with combined facts

---

## Phase 4 — Graceful Forgetting (P3)

### 4A: Ebbinghaus Forgetting Curve

**Mizuta parallel:** She chose to stop taking notes — acceptance over documentation. Not everything needs to be remembered.

#### Decay Formula
Every sleep cycle (6h default):
```
strength *= 0.95
```

Accessing an entry (via recall/search) boosts:
```
strength += 0.3  (capped at 1.0)
```

Red-ink entries have minimum `strength = 0.5` (never fully decay).

#### Archival
Entries below `strength < 0.1` → move to `memory_archive` table (still searchable, not injected by default).

#### REST API
- `GET /memory/{project}/decay` — show decay status for all entries
- `POST /memory/{project}/boost/{idx}` — boost an entry's strength (manual access)
- `GET /memory/{project}/archive` — search archived entries

#### CLI
- `!memoria decay` — show which entries are fading
- `!memoria recall <query>` — auto-boosts accessed entries

---

### 4B: Memory Cost Analytics

**Purpose:** Track token costs to prove graceful forgetting saves money without hurting quality.

#### New Table: `memory_costs`
```sql
CREATE TABLE memory_costs (
    id SERIAL PRIMARY KEY,
    session_id VARCHAR(100),
    project VARCHAR(200),
    tokens_injected INT DEFAULT 0,
    tokens_saved_injection INT DEFAULT 0,
    tokens_saved_forgetting INT DEFAULT 0,
    context_type VARCHAR(20),  -- full, briefing, procedural, red_ink
    task_outcome VARCHAR(20),  -- success, fail, partial
    created_at FLOAT DEFAULT EXTRACT(EPOCH FROM NOW())
);
```

#### Metrics Tracked
1. **Injection cost:** Tokens spent on context injection per session
2. **Savings from selective injection:** `(full_context_tokens - briefing_card_tokens)`
3. **Savings from forgetting:** Tokens NOT spent because decayed memories weren't injected
4. **Effectiveness:** Did the task succeed? Correlate context size with outcome
5. **Cost-benefit ratio:** `tokens_saved / (1 - success_rate_delta)`

#### REST API
- `GET /costs/analysis?project=<project>&days=30` — cost analysis
- `GET /costs/trends?project=<project>&days=90` — cost trends over time

#### CLI
- `!memoria costs [days]` — show cost analysis

---

## File Changes Summary

| File | Changes | Phase |
|---|---|---|
| `pg.py` | Add columns: `priority`, `memory_type`, `strength`, `last_accessed` to `project_memory`; new tables: `procedural_memory`, `consolidated_memory`, `memory_archive`, `memory_costs`; new queries for briefing, red-ink, consolidation, procedural, cost analytics | 1A-4B |
| `memoriad_global.py` | New endpoints: `/briefing`, `/red-ink/{project}`, `/procedural/*`, `/consolidation/*`, `/anchors/*`, `/costs/*`; enhance sleep cycle for consolidation + decay; add briefing auto-trigger; procedural formation on task completion; cost tracking on injection | 1A-4B |
| `memoria.py` | New CLI commands: `--red`, `--important`, `--type`, `briefing`, `consolidate`, `procedure`, `decay`, `costs`, `red-ink`, `promote`, `demote`, `anchors`; update `add`, `list`, `recall` | 1A-4B |
| `enrichment.py` | New consolidation prompt template; auto-classify prompt for migration; procedure extraction prompt | 2A, 2B, 1C |
| `cortex.py` | Enhance `HippocampalReplay` with consolidation, pruning, bridging phases; connect procedural memory to PFC-BG gating | 1C, 3B |
| `compress.py` | No changes needed (operates on tool output, not memory) | — |
| `opencode-integration/plugins/memoria.mjs` | Add `RED_INK_CTX`, `BRIEFING_CTX`, `ANCHOR_CTX`, `PROCEDURAL_CTX` injection blocks; trigger `/briefing` on session creation; cost tracking on injection | 1A-1C |
| `sql/` | Migration scripts for new columns and tables | 1A-4B |
| `social_learning.py` | Connect consolidation pipeline to cultural memory promotion | 2B |

## Implementation Priority

| Phase | Features | Est. Lines | Est. Time |
|---|---|---|---|
| Phase 1A | Red Ink System | ~300 | 1-2 days |
| Phase 1B | Briefing Cards | ~400 | 1-2 days |
| Phase 1C | Procedural Memory | ~350 | 2-3 days |
| Phase 2A | Typed Sections + Migration | ~500 | 2-3 days |
| Phase 2B | Nightly Consolidation | ~600 | 2-3 days |
| Phase 3A | Environmental Anchors | ~200 | 0.5-1 day |
| Phase 3B | Replay Enhancement | ~300 | 1-2 days |
| Phase 4A | Forgetting Curve | ~300 | 1-2 days |
| Phase 4B | Cost Analytics | ~250 | 1 day |

## Key Design Principles

Following Mizuta's eventual wisdom:

1. **Be present, not exhaustive** — Briefing cards inject only relevant context, not everything
2. **Reinforce what works** — Procedural memory stores proven paths at full fidelity, never compressed
3. **Let go of what's unused** — Declarative memories decay via Ebbinghaus curve unless accessed
4. **Never forget what's critical** — Red ink memories survive everything
5. **Track the cost** — Measure token savings to prove the approach works
6. **Automatic consolidation** — Hippocampus→cortex transfer happens during sleep, not manually
7. **Graceful forgetting is a feature** — Not every session needs to be remembered forever