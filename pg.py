"""
PostgreSQL storage backend for Memoria.

Replaces all flat-file I/O (JSONL, JSON, Markdown) with asyncpg queries.
Pool injected by memoriad_global.py lifespan.
All functions accept an optional pool param; if None, uses module-level _pool.
"""

import json
import logging
import math
import time
from typing import Any, Optional

logger = logging.getLogger("memoria.pg")

try:
    import asyncpg
except ImportError:
    asyncpg = None

_pool: Any = None


def init_pool(pool) -> None:
    global _pool
    _pool = pool


async def _get_pool():
    if _pool is None:
        raise RuntimeError("PostgreSQL pool not initialized")
    return _pool


# ═════════════════════════════════════════════════════════════════
#  Chitchat Messages
# ═════════════════════════════════════════════════════════════════


async def store_message(msg: dict, room: str, pool=None):
    if pool is None:
        pool = await _get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO chitchat_messages (id, room, from_name, text, topic, ts, type, ingested_at) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8) ON CONFLICT (id) DO NOTHING",
            msg.get("ts", ""),
            room,
            msg.get("from", ""),
            msg.get("text", ""),
            msg.get("topic", ""),
            msg.get("ts", ""),
            msg.get("type", "message"),
            time.time(),
        )


async def get_recent_messages(room: str, limit: int = 100, pool=None) -> list[dict]:
    if pool is None:
        pool = await _get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM chitchat_messages WHERE room = $1 ORDER BY ingested_at DESC LIMIT $2",
            room,
            limit,
        )
        return [dict(r) for r in rows][::-1]  # oldest-first


async def search_messages(query: str, limit: int = 10, pool=None) -> list[dict]:
    if pool is None:
        pool = await _get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, room, from_name, text, "
            "  ts, type, ingested_at, "
            "  similarity(text, $1) AS rank "
            "FROM chitchat_messages "
            "WHERE text % $1 OR text ILIKE '%' || $1 || '%' "
            "ORDER BY rank DESC LIMIT $2",
            query,
            limit,
        )
        return [dict(r) for r in rows]


async def search_messages_enriched(
    query: str,
    expansion_terms: list[str],
    weight: float = 0.5,
    limit: int = 10,
    pool=None,
) -> list[dict]:
    if pool is None:
        pool = await _get_pool()
    expansion = " ".join(expansion_terms) if expansion_terms else ""
    async with pool.acquire() as conn:
        if expansion:
            rows = await conn.fetch(
                "SELECT id, room, from_name, text, ts, type, ingested_at, "
                "  similarity(text, $1) * 1.0 + "
                "  similarity(COALESCE(array_to_string(search_enrichments, ' '), ''), $2) * $3 AS rank "
                "FROM chitchat_messages "
                "WHERE text % $1 OR text ILIKE '%' || $1 || '%' "
                "   OR array_to_string(search_enrichments, ' ') % $2 "
                "ORDER BY rank DESC LIMIT $4",
                query,
                expansion,
                weight,
                limit,
            )
        else:
            rows = await conn.fetch(
                "SELECT id, room, from_name, text, ts, type, ingested_at, "
                "  similarity(text, $1) AS rank "
                "FROM chitchat_messages "
                "WHERE text % $1 OR text ILIKE '%' || $1 || '%' "
                "ORDER BY rank DESC LIMIT $2",
                query,
                limit,
            )
        return [dict(r) for r in rows]


async def prune_messages(
    room: str, consolidated_through: float, max_messages: int = 10000, pool=None
) -> int:
    if pool is None:
        pool = await _get_pool()
    async with pool.acquire() as conn:
        # Count rows to keep: unconsolidated + recent consolidated
        total = await conn.fetchval(
            "SELECT COUNT(*) FROM chitchat_messages WHERE room = $1",
            room,
        )
        if total <= max_messages:
            return 0
        # Delete oldest consolidated messages
        result = await conn.execute(
            "DELETE FROM chitchat_messages "
            "WHERE room = $1 AND ingested_at <= $2 "
            "  AND id NOT IN ("
            "    SELECT id FROM chitchat_messages "
            "    WHERE room = $1 "
            "    ORDER BY ingested_at DESC LIMIT $3"
            "  )",
            room,
            consolidated_through,
            max_messages,
        )
        return int(result.split()[-1]) if result else 0


async def get_all_messages(pool=None) -> list[dict]:
    if pool is None:
        pool = await _get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM chitchat_messages ORDER BY ingested_at")
        return [dict(r) for r in rows]


# ═════════════════════════════════════════════════════════════════
#  Sessions
# ═════════════════════════════════════════════════════════════════


async def append_session(rec: dict, pool=None):
    if pool is None:
        pool = await _get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO sessions (id, title, project, directory, created, task, outcome, "
            "  tools, tool_count, summary) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10) "
            "ON CONFLICT (id) DO NOTHING",
            rec.get("id", ""),
            rec.get("title", ""),
            rec.get("project", ""),
            rec.get("directory", ""),
            rec.get("created", 0),
            rec.get("task", ""),
            rec.get("outcome", ""),
            rec.get("tools", []),
            rec.get("tool_count", 0),
            (rec.get("summary", "") or "")[:500],
        )


async def search_sessions(query: str, limit: int = 10, pool=None) -> list[dict]:
    if pool is None:
        pool = await _get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, title, project, directory, created, task, "
            "  tools, tool_count, summary, outcome, "
            "  similarity(COALESCE(title, '') || ' ' || COALESCE(summary, ''), $1) AS rank "
            "FROM sessions "
            "WHERE title % $1 OR summary % $1 OR task % $1 "
            "   OR title ILIKE '%' || $1 || '%' "
            "   OR summary ILIKE '%' || $1 || '%' "
            "ORDER BY rank DESC LIMIT $2",
            query,
            limit,
        )
        return [dict(r) for r in rows]


async def search_sessions_enriched(
    query: str,
    expansion_terms: list[str],
    weight: float = 0.5,
    limit: int = 10,
    pool=None,
) -> list[dict]:
    if pool is None:
        pool = await _get_pool()
    expansion = " ".join(expansion_terms) if expansion_terms else ""
    text_base = "COALESCE(title, '') || ' ' || COALESCE(summary, '') || ' ' || COALESCE(task, '')"
    enrich_base = "COALESCE(array_to_string(search_enrichments, ' '), '')"
    async with pool.acquire() as conn:
        if expansion:
            rows = await conn.fetch(
                f"SELECT id, title, project, directory, created, task, "
                f"  tools, tool_count, summary, outcome, "
                f"  similarity({text_base}, $1) * 1.0 + "
                f"  similarity({enrich_base}, $2) * $3 AS rank "
                f"FROM sessions "
                f"WHERE title % $1 OR summary % $1 OR task % $1 "
                f"   OR title ILIKE '%' || $1 || '%' "
                f"   OR summary ILIKE '%' || $1 || '%' "
                f"   OR {enrich_base} % $2 "
                f"ORDER BY rank DESC LIMIT $4",
                query,
                expansion,
                weight,
                limit,
            )
        else:
            rows = await conn.fetch(
                f"SELECT id, title, project, directory, created, task, "
                f"  tools, tool_count, summary, outcome, "
                f"  similarity({text_base}, $1) AS rank "
                f"FROM sessions "
                f"WHERE title % $1 OR summary % $1 OR task % $1 "
                f"   OR title ILIKE '%' || $1 || '%' "
                f"   OR summary ILIKE '%' || $1 || '%' "
                f"ORDER BY rank DESC LIMIT $2",
                query,
                limit,
            )
        return [dict(r) for r in rows]


