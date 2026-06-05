"""
memoriad — background daemon for opencode session indexing.

Scans opencode SQLite DB periodically, extracts session summaries,
builds FTS5 search index, writes structured records to sessions.jsonl.
"""

import asyncio
import json
import os
import signal
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

WORKDIR = Path("/var/tmp/memoria")
POLL_INTERVAL = 30
_raw_db = os.environ.get("OPENCODE_DB")
OPENCODE_DB = (
    Path(_raw_db) if _raw_db else Path.home() / ".local/share/opencode/opencode.db"
)

_exit = asyncio.Event()


def project_name() -> str:
    return os.environ.get("MEMORIA_PROJECT") or Path.cwd().name


def wd() -> Path:
    p = WORKDIR / project_name()
    p.mkdir(parents=True, exist_ok=True)
    return p


def read_pid() -> int | None:
    p = wd() / "daemon.pid"
    if not p.exists():
        return None
    pid = int(p.read_text().strip())
    try:
        os.kill(pid, 0)
        return pid
    except OSError:
        return None


def write_pid():
    wd().joinpath("daemon.pid").write_text(str(os.getpid()))


def clean_pid():
    p = wd() / "daemon.pid"
    if p.exists():
        p.unlink()


def append_session(rec: dict):
    path = wd() / "sessions.jsonl"
    with open(path, "a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    _update_index(rec)


def _open_index() -> sqlite3.Connection:
    path = wd() / "index.db"
    db = sqlite3.connect(str(path))
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=OFF")
    db.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS sessions_fts "
        "USING fts5(id UNINDEXED, title, summary, tokenize='porter unicode61')"
    )
    return db


def _update_index(rec: dict):
    try:
        db = _open_index()
        db.execute(
            "INSERT OR REPLACE INTO sessions_fts(id, title, summary) VALUES (?, ?, ?)",
            (
                rec.get("id", ""),
                rec.get("title", "")[:200],
                rec.get("summary", "")[:500],
            ),
        )
        db.commit()
        db.close()
    except Exception:
        pass


# ── OpenCode DB Queries ───────────────────────────────────────


def _opencode_db() -> sqlite3.Connection | None:
    if not OPENCODE_DB.exists():
        return None
    db = sqlite3.connect(str(OPENCODE_DB))
    db.row_factory = sqlite3.Row
    return db


def _extract_session(
    session_row: sqlite3.Row, db: sqlite3.Connection
) -> dict[str, Any]:
    sid = session_row["id"]
    title = session_row["title"] or "untitled"
    created = session_row["time_created"] or 0
    directory = session_row["directory"] or ""
    project = session_row["project_id"] or Path(directory).name if directory else ""

    # Get messages for this session
    messages = db.execute(
        "SELECT id, data FROM message WHERE session_id = ? ORDER BY id",
        (sid,),
    ).fetchall()

    # Get parts for all messages in this session
    parts = db.execute(
        "SELECT message_id, data FROM part WHERE session_id = ? ORDER BY id",
        (sid,),
    ).fetchall()

    # Reconstruct conversation
    first_task = ""
    tools_used: list[str] = []
    last_outcome = ""
    tool_count = 0

    for p in parts:
        data = p["data"]
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except json.JSONDecodeError:
                continue
        ptype = data.get("type", "")
        if ptype == "text":
            text = data.get("text", "").strip()
            # Find the parent message's role
            msg_id = p["message_id"]
            msg = next((m for m in messages if m["id"] == msg_id), None)
            if msg:
                msg_data = msg["data"]
                if isinstance(msg_data, str):
                    try:
                        msg_data = json.loads(msg_data)
                    except json.JSONDecodeError:
                        msg_data = {}
                role = msg_data.get("role", "")
                if role == "user" and not first_task:
                    first_task = text[:500]
                elif role == "assistant" and text:
                    last_outcome = text[:500]
        elif ptype == "tool":
            tool_name = data.get("tool", "")
            if tool_name and tool_name not in tools_used:
                tools_used.append(tool_name)
            tool_count += 1

    # Build summary
    summary_parts = []
    if first_task:
        summary_parts.append(f"Task: {first_task[:200]}")
    if tools_used:
        summary_parts.append(f"Tools: {', '.join(tools_used)} ({tool_count} calls)")
    if last_outcome:
        summary_parts.append(f"Outcome: {last_outcome[:300]}")
    summary = " | ".join(summary_parts) if summary_parts else ""

    return {
        "id": sid,
        "title": title,
        "project": project or project_name(),
        "directory": directory,
        "created": created,
        "task": first_task[:500],
        "outcome": last_outcome[:500],
        "tools": list(dict.fromkeys(tools_used)),
        "tool_count": tool_count,
        "summary": summary,
    }


def poll_new_sessions(last_id: str | None) -> tuple[list[dict[str, Any]], str | None]:
    db = _opencode_db()
    if db is None:
        return [], last_id
    try:
        if last_id:
            rows = db.execute(
                "SELECT id, title, directory, project_id, time_created, time_updated "
                "FROM session "
                "WHERE id > ? "
                "ORDER BY id LIMIT 20",
                (last_id,),
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT id, title, directory, project_id, time_created, time_updated "
                "FROM session "
                "ORDER BY id DESC LIMIT 20",
            ).fetchall()
            rows = list(reversed(rows))
    except Exception:
        db.close()
        return [], last_id

    results = []
    new_last = last_id
    for r in rows:
        rec = _extract_session(r, db)
        results.append(rec)
        if new_last is None or r["id"] > new_last:
            new_last = r["id"]
    db.close()
    return results, new_last


# ── Main Loop ─────────────────────────────────────────────────


async def run():
    wd_path = wd()
    wd_path.mkdir(parents=True, exist_ok=True)
    last_id = None

    # Load last processed ID
    cursor_path = wd_path / "last_id.txt"
    if cursor_path.exists():
        last_id = cursor_path.read_text().strip()

    write_pid()

    while not _exit.is_set():
        try:
            sessions, last_id = poll_new_sessions(last_id)
            for s in sessions:
                append_session(s)
                if s["id"]:
                    cursor_path.write_text(s["id"])
            # Sleep in short increments so SIGTERM responds quickly
            for _ in range(POLL_INTERVAL):
                if _exit.is_set():
                    break
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            break
        except Exception:
            for _ in range(POLL_INTERVAL):
                if _exit.is_set():
                    break
                await asyncio.sleep(1)


def handle_sigterm(signum, frame):
    _exit.set()


def main():
    pid = read_pid()
    if pid:
        print(f"daemon already running (pid {pid})", file=sys.stderr)
        sys.exit(1)
    signal.signal(signal.SIGTERM, handle_sigterm)
    signal.signal(signal.SIGINT, handle_sigterm)
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
    finally:
        clean_pid()


if __name__ == "__main__":
    main()
