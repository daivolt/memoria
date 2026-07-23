# Paper Brain MCP Server — Implementation Plan

> MCP server that exposes all Paper Brain features as tools for opencode.
> Complements the existing `memoria.mjs` plugin (which handles passive context injection).
> Toggled via environment variables per feature — all ON by default, no restart needed.

## Architecture

```
opencode
├── plugin: memoria.mjs          → passive injection (RED_INK_CTX, ANCHOR_CTX, BRIEFING_CTX, PROCEDURAL_CTX)
└── mcp: paper-brain-mcp          → active tools (add memory, search, consolidate, classify, decay, costs, etc.)
       └── HTTP calls → memoriad_global REST server (localhost:19998)
```

### Key Design Decisions

- **Wraps REST API** — MCP server makes HTTP calls to `localhost:19998`. Decoupled, works even if memoria server is on another machine.
- **Python** — Matches existing memoria codebase. Uses `mcp` Python package (like the browser MCP server).
- **Complements plugin** — Plugin continues passive context injection. MCP adds active tool calls the LLM can invoke on demand.
- **Env var toggles** — Per-feature `PB_*` env vars. Read on each tool call (no restart needed). `pb_help` always enabled.

## File Structure

```
/mnt/external-drive/code/memoria/opencode-integration/paper-brain-mcp/
├── server.py              ← MCP server with all 29 tools
├── help_texts.py          ← All help text as string constants (organized by module)
└── requirements.txt       ← mcp>=1.0, aiohttp
```

## opencode.json Config

```json
{
  "mcp": {
    "paper-brain": {
      "type": "local",
      "command": ["python3", "/mnt/external-drive/code/memoria/opencode-integration/paper-brain-mcp/server.py"],
      "enabled": true,
      "environment": {
        "MEMORIA_URL": "http://localhost:19998",
        "PB_MEMORY": "true",
        "PB_RECALL": "true",
        "PB_RED_INK": "true",
        "PB_BRIEFING": "true",
        "PB_PROCEDURAL": "true",
        "PB_TYPED_SECTIONS": "true",
        "PB_CONSOLIDATION": "true",
        "PB_ANCHORS": "true",
        "PB_DECAY": "true",
        "PB_COSTS": "true"
      }
    }
  }
}
```

## Environment Variables (per-feature toggle, all default ON)

| Env Var | Controls | Default |
|---------|----------|---------|
| `MEMORIA_URL` | Base URL for REST API | `http://localhost:19998` |
| `PB_MEMORY` | Core memory ops (add, get, replace, context) | `true` |
| `PB_RECALL` | Unified recall across sessions/chats | `true` |
| `PB_RED_INK` | Phase 1A: Criticality tagging | `true` |
| `PB_BRIEFING` | Phase 1B: Task briefing cards | `true` |
| `PB_PROCEDURAL` | Phase 1C: Behavioral reinforcement | `true` |
| `PB_TYPED_SECTIONS` | Phase 2A: Memory classification | `true` |
| `PB_CONSOLIDATION` | Phase 2B: Nightly consolidation | `true` |
| `PB_ANCHORS` | Phase 3A: Environmental reminders | `true` |
| `PB_DECAY` | Phase 4A: Ebbinghaus forgetting | `true` |
| `PB_COSTS` | Phase 4B: Cost analytics | `true` |

**Toggle behavior:** Each tool checks its `PB_*` env var before executing. If disabled, returns `"This feature is currently disabled. Set PB_<MODULE>=true to enable."`. Env vars are read on each call — no restart needed.

**Independence:** All modules are fully independent. You can enable/disable any combination. Example configs:

```bash
# Only red ink + core memory (minimal critical-facts mode)
PB_MEMORY=true PB_RECALL=true PB_RED_INK=true PB_BRIEFING=false PB_PROCEDURAL=false PB_TYPED_SECTIONS=false PB_CONSOLIDATION=false PB_ANCHORS=false PB_DECAY=false PB_COSTS=false

# Everything except costs
PB_MEMORY=true PB_RECALL=true PB_RED_INK=true PB_BRIEFING=true PB_PROCEDURAL=true PB_TYPED_SECTIONS=true PB_CONSOLIDATION=true PB_ANCHORS=true PB_DECAY=true PB_COSTS=false

# Only procedural + consolidation (sleep/reinforcement testing)
PB_MEMORY=true PB_RECALL=false PB_RED_INK=false PB_BRIEFING=false PB_PROCEDURAL=true PB_TYPED_SECTIONS=false PB_CONSOLIDATION=true PB_ANCHORS=false PB_DECAY=false PB_COSTS=false
```

