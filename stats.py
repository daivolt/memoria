"""
stats — Time tracking for AI sessions vs human-equivalent estimates.

PostgreSQL backend via asyncpg. Pool injected by memoriad_global.py lifespan.

All timestamps are double-precision Unix epoch seconds for JSONL compat.
Exports session-level and operation-level data with human-estimate computation.
"""

import time
import uuid
from collections import Counter, defaultdict
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query

try:
    import asyncpg
except ImportError:
    asyncpg = None  # graceful degradation — endpoints will 503

# ── Default cost model (minutes per operation) ─────────────────────

DEFAULT_ESTIMATES: dict[str, float] = {
    # Database
    "db_query": 1.0,
    "db_insert_single": 1.5,
    "db_insert_batch": 0.3,
    "db_update": 1.0,
    "db_delete": 0.5,
    "db_schema": 5.0,
    # Sheet
    "sheet_create_tab": 2.0,
    "sheet_delete_tab": 1.0,
    "sheet_add_row": 2.0,
    "sheet_batch_add": 0.5,
    "sheet_delete_row": 0.5,
    "sheet_update_cell": 1.0,
    "sheet_read_data": 3.0,
    # File
    "file_create": 8.0,
    "file_edit": 2.0,
    "file_delete": 0.5,
    "file_refactor": 5.0,
    # CDP
    "cdp_navigate": 0.5,
    "cdp_inject": 3.0,
    "cdp_form_post": 2.0,
    "cdp_evaluate": 0.5,
    # Debug
    "debug_retry": 3.0,
    "debug_diagnose": 5.0,
    "debug_fix": 4.0,
    # Planning
    "research": 5.0,
    "planning": 10.0,
    "context_switch": 10.0,
    # Verification
    "verify_data": 3.0,
    "verify_test": 5.0,
    # Communication
    "question_user": 2.0,
    "report_generate": 8.0,
}

# ── Schema ─────────────────────────────────────────────────────────

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS time_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project TEXT NOT NULL,
    task_id TEXT DEFAULT '',
    tags TEXT[] DEFAULT '{}',
    notes TEXT DEFAULT '',
    started DOUBLE PRECISION NOT NULL,
    ended DOUBLE PRECISION,
    ai_wall_seconds DOUBLE PRECISION DEFAULT 0,
    human_estimate_minutes DOUBLE PRECISION DEFAULT 0,
    ops_count INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS time_operations (
    id SERIAL PRIMARY KEY,
    session_id UUID NOT NULL REFERENCES time_sessions(id) ON DELETE CASCADE,
    op_type TEXT NOT NULL,
    count INTEGER DEFAULT 0,
    unit_cost_minutes DOUBLE PRECISION DEFAULT 2.0,
    total_estimate_minutes DOUBLE PRECISION DEFAULT 0
);

