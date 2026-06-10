"""
PostgreSQL storage backend for Memoria.

Replaces all flat-file I/O (JSONL, JSON, Markdown) with asyncpg queries.
Pool injected by memoriad_global.py lifespan.
All functions accept an optional pool param; if None, uses module-level _pool.
"""

import json
import time
from typing import Any, Optional

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
                    "SELECT id, filename, title, "
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
                    "SELECT id, filename, title, "
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


async def add_memory_entry(project: str, text: str, pool=None):
    if pool is None:
        pool = await _get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO project_memory (project, entry) VALUES ($1, $2)",
            project,
            text,
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
            index,
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