## MCP Tools (29 total)

### Tool: pb_help (always enabled, cannot be disabled)

**Description:** Get help about Paper Brain modules and tools.

**Parameters:**
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `module` | string | `""` | If empty, returns full system overview. If set (e.g., `"red_ink"`), returns detailed info about that module only. Valid values: `""` (all), `"memory"`, `"recall"`, `"red_ink"`, `"briefing"`, `"procedural"`, `"typed_sections"`, `"consolidation"`, `"anchors"`, `"decay"`, `"costs"` |
| `format` | string | `"markdown"` | `"markdown"` for rich text, `"compact"` for one-line per tool |

**What pb_help returns (no module arg — full overview):**

```markdown
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
- Everything: all PB_*=true (default)
```

**What pb_help returns (with `module="red_ink"`):**

```markdown
# Red Ink (Phase 1A) — Criticality Tagging

Status: ENABLED (PB_RED_INK=true)

## What It Does

Red ink entries are critical facts that MUST survive compaction/summarization.
Inspired by Junko Mizuta's red ink highlighting — critical info visually distinct
in her notebooks. In Paper Brain, red-ink entries:

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
Args: project (str), index (int)
Returns: {ok: true}

## How To Use

1. Add a memory entry, then promote it: pb_set_priority(project, index, "critical")
2. Or use pb_add_memory with priority="critical" directly
3. Red-ink entries appear in system prompt as <RED_INK_CTX> block (via plugin)
4. They also appear in anchors as "CRITICAL reminders" (via plugin)
5. They survive compaction — the plugin instructs summarizer to preserve them

## Dependencies
- Requires PB_MEMORY=true (entries must exist)
- Consolidation promotes red-ink to timeless automatically
```

**Module-specific help reflects current enabled/disabled status.** If `PB_COSTS=false`, the costs help shows "Status: DISABLED (PB_COSTS=false)" and notes that tools will return a disabled message.

---

### Core Memory (PB_MEMORY) — 5 tools

| Tool | REST endpoint | Parameters | Description |
|------|---------------|------------|-------------|
| `pb_add_memory` | `POST /memory/{project}` | project (str), text (str), priority (str, default "normal"), memory_type (str, default None) | Add a memory entry with optional priority and type |
| `pb_get_memory` | `GET /memory/{project}` | project (str) | Get all memory entries (text only) |
| `pb_get_full_memory` | `GET /memory/{project}/full` | project (str) | Get all memory entries with full metadata (type, priority, strength, timestamps) |
| `pb_replace_memory` | `PUT /memory/{project}` | project (str), old (str), new (str) | Replace a memory entry containing `old` substring with `new` text |
| `pb_get_context` | `GET /ctx/{project}` | project (str) | Get assembled injection context (markdown + typed sections + red ink). This is what the plugin injects. |

### Recall (PB_RECALL) — 1 tool

| Tool | REST endpoint | Parameters | Description |
|------|---------------|------------|-------------|
| `pb_recall` | `GET /recall` | q (str), project (str, optional), limit (int, default 5), source (str, optional: "sessions"/"chats"), enrich (bool, default true), boost (bool, default true) | Unified multi-surface recall across sessions and chats. LLM enriches query. Optionally boosts matching memory entries (spaced repetition). |

### Red Ink (PB_RED_INK) — 4 tools

| Tool | REST endpoint | Parameters | Description |
|------|---------------|------------|-------------|
| `pb_get_red_ink` | `GET /red-ink/{project}` | project (str), min_strength (float, default 0.0) | Get critical (red ink) memory entries. Filter by minimum strength. |
| `pb_set_priority` | `PUT /memory/{project}/priority` | project (str), index (int), priority (str: "critical"/"important"/"normal") | Set priority level on a memory entry. Critical = red ink (never compressed, never decays below 0.5). |
| `pb_touch_entry` | `POST /memory/{project}/touch` | project (str), index (int) | Refresh access timestamp on an entry. Prevents decay. |
| `pb_boost_entry` | `POST /memory/{project}/boost/{idx}` | project (str), idx (int) | Boost an entry's strength by 0.3 (spaced repetition hit). Capped at 1.0. |

### Briefing (PB_BRIEFING) — 2 tools