CREATE TABLE IF NOT EXISTS time_estimates (
    op_type TEXT PRIMARY KEY,
    minutes_per_unit DOUBLE PRECISION NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_time_sessions_project ON time_sessions(project);
CREATE INDEX IF NOT EXISTS idx_time_sessions_started ON time_sessions(started);
CREATE INDEX IF NOT EXISTS idx_time_operations_session ON time_operations(session_id);
CREATE INDEX IF NOT EXISTS idx_time_operations_type ON time_operations(op_type);
"""

SEED_ESTIMATES_SQL = """
INSERT INTO time_estimates (op_type, minutes_per_unit)
SELECT $1, $2
WHERE NOT EXISTS (SELECT 1 FROM time_estimates WHERE op_type = $1);
"""

# ── Pool (injected from outside) ──────────────────────────────────

_pool: Any = None


def init_pool(pool) -> None:
    global _pool
    _pool = pool


async def _get_pool():
    if _pool is None:
        raise HTTPException(503, "Database not initialized")
    return _pool


# ── Bootstrap ──────────────────────────────────────────────────────


async def ensure_tables():
    """Create tables + seed estimates on first run. Safe to call repeatedly."""
    if asyncpg is None:
        return
    pool = await _get_pool()
    async with pool.acquire() as conn:
        await conn.execute(SCHEMA_SQL)
        for op_type, mins in DEFAULT_ESTIMATES.items():
            await conn.execute(SEED_ESTIMATES_SQL, op_type, mins)


# ── Helpers ──────────────────────────────────────────────────────────


def _format_duration(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    if h > 0:
        return f"{h}h {m:02d}m"
    if m > 0:
        return f"{m}m {s:02d}s"
    return f"{s}s"


def _compute_human_estimate(
    ops: list[dict], estimates: dict[str, float], manual_blocks: list[dict]
) -> float:
    total = 0.0
    for op in ops:
        op_type = op.get("type", "")
        count = max(0, op.get("count", 0))
        unit_cost = estimates.get(op_type, 2.0)
        total += count * unit_cost
    for block in manual_blocks:
        total += float(block.get("minutes", 0) or 0)
    return total


def _ensure_session_id(id_val: str) -> str:
    return id_val or str(uuid.uuid4())


def _validate_uuid(s: str) -> bool:
    try:
        uuid.UUID(s)
        return True
    except ValueError:
        return False


async def _load_estimates_dict() -> dict[str, float]:
    pool = await _get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT op_type, minutes_per_unit FROM time_estimates")
    if not rows:
        return dict(DEFAULT_ESTIMATES)
    return {r["op_type"]: r["minutes_per_unit"] for r in rows}


async def _apply_estimates(estimates: dict[str, float]):
    pool = await _get_pool()
    async with pool.acquire() as conn:
        for op_type, mins in estimates.items():
            await conn.execute(
                """INSERT INTO time_estimates (op_type, minutes_per_unit, updated_at)
                   VALUES ($1, $2, now())
                   ON CONFLICT (op_type) DO UPDATE SET
                       minutes_per_unit = EXCLUDED.minutes_per_unit,
                       updated_at = now()""",
                op_type,
                mins,
            )


# ── Router ─────────────────────────────────────────────────────────

router = APIRouter(prefix="/stats", tags=["stats"])


@router.post("/session")
async def create_session(body: dict):
    """Start a new session. Returns session_id and started timestamp."""
    if _pool is None:
        raise HTTPException(503, "Database not initialized")
    sid = _ensure_session_id(body.get("id", ""))
    now = time.time()
    project = body.get("project", "unknown") or "unknown"
    task_id = body.get("task_id", "") or ""
    tags = body.get("tags", []) or []
    notes = body.get("notes", "") or ""
    pool = await _get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO time_sessions (id, project, task_id, started, tags, notes)
               VALUES ($1::uuid, $2, $3, $4, $5, $6)""",
            sid,
            project,
            task_id,
            now,
            tags,
            notes,
        )
    return {"session_id": sid, "started": now}


@router.patch("/session/{session_id}")
async def end_session(session_id: str, body: dict):
    """End a session. Provide ops and manual_blocks to compute estimate.

    Idempotent: if session already ended, returns existing result.
    Ops from body are INSERTed in addition to any ops already in DB
    from prior POST /session/{id}/ops calls.
    """
    if _pool is None:
        raise HTTPException(503, "Database not initialized")
    if not _validate_uuid(session_id):
        raise HTTPException(404, f"Session {session_id} not found")
    pool = await _get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM time_sessions WHERE id = $1::uuid",
            session_id,
        )
        if row is None:
            raise HTTPException(404, f"Session {session_id} not found")
        existing_ended = row["ended"]
        started = row["started"]
        now = body.get("ended") if body.get("ended") is not None else time.time()
        ai_wall = max(0, now - started)
        if existing_ended is not None:
            # Already ended — recompute response from stored data
            ops_rows = await conn.fetch(
                "SELECT * FROM time_operations WHERE session_id = $1::uuid",
                session_id,
            )
            stored_human = row["human_estimate_minutes"] or 0
            stored_ops_count = row["ops_count"] or 0
            return {
                "session_id": session_id,
                "ai_wall_seconds": row["ai_wall_seconds"] or ai_wall,
                "ai_wall_display": _format_duration(row["ai_wall_seconds"] or ai_wall),
                "human_estimate_minutes": stored_human,
                "human_estimate_display": _format_duration(stored_human * 60),
                "savings_ratio": round(
                    (stored_human * 60) / max((row["ai_wall_seconds"] or ai_wall), 1), 2
                ),
                "ops_tracked": stored_ops_count,
            }
        body_ops = body.get("ops", []) or []
        manual_blocks = body.get("manual_blocks", []) or []
        estimates = await _load_estimates_dict()
        for op in body_ops:
            op_type = op.get("type", "")
            count = max(0, op.get("count", 0))
            unit_cost = estimates.get(op_type, 2.0)
            total_est = count * unit_cost
            await conn.execute(
                """INSERT INTO time_operations
                       (session_id, op_type, count, unit_cost_minutes, total_estimate_minutes)
                   VALUES ($1::uuid, $2, $3, $4, $5)""",
                session_id,
                op_type,
                count,
                unit_cost,
                total_est,
            )
        # Recompute from all ops (DB + new body_ops + manual_blocks)
        all_ops_rows = await conn.fetch(
            "SELECT * FROM time_operations WHERE session_id = $1::uuid",
            session_id,
        )
        all_ops_list = [dict(r) for r in all_ops_rows]
        human_min = _compute_human_estimate(all_ops_list, estimates, manual_blocks)
        ops_count = sum(max(0, r.get("count", 0)) for r in all_ops_list)
        notes = body.get("notes", "")
        tags = body.get("tags", []) or []
        await conn.execute(
            """UPDATE time_sessions SET
                   ended = $1, ai_wall_seconds = $2,
                   human_estimate_minutes = $3, ops_count = $4,
                   notes = CASE WHEN $5 <> '' THEN $5 ELSE notes END,
                   tags = array_cat(tags, $6)
               WHERE id = $7::uuid""",
            now,
            ai_wall,
            human_min,
            ops_count,
            notes,
            tags,
            session_id,
        )
    return {
        "session_id": session_id,
        "ai_wall_seconds": ai_wall,
        "ai_wall_display": _format_duration(ai_wall),
        "human_estimate_minutes": human_min,
        "human_estimate_display": _format_duration(human_min * 60),
        "savings_ratio": round((human_min * 60) / max(ai_wall, 1), 2),
        "ops_tracked": ops_count,
    }


@router.get("/session/{session_id}")
async def get_session(session_id: str):
    if _pool is None:
        raise HTTPException(503, "Database not initialized")
    if not _validate_uuid(session_id):
        raise HTTPException(404, f"Session {session_id} not found")
    pool = await _get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM time_sessions WHERE id = $1::uuid",
            session_id,
        )
        if row is None:
            raise HTTPException(404, f"Session {session_id} not found")
        ops_rows = await conn.fetch(
            "SELECT * FROM time_operations WHERE session_id = $1::uuid ORDER BY id",
            session_id,
        )
    session = dict(row)
    session["started"] = float(session["started"]) if session.get("started") else None
    session["ended"] = float(session["ended"]) if session.get("ended") else None
    session["tags"] = list(session.get("tags") or [])
    session["id"] = str(session["id"])
    operations = [dict(o) for o in ops_rows]
    for o in operations:
        o["session_id"] = str(o["session_id"])
    return {**session, "operations": operations}


@router.get("/project/{project}")
async def project_stats(project: str):
    if _pool is None:
        raise HTTPException(503, "Database not initialized")
    pool = await _get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM time_sessions WHERE project = $1 ORDER BY started DESC",
            project,
        )
        all_ops = await conn.fetch(
            """SELECT to2.* FROM time_operations to2
               JOIN time_sessions ts ON ts.id = to2.session_id
               WHERE ts.project = $1""",
            project,
        )
    sessions = []
    for r in rows:
        d = dict(r)
        d["id"] = str(d["id"])
        d["started"] = float(d["started"]) if d.get("started") else None
        d["ended"] = float(d["ended"]) if d.get("ended") else None
        d["tags"] = list(d.get("tags") or [])
        sessions.append(d)
    total_ai = sum(s.get("ai_wall_seconds", 0) for s in sessions)
    total_human = sum(s.get("human_estimate_minutes", 0) for s in sessions)
    total_human_sec = total_human * 60
    op_totals: dict[str, float] = {}
    for o in all_ops:
        op_totals[o["op_type"]] = (
            op_totals.get(o["op_type"], 0) + o["total_estimate_minutes"]
        )
    top_ops = sorted(op_totals.items(), key=lambda x: -x[1])[:10]
    return {
        "project": project,
        "sessions": len(sessions),
        "total_ai_seconds": total_ai,
        "total_ai_display": _format_duration(total_ai),
        "total_human_minutes": total_human,
        "total_human_display": _format_duration(total_human_sec),
        "savings_ratio": round(total_human_sec / max(total_ai, 1), 2),
        "top_operations": [
            {"type": t, "estimated_minutes": round(m, 1)} for t, m in top_ops
        ],
        "sessions_list": [
            {
                "id": s["id"],
                "task_id": s.get("task_id", ""),
                "started": s.get("started"),
                "ai_display": _format_duration(s.get("ai_wall_seconds", 0)),
                "human_display": _format_duration(
                    s.get("human_estimate_minutes", 0) * 60
                ),
                "ops": s.get("ops_count", 0),
            }
            for s in sessions
        ],
    }


@router.get("/report")
async def report(
    project: Optional[str] = Query(None),
    task_id: Optional[str] = Query(None),
    days: Optional[int] = Query(None),
    format: str = Query("chat"),
):
    """Generate a human-readable report (supports 'chat' and 'markdown' formats)."""
    if _pool is None:
        raise HTTPException(503, "Database not initialized")
    pool = await _get_pool()
    now_ts = time.time()
    cutoff = (now_ts - days * 86400) if days and days > 0 else 0
    conditions = ["1=1"]
    params = []
    if project:
        conditions.append("project = $" + str(len(params) + 1))
        params.append(project)
    if task_id:
        conditions.append("task_id = $" + str(len(params) + 1))
        params.append(task_id)
    if cutoff > 0:
        conditions.append("started >= $" + str(len(params) + 1))
        params.append(cutoff)
    where = " AND ".join(conditions)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"SELECT * FROM time_sessions WHERE {where} ORDER BY started DESC",
            *params,
        )
        all_ops = await conn.fetch(
            """SELECT to2.* FROM time_operations to2
               JOIN time_sessions ts ON ts.id = to2.session_id
               WHERE ts.id = ANY($1::uuid[])""",
            [r["id"] for r in rows],
        )
    sessions = []
    for r in rows:
        d = dict(r)
        d["id"] = str(d["id"])
        d["started"] = float(d["started"]) if d.get("started") else None
        d["ended"] = float(d["ended"]) if d.get("ended") else None
        d["tags"] = list(d.get("tags") or [])
        sessions.append(d)
    if not sessions:
        return {"report": "No sessions found matching the criteria."}
    total_ai = sum(s.get("ai_wall_seconds", 0) for s in sessions)
    total_human = sum(s.get("human_estimate_minutes", 0) for s in sessions)
    total_human_sec = total_human * 60
    savings = round(total_human_sec / max(total_ai, 1), 2)
    task_counts = Counter(s.get("task_id", "") for s in sessions)
    top_tasks = task_counts.most_common(5)
    session_ids = {s["id"] for s in sessions}
    op_totals: dict[str, float] = {}
    for o in all_ops:
        op_totals[o["op_type"]] = (
            op_totals.get(o["op_type"], 0) + o["total_estimate_minutes"]
        )
    if format == "markdown":
        lines = [
            f"## Project: {project or 'all'} | Sessions: {len(sessions)}",
            "",
            f"**AI Wall Clock:** {_format_duration(total_ai)}",
            f"**Human Estimate:** {_format_duration(total_human_sec)}",
            f"**Savings:** {savings}× (Δ{_format_duration(total_human_sec - total_ai)} saved)",
            "",
            "### Top Tasks",
        ]
        for tid, cnt in top_tasks:
            if tid:
                lines.append(f"- **{tid}**: {cnt} session{'s' if cnt > 1 else ''}")
        lines.append("")
        lines.append("### Operation Breakdown")
        for op_type, mins in sorted(op_totals.items(), key=lambda x: -x[1])[:10]:
            lines.append(f"- {op_type}: {round(mins, 1)}m")
        return {"report": "\n".join(lines)}
    lines = [
        f"📍 **{project or 'All projects'}** — {len(sessions)} session{'s' if len(sessions) != 1 else ''}",
        f"⏱ AI: {_format_duration(total_ai)} | 👤 Human est: {_format_duration(total_human_sec)} | 💰 Savings: **{savings}×**",
        f"📊 Δ{_format_duration(total_human_sec - total_ai)} saved",
    ]
    if top_tasks and project:
        lines.append(f"\nTop tasks:")
        for tid, cnt in top_tasks[:3]:
            if tid:
                lines.append(f"  • {tid} ({cnt}x)")
    return {"report": "\n".join(lines)}