async def get_recent_sessions(
    n: int = 3, project: str | None = None, pool=None
) -> list[dict]:
    if pool is None:
        pool = await _get_pool()
    async with pool.acquire() as conn:
        if project:
            rows = await conn.fetch(
                "SELECT id, title, project, directory, created, task, "
                "  tools, tool_count, summary "
                "FROM sessions WHERE project = $1 ORDER BY created DESC LIMIT $2",
                project,
                n,
            )
        else:
            rows = await conn.fetch(
                "SELECT id, title, project, directory, created, task, "
                "  tools, tool_count, summary "
                "FROM sessions ORDER BY created DESC LIMIT $1",
                n,
            )
        return [dict(r) for r in rows]


async def get_session_count(pool=None) -> int:
    if pool is None:
        pool = await _get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval("SELECT COUNT(*) FROM sessions")


# ═════════════════════════════════════════════════════════════════
#  Agents
# ═════════════════════════════════════════════════════════════════


async def get_agent(agent_id: str, pool=None) -> dict | None:
    if pool is None:
        pool = await _get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM agents WHERE id = $1", agent_id)
        return dict(row) if row else None


async def save_agent(agent: dict, pool=None):
    if pool is None:
        pool = await _get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO agents (id, project, task, files, pid, started_at, "
            "  last_heartbeat, status, activity, commit_log, chitchat_name, "
            "  conflicts_warned, capabilities) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13) "
            "ON CONFLICT (id) DO UPDATE SET "
            "  last_heartbeat = EXCLUDED.last_heartbeat, "
            "  status = EXCLUDED.status, "
            "  activity = EXCLUDED.activity, "
            "  commit_log = EXCLUDED.commit_log",
            agent.get("id", ""),
            agent.get("project", ""),
            agent.get("task", ""),
            agent.get("files", []),
            agent.get("pid", 0),
            agent.get("started_at", time.time()),
            agent.get("last_heartbeat", time.time()),
            agent.get("status", "active"),
            agent.get("activity", ""),
            agent.get("commit_log", []),
            agent.get("chitchat_name", ""),
            agent.get("conflicts_warned", []),
            agent.get("capabilities", ["general"]),
        )


async def delete_agent(agent_id: str, pool=None):
    if pool is None:
        pool = await _get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM agents WHERE id = $1", agent_id)


async def list_agents(project: str | None = None, pool=None) -> list[dict]:
    if pool is None:
        pool = await _get_pool()
    async with pool.acquire() as conn:
        if project:
            rows = await conn.fetch(
                "SELECT * FROM agents WHERE project = $1 ORDER BY last_heartbeat DESC",
                project,
            )
        else:
            rows = await conn.fetch("SELECT * FROM agents ORDER BY last_heartbeat DESC")
        return [dict(r) for r in rows]


async def update_agent_heartbeat(
    agent_id: str,
    status: str = "active",
    activity: str = "",
    commit_log: list[str] | None = None,
    pool=None,
):
    if pool is None:
        pool = await _get_pool()
    async with pool.acquire() as conn:
        if commit_log:
            await conn.execute(
                "UPDATE agents SET last_heartbeat = $1, status = $2, activity = $3, "
                "  commit_log = array_cat(commit_log, $4::text[]) "
                "WHERE id = $5",
                time.time(),
                status,
                activity,
                commit_log,
                agent_id,
            )
        else:
            await conn.execute(
                "UPDATE agents SET last_heartbeat = $1, status = $2, activity = $3 "
                "WHERE id = $4",
                time.time(),
                status,
                activity,
                agent_id,
            )


# ═════════════════════════════════════════════════════════════════
#  Tasks
# ═════════════════════════════════════════════════════════════════


async def get_task(task_id: str, pool=None) -> dict | None:
    if pool is None:
        pool = await _get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM tasks WHERE id = $1", task_id)
        return dict(row) if row else None


async def save_task(task: dict, pool=None):
    if pool is None:
        pool = await _get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO tasks (id, project, title, description, status, "
            "  assigned_to, depends_on, created_at, assigned_at, "
            "  result, error, rollback_commit) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12) "
            "ON CONFLICT (id) DO UPDATE SET "
            "  status = EXCLUDED.status, "
            "  assigned_to = EXCLUDED.assigned_to, "
            "  assigned_at = EXCLUDED.assigned_at, "
            "  result = EXCLUDED.result, "
            "  error = EXCLUDED.error, "
            "  rollback_commit = EXCLUDED.rollback_commit",
            task.get("id", ""),
            task.get("project", ""),
            task.get("title", ""),
            task.get("description", ""),
            task.get("status", "pending"),
            task.get("assigned_to", ""),
            task.get("depends_on", []),
            task.get("created_at", time.time()),
            task.get("assigned_at"),
            task.get("result", ""),
            task.get("error", ""),
            task.get("rollback_commit", ""),
        )


async def delete_task(task_id: str, pool=None):
    if pool is None:
        pool = await _get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM tasks WHERE id = $1", task_id)


async def list_tasks(
    project: str | None = None, status: str | None = None, pool=None
) -> list[dict]:
    if pool is None:
        pool = await _get_pool()
    async with pool.acquire() as conn:
        if project and status:
            rows = await conn.fetch(
                "SELECT * FROM tasks WHERE project = $1 AND status = $2 ORDER BY created_at DESC",
                project,
                status,
            )
        elif project:
            rows = await conn.fetch(
                "SELECT * FROM tasks WHERE project = $1 ORDER BY created_at DESC",
                project,
            )
        elif status:
            rows = await conn.fetch(
                "SELECT * FROM tasks WHERE status = $1 ORDER BY created_at DESC",
                status,
            )
        else:
            rows = await conn.fetch("SELECT * FROM tasks ORDER BY created_at DESC")
        return [dict(r) for r in rows]


async def update_task(task_id: str, updates: dict, pool=None):
    if pool is None:
        pool = await _get_pool()
    sets = []
    vals = []
    i = 1
    for key, val in updates.items():
        if val is not None:
            sets.append(f"{key} = ${i}")
            vals.append(val)
            i += 1
    if not sets:
        return
    vals.append(task_id)
    sql = f"UPDATE tasks SET {', '.join(sets)} WHERE id = ${i}"
    async with pool.acquire() as conn:
        await conn.execute(sql, *vals)


# ═════════════════════════════════════════════════════════════════
#  Topics
# ═════════════════════════════════════════════════════════════════


async def list_topic_names(pool=None) -> list[str]:
    if pool is None:
        pool = await _get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT name FROM topics ORDER BY name")
        return [r["name"] for r in rows]


async def get_topic_facts(name: str, pool=None) -> list[str]:
    if pool is None:
        pool = await _get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT facts FROM topics WHERE name = $1", name)
        return row["facts"] if row else []


async def add_topic_fact(name: str, text: str, pool=None):
    if pool is None:
        pool = await _get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO topics (name, facts, updated_at) "
            "VALUES ($1, ARRAY[$2], now()) "
            "ON CONFLICT (name) DO UPDATE SET "
            "  facts = array_append(topics.facts, $2), "
            "  updated_at = now()",
            name,
            text,
        )


async def delete_topic_fact(name: str, index: int, pool=None):
    if pool is None:
        pool = await _get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE topics SET facts = facts[:$2] || facts[$2+2:], updated_at = now() "
            "WHERE name = $1",
            name,
            index,
        )