| Tool | REST endpoint | Parameters | Description |
|------|---------------|------------|-------------|
| `pb_briefing` | `POST /briefing` | task_description (str), project (str), max_tokens (int, default 2000) | Generate a task-specific briefing card. Aggregates hippocampal recall, topic search, session search, cultural memory, procedural steps, and lessons. |
| `pb_get_briefing_anchors` | `GET /anchors/{project}` | project (str) | Get environmental anchors: active/completed tasks, recent commits, most-edited files, red-ink reminders. |

### Procedural (PB_PROCEDURAL) — 5 tools

| Tool | REST endpoint | Parameters | Description |
|------|---------------|------------|-------------|
| `pb_list_procedures` | `GET /procedural/{project}` | project (str) | List all procedures (proven task patterns) for a project |
| `pb_add_procedure` | `POST /procedural/{project}` | project (str), task_pattern (str), steps (list[str]), task_type (str, optional) | Manually add a procedure. Usually auto-created on task completion. |
| `pb_search_procedures` | `POST /procedural/{project}/search` | project (str), query (str), limit (int, default 5) | Search procedures by task description. Uses similarity threshold ≥ 0.7. |
| `pb_procedure_outcome` | `PATCH /procedural/{project}/{proc_id}` | project (str), proc_id (int), outcome (str: "success"/"fail"/"partial") | Record the outcome of applying a procedure. Drives reinforcement score. |
| `pb_retire_procedure` | `POST /procedural/{project}/retire/{proc_id}` | project (str), proc_id (int) | Retire a procedure. It will no longer appear in briefings/search. |

### Typed Sections (PB_TYPED_SECTIONS) — 2 tools

| Tool | REST endpoint | Parameters | Description |
|------|---------------|------------|-------------|
| `pb_classify_memory` | `POST /memory/{project}/classify` | project (str) | Auto-classify all memory entries into types (red/concept/procedural/temporal/relation) via LLM enrichment. |
| `pb_set_type` | `PUT /memory/{project}/type` | project (str), index (int), memory_type (str: "red"/"concept"/"procedural"/"temporal"/"relation") | Manually set the type of a memory entry. |

### Consolidation (PB_CONSOLIDATION) — 3 tools

| Tool | REST endpoint | Parameters | Description |
|------|---------------|------------|-------------|
| `pb_trigger_consolidation` | `POST /consolidation/{project}/trigger` | project (str) | Run the consolidation pipeline now. Creates immediate tier, extracts insights, promotes entries, archives old temporal. |
| `pb_get_consolidation` | `GET /consolidation/{project}` | project (str), tier (str, optional: "immediate"/"consolidated"/"timeless") | Get consolidated memory entries. Optionally filter by tier. |
| `pb_consolidation_status` | `GET /consolidation/{project}/status` | project (str) | Show consolidation status: per-tier counts. |

### Anchors (PB_ANCHORS) — 1 tool

| Tool | REST endpoint | Parameters | Description |
|------|---------------|------------|-------------|
| `pb_get_anchors` | `GET /anchors/{project}` | project (str) | Get environmental anchors: active tasks, completed tasks, commit log, most-edited files (24h), red-ink reminders. |

### Decay (PB_DECAY) — 2 tools

| Tool | REST endpoint | Parameters | Description |
|------|---------------|------------|-------------|
| `pb_decay_status` | `GET /memory/{project}/decay` | project (str) | Show decay status for all entries: current strength, half-life, last-access time. |
| `pb_search_archive` | `GET /memory/{project}/archive` | project (str), q (str, default ""), limit (int, default 20) | Search archived (forgotten) entries. Below 0.1 strength. Still searchable, not injected. |

### Cost Analytics (PB_COSTS) — 3 tools

| Tool | REST endpoint | Parameters | Description |
|------|---------------|------------|-------------|
| `pb_cost_analysis` | `GET /costs/analysis` | project (str), days (int, default 30) | Full cost analysis: tokens injected, saved by selective injection, saved by forgetting, effectiveness (success rate vs context size). |
| `pb_cost_trends` | `GET /costs/trends` | project (str), days (int, default 90) | Raw per-record cost trend data for charting over time. |
| `pb_record_cost` | `POST /costs/record` | project (str), session_id (str), tokens_injected (int), tokens_saved_injection (int), tokens_saved_forgetting (int), context_type (str), task_outcome (str), breakdown (dict, optional) | Record a cost observation. Called after context injection and on task completion. |

