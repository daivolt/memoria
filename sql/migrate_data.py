"""
One-shot data migration: flat files → PostgreSQL.

Reads all existing flat-file storage from /var/tmp/memoria/
and inserts into the memoria PostgreSQL database.

Safe to re-run (uses INSERT ON CONFLICT DO NOTHING for idempotency).
"""

import asyncio
import json
import time
from pathlib import Path

import asyncpg

WORKDIR = Path("/var/tmp/memoria")
DSN = "postgresql://postgres@localhost/memoria"


async def migrate_chitchat(pool):
    """chitchat/{room}/inbox.jsonl → chitchat_messages table"""
    chitchat_dir = WORKDIR / "chitchat"
    if not chitchat_dir.exists():
        return 0
    total = 0
    for room_dir in sorted(chitchat_dir.iterdir()):
        if not room_dir.is_dir():
            continue
        path = room_dir / "inbox.jsonl"
        if not path.exists():
            continue
        lines = path.read_text().strip().splitlines()
        batch = []
        for line in lines:
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            batch.append(
                (
                    msg.get("id", ""),
                    msg.get("room", room_dir.name),
                    msg.get("from", ""),
                    msg.get("text", ""),
                    msg.get("topic", ""),
                    msg.get("ts", ""),
                    msg.get("type", "message"),
                    msg.get("ingested_at", time.time()),
                )
            )
        if batch:
            await pool.executemany(
                "INSERT INTO chitchat_messages (id, room, from_name, text, topic, ts, type, ingested_at) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8) "
                "ON CONFLICT (id) DO NOTHING",
                batch,
            )
            total += len(batch)
            print(f"  chitchat/{room_dir.name}: {len(batch)} messages")
    return total


async def migrate_topics(pool):
    """topics/{name}.md → topics table"""
    topics_dir = WORKDIR / "topics"
    if not topics_dir.exists():
        return 0
    total = 0
    for f in sorted(topics_dir.iterdir()):
        if f.suffix != ".md":
            continue
        raw = f.read_text().strip()
        facts = [e.strip() for e in raw.split("§") if e.strip()]
        if facts:
            await pool.execute(
                "INSERT INTO topics (name, facts, updated_at) VALUES ($1, $2, now()) "
                "ON CONFLICT (name) DO UPDATE SET facts = EXCLUDED.facts, updated_at = now()",
                f.stem,
                facts,
            )
            total += 1
            print(f"  topics/{f.stem}: {len(facts)} facts")
    return total


async def migrate_sessions(pool):
    """sessions.jsonl → sessions table"""
    path = WORKDIR / "sessions.jsonl"
    if not path.exists():
        return 0
    lines = path.read_text().strip().splitlines()
    batch = []
    for line in lines:
        try:
            s = json.loads(line)
        except json.JSONDecodeError:
            continue
        batch.append(
            (
                s.get("id", ""),
                s.get("title", ""),
                s.get("project", ""),
                s.get("directory", ""),
                s.get("created", 0),
                s.get("task", ""),
                s.get("outcome", ""),
                s.get("tools", []),
                s.get("tool_count", 0),
                (s.get("summary", "") or "")[:500],
            )
        )
    if batch:
        await pool.executemany(
            "INSERT INTO sessions (id, title, project, directory, created, task, outcome, "
            "tools, tool_count, summary) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10) "
            "ON CONFLICT (id) DO NOTHING",
            batch,
        )
        print(f"  sessions: {len(batch)} records")
    return len(batch)


async def migrate_proposals(pool):
    """proposals.jsonl → proposals table"""
    path = WORKDIR / "proposals.jsonl"
    if not path.exists():
        return 0
    lines = path.read_text().strip().splitlines()
    batch = []
    for line in lines:
        try:
            p = json.loads(line)
        except json.JSONDecodeError:
            continue
        batch.append(
            (
                p.get("id", ""),
                p.get("text", ""),
                p.get("topic", ""),
                p.get("proposed_at", time.time()),
                p.get("source", ""),
            )
        )
    if batch:
        await pool.executemany(
            "INSERT INTO proposals (id, text, topic, proposed_at, source) "
            "VALUES ($1, $2, $3, $4, $5) "
            "ON CONFLICT (id) DO NOTHING",
            batch,
        )
        print(f"  proposals: {len(batch)} records")
    return len(batch)


async def migrate_project_memory(pool):
    """{project}/MEMORY.md → project_memory table"""
    for entry in WORKDIR.iterdir():
        if not entry.is_dir():
            continue
        mem_path = entry / "MEMORY.md"
        if not mem_path.exists():
            continue
        raw = mem_path.read_text().strip()
        entries = [e.strip() for e in raw.split("§") if e.strip()]
        if not entries:
            continue
        batch = [(entry.name, e) for e in entries]
        await pool.executemany(
            "INSERT INTO project_memory (project, entry) VALUES ($1, $2)",
            batch,
        )
        print(f"  memory/{entry.name}: {len(entries)} entries")
    return 0  # count unknown, OK