async def edit_topic_fact(name: str, index: int, new_text: str, pool=None):
    if pool is None:
        pool = await _get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE topics SET facts[$2+1] = $3, updated_at = now() WHERE name = $1",
            name,
            index,
            new_text,
        )


async def delete_topic(name: str, pool=None):
    if pool is None:
        pool = await _get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM topics WHERE name = $1", name)


async def search_topics(query: str, limit: int = 3, pool=None) -> list[dict]:
    if pool is None:
        pool = await _get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT name, facts[1:3] AS preview_facts, "
            "  similarity(name || ' ' || array_to_string(facts, ' '), $1) AS rank "
            "FROM topics "
            "WHERE name % $1 "
            "   OR EXISTS (SELECT 1 FROM unnest(facts) f WHERE f % $1) "
            "ORDER BY rank DESC LIMIT $2",
            query,
            limit,
        )
        return [{"name": r["name"], "facts": r["preview_facts"]} for r in rows]


async def search_topics_enriched(
    query: str,
    expansion_terms: list[str],
    weight: float = 0.5,
    limit: int = 3,
    pool=None,
) -> list[dict]:
    if pool is None:
        pool = await _get_pool()
    expansion = " ".join(expansion_terms) if expansion_terms else ""
    text_base = "name || ' ' || array_to_string(facts, ' ')"
    enrich_base = "COALESCE(array_to_string(search_enrichments, ' '), '')"
    async with pool.acquire() as conn:
        if expansion:
            rows = await conn.fetch(
                f"SELECT name, facts[1:3] AS preview_facts, "
                f"  similarity({text_base}, $1) * 1.0 + "
                f"  similarity({enrich_base}, $2) * $3 AS rank "
                f"FROM topics "
                f"WHERE name % $1 "
                f"   OR EXISTS (SELECT 1 FROM unnest(facts) f WHERE f % $1) "
                f"   OR {enrich_base} % $2 "
                f"ORDER BY rank DESC LIMIT $4",
                query,
                expansion,
                weight,
                limit,
            )
        else:
            rows = await conn.fetch(
                f"SELECT name, facts[1:3] AS preview_facts, "
                f"  similarity({text_base}, $1) AS rank "
                f"FROM topics "
                f"WHERE name % $1 "
                f"   OR EXISTS (SELECT 1 FROM unnest(facts) f WHERE f % $1) "
                f"ORDER BY rank DESC LIMIT $2",
                query,
                limit,
            )
        return [{"name": r["name"], "facts": r["preview_facts"]} for r in rows]


async def search_papers(
    query: str,
    expansion_terms: list[str] = None,
    weight: float = 0.5,
    limit: int = 5,
    pool=None,
) -> list[dict]:
    if pool is None:
        pool = await _get_pool()
    expansion = " ".join(expansion_terms) if expansion_terms else ""
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SET LOCAL pg_trgm.similarity_threshold = 0.01")
            if expansion:
                rows = await conn.fetch(
                    "SELECT id, filename, folder, title, "
                    "  similarity(text, $1) * 1.0 + "
                    "  similarity(COALESCE(enriched_text, ''), $2) * $3 AS rank "
                    "FROM papers "
                    "WHERE text % $1 OR enriched_text % $2 "
                    "   OR text ILIKE '%' || $1 || '%' "
                    "ORDER BY rank DESC LIMIT $4",
                    query,
                    expansion,
                    weight,
                    limit,
                )
            else:
                rows = await conn.fetch(
                    "SELECT id, filename, folder, title, "
                    "  similarity(text, $1) AS rank "
                    "FROM papers "
                    "WHERE text % $1 OR text ILIKE '%' || $1 || '%' "
                    "ORDER BY rank DESC LIMIT $2",
                    query,
                    limit,
                )
            return [dict(r) for r in rows]


async def search_memory(query: str, limit: int = 5, pool=None) -> list[dict]:
    if pool is None:
        pool = await _get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, project, entry, similarity(entry, $1) AS rank "
            "FROM project_memory "
            "WHERE entry % $1 OR entry ILIKE '%' || $1 || '%' "
            "ORDER BY rank DESC LIMIT $2",
            query,
            limit,
        )
        return [dict(r) for r in rows]


async def search_memory_by_project(
    project: str, query: str, limit: int = 5, pool=None
) -> list[dict]:
    if pool is None:
        pool = await _get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, project, entry, similarity(entry, $1) AS rank "
            "FROM project_memory "
            "WHERE project = $2 AND (entry % $1 OR entry ILIKE '%' || $1 || '%') "
            "ORDER BY rank DESC LIMIT $3",
            query,
            project,
            limit,
        )
        return [dict(r) for r in rows]


async def search_memory_enriched(
    query: str,
    expansion_terms: list[str] = None,
    weight: float = 0.5,
    limit: int = 5,
    pool=None,
) -> list[dict]:
    if pool is None:
        pool = await _get_pool()
    expansion = " ".join(expansion_terms) if expansion_terms else ""
    enrich_base = "COALESCE(array_to_string(search_enrichments, ' '), '')"
    async with pool.acquire() as conn:
        if expansion:
            rows = await conn.fetch(
                f"SELECT id, project, entry, "
                f"  similarity(entry, $1) * 1.0 + "
                f"  similarity({enrich_base}, $2) * $3 AS rank "
                f"FROM project_memory "
                f"WHERE entry % $1 OR entry ILIKE '%' || $1 || '%' "
                f"   OR {enrich_base} % $2 "
                f"ORDER BY rank DESC LIMIT $4",
                query,
                expansion,
                weight,
                limit,
            )
        else:
            rows = await conn.fetch(
                "SELECT id, project, entry, similarity(entry, $1) AS rank "
                "FROM project_memory "
                "WHERE entry % $1 OR entry ILIKE '%' || $1 || '%' "
                "ORDER BY rank DESC LIMIT $2",
                query,
                limit,
            )
        return [dict(r) for r in rows]


# ═════════════════════════════════════════════════════════════════
#  Proposals
# ═════════════════════════════════════════════════════════════════


async def list_proposals(pool=None) -> list[dict]:
    if pool is None:
        pool = await _get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM proposals ORDER BY proposed_at DESC")
        return [dict(r) for r in rows]


async def create_proposal(pid: str, text: str, topic: str, pool=None):
    if pool is None:
        pool = await _get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO proposals (id, text, topic, proposed_at, source) "
            "VALUES ($1, $2, $3, $4, '') ON CONFLICT (id) DO NOTHING",
            pid,
            text,
            topic,
            time.time(),
        )


async def delete_proposal(pid: str, pool=None) -> bool:
    if pool is None:
        pool = await _get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute("DELETE FROM proposals WHERE id = $1", pid)
        return "DELETE 1" in result


async def clear_proposals(pool=None):
    if pool is None:
        pool = await _get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM proposals")


async def accept_proposal(pid: str, pool=None) -> dict | None:
    if pool is None:
        pool = await _get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "DELETE FROM proposals WHERE id = $1 RETURNING *", pid
        )
        if row:
            await add_topic_fact(row["topic"], row["text"], pool=pool)
            return dict(row)
        return None


# ═════════════════════════════════════════════════════════════════
#  Project Memory
# ═════════════════════════════════════════════════════════════════


async def get_memory_entries(project: str, pool=None) -> list[str]:
    if pool is None:
        pool = await _get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT entry FROM project_memory WHERE project = $1 ORDER BY id",
            project,
        )
        return [r["entry"] for r in rows]


async def get_memory_entries_full(project: str, pool=None) -> list[dict]:
    if pool is None:
        pool = await _get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, entry, priority, memory_type, strength, last_accessed "
            "FROM project_memory WHERE project = $1 ORDER BY id",
            project,
        )
        return [dict(r) for r in rows]


async def get_red_ink(project: str, min_strength: float = 0.0, pool=None) -> list[dict]:
    if pool is None:
        pool = await _get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, entry, priority, memory_type, strength "
            "FROM project_memory WHERE project = $1 AND priority = 'critical' "
            "AND strength >= $2 ORDER BY id",
            project,
            min_strength,
        )
        return [dict(r) for r in rows]


async def get_consolidated_for_injection(project: str, pool=None) -> list[dict]:
    """Get consolidated memory entries for context injection.
    Only returns entries with strength >= 0.3 (above archive threshold).
    """
    if pool is None:
        pool = await _get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, content, tier, priority, memory_type, strength "
            "FROM consolidated_memory WHERE project = $1 "
            "AND strength >= 0.3 ORDER BY priority DESC, strength DESC",
            project,
        )
        return [dict(r) for r in rows]


async def touch_consolidated_by_ids(ids: list[int], pool=None) -> int:
    if not ids:
        return 0
    if pool is None:
        pool = await _get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE consolidated_memory SET last_accessed = extract(epoch from now()), "
            "strength = LEAST(strength + 0.3, 1.0) WHERE id = ANY($1)",
            ids,
        )
        tag = result.split()[-1] if result else "0"
        return int(tag)


async def update_memory_priority(
    project: str, index: int, priority: str, pool=None
) -> bool:
    if pool is None:
        pool = await _get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id FROM project_memory WHERE project = $1 ORDER BY id "
            "OFFSET $2 LIMIT 1",
            project,
            max(index - 1, 0),
        )
        if not row:
            return False
        await conn.execute(
            "UPDATE project_memory SET priority = $1 WHERE id = $2",
            priority,
            row["id"],
        )
        return True


async def update_memory_type(
    project: str, index: int, memory_type: str, pool=None
) -> bool:
    if pool is None:
        pool = await _get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id FROM project_memory WHERE project = $1 ORDER BY id "
            "OFFSET $2 LIMIT 1",
            project,
            max(index - 1, 0),
        )
        if not row:
            return False
        await conn.execute(
            "UPDATE project_memory SET memory_type = $1 WHERE id = $2",
            memory_type,
            row["id"],
        )
        return True


async def touch_memory_access(project: str, index: int, pool=None) -> bool:
    if pool is None:
        pool = await _get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id FROM project_memory WHERE project = $1 ORDER BY id "
            "OFFSET $2 LIMIT 1",
            project,
            max(index - 1, 0),
        )
        if not row:
            return False
        await conn.execute(
            "UPDATE project_memory SET last_accessed = extract(epoch from now()), "
            "strength = LEAST(strength + 0.3, 1.0) WHERE id = $1",
            row["id"],
        )
        return True


async def touch_memory_by_ids(ids: list[int], pool=None) -> int:
    if not ids:
        return 0
    if pool is None:
        pool = await _get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE project_memory SET last_accessed = extract(epoch from now()), "
            "strength = LEAST(strength + 0.3, 1.0) WHERE id = ANY($1)",
            ids,
        )
        tag = result.split()[-1] if result else "0"
        return int(tag)


async def add_memory_entry(
    project: str, text: str, priority: str = "normal", pool=None
):
    if pool is None:
        pool = await _get_pool()
    memory_type = "red" if priority == "critical" else "temporal"
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO project_memory (project, entry, priority, memory_type) VALUES ($1, $2, $3, $4)",
            project,
            text,
            priority,
            memory_type,
        )


async def get_memory_entry_id(project: str, text: str, pool=None) -> int | None:
    if pool is None:
        pool = await _get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT id FROM project_memory WHERE project = $1 AND entry = $2 "
            "ORDER BY created_at DESC LIMIT 1",
            project,
            text,
        )


async def replace_memory_entry(
    project: str, old_substring: str, new_text: str, pool=None
) -> bool:
    if pool is None:
        pool = await _get_pool()
    async with pool.acquire() as conn:
        # Find entry containing old_substring and replace it
        row = await conn.fetchrow(
            "SELECT id, entry FROM project_memory "
            "WHERE project = $1 AND entry LIKE '%' || $2 || '%' LIMIT 1",
            project,
            old_substring,
        )
        if not row:
            return False
        await conn.execute(
            "UPDATE project_memory SET entry = $1 WHERE id = $2",
            new_text,
            row["id"],
        )
        return True


async def delete_memory_entry(project: str, index: int, pool=None):
    if pool is None:
        pool = await _get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM project_memory WHERE project = $1 AND id IN ("
            "  SELECT id FROM project_memory WHERE project = $1 ORDER BY id OFFSET $2 LIMIT 1"
            ")",
            project,
            max(index - 1, 0),
        )


async def get_memory_char_count(project: str, pool=None) -> int:
    if pool is None:
        pool = await _get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT COALESCE(SUM(LENGTH(entry)), 0) FROM project_memory WHERE project = $1",
            project,
        )
        return row[0]


async def get_memory_by_type(project: str, memory_type: str, pool=None) -> list[dict]:
    if pool is None:
        pool = await _get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, entry, priority, memory_type, strength, last_accessed "
            "FROM project_memory WHERE project = $1 AND memory_type = $2 ORDER BY id",
            project,
            memory_type,
        )
        return [dict(r) for r in rows]


async def auto_classify_migration(project: str, pool=None) -> dict:
    import enrichment

    if pool is None:
        pool = await _get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, entry, priority, memory_type "
            "FROM project_memory WHERE project = $1 AND memory_type = 'temporal' "
            "ORDER BY id",
            project,
        )
    classified = 0
    errors = 0
    skipped = 0
    for row in rows:
        entry_id = row["id"]
        entry_text = row["entry"]
        priority = row["priority"]
        if priority == "critical":
            await update_memory_type_by_id(entry_id, "red", pool)
            classified += 1
            continue
        try:
            label = await enrichment.classify_memory_entry(entry_text)
        except Exception:
            errors += 1
            continue
        if label and label in ("red", "concept", "procedural", "relation"):
            await update_memory_type_by_id(entry_id, label, pool)
            classified += 1
        else:
            skipped += 1
    return {
        "classified": classified,
        "skipped": skipped,
        "errors": errors,
        "total": len(rows),
    }


async def update_memory_type_by_id(entry_id: int, memory_type: str, pool=None):
    valid = ("red", "concept", "procedural", "temporal", "relation")
    if memory_type not in valid:
        raise ValueError(f"memory_type must be one of {valid}, got {memory_type!r}")
    if pool is None:
        pool = await _get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE project_memory SET memory_type = $1 WHERE id = $2",
            memory_type,
            entry_id,
        )


# ═════════════════════════════════════════════════════════════════
#  Safety Snapshots
# ═════════════════════════════════════════════════════════════════


async def create_snapshot(
    sid: str,
    project: str,
    project_dir: str,
    commit_hash: str,
    message: str,
    agent_id: str,
    pool=None,
):
    if pool is None:
        pool = await _get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO safety_snapshots (id, project, project_dir, commit_hash, "
            "  message, agent_id, created_at) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7)",
            sid,
            project,
            project_dir,
            commit_hash,
            message,
            agent_id,
            time.time(),
        )


async def list_snapshots(project: str, pool=None) -> list[dict]:
    if pool is None:
        pool = await _get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM safety_snapshots WHERE project = $1 ORDER BY created_at DESC",
            project,
        )
        return [dict(r) for r in rows]


# ═════════════════════════════════════════════════════════════════
#  Cortex — read/write per-project rows
# ═════════════════════════════════════════════════════════════════


async def read_cortex_row(table: str, project: str, pool=None) -> dict | None:
    if pool is None:
        pool = await _get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT * FROM {table} WHERE project = $1",
            project,
        )
        return dict(row) if row else None


async def upsert_cortex_row(table: str, project: str, data: dict, pool=None):
    if pool is None:
        pool = await _get_pool()
    sets = []
    vals = [project]
    i = 2
    for key, val in data.items():
        if key == "project":
            continue
        sets.append(f"{key} = ${i}")
        vals.append(val)
        i += 1
    sql = (
        f"INSERT INTO {table} (project, {', '.join(k for k in data if k != 'project')}) "
        f"VALUES ($1, {', '.join(f'${j}' for j in range(2, i))}) "
        f"ON CONFLICT (project) DO UPDATE SET "
        f"{', '.join(sets)}, updated_at = now()"
    )
    async with pool.acquire() as conn:
        await conn.execute(sql, *vals)


async def append_efferent(project: str, entry: dict, pool=None):
    if pool is None:
        pool = await _get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO cortex_efferent (project, state, action, rpe, go_after, nogo_after, timestamp) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7)",
            project,
            entry.get("state", ""),
            entry.get("action", ""),
            entry.get("rpe"),
            entry.get("Go_after"),
            entry.get("NoGo_after"),
            entry.get("timestamp", time.time()),
        )


# ═════════════════════════════════════════════════════════════════
#  Federation
# ═════════════════════════════════════════════════════════════════


async def list_federation_peers(pool=None) -> list[dict]:
    if pool is None:
        pool = await _get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM federation_peers ORDER BY name")
        return [dict(r) for r in rows]


async def save_federation_peer(name: str, url: str, api_key: str = "", pool=None):
    if pool is None:
        pool = await _get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO federation_peers (name, url, api_key, updated_at) "
            "VALUES ($1, $2, $3, $4) "
            "ON CONFLICT (name) DO UPDATE SET "
            "  url = EXCLUDED.url, api_key = EXCLUDED.api_key, updated_at = EXCLUDED.updated_at",
            name,
            url,
            api_key,
            time.time(),
        )


async def delete_federation_peer(name: str, pool=None) -> bool:
    if pool is None:
        pool = await _get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM federation_peers WHERE name = $1", name
        )
        return "DELETE 1" in result


async def get_federation_sync_state(pool=None) -> dict:
    if pool is None:
        pool = await _get_pool()
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT state FROM federation_sync_state WHERE name = 'singleton'",
            )
            return dict(row["state"]) if row else {}
    except Exception:
        return {}


async def save_federation_sync_state(state: dict, pool=None):
    if pool is None:
        pool = await _get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO federation_sync_state (name, state, updated_at) "
            "VALUES ('singleton', $1::jsonb, now()) "
            "ON CONFLICT (name) DO UPDATE SET state = EXCLUDED.state, updated_at = now()",
            json.dumps(state),
        )


# ═════════════════════════════════════════════════════════════════
#  Cultural Memory (per-project JSONB row)
# ═════════════════════════════════════════════════════════════════


async def read_cultural_memory(project: str, pool=None) -> dict | None:
    if pool is None:
        pool = await _get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM cultural_memory WHERE project = $1",
            project,
        )
        return dict(row) if row else None


async def upsert_cultural_memory(
    project: str, facts: list[dict], generation: int, pool=None
):
    if pool is None:
        pool = await _get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO cultural_memory (project, facts, generation, updated_at) "
            "VALUES ($1, $2::jsonb, $3, now()) "
            "ON CONFLICT (project) DO UPDATE SET "
            "  facts = EXCLUDED.facts, generation = EXCLUDED.generation, updated_at = now()",
            project,
            json.dumps(facts),
            generation,
        )


# ═════════════════════════════════════════════════════════════════
#  Lessons
# ═════════════════════════════════════════════════════════════════


async def save_lesson(
    lesson_id: str,
    title: str,
    topic: str,
    prerequisites: list[str],
    facts: list[str],
    examples: list[str],
    exercises: list[str],
    teacher_agent: str,
    creator_project: str,
    generation: int = 0,
    parent_id: str | None = None,
    score: float = 0.5,
    n_students: int = 0,
    created_at: float | None = None,
    pool=None,
):
    if pool is None:
        pool = await _get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO lessons (lesson_id, title, topic, prerequisites, facts, "
            "  examples, exercises, teacher_agent, creator_project, "
            "  generation, parent_id, score, n_students, created_at) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14) "
            "ON CONFLICT (lesson_id) DO UPDATE SET "
            "  score = EXCLUDED.score, n_students = EXCLUDED.n_students, "
            "  facts = EXCLUDED.facts",
            lesson_id,
            title,
            topic,
            prerequisites,
            facts,
            examples,
            exercises,
            teacher_agent,
            creator_project,
            generation,
            parent_id,
            score,
            n_students,
            created_at or time.time(),
        )


async def load_lesson(lesson_id: str, pool=None) -> dict | None:
    if pool is None:
        pool = await _get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM lessons WHERE lesson_id = $1", lesson_id
        )
        return dict(row) if row else None


async def delete_lesson_row(lesson_id: str, pool=None):
    if pool is None:
        pool = await _get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM lessons WHERE lesson_id = $1", lesson_id)


async def list_lessons(
    topic: str | None = None,
    project: str | None = None,
    min_score: float = 0.0,
    pool=None,
) -> list[dict]:
    if pool is None:
        pool = await _get_pool()
    async with pool.acquire() as conn:
        if topic and project:
            rows = await conn.fetch(
                "SELECT * FROM lessons WHERE topic = $1 AND creator_project = $2 AND score >= $3 "
                "ORDER BY score DESC",
                topic,
                project,
                min_score,
            )
        elif topic:
            rows = await conn.fetch(
                "SELECT * FROM lessons WHERE topic = $1 AND score >= $2 ORDER BY score DESC",
                topic,
                min_score,
            )
        elif project:
            rows = await conn.fetch(
                "SELECT * FROM lessons WHERE creator_project = $1 AND score >= $2 ORDER BY score DESC",
                project,
                min_score,
            )
        else:
            rows = await conn.fetch(
                "SELECT * FROM lessons WHERE score >= $1 ORDER BY score DESC",
                min_score,
            )
        return [dict(r) for r in rows]


async def record_student_outcome(
    lesson_id: str,
    student_agent: str,
    success: bool,
    outcome: float,
    score_after: float,
    pool=None,
):
    if pool is None:
        pool = await _get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO student_outcomes (lesson_id, student_agent, success, outcome, score_after, timestamp) "
            "VALUES ($1, $2, $3, $4, $5, $6)",
            lesson_id,
            student_agent,
            success,
            outcome,
            score_after,
            time.time(),
        )


async def get_student_outcomes(
    student_agent: str, limit: int = 20, pool=None
) -> list[dict]:
    if pool is None:
        pool = await _get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM student_outcomes WHERE student_agent = $1 "
            "ORDER BY timestamp DESC LIMIT $2",
            student_agent,
            limit,
        )
        return [dict(r) for r in rows]


# ═════════════════════════════════════════════════════════════════
#  Procedural Memory (basal ganglia)
# ═════════════════════════════════════════════════════════════════