---

## Implementation Details

### server.py

```python
"""
Paper Brain MCP Server — exposes Paper Brain features as MCP tools for opencode.

Wraps the memoria REST API (localhost:19998) and provides per-feature toggle
via PB_* environment variables. All features default to enabled.

Transport: stdio (spawned by opencode as child process)
"""

import os
import json
import aiohttp
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

# --- Configuration ---
MEMORIA_URL = os.environ.get("MEMORIA_URL", "http://localhost:19998")

FEATURES = {
    "memory":          os.environ.get("PB_MEMORY",          "true").lower() == "true",
    "recall":          os.environ.get("PB_RECALL",          "true").lower() == "true",
    "red_ink":         os.environ.get("PB_RED_INK",         "true").lower() == "true",
    "briefing":        os.environ.get("PB_BRIEFING",        "true").lower() == "true",
    "procedural":      os.environ.get("PB_PROCEDURAL",      "true").lower() == "true",
    "typed_sections":  os.environ.get("PB_TYPED_SECTIONS",   "true").lower() == "true",
    "consolidation":   os.environ.get("PB_CONSOLIDATION",    "true").lower() == "true",
    "anchors":         os.environ.get("PB_ANCHORS",          "true").lower() == "true",
    "decay":           os.environ.get("PB_DECAY",            "true").lower() == "true",
    "costs":           os.environ.get("PB_COSTS",            "true").lower() == "true",
}

# --- HTTP helpers ---
async def api_get(path: str, params: dict | None = None) -> dict:
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{MEMORIA_URL}{path}", params=params) as resp:
            return await resp.json()

async def api_post(path: str, json_data: dict | None = None) -> dict:
    async with aiohttp.ClientSession() as session:
        async with session.post(f"{MEMORIA_URL}{path}", json=json_data) as resp:
            return await resp.json()

async def api_put(path: str, json_data: dict | None = None) -> dict:
    async with aiohttp.ClientSession() as session:
        async with session.put(f"{MEMORIA_URL}{path}", json=json_data) as resp:
            return await resp.json()

async def api_patch(path: str, json_data: dict | None = None) -> dict:
    async with aiohttp.ClientSession() as session:
        async with session.patch(f"{MEMORIA_URL}{path}", json=json_data) as resp:
            return await resp.json()

def disabled_msg(feature: str) -> str:
    return f"Paper Brain feature '{feature}' is currently disabled. Set PB_{feature.upper()}=true to enable."

# --- Tool definitions ---
# See help_texts.py for detailed descriptions.
# Tool schemas use Zod-compatible JSON Schema via the mcp Python SDK.

# ... (full implementation with all 29 tool handlers)

# --- Feature gate ---
# Each tool handler checks FEATURES[<module>] before executing.
# If disabled, returns TextContent with the disabled message.
# pb_help is always enabled and shows current feature status.
```

### help_texts.py

Contains all help text as string constants organized by module:

```python
HELP_OVERVIEW = """..."""  # Full system overview (shown above)
HELP_RED_INK = """..."""    # Module-specific help (shown above)
HELP_BRIEFING = """..."""
HELP_PROCEDURAL = """..."""
HELP_TYPED_SECTIONS = """..."""
HELP_CONSOLIDATION = """..."""
HELP_ANCHORS = """..."""
HELP_DECAY = """..."""
HELP_COSTS = """..."""
HELP_MEMORY = """..."""
HELP_RECALL = """..."""

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

def get_help(module: str = "", fmt: str = "markdown") -> str:
    text = HELPS.get(module, HELPS[""])
    if fmt == "compact":
        # Convert to one-line-per-tool format
        lines = []
        for line in text.split("\n"):
            if line.startswith("- "):
                lines.append(line)
        return "\n".join(lines) if lines else text
    # Inject current feature status
    status_lines = []
    for feature, enabled in FEATURES.items():
        status_lines.append(f"  PB_{feature.upper()}: {'ENABLED' if enabled else 'DISABLED'}")
    status_block = "## Current Feature Status\n\n" + "\n".join(status_lines)
    return text + "\n\n" + status_block
```

### requirements.txt

```
mcp>=1.0
aiohttp>=3.9
```

### Tool schema pattern (Python MCP SDK)

Each tool is registered like:

```python
from mcp.server import Server
from mcp.types import Tool, TextContent
import json

server = Server("paper-brain")

@server.list_tools()
async def list_tools() -> list[Tool]:
    tools = []
    # pb_help is always available
    tools.append(Tool(
        name="pb_help",
        description="Get help about Paper Brain modules and tools. Returns overview or module-specific docs.",
        inputSchema={
            "type": "object",
            "properties": {
                "module": {"type": "string", "default": "", "enum": ["", "memory", "recall", "red_ink", "briefing", "procedural", "typed_sections", "consolidation", "anchors", "decay", "costs"], "description": "Module name for specific help. Empty for full overview."},
                "format": {"type": "string", "default": "markdown", "enum": ["markdown", "compact"], "description": "Output format: markdown (rich) or compact (one-line per tool)"},
            },
        },
    ))
    # Feature-gated tools...
    if FEATURES["memory"]:
        tools.append(Tool(name="pb_add_memory", ...))
        tools.append(Tool(name="pb_get_memory", ...))
        # etc.
    # ... all other feature-gated tools
    return tools

@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name == "pb_help":
        return [TextContent(type="text", text=get_help(arguments.get("module", ""), arguments.get("format", "markdown")))]
    
    # Feature gates
    if name in MEMORY_TOOLS and not FEATURES["memory"]:
        return [TextContent(type="text", text=disabled_msg("memory"))]
    # ... etc for all features
    
    # Tool implementations (HTTP calls to REST API)
    if name == "pb_add_memory":
        result = await api_post(f"/memory/{arguments['project']}", {"text": arguments["text"], "priority": arguments.get("priority", "normal"), "memory_type": arguments.get("memory_type")})
        return [TextContent(type="text", text=json.dumps(result, indent=2))]
    # ... etc

async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
```

**Important:** `list_tools()` only returns tools for enabled features. `call_tool()` also gates by feature but as a safety net. This means the LLM only sees tools for currently enabled features.

---

## Implementation Steps

1. **Create `paper-brain-mcp/` directory** under `/mnt/external-drive/code/memoria/opencode-integration/`
2. **Write `help_texts.py`** — all module help text as string constants with status injection
3. **Write `server.py`** — MCP server with all 29 tools, feature gating, HTTP client
4. **Write `requirements.txt`** — `mcp>=1.0`, `aiohttp>=3.9`
5. **Test locally** — `python3 server.py` (should start stdio MCP server)
6. **Add to `opencode.json`** — MCP server config entry
7. **Restart opencode** — verify `pb_help` appears in tool list
8. **Test each tool** — verify HTTP calls to REST API work correctly
9. **Test feature toggling** — set `PB_PROCEDURAL=false`, verify tools disappear and disabled message appears

## Already-Completed Gap Fixes (from this session)

These were bugs/gaps found in the existing Paper Brain implementation, all fixed:

| # | Fix | Files Changed |
|---|-----|--------------|
| 1 | HippocampalMemory import — added to `from cortex import`, used directly | `memoriad_global.py` |
| 2 | `pg.find_lessons_for_topic` → `find_lessons_for_topic()` — direct import, sync call, `top_k=5` | `memoriad_global.py` |
| 3 | `search_procedures` similarity threshold — 0.01 → 0.7 | `pg.py` |
| 4 | Reinforcement score formula — `sqrt(success+1)` → `accuracy * log(success+1)` | `pg.py` |
| 5 | Procedure extraction prompt + function — `PROCEDURE_EXTRACT_PROMPT` and `extract_procedure()` in enrichment, connected to task completion auto-formation | `enrichment.py`, `memoriad_global.py` |
| 6 | Consolidated→timeless promotion — entries with `strength≥0.8` OR `source_sessions≥3` auto-promoted; hippocampal episodes with `reward≥0.7` promoted to cultural memory | `pg.py` |
| 7 | Immediate tier creation — `run_consolidation` now creates/updates `tier=immediate` entries from latest session | `pg.py` |
| 8 | Anchors enrichment — added `red_ink_reminders` and `most_edited_files` to anchors; plugin injects them in ANCHOR_CTX | `memoriad_global.py`, `memoria.mjs` |
| 9 | Phase 3B Bridge — cross-project topic proposals when consolidated entries overlap across projects | `pg.py` |
| 10 | Prune Phase red-ink exception — ancient episodes with `priority=critical` are spared from pruning | `cortex.py` |
| 11 | Plugin outcome tracking — already records `task_outcome: "completed"` on session close (LOW, adequate) | no changes needed |