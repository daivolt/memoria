"""
Paper Brain MCP Server — exposes Paper Brain features as MCP tools for opencode.

Wraps the memoria REST API (localhost:19998) and provides per-feature toggle
via PB_* environment variables. All features default to enabled.

Transport: stdio (spawned by opencode as child process)
"""

import os
import sys
import json
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import aiohttp
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from help_texts import get_help, HELPS

MEMORIA_URL = os.environ.get("MEMORIA_URL", "http://localhost:19998")


def read_features() -> dict:
    return {
        "memory": os.environ.get("PB_MEMORY", "true").lower() == "true",
        "recall": os.environ.get("PB_RECALL", "true").lower() == "true",
        "red_ink": os.environ.get("PB_RED_INK", "true").lower() == "true",
        "briefing": os.environ.get("PB_BRIEFING", "true").lower() == "true",
        "procedural": os.environ.get("PB_PROCEDURAL", "true").lower() == "true",
        "typed_sections": os.environ.get("PB_TYPED_SECTIONS", "true").lower() == "true",
        "consolidation": os.environ.get("PB_CONSOLIDATION", "true").lower() == "true",
        "anchors": os.environ.get("PB_ANCHORS", "true").lower() == "true",
        "decay": os.environ.get("PB_DECAY", "true").lower() == "true",
        "costs": os.environ.get("PB_COSTS", "true").lower() == "true",
    }


FEATURE_TOOLS = {
    "memory": [
        "pb_add_memory",
        "pb_get_memory",
        "pb_get_full_memory",
        "pb_replace_memory",
        "pb_get_context",
    ],
    "recall": ["pb_recall"],
    "red_ink": [
        "pb_get_red_ink",
        "pb_set_priority",
        "pb_touch_entry",
        "pb_boost_entry",
    ],
    "briefing": ["pb_briefing", "pb_get_briefing_anchors"],
    "procedural": [
        "pb_list_procedures",
        "pb_add_procedure",
        "pb_search_procedures",
        "pb_procedure_outcome",
        "pb_retire_procedure",
    ],
    "typed_sections": ["pb_classify_memory", "pb_set_type"],
    "consolidation": [
        "pb_trigger_consolidation",
        "pb_get_consolidation",
        "pb_consolidation_status",
    ],
    "anchors": ["pb_get_anchors"],
    "decay": ["pb_decay_status", "pb_search_archive"],
    "costs": ["pb_cost_analysis", "pb_cost_trends", "pb_record_cost"],
}

TOOL_TO_FEATURE = {}
for feat, tools in FEATURE_TOOLS.items():
    for t in tools:
        TOOL_TO_FEATURE[t] = feat


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


def fmt(result: dict | list) -> str:
    return json.dumps(result, indent=2, ensure_ascii=False)


server = Server("paper-brain")