async def migrate_federation(pool):
    """federation/peers.json + sync_state.json → federation tables"""
    peers_path = WORKDIR / "federation" / "peers.json"
    if peers_path.exists() and peers_path.stat().st_size > 0:
        try:
            peers = json.loads(peers_path.read_text())
            if peers:
                batch = []
                for p in peers:
                    batch.append(
                        (
                            p.get("name", ""),
                            p.get("url", ""),
                            p.get("api_key", ""),
                            p.get("created_at", time.time()),
                            p.get("updated_at", time.time()),
                        )
                    )
                if batch:
                    await pool.executemany(
                        "INSERT INTO federation_peers (name, url, api_key, created_at, updated_at) "
                        "VALUES ($1, $2, $3, $4, $5) "
                        "ON CONFLICT (name) DO NOTHING",
                        batch,
                    )
                    print(f"  federation/peers: {len(batch)} records")
        except (json.JSONDecodeError, OSError):
            pass

    sync_path = WORKDIR / "federation" / "sync_state.json"
    if sync_path.exists() and sync_path.stat().st_size > 0:
        try:
            state = json.loads(sync_path.read_text())
            if state:
                await pool.execute(
                    "INSERT INTO federation_sync_state (name, state, updated_at) "
                    "VALUES ('singleton', $1::jsonb, now()) "
                    "ON CONFLICT (name) DO UPDATE SET state = EXCLUDED.state, updated_at = now()",
                    json.dumps(state),
                )
                print(f"  federation/sync_state: migrated")
        except (json.JSONDecodeError, OSError):
            pass


async def migrate_time_tracking(pool):
    """Move time tracking data from bloom_terminal to memoria"""
    # Source: bloom_terminal database
    try:
        src = await asyncpg.connect("postgresql://postgres@localhost/bloom_terminal")
    except Exception as e:
        print(f"  time tracking: cannot connect to bloom_terminal: {e}")
        return

    try:
        # Copy time_sessions
        rows = await src.fetch("SELECT * FROM time_sessions")
        if rows:
            batch = [
                (
                    r["id"],
                    r["project"],
                    r["task_id"],
                    r["tags"],
                    r["notes"],
                    r["started"],
                    r["ended"],
                    r["ai_wall_seconds"],
                    r["human_estimate_minutes"],
                    r["ops_count"],
                )
                for r in rows
            ]
            await pool.executemany(
                "INSERT INTO time_sessions (id, project, task_id, tags, notes, started, ended, "
                "ai_wall_seconds, human_estimate_minutes, ops_count) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10) "
                "ON CONFLICT (id) DO NOTHING",
                batch,
            )
            print(f"  time_sessions: {len(batch)} records")

        # Copy time_operations
        rows = await src.fetch("SELECT * FROM time_operations")
        if rows:
            batch = [
                (
                    r["session_id"],
                    r["op_type"],
                    r["count"],
                    r["unit_cost_minutes"],
                    r["total_estimate_minutes"],
                )
                for r in rows
            ]
            await pool.executemany(
                "INSERT INTO time_operations (session_id, op_type, count, "
                "unit_cost_minutes, total_estimate_minutes) "
                "VALUES ($1, $2, $3, $4, $5)",
                batch,
            )
            print(f"  time_operations: {len(batch)} records")

        # Copy time_estimates
        rows = await src.fetch("SELECT * FROM time_estimates")
        if rows:
            batch = [
                (r["op_type"], r["minutes_per_unit"], r["updated_at"]) for r in rows
            ]
            await pool.executemany(
                "INSERT INTO time_estimates (op_type, minutes_per_unit, updated_at) "
                "VALUES ($1, $2, $3) "
                "ON CONFLICT (op_type) DO NOTHING",
                batch,
            )
            print(f"  time_estimates: {len(batch)} records")

    finally:
        await src.close()


async def main():
    pool = await asyncpg.create_pool(DSN, min_size=1, max_size=2)

    print("Migrating chitchat...")
    await migrate_chitchat(pool)

    print("Migrating topics...")
    await migrate_topics(pool)

    print("Migrating sessions...")
    await migrate_sessions(pool)

    print("Migrating proposals...")
    await migrate_proposals(pool)

    print("Migrating project memory...")
    await migrate_project_memory(pool)

    print("Migrating federation...")
    await migrate_federation(pool)

    print("Migrating time tracking (bloom_terminal → memoria)...")
    await migrate_time_tracking(pool)

    # Print final counts
    for table in [
        "chitchat_messages",
        "sessions",
        "agents",
        "tasks",
        "topics",
        "proposals",
        "project_memory",
        "cortex_gating",
        "cortex_hippocampus",
        "cortex_replay",
        "cortex_auction",
        "cortex_working_memory",
        "cortex_socratic",
        "cortex_efferent",
        "lessons",
        "student_outcomes",
        "cultural_memory",
        "federation_peers",
        "federation_sync_state",
        "safety_snapshots",
        "time_sessions",
        "time_operations",
        "time_estimates",
    ]:
        count = await pool.fetchval(f"SELECT COUNT(*) FROM {table}")
        print(f"  {table}: {count} rows")

    await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