async def add_procedure(
    project: str,
    task_pattern: str,
    task_type: str | None,
    steps: list[str],
    proven_by: str = "",
    pool=None,
) -> int:
    if pool is None:
        pool = await _get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO procedural_memory "
            "(project, task_pattern, task_type, steps, proven_by) "
            "VALUES ($1, $2, $3, $4::jsonb, "
            "  CASE WHEN $5 != '' THEN ARRAY[$5] ELSE '{}'::text[] END) "
            "RETURNING id",
            project,
            task_pattern,
            task_type,
            json.dumps(steps),
            proven_by,
        )
        return row["id"]


async def search_procedures(
    project: str,
    query: str,
    limit: int = 5,
    pool=None,
    similarity_threshold: float | None = None,
) -> list[dict]:
    if pool is None:
        pool = await _get_pool()
    threshold = similarity_threshold if similarity_threshold is not None else 0.7
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(f"SET LOCAL pg_trgm.similarity_threshold = {threshold}")
            rows = await conn.fetch(
                "SELECT id, project, task_pattern, task_type, steps, "
                "  success_count, fail_count, reinforcement_score, "
                "  retired, proven_by, created_at "
                "FROM procedural_memory "
                "WHERE project = $1 AND retired = FALSE "
                "  AND (task_pattern % $2 OR task_pattern ILIKE '%' || $2 || '%') "
                "ORDER BY reinforcement_score DESC LIMIT $3",
                project,
                query,
                limit,
            )
            return [dict(r) for r in rows]


async def update_procedure_outcome(
    procedure_id: int, success: bool, proven_by: str = "", pool=None
) -> dict | None:
    if pool is None:
        pool = await _get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT success_count, fail_count FROM procedural_memory WHERE id = $1",
                procedure_id,
            )
            if not row:
                return None
            cur_succ = row["success_count"]
            cur_fail = row["fail_count"]
            if success:
                new_succ = cur_succ + 1
                new_fail = cur_fail
                accuracy = (
                    new_succ / (new_succ + new_fail) if new_succ + new_fail > 0 else 0.5
                )
                new_score = accuracy * math.log(new_succ + 1)
                new_score = round(max(0.0, min(1.0, new_score)), 6)
                if proven_by:
                    row2 = await conn.fetchrow(
                        "UPDATE procedural_memory SET success_count = $1, fail_count = $2, "
                        "  reinforcement_score = $3, last_success_at = extract(epoch from now()), "
                        "  proven_by = array_append(proven_by, $4) "
                        "WHERE id = $5 RETURNING *",
                        new_succ,
                        new_fail,
                        new_score,
                        proven_by,
                        procedure_id,
                    )
                else:
                    row2 = await conn.fetchrow(
                        "UPDATE procedural_memory SET success_count = $1, fail_count = $2, "
                        "  reinforcement_score = $3, last_success_at = extract(epoch from now()) "
                        "WHERE id = $4 RETURNING *",
                        new_succ,
                        new_fail,
                        new_score,
                        procedure_id,
                    )
            else:
                new_succ = cur_succ
                new_fail = cur_fail + 1
                accuracy = new_succ / (new_succ + new_fail) if new_succ > 0 else 0.0
                new_score = accuracy * math.log(new_succ + 1)
                new_score = round(max(0.0, min(1.0, new_score)), 6)
                row2 = await conn.fetchrow(
                    "UPDATE procedural_memory SET success_count = $1, fail_count = $2, "
                    "  reinforcement_score = $3 "
                    "WHERE id = $4 RETURNING *",
                    new_succ,
                    new_fail,
                    new_score,
                    procedure_id,
                )
            if (
                row2
                and row2["fail_count"] > row2["success_count"] * 2
                and row2["fail_count"] > 5
            ):
                await conn.execute(
                    "UPDATE procedural_memory SET retired = TRUE WHERE id = $1",
                    procedure_id,
                )
            return dict(row2) if row2 else None


async def list_procedures(project: str, retired: bool = False, pool=None) -> list[dict]:
    if pool is None:
        pool = await _get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, project, task_pattern, task_type, steps, "
            "  success_count, fail_count, reinforcement_score, "
            "  retired, proven_by, created_at "
            "FROM procedural_memory "
            "WHERE project = $1 AND retired = $2 "
            "ORDER BY reinforcement_score DESC",
            project,
            retired,
        )
        return [dict(r) for r in rows]


async def retire_procedure(procedure_id: int, pool=None) -> bool:
    if pool is None:
        pool = await _get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE procedural_memory SET retired = TRUE WHERE id = $1",
            procedure_id,
        )
        return "UPDATE 1" in result


# ═════════════════════════════════════════════════════════════════
#  Consolidated Memory (3-tier cortex)
# ═════════════════════════════════════════════════════════════════


async def add_consolidated(
    project: str,
    tier: str,
    content: str,
    memory_type: str = "concept",
    priority: str = "normal",
    source_sessions: list[str] | None = None,
    source_episode_ids: list[str] | None = None,
    pool=None,
) -> int:
    valid_tiers = ("immediate", "consolidated", "timeless")
    if tier not in valid_tiers:
        raise ValueError(f"tier must be one of {valid_tiers}, got {tier!r}")
    valid_types = ("red", "concept", "procedural", "temporal", "relation")
    if memory_type not in valid_types:
        raise ValueError(
            f"memory_type must be one of {valid_types}, got {memory_type!r}"
        )
    valid_priorities = ("critical", "important", "normal")
    if priority not in valid_priorities:
        raise ValueError(
            f"priority must be one of {valid_priorities}, got {priority!r}"
        )
    if pool is None:
        pool = await _get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO consolidated_memory "
            "(project, tier, content, memory_type, priority, source_sessions, source_episode_ids) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING id",
            project,
            tier,
            content,
            memory_type,
            priority,
            source_sessions or [],
            source_episode_ids or [],
        )
        return row["id"]


async def get_consolidated(
    project: str, tier: str | None = None, pool=None
) -> list[dict]:
    if pool is None:
        pool = await _get_pool()
    async with pool.acquire() as conn:
        if tier:
            rows = await conn.fetch(
                "SELECT * FROM consolidated_memory "
                "WHERE project = $1 AND tier = $2 "
                "ORDER BY created_at DESC",
                project,
                tier,
            )
        else:
            rows = await conn.fetch(
                "SELECT * FROM consolidated_memory WHERE project = $1 "
                "ORDER BY tier, created_at DESC",
                project,
            )
        return [dict(r) for r in rows]


async def promote_consolidated(consolidated_id: int, new_tier: str, pool=None) -> bool:
    if pool is None:
        pool = await _get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE consolidated_memory SET tier = $1 WHERE id = $2",
            new_tier,
            consolidated_id,
        )
        return "UPDATE 1" in result