@router.get("/active")
async def get_active_session(project: str = Query(...)):
    """Get the most recent un-ended session for a project."""
    if _pool is None:
        raise HTTPException(503, "Database not initialized")
    pool = await _get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT * FROM time_sessions
               WHERE project = $1 AND ended IS NULL
               ORDER BY started DESC LIMIT 1""",
            project,
        )
        if row is None:
            return {"session_id": None, "active": False}
    session = dict(row)
    session["id"] = str(session["id"])
    session["started"] = float(session["started"]) if session.get("started") else None
    session["tags"] = list(session.get("tags") or [])
    session["active"] = True
    return session


@router.post("/session/{session_id}/ops")
async def add_ops(session_id: str, body: dict):
    """Append operations to an active (un-ended) session.

    Ops from body are INSERTed into time_operations.
    The session must exist and must not have ended yet.
    """
    if _pool is None:
        raise HTTPException(503, "Database not initialized")
    if not _validate_uuid(session_id):
        raise HTTPException(404, f"Session {session_id} not found")
    pool = await _get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM time_sessions WHERE id = $1::uuid",
            session_id,
        )
        if row is None:
            raise HTTPException(404, f"Session {session_id} not found")
        if row["ended"] is not None:
            raise HTTPException(400, "Session already ended")
        body_ops = body.get("ops", []) or []
        estimates = await _load_estimates_dict()
        for op in body_ops:
            op_type = op.get("type", "")
            count = max(0, op.get("count", 0))
            unit_cost = estimates.get(op_type, 2.0)
            total_est = count * unit_cost
            await conn.execute(
                """INSERT INTO time_operations
                       (session_id, op_type, count, unit_cost_minutes, total_estimate_minutes)
                   VALUES ($1::uuid, $2, $3, $4, $5)""",
                session_id,
                op_type,
                count,
                unit_cost,
                total_est,
            )
    return {"session_id": session_id, "added": len(body_ops)}


@router.get("/estimates")
async def get_estimates():
    if _pool is None:
        raise HTTPException(503, "Database not initialized")
    return await _load_estimates_dict()


@router.patch("/estimates")
async def update_estimates(body: dict):
    """Update one or more estimate values. Body: {op_type: minutes_per_unit, ...}"""
    if _pool is None:
        raise HTTPException(503, "Database not initialized")
    await _apply_estimates(body)
    return await _load_estimates_dict()


@router.get("/estimates/calibrate")
async def calibrate_estimates():
    """LLM-style calibration: adjust estimates based on actual session data."""
    if _pool is None:
        raise HTTPException(503, "Database not initialized")
    pool = await _get_pool()
    async with pool.acquire() as conn:
        session_count = await conn.fetchval(
            "SELECT COUNT(*) FROM time_sessions WHERE ended IS NOT NULL"
        )
    if session_count < 3:
        current = await _load_estimates_dict()
        return {
            "suggestion": f"Need at least 3 completed sessions for calibration (have {session_count})",
            "current": current,
            "adjusted": current,
        }
    pool = await _get_pool()
    async with pool.acquire() as conn:
        all_ops = await conn.fetch(
            """SELECT to2.* FROM time_operations to2
               JOIN time_sessions ts ON ts.id = to2.session_id
               WHERE ts.ended IS NOT NULL"""
        )
    by_type: dict[str, list[dict]] = defaultdict(list)
    for o in all_ops:
        by_type[o["op_type"]].append(dict(o))
    estimates = await _load_estimates_dict()
    adjusted = dict(estimates)
    suggestions = []
    for op_type, ops in by_type.items():
        counts = [o.get("count", 0) for o in ops]
        if not counts or sum(counts) == 0:
            continue
        current_cost = estimates.get(op_type, 2.0)
        total_ops = sum(counts)
        if total_ops < 10:
            continue
        # Frequency-based calibration: ops used >1x per session are routine (cheaper),
        # ops used <0.2x per session are complex (more expensive)
        freq_per_session = total_ops / max(session_count, 1)
        if freq_per_session > 1.0:
            new_cost = round(current_cost * 0.7, 1)
            direction = "frequent"
        elif freq_per_session < 0.2:
            new_cost = round(current_cost * 1.5, 1)
            direction = "rare"
        else:
            continue
        if abs(new_cost - current_cost) >= 0.3:
            adjusted[op_type] = new_cost
            suggestions.append(
                f"{op_type}: {current_cost}→{new_cost}m ({direction}, {total_ops} ops)"
            )
    return {
        "suggestion": f"Adjusted {len(suggestions)} operation types based on {len(all_ops)} operations across {session_count} sessions",
        "adjustments": suggestions,
        "current": estimates,
        "adjusted": adjusted,
    }


@router.post("/estimates/calibrate/apply")
async def apply_calibration():
    """Apply the calibrated estimates."""
    result = await calibrate_estimates()
    await _apply_estimates(result["adjusted"])
    return {"applied": result["adjustments"], "estimates": result["adjusted"]}


@router.get("/export/{fmt}")
async def export_stats(
    fmt: str,
    project: Optional[str] = Query(None),
    days: Optional[int] = Query(None),
):
    if _pool is None:
        raise HTTPException(503, "Database not initialized")
    pool = await _get_pool()
    now_ts = time.time()
    cutoff = (now_ts - days * 86400) if days and days > 0 else 0
    conditions = ["1=1"]
    params = []
    if project:
        conditions.append("project = $" + str(len(params) + 1))
        params.append(project)
    if cutoff > 0:
        conditions.append("started >= $" + str(len(params) + 1))
        params.append(cutoff)
    where = " AND ".join(conditions)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"SELECT * FROM time_sessions WHERE {where} ORDER BY started",
            *params,
        )
        all_ops = await conn.fetch(
            """SELECT to2.* FROM time_operations to2
               JOIN time_sessions ts ON ts.id = to2.session_id
               WHERE ts.id = ANY($1::uuid[])""",
            [r["id"] for r in rows],
        )
    sessions = []
    for r in rows:
        d = dict(r)
        d["id"] = str(d["id"])
        d["started"] = float(d["started"]) if d.get("started") else None
        d["ended"] = float(d["ended"]) if d.get("ended") else None
        d["tags"] = list(d.get("tags") or [])
        sessions.append(d)
    ops_list = [dict(o) for o in all_ops]
    for o in ops_list:
        o["session_id"] = str(o["session_id"])
    if fmt == "json":
        return {"sessions": sessions, "operations": ops_list}
    elif fmt == "csv":
        lines = [
            "session_id,project,task_id,started,ended,ai_seconds,human_minutes,ops_count"
        ]
        for s in sessions:
            lines.append(
                ",".join(
                    str(s.get(k, ""))
                    for k in [
                        "id",
                        "project",
                        "task_id",
                        "started",
                        "ended",
                        "ai_wall_seconds",
                        "human_estimate_minutes",
                        "ops_count",
                    ]
                )
            )
        return {"csv": "\n".join(lines)}
    return {"error": "Unsupported format. Use 'json' or 'csv'."}