@server.list_tools()
async def list_tools() -> list[Tool]:
    features = read_features()
    tools = []

    tools.append(
        Tool(
            name="pb_help",
            description="Get help about Paper Brain modules and tools. Returns overview or module-specific docs.",
            inputSchema={
                "type": "object",
                "properties": {
                    "module": {
                        "type": "string",
                        "default": "",
                        "enum": [
                            "",
                            "memory",
                            "recall",
                            "red_ink",
                            "briefing",
                            "procedural",
                            "typed_sections",
                            "consolidation",
                            "anchors",
                            "decay",
                            "costs",
                        ],
                        "description": "Module name for specific help. Empty for full overview.",
                    },
                    "format": {
                        "type": "string",
                        "default": "markdown",
                        "enum": ["markdown", "compact"],
                        "description": "Output format: markdown (rich) or compact (one-line per tool)",
                    },
                },
            },
        )
    )

    if features["memory"]:
        tools.append(
            Tool(
                name="pb_add_memory",
                description="Add a memory entry with optional priority and type.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "project": {"type": "string", "description": "Project name"},
                        "text": {"type": "string", "description": "Memory entry text"},
                        "priority": {
                            "type": "string",
                            "default": "normal",
                            "enum": ["critical", "important", "normal"],
                            "description": "Priority level",
                        },
                        "memory_type": {
                            "type": "string",
                            "enum": [
                                "red",
                                "concept",
                                "procedural",
                                "temporal",
                                "relation",
                            ],
                            "description": "Memory type classification (optional)",
                        },
                    },
                    "required": ["project", "text"],
                },
            )
        )
        tools.append(
            Tool(
                name="pb_get_memory",
                description="Get all memory entries for a project (text only).",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "project": {"type": "string", "description": "Project name"},
                    },
                    "required": ["project"],
                },
            )
        )
        tools.append(
            Tool(
                name="pb_get_full_memory",
                description="Get all memory entries with full metadata (type, priority, strength, timestamps).",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "project": {"type": "string", "description": "Project name"},
                    },
                    "required": ["project"],
                },
            )
        )
        tools.append(
            Tool(
                name="pb_replace_memory",
                description="Replace a memory entry containing a substring with new text.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "project": {"type": "string", "description": "Project name"},
                        "old": {
                            "type": "string",
                            "description": "Substring to find in existing entry",
                        },
                        "new": {"type": "string", "description": "Replacement text"},
                    },
                    "required": ["project", "old", "new"],
                },
            )
        )
        tools.append(
            Tool(
                name="pb_get_context",
                description="Get assembled injection context — the full markdown + typed sections + red ink that gets injected into the system prompt.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "project": {"type": "string", "description": "Project name"},
                    },
                    "required": ["project"],
                },
            )
        )

    if features["recall"]:
        tools.append(
            Tool(
                name="pb_recall",
                description="Search across sessions, chats, and memory with LLM-driven query enrichment.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "q": {"type": "string", "description": "Search query"},
                        "project": {
                            "type": "string",
                            "description": "Project name (optional)",
                        },
                        "limit": {
                            "type": "integer",
                            "default": 5,
                            "description": "Max results to return",
                        },
                        "source": {
                            "type": "string",
                            "enum": ["sessions", "chats"],
                            "description": "Filter to specific source (optional)",
                        },
                        "enrich": {
                            "type": "boolean",
                            "default": True,
                            "description": "Enable LLM query enrichment",
                        },
                        "boost": {
                            "type": "boolean",
                            "default": True,
                            "description": "Boost matching memory entries (spaced repetition)",
                        },
                    },
                    "required": ["q"],
                },
            )
        )

    if features["red_ink"]:
        tools.append(
            Tool(
                name="pb_get_red_ink",
                description="Get critical (red ink) memory entries. Filter by minimum strength.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "project": {"type": "string", "description": "Project name"},
                        "min_strength": {
                            "type": "number",
                            "default": 0.0,
                            "description": "Minimum strength threshold",
                        },
                    },
                    "required": ["project"],
                },
            )
        )
        tools.append(
            Tool(
                name="pb_set_priority",
                description="Set priority level on a memory entry. Critical = red ink (never compressed, never decays below 0.5).",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "project": {"type": "string", "description": "Project name"},
                        "index": {"type": "integer", "description": "Entry index"},
                        "priority": {
                            "type": "string",
                            "enum": ["critical", "important", "normal"],
                            "description": "Priority level",
                        },
                    },
                    "required": ["project", "index", "priority"],
                },
            )
        )
        tools.append(
            Tool(
                name="pb_touch_entry",
                description="Refresh access timestamp on an entry. Prevents decay.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "project": {"type": "string", "description": "Project name"},
                        "index": {"type": "integer", "description": "Entry index"},
                    },
                    "required": ["project", "index"],
                },
            )
        )
        tools.append(
            Tool(
                name="pb_boost_entry",
                description="Boost an entry's strength by 0.3 (spaced repetition). Capped at 1.0.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "project": {"type": "string", "description": "Project name"},
                        "idx": {"type": "integer", "description": "Entry index"},
                    },
                    "required": ["project", "idx"],
                },
            )
        )

    if features["briefing"]:
        tools.append(
            Tool(
                name="pb_briefing",
                description="Generate a task-specific briefing card. Aggregates hippocampal recall, topic search, session search, cultural memory, procedural steps, and lessons.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "task_description": {
                            "type": "string",
                            "description": "Description of the task to brief for",
                        },
                        "project": {"type": "string", "description": "Project name"},
                        "max_tokens": {
                            "type": "integer",
                            "default": 2000,
                            "description": "Max tokens for briefing output",
                        },
                    },
                    "required": ["task_description", "project"],
                },
            )
        )
        tools.append(
            Tool(
                name="pb_get_briefing_anchors",
                description="Get environmental anchors: active/completed tasks, recent commits, most-edited files, red-ink reminders.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "project": {"type": "string", "description": "Project name"},
                    },
                    "required": ["project"],
                },
            )
        )

    if features["procedural"]:
        tools.append(
            Tool(
                name="pb_list_procedures",
                description="List all procedures (proven task patterns) for a project.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "project": {"type": "string", "description": "Project name"},
                    },
                    "required": ["project"],
                },
            )
        )
        tools.append(
            Tool(
                name="pb_add_procedure",
                description="Manually add a procedure. Usually auto-created on task completion.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "project": {"type": "string", "description": "Project name"},
                        "task_pattern": {
                            "type": "string",
                            "description": "Description of the task pattern",
                        },
                        "steps": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "List of steps",
                        },
                        "task_type": {
                            "type": "string",
                            "description": "Task type classification (optional)",
                        },
                    },
                    "required": ["project", "task_pattern", "steps"],
                },
            )
        )
        tools.append(
            Tool(
                name="pb_search_procedures",
                description="Search procedures by task description. Uses similarity threshold >= 0.7.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "project": {"type": "string", "description": "Project name"},
                        "query": {"type": "string", "description": "Search query"},
                        "limit": {
                            "type": "integer",
                            "default": 5,
                            "description": "Max results to return",
                        },
                    },
                    "required": ["project", "query"],
                },
            )
        )
        tools.append(
            Tool(
                name="pb_procedure_outcome",
                description="Record the outcome of applying a procedure. Drives reinforcement score.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "project": {"type": "string", "description": "Project name"},
                        "proc_id": {"type": "integer", "description": "Procedure ID"},
                        "outcome": {
                            "type": "string",
                            "enum": ["success", "fail", "partial"],
                            "description": "Outcome of applying the procedure",
                        },
                    },
                    "required": ["project", "proc_id", "outcome"],
                },
            )
        )
        tools.append(
            Tool(
                name="pb_retire_procedure",
                description="Retire a procedure. It will no longer appear in briefings/search.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "project": {"type": "string", "description": "Project name"},
                        "proc_id": {"type": "integer", "description": "Procedure ID"},
                    },
                    "required": ["project", "proc_id"],
                },
            )
        )

    if features["typed_sections"]:
        tools.append(
            Tool(
                name="pb_classify_memory",
                description="Auto-classify all memory entries into types (red/concept/procedural/temporal/relation) via LLM enrichment.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "project": {"type": "string", "description": "Project name"},
                    },
                    "required": ["project"],
                },
            )
        )
        tools.append(
            Tool(
                name="pb_set_type",
                description="Manually set the type of a memory entry.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "project": {"type": "string", "description": "Project name"},
                        "index": {"type": "integer", "description": "Entry index"},
                        "memory_type": {
                            "type": "string",
                            "enum": [
                                "red",
                                "concept",
                                "procedural",
                                "temporal",
                                "relation",
                            ],
                            "description": "Memory type",
                        },
                    },
                    "required": ["project", "index", "memory_type"],
                },
            )
        )

    if features["consolidation"]:
        tools.append(
            Tool(
                name="pb_trigger_consolidation",
                description="Run the consolidation pipeline now. Creates immediate tier, extracts insights, promotes entries, archives old temporal.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "project": {"type": "string", "description": "Project name"},
                    },
                    "required": ["project"],
                },
            )
        )
        tools.append(
            Tool(
                name="pb_get_consolidation",
                description="Get consolidated memory entries. Optionally filter by tier.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "project": {"type": "string", "description": "Project name"},
                        "tier": {
                            "type": "string",
                            "enum": ["immediate", "consolidated", "timeless"],
                            "description": "Filter by tier (optional)",
                        },
                    },
                    "required": ["project"],
                },
            )
        )
        tools.append(
            Tool(
                name="pb_consolidation_status",
                description="Show consolidation status: per-tier counts.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "project": {"type": "string", "description": "Project name"},
                    },
                    "required": ["project"],
                },
            )
        )

    if features["anchors"]:
        tools.append(
            Tool(
                name="pb_get_anchors",
                description="Get environmental anchors: active tasks, completed tasks, commit log, most-edited files, red-ink reminders.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "project": {"type": "string", "description": "Project name"},
                    },
                    "required": ["project"],
                },
            )
        )

    if features["decay"]:
        tools.append(
            Tool(
                name="pb_decay_status",
                description="Show decay status for all entries: current strength, half-life, last-access time.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "project": {"type": "string", "description": "Project name"},
                    },
                    "required": ["project"],
                },
            )
        )
        tools.append(
            Tool(
                name="pb_search_archive",
                description="Search archived (forgotten) entries. Below 0.1 strength. Still searchable.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "project": {"type": "string", "description": "Project name"},
                        "q": {
                            "type": "string",
                            "default": "",
                            "description": "Search query (empty for all archived)",
                        },
                        "limit": {
                            "type": "integer",
                            "default": 20,
                            "description": "Max results",
                        },
                    },
                    "required": ["project"],
                },
            )
        )

    if features["costs"]:
        tools.append(
            Tool(
                name="pb_cost_analysis",
                description="Full cost analysis: tokens injected, saved by selective injection, saved by forgetting, effectiveness.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "project": {"type": "string", "description": "Project name"},
                        "days": {
                            "type": "integer",
                            "default": 30,
                            "description": "Number of days to analyze",
                        },
                    },
                    "required": ["project"],
                },
            )
        )
        tools.append(
            Tool(
                name="pb_cost_trends",
                description="Raw per-record cost trend data for charting over time.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "project": {"type": "string", "description": "Project name"},
                        "days": {
                            "type": "integer",
                            "default": 90,
                            "description": "Number of days of trend data",
                        },
                    },
                    "required": ["project"],
                },
            )
        )
        tools.append(
            Tool(
                name="pb_record_cost",
                description="Record a cost observation. Called after context injection and on task completion.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "project": {"type": "string", "description": "Project name"},
                        "session_id": {
                            "type": "string",
                            "description": "Session identifier",
                        },
                        "tokens_injected": {
                            "type": "integer",
                            "description": "Tokens injected into context",
                        },
                        "tokens_saved_injection": {
                            "type": "integer",
                            "description": "Tokens saved by selective injection",
                        },
                        "tokens_saved_forgetting": {
                            "type": "integer",
                            "description": "Tokens saved by forgetting (decay)",
                        },
                        "context_type": {
                            "type": "string",
                            "description": "Type of context (e.g. 'full', 'briefing', 'anchors')",
                        },
                        "task_outcome": {
                            "type": "string",
                            "description": "Task outcome (e.g. 'success', 'partial', 'fail')",
                        },
                        "breakdown": {
                            "type": "object",
                            "description": "Optional breakdown dict with token details",
                        },
                    },
                    "required": [
                        "project",
                        "session_id",
                        "tokens_injected",
                        "tokens_saved_injection",
                        "tokens_saved_forgetting",
                        "context_type",
                        "task_outcome",
                    ],
                },
            )
        )

    return tools


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    features = read_features()

    try:
        if name == "pb_help":
            text = get_help(
                module=arguments.get("module", ""),
                fmt=arguments.get("format", "markdown"),
                features=features,
            )
            return [TextContent(type="text", text=text)]

        feature = TOOL_TO_FEATURE.get(name)
        if feature and not features.get(feature):
            return [TextContent(type="text", text=disabled_msg(feature))]

        if name == "pb_add_memory":
            body = {"text": arguments["text"]}
            if "priority" in arguments:
                body["priority"] = arguments["priority"]
            if "memory_type" in arguments:
                body["memory_type"] = arguments["memory_type"]
            result = await api_post(f"/memory/{arguments['project']}", body)
            return [TextContent(type="text", text=fmt(result))]

        if name == "pb_get_memory":
            result = await api_get(f"/memory/{arguments['project']}")
            return [TextContent(type="text", text=fmt(result))]

        if name == "pb_get_full_memory":
            result = await api_get(f"/memory/{arguments['project']}/full")
            return [TextContent(type="text", text=fmt(result))]

        if name == "pb_replace_memory":
            result = await api_put(
                f"/memory/{arguments['project']}",
                {
                    "old": arguments["old"],
                    "new": arguments["new"],
                },
            )
            return [TextContent(type="text", text=fmt(result))]

        if name == "pb_get_context":
            result = await api_get(f"/ctx/{arguments['project']}")
            return [TextContent(type="text", text=fmt(result))]

        if name == "pb_recall":
            params = {"q": arguments["q"]}
            for k in ("project", "limit", "source", "enrich", "boost"):
                if k in arguments:
                    params[k] = arguments[k]
            result = await api_get("/recall", params)
            return [TextContent(type="text", text=fmt(result))]

        if name == "pb_get_red_ink":
            params = {}
            if "min_strength" in arguments:
                params["min_strength"] = arguments["min_strength"]
            result = await api_get(f"/red-ink/{arguments['project']}", params)
            return [TextContent(type="text", text=fmt(result))]

        if name == "pb_set_priority":
            result = await api_put(
                f"/memory/{arguments['project']}/priority",
                {
                    "index": arguments["index"],
                    "priority": arguments["priority"],
                },
            )
            return [TextContent(type="text", text=fmt(result))]

        if name == "pb_touch_entry":
            result = await api_post(
                f"/memory/{arguments['project']}/touch",
                {
                    "index": arguments["index"],
                },
            )
            return [TextContent(type="text", text=fmt(result))]

        if name == "pb_boost_entry":
            result = await api_post(
                f"/memory/{arguments['project']}/boost/{arguments['idx']}"
            )
            return [TextContent(type="text", text=fmt(result))]

        if name == "pb_briefing":
            body = {
                "task_description": arguments["task_description"],
                "project": arguments["project"],
            }
            if "max_tokens" in arguments:
                body["max_tokens"] = arguments["max_tokens"]
            result = await api_post("/briefing", body)
            return [TextContent(type="text", text=fmt(result))]

        if name == "pb_get_briefing_anchors":
            result = await api_get(f"/anchors/{arguments['project']}")
            return [TextContent(type="text", text=fmt(result))]

        if name == "pb_list_procedures":
            result = await api_get(f"/procedural/{arguments['project']}")
            return [TextContent(type="text", text=fmt(result))]

        if name == "pb_add_procedure":
            body = {
                "task_pattern": arguments["task_pattern"],
                "steps": arguments["steps"],
            }
            if "task_type" in arguments:
                body["task_type"] = arguments["task_type"]
            result = await api_post(f"/procedural/{arguments['project']}", body)
            return [TextContent(type="text", text=fmt(result))]

        if name == "pb_search_procedures":
            body = {"query": arguments["query"]}
            if "limit" in arguments:
                body["limit"] = arguments["limit"]
            result = await api_post(f"/procedural/{arguments['project']}/search", body)
            return [TextContent(type="text", text=fmt(result))]

        if name == "pb_procedure_outcome":
            result = await api_patch(
                f"/procedural/{arguments['project']}/{arguments['proc_id']}",
                {
                    "outcome": arguments["outcome"],
                },
            )
            return [TextContent(type="text", text=fmt(result))]

        if name == "pb_retire_procedure":
            result = await api_post(
                f"/procedural/{arguments['project']}/retire/{arguments['proc_id']}"
            )
            return [TextContent(type="text", text=fmt(result))]

        if name == "pb_classify_memory":
            result = await api_post(f"/memory/{arguments['project']}/classify")
            return [TextContent(type="text", text=fmt(result))]

        if name == "pb_set_type":
            result = await api_put(
                f"/memory/{arguments['project']}/type",
                {
                    "index": arguments["index"],
                    "memory_type": arguments["memory_type"],
                },
            )
            return [TextContent(type="text", text=fmt(result))]

        if name == "pb_trigger_consolidation":
            result = await api_post(f"/consolidation/{arguments['project']}/trigger")
            return [TextContent(type="text", text=fmt(result))]

        if name == "pb_get_consolidation":
            params = {}
            if "tier" in arguments:
                params["tier"] = arguments["tier"]
            result = await api_get(f"/consolidation/{arguments['project']}", params)
            return [TextContent(type="text", text=fmt(result))]

        if name == "pb_consolidation_status":
            result = await api_get(f"/consolidation/{arguments['project']}/status")
            return [TextContent(type="text", text=fmt(result))]

        if name == "pb_get_anchors":
            result = await api_get(f"/anchors/{arguments['project']}")
            return [TextContent(type="text", text=fmt(result))]

        if name == "pb_decay_status":
            result = await api_get(f"/memory/{arguments['project']}/decay")
            return [TextContent(type="text", text=fmt(result))]

        if name == "pb_search_archive":
            params = {}
            if "q" in arguments:
                params["q"] = arguments["q"]
            if "limit" in arguments:
                params["limit"] = arguments["limit"]
            result = await api_get(f"/memory/{arguments['project']}/archive", params)
            return [TextContent(type="text", text=fmt(result))]

        if name == "pb_cost_analysis":
            params = {"project": arguments["project"]}
            if "days" in arguments:
                params["days"] = arguments["days"]
            result = await api_get("/costs/analysis", params)
            return [TextContent(type="text", text=fmt(result))]

        if name == "pb_cost_trends":
            params = {"project": arguments["project"]}
            if "days" in arguments:
                params["days"] = arguments["days"]
            result = await api_get("/costs/trends", params)
            return [TextContent(type="text", text=fmt(result))]

        if name == "pb_record_cost":
            body = {
                "project": arguments["project"],
                "session_id": arguments["session_id"],
                "tokens_injected": arguments["tokens_injected"],
                "tokens_saved_injection": arguments["tokens_saved_injection"],
                "tokens_saved_forgetting": arguments["tokens_saved_forgetting"],
                "context_type": arguments["context_type"],
                "task_outcome": arguments["task_outcome"],
            }
            if "breakdown" in arguments:
                body["breakdown"] = arguments["breakdown"]
            result = await api_post("/costs/record", body)
            return [TextContent(type="text", text=fmt(result))]

        return [TextContent(type="text", text=f"Unknown tool: {name}")]

    except aiohttp.ClientError as e:
        return [
            TextContent(
                type="text",
                text=f"Error connecting to Paper Brain API at {MEMORIA_URL}: {e}",
            )
        ]
    except Exception as e:
        return [
            TextContent(
                type="text",
                text=f"Error: {type(e).__name__}: {e}\n{traceback.format_exc()}",
            )
        ]


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream, write_stream, server.create_initialization_options()
        )


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