async def run_consolidation(project: str, pool=None) -> dict:
    import enrichment

    if pool is None:
        pool = await _get_pool()
    seven_days_ago = time.time() - 7 * 86400
    result = {
        "immediate_created": 0,
        "insights_created": 0,
        "consolidated_to_timeless": 0,
        "archived_temporal": 0,
        "errors": 0,
    }

    async with pool.acquire() as conn:
        latest_session = await conn.fetchrow(
            "SELECT id, title, summary, task, created FROM sessions "
            "WHERE project = $1 ORDER BY created DESC LIMIT 1",
            project,
        )

    if latest_session:
        summary_text = " | ".join(
            p
            for p in [
                latest_session.get("title", ""),
                latest_session.get("summary", ""),
                latest_session.get("task", ""),
            ]
            if p
        ).strip()[:500]
        if summary_text:
            async with pool.acquire() as conn_imm:
                existing_immediate = await conn_imm.fetch(
                    "SELECT id FROM consolidated_memory "
                    "WHERE project = $1 AND tier = 'immediate' LIMIT 1",
                    project,
                )
            if not existing_immediate:
                await add_consolidated(
                    project,
                    tier="immediate",
                    content=summary_text,
                    memory_type="temporal",
                    source_sessions=[str(latest_session["id"])]
                    if latest_session.get("id")
                    else [],
                    pool=pool,
                )
                result["immediate_created"] += 1
            else:
                async with pool.acquire() as conn3:
                    await conn3.execute(
                        "UPDATE consolidated_memory SET content = $1, "
                        "source_sessions = $2, last_accessed = extract(epoch from now()) "
                        "WHERE project = $3 AND tier = 'immediate'",
                        summary_text,
                        [str(latest_session["id"])] if latest_session.get("id") else [],
                        project,
                    )

    async with pool.acquire() as conn:
        recent_sessions = await conn.fetch(
            "SELECT id, title, summary, task, created FROM sessions "
            "WHERE project = $1 AND created >= $2 ORDER BY created DESC LIMIT 20",
            project,
            seven_days_ago,
        )

    if recent_sessions:
        summaries = []
        for s in recent_sessions:
            parts = [s.get("title", ""), s.get("summary", ""), s.get("task", "")]
            text = " | ".join(p for p in parts if p)
            if text.strip():
                summaries.append(text.strip()[:500])
        if summaries:
            try:
                insights = await enrichment.consolidate_sessions(summaries)
                session_ids = [str(s["id"]) for s in recent_sessions if s.get("id")]
                for insight in insights:
                    content = insight.get("content", "").strip()
                    mtype = insight.get("type", "concept")
                    if not content:
                        continue
                    if mtype not in ("concept", "procedural", "relation"):
                        mtype = "concept"
                    await add_consolidated(
                        project,
                        tier="consolidated",
                        content=content,
                        memory_type=mtype,
                        source_sessions=session_ids,
                        pool=pool,
                    )
                    result["insights_created"] += 1
            except Exception:
                logger.warning("consolidation LLM call failed", exc_info=True)
                result["errors"] += 1

    async with pool.acquire() as conn:
        critical_consolidated = await conn.fetch(
            "SELECT id FROM consolidated_memory "
            "WHERE project = $1 AND tier = 'consolidated' AND priority = 'critical'",
            project,
        )
    for entry in critical_consolidated:
        ok = await promote_consolidated(entry["id"], "timeless", pool=pool)
        if ok:
            result["consolidated_to_timeless"] += 1

    async with pool.acquire() as conn:
        high_access = await conn.fetch(
            "SELECT id FROM consolidated_memory "
            "WHERE project = $1 AND tier = 'consolidated' "
            "AND (strength >= 0.8 OR array_length(source_sessions, 1) >= 3) "
            "AND priority != 'critical'",
            project,
        )
    for entry in high_access:
        ok = await promote_consolidated(entry["id"], "timeless", pool=pool)
        if ok:
            result["consolidated_to_timeless"] += 1

    try:
        from social_learning import get_cultural_memory, consolidate_cultural_knowledge

        cultural = get_cultural_memory(project)
        if cultural and cultural.get("facts"):
            async with pool.acquire() as conn2:
                hipp_row = await conn2.fetchrow(
                    "SELECT episodes FROM cortex_hippocampus WHERE project = $1",
                    project,
                )
            if hipp_row and hipp_row.get("episodes"):
                episodes = hipp_row["episodes"]
                if isinstance(episodes, str):
                    episodes = json.loads(episodes)
                for ep in episodes:
                    if isinstance(ep, dict) and ep.get("reward", 0) >= 0.7:
                        summary = (
                            ep.get("summary") or ep.get("meta", {}).get("summary") or ""
                        )
                        if summary and summary not in (cultural.get("facts") or []):
                            consolidate_cultural_knowledge(project)
                            break
    except Exception:
        pass

    async with pool.acquire() as conn:
        old_temporal = await conn.fetch(
            "SELECT id FROM project_memory WHERE project = $1 AND memory_type = 'temporal' "
            "AND created_at < now() - interval '30 days' AND priority = 'normal'",
            project,
        )
    for row in old_temporal:
        ok = await archive_memory_entry(project, row["id"], pool=pool)
        if ok:
            result["archived_temporal"] += 1

    async with pool.acquire() as conn:
        tier_counts: dict[str, int] = {}
        for tier in ("immediate", "consolidated", "timeless"):
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM consolidated_memory WHERE project = $1 AND tier = $2",
                project,
                tier,
            )
            tier_counts[tier] = count
    result["tiers"] = tier_counts

    try:
        async with pool.acquire() as conn_bridge:
            other_projects = await conn_bridge.fetch(
                "SELECT DISTINCT project FROM consolidated_memory "
                "WHERE project != $1 AND tier IN ('consolidated', 'timeless') LIMIT 5",
                project,
            )
            for op in other_projects:
                cross_match = await conn_bridge.fetch(
                    "SELECT a.content, a.memory_type FROM consolidated_memory a "
                    "JOIN consolidated_memory b ON a.content ILIKE '%' || b.content || '%' "
                    "WHERE a.project = $1 AND b.project = $2 "
                    "AND a.tier IN ('consolidated', 'timeless') "
                    "AND b.tier IN ('consolidated', 'timeless') LIMIT 3",
                    project,
                    op["project"],
                )
                if cross_match:
                    existing_proposal = await conn_bridge.fetchval(
                        "SELECT id FROM proposals WHERE project = $1 "
                        "AND title LIKE 'Cross-project: ' || $2 LIMIT 1",
                        project,
                        op["project"],
                    )
                    if not existing_proposal:
                        facts = [
                            f"- {r['content'][:200]} ({r['memory_type']})"
                            for r in cross_match[:3]
                        ]
                        proposal_text = (
                            f"Shared knowledge with {op['project']}:\n"
                            + "\n".join(facts)
                        )
                        await conn_bridge.execute(
                            "INSERT INTO proposals (project, title, body, status, created_at) "
                            "VALUES ($1, $2, $3, 'pending', extract(epoch from now()))",
                            project,
                            f"Cross-project: {op['project']}",
                            proposal_text,
                        )
                        result.setdefault("cross_project_proposals", 0)
                        result["cross_project_proposals"] = (
                            result.get("cross_project_proposals", 0) + 1
                        )
    except Exception:
        pass

    return result


# ═════════════════════════════════════════════════════════════════
#  Memory Archive (forgotten but searchable)
# ═════════════════════════════════════════════════════════════════


async def archive_memory_entry(project: str, entry_id: int, pool=None) -> bool:
    if pool is None:
        pool = await _get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT id, entry, priority, memory_type, strength "
                "FROM project_memory WHERE id = $1 AND project = $2",
                entry_id,
                project,
            )
            if not row:
                return False
            await conn.execute(
                "INSERT INTO memory_archive "
                "(original_id, project, entry, priority, memory_type, strength) "
                "VALUES ($1, $2, $3, $4, $5, $6)",
                row["id"],
                project,
                row["entry"],
                row["priority"],
                row["memory_type"],
                row["strength"],
            )
            await conn.execute("DELETE FROM project_memory WHERE id = $1", entry_id)
            return True


async def search_archived(
    project: str, query: str, limit: int = 5, pool=None
) -> list[dict]:
    if pool is None:
        pool = await _get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, original_id, project, entry, priority, memory_type, strength, archived_at "
            "FROM memory_archive "
            "WHERE project = $1 AND (entry % $2 OR entry ILIKE '%' || $2 || '%') "
            "ORDER BY archived_at DESC LIMIT $3",
            project,
            query,
            limit,
        )
        return [dict(r) for r in rows]


# ═════════════════════════════════════════════════════════════════
#  Memory Cost Analytics
# ═════════════════════════════════════════════════════════════════


async def record_memory_cost(
    project: str,
    session_id: str = "",
    tokens_injected: int = 0,
    tokens_saved_injection: int = 0,
    tokens_saved_forgetting: int = 0,
    context_type: str = "full",
    task_outcome: str = "",
    breakdown: dict | None = None,
    pool=None,
):
    if pool is None:
        pool = await _get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO memory_costs "
            "(project, session_id, tokens_injected, tokens_saved_injection, "
            "  tokens_saved_forgetting, context_type, task_outcome, breakdown) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
            project,
            session_id,
            tokens_injected,
            tokens_saved_injection,
            tokens_saved_forgetting,
            context_type,
            task_outcome,
            json.dumps(breakdown) if breakdown else None,
        )


async def get_memory_costs(project: str, days: int = 30, pool=None) -> list[dict]:
    if pool is None:
        pool = await _get_pool()
    cutoff = time.time() - (days * 86400)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM memory_costs "
            "WHERE project = $1 AND created_at >= $2 "
            "ORDER BY created_at DESC",
            project,
            cutoff,
        )
        return [dict(r) for r in rows]


async def get_memory_cost_summary(project: str, days: int = 30, pool=None) -> dict:
    if pool is None:
        pool = await _get_pool()
    cutoff = time.time() - (days * 86400)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT COUNT(*) as count, "
            "  SUM(tokens_injected) as total_injected, "
            "  SUM(tokens_saved_injection) as total_saved_injection, "
            "  SUM(tokens_saved_forgetting) as total_saved_forgetting, "
            "  AVG(tokens_injected) as avg_injected "
            "FROM memory_costs "
            "WHERE project = $1 AND created_at >= $2",
            project,
            cutoff,
        )
        return dict(row) if row else {}


async def apply_decay(
    pool=None, decay_factor: float = 0.95, recent_threshold_seconds: float = 3600
) -> dict:
    """Apply Ebbinghaus forgetting curve: strength *= decay_factor for all entries.

    Red-ink (priority='critical') entries have a floor of 0.5.
    Entries accessed within recent_threshold_seconds are skipped to avoid
    decaying freshly-touched memories (race condition mitigation).
    Also applies to consolidated_memory entries (with same rules).
    Returns stats: {total, decayed, red_ink_protected, consolidated_decayed}.
    """
    if pool is None:
        pool = await _get_pool()
    async with pool.acquire() as conn:
        total = await conn.fetchval("SELECT COUNT(*) FROM project_memory")
        red_ink = await conn.fetchval(
            "SELECT COUNT(*) FROM project_memory WHERE priority = 'critical'"
        )
        recent_cutoff = time.time() - recent_threshold_seconds
        await conn.execute(
            "UPDATE project_memory SET strength = strength * $1 "
            "WHERE priority != 'critical' "
            "AND (last_accessed IS NULL OR last_accessed < $2)",
            decay_factor,
            recent_cutoff,
        )
        await conn.execute(
            "UPDATE project_memory SET strength = GREATEST(strength * $1, 0.5) "
            "WHERE priority = 'critical' "
            "AND (last_accessed IS NULL OR last_accessed < $2)",
            decay_factor,
            recent_cutoff,
        )
        consolidated_total = await conn.fetchval(
            "SELECT COUNT(*) FROM consolidated_memory"
        )
        consolidated_red = await conn.fetchval(
            "SELECT COUNT(*) FROM consolidated_memory WHERE priority = 'critical'"
        )
        await conn.execute(
            "UPDATE consolidated_memory SET strength = strength * $1 "
            "WHERE priority != 'critical' "
            "AND (last_accessed IS NULL OR last_accessed < $2)",
            decay_factor,
            recent_cutoff,
        )
        await conn.execute(
            "UPDATE consolidated_memory SET strength = GREATEST(strength * $1, 0.5) "
            "WHERE priority = 'critical' "
            "AND (last_accessed IS NULL OR last_accessed < $2)",
            decay_factor,
            recent_cutoff,
        )
        return {
            "total": total,
            "decayed": total - red_ink,
            "red_ink_protected": red_ink,
            "consolidated_total": consolidated_total,
            "consolidated_decayed": consolidated_total - consolidated_red,
            "consolidated_red_ink_protected": consolidated_red,
        }


async def archive_decay(pool=None, threshold: float = 0.1) -> dict:
    """Move entries with strength < threshold to memory_archive.

    Red-ink entries are never archived.
    Also archives low-strength consolidated_memory entries.
    Returns stats: {archived, consolidated_archived, remaining, consolidated_remaining}.
    """
    if pool is None:
        pool = await _get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            rows = await conn.fetch(
                "SELECT id, project, entry, priority, memory_type, strength "
                "FROM project_memory WHERE strength < $1 AND priority != 'critical'",
                threshold,
            )
            for row in rows:
                await conn.execute(
                    "INSERT INTO memory_archive "
                    "(original_id, project, entry, priority, memory_type, strength) "
                    "VALUES ($1, $2, $3, $4, $5, $6)",
                    row["id"],
                    row["project"],
                    row["entry"],
                    row["priority"],
                    row["memory_type"],
                    row["strength"],
                )
            if rows:
                ids = [row["id"] for row in rows]
                await conn.execute(
                    "DELETE FROM project_memory WHERE id = ANY($1) AND priority != 'critical'",
                    ids,
                )
            c_rows = await conn.fetch(
                "SELECT id, project, content, priority, memory_type, strength "
                "FROM consolidated_memory WHERE strength < $1 AND priority != 'critical'",
                threshold,
            )
            for row in c_rows:
                await conn.execute(
                    "INSERT INTO memory_archive "
                    "(original_id, project, entry, priority, memory_type, strength) "
                    "VALUES ($1, $2, $3, $4, $5, $6)",
                    row["id"],
                    row["project"],
                    row["content"],
                    row["priority"],
                    row["memory_type"],
                    row["strength"],
                )
            if c_rows:
                c_ids = [row["id"] for row in c_rows]
                await conn.execute(
                    "DELETE FROM consolidated_memory WHERE id = ANY($1) AND priority != 'critical'",
                    c_ids,
                )
            remaining = await conn.fetchval("SELECT COUNT(*) FROM project_memory")
            c_remaining = await conn.fetchval(
                "SELECT COUNT(*) FROM consolidated_memory"
            )
            return {
                "archived": len(rows),
                "consolidated_archived": len(c_rows),
                "remaining": remaining,
                "consolidated_remaining": c_remaining,
            }


async def get_decay_status(project: str, pool=None) -> dict:
    """Get decay status for all entries in a project."""
    if pool is None:
        pool = await _get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, entry, priority, memory_type, strength, last_accessed "
            "FROM project_memory WHERE project = $1 ORDER BY strength ASC",
            project,
        )
        fading = [dict(r) for r in rows if r["strength"] < 0.4]
        stable = [dict(r) for r in rows if r["strength"] >= 0.4]
        return {
            "project": project,
            "total": len(rows),
            "fading": fading,
            "stable_count": len(stable),
            "fading_count": len(fading),
        }


async def get_all_projects(pool=None) -> list[str]:
    if pool is None:
        pool = await _get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT DISTINCT project FROM project_memory ORDER BY project"
        )
        return [r["project"] for r in rows]
