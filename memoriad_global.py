"""
memoriad-global — REST server for memoria.
Binds 0.0.0.0:19998, serves all memoria endpoints.
Background task polls opencode.db every 30s for new sessions.
"""

import asyncio
import html
import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
import tempfile
import urllib.parse
import urllib.request
import uuid
from collections import Counter, deque
import difflib
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from compress import compress
from context import current_state, write_state, release_state, claim_file

# ── Paths ──────────────────────────────────────────────────────

WORKDIR = Path("/var/tmp/memoria")
AGENTS_DIR = WORKDIR / "agents"
TASKS_DIR = WORKDIR / "tasks"
SAFETY_DIR = WORKDIR / "safety"
OPENCODE_DB = Path.home() / ".local/share/opencode/opencode.db"
MEMORY_LIMIT = 5000
POLL_INTERVAL = 30
AGENT_STALE_SEC = 300
CHITCHAT_URL = "http://localhost:19999"
CHITCHAT_POLL_INTERVAL = 3
CHITCHAT_CONSOLIDATE_THRESHOLD = 20
CHITCHAT_DIR = WORKDIR / "chitchat"
CHITCHAT_MAX_MESSAGES = 10000          # per-room slot limit (hippocampal capacity)
CHITCHAT_ROOMS = [r.strip() for r in os.environ.get("CHITCHAT_ROOMS", "general").split(",")]
SLEEP_CYCLE_HOURS = int(os.environ.get("SLEEP_CYCLE_HOURS", "6"))
SESSION_MAX_RECORDS = int(os.environ.get("SESSION_MAX_RECORDS", "5000"))  # total session slot limit
AUTO_ACCEPT_THRESHOLD = int(os.environ.get("AUTO_ACCEPT_THRESHOLD", "3"))

# ── Shared modules for session extraction ────────────────────


def _opencode_db() -> sqlite3.Connection | None:
    if not OPENCODE_DB.exists():
        return None
    db = sqlite3.connect(f"file:{OPENCODE_DB}?mode=ro", uri=True)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA cache_size=-64000")
    db.row_factory = sqlite3.Row
    return db


def _extract_session(sid: str, db: sqlite3.Connection) -> dict[str, Any]:
    s = db.execute("SELECT * FROM session WHERE id = ?", (sid,)).fetchone()
    if s is None:
        return {}
    title = s["title"] or "untitled"
    created = s["time_created"] or 0
    directory = s["directory"] or ""
    project = s["project_id"] or Path(directory).name if directory else ""

    messages = db.execute(
        "SELECT id, data FROM message WHERE session_id = ? ORDER BY id", (sid,)
    ).fetchall()
    parts = db.execute(
        "SELECT message_id, data FROM part WHERE session_id = ? ORDER BY id", (sid,)
    ).fetchall()

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
            msg = next((m for m in messages if m["id"] == p["message_id"]), None)
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
            tn = data.get("tool", "")
            if tn and tn not in tools_used:
                tools_used.append(tn)
            tool_count += 1

    summary = ""
    parts_list = []
    if first_task:
        parts_list.append(f"Task: {first_task[:200]}")
    if tools_used:
        parts_list.append(f"Tools: {', '.join(tools_used)} ({tool_count} calls)")
    if last_outcome:
        parts_list.append(f"Outcome: {last_outcome[:300]}")
    summary = " | ".join(parts_list)

    return {
        "id": sid,
        "title": title,
        "project": project,
        "directory": directory,
        "created": created,
        "task": first_task[:500],
        "outcome": last_outcome[:500],
        "tools": list(dict.fromkeys(tools_used)),
        "tool_count": tool_count,
        "summary": summary,
    }


def _init_index():
    WORKDIR.mkdir(parents=True, exist_ok=True)
    AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    TASKS_DIR.mkdir(parents=True, exist_ok=True)
    SAFETY_DIR.mkdir(parents=True, exist_ok=True)
    CHITCHAT_DIR.mkdir(parents=True, exist_ok=True)
    path = WORKDIR / "index.db"
    db = sqlite3.connect(str(path))
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=OFF")
    db.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS sessions_fts "
        "USING fts5(id UNINDEXED, title, summary, tokenize='porter unicode61')"
    )
    db.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS chat_fts "
        "USING fts5(id UNINDEXED, room, from_name, text, tokenize='porter unicode61')"
    )
    db.close()


def _index_session(rec: dict):
    path = WORKDIR / "index.db"
    try:
        db = sqlite3.connect(str(path))
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


def _append_session(rec: dict):
    path = WORKDIR / "sessions.jsonl"
    with open(path, "a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    _index_session(rec)


def _glob_wd(project: str) -> Path:
    p = WORKDIR / project
    p.mkdir(parents=True, exist_ok=True)
    return p


def _memory_path(project: str) -> Path:
    return _glob_wd(project) / "MEMORY.md"


def _parse_memory(project: str) -> list[str]:
    p = _memory_path(project)
    if not p.exists():
        return []
    raw = p.read_text().strip()
    if not raw:
        return []
    return [e.strip() for e in raw.split("§") if e.strip()]


def _write_memory(project: str, entries: list[str]):
    p = _memory_path(project)
    content = "\n§\n".join(entries)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent))
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content + "\n")
        os.replace(tmp, p)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


# ── Background poll ───────────────────────────────────────────


_last_id: str | None = None

# ── Chitchat state ────────────────────────────────────────────

_chitchat_cursors: dict[str, str] = {}
_chitchat_unconsolidated: int = 0
_chitchat_consolidated_through: dict[str, float] = {}  # newest ingested_at processed per room
_recent_chat_texts: dict[str, deque[str]] = {}  # per-room sliding window for dedup


def poll_sessions():
    global _last_id
    db = _opencode_db()
    if db is None:
        return
    try:
        if _last_id:
            rows = db.execute(
                "SELECT id FROM session WHERE id > ? ORDER BY id LIMIT 10",
                (_last_id,),
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT id FROM session ORDER BY id DESC LIMIT 10"
            ).fetchall()
            rows = list(reversed(rows))
    except Exception:
        db.close()
        return
    for r in rows:
        sid = r["id"]
        rec = _extract_session(sid, db)
        if rec.get("id"):
            _append_session(rec)
            _last_id = sid
    db.close()
    if _last_id:
        (WORKDIR / "last_id.txt").write_text(_last_id)


# ── Chitchat poller & storage ────────────────────────────────


def _chitchat_rooms() -> list[dict]:
    try:
        req = urllib.request.Request(f"{CHITCHAT_URL}/rooms")
        with urllib.request.urlopen(req, timeout=3) as resp:
            return json.loads(resp.read()).get("rooms", [])
    except Exception:
        return []


def _chitchat_history(room: str) -> list[dict]:
    try:
        req = urllib.request.Request(f"{CHITCHAT_URL}/{urllib.parse.quote(room)}/history")
        with urllib.request.urlopen(req, timeout=3) as resp:
            return json.loads(resp.read()).get("messages", [])
    except Exception:
        return []


def _normalize_text(text: str) -> str:
    text = text.lower()
    text = "".join(c for c in text if c.isalnum() or c.isspace())
    return " ".join(text.split())


def _index_chat_message(rec: dict):
    room = rec.get("room", "?")
    text = rec.get("text", "")[:500]
    norm = _normalize_text(text)
    if len(norm) >= 10:
        window = _recent_chat_texts.setdefault(room, deque(maxlen=20))
        for existing in window:
            ratio = difflib.SequenceMatcher(None, norm, existing).ratio()
            if ratio > 0.85:
                return  # pattern separation: skip near-duplicate
        window.append(norm)
    path = WORKDIR / "index.db"
    try:
        db = sqlite3.connect(str(path))
        db.execute(
            "INSERT OR IGNORE INTO chat_fts(id, room, from_name, text) VALUES (?, ?, ?, ?)",
            (rec["id"], room, rec.get("from", ""), text),
        )
        db.commit()
        db.close()
    except Exception:
        pass


def _store_message(msg: dict, room: str):
    record = {
        "id": msg.get("ts", ""),
        "from": msg.get("from", ""),
        "text": msg.get("text", ""),
        "topic": msg.get("topic", ""),
        "room": room,
        "ts": msg.get("ts", ""),
        "type": msg.get("type", "message"),
        "ingested_at": time.time(),
    }
    room_dir = CHITCHAT_DIR / room
    room_dir.mkdir(parents=True, exist_ok=True)
    path = room_dir / "inbox.jsonl"
    with open(path, "a") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    _index_chat_message(record)


def poll_chitchat():
    global _chitchat_unconsolidated
    global _chitchat_cursors
    rooms = _chitchat_rooms()
    if not rooms:
        return
    for r in rooms:
        name = r["name"]
        if name not in CHITCHAT_ROOMS:
            continue
        history = _chitchat_history(name)
        cursor = _chitchat_cursors.get(name, "")
        new_count = 0
        for msg in history:
            ts = msg.get("ts", "")
            if ts <= cursor:
                continue
            if msg.get("type") not in ("message",):
                continue
            _store_message(msg, name)
            _chitchat_cursors[name] = ts
            new_count += 1
        _chitchat_unconsolidated += new_count


def _load_existing_topic_names() -> list[str]:
    topics_dir = WORKDIR / "topics"
    if not topics_dir.exists():
        return []
    return sorted(f.stem for f in topics_dir.iterdir() if f.suffix == ".md")


def _load_topic_facts(name: str) -> list[str]:
    path = WORKDIR / "topics" / f"{name}.md"
    if not path.exists():
        return []
    return [e.strip() for e in path.read_text().split("§") if e.strip()]


def _find_matching_topic(keywords: list[str], existing_topics: list[str]) -> str | None:
    kw_set = set(keywords)
    for topic in existing_topics:
        t_lower = topic.lower()
        t_words = set(t_lower.split())
        overlap = kw_set & t_words
        if overlap:
            return topic
    return None


def _prune_chitchat():
    all_pruned_ids: list[str] = []
    for room_dir in sorted(CHITCHAT_DIR.iterdir()):
        if not room_dir.is_dir():
            continue
        path = room_dir / "inbox.jsonl"
        if not path.exists():
            continue
        lines = path.read_text().strip().splitlines()
        if len(lines) <= CHITCHAT_MAX_MESSAGES:
            continue
        consolidated_through = _chitchat_consolidated_through.get(room_dir.name, 0)
        unconsolidated: list[str] = []
        consolidated: list[tuple[float, str]] = []
        pruned_ids: list[str] = []
        for line in lines:
            try:
                msg = json.loads(line)
                ingested = msg.get("ingested_at", 0)
                if ingested > consolidated_through:
                    unconsolidated.append(line)
                else:
                    consolidated.append((ingested, line))
            except json.JSONDecodeError:
                unconsolidated.append(line)
        consolidated.sort(key=lambda x: x[0])  # oldest first
        slot_remaining = CHITCHAT_MAX_MESSAGES - len(unconsolidated)
        if slot_remaining < 0:
            unconsolidated = unconsolidated[-(CHITCHAT_MAX_MESSAGES // 2):]
            slot_remaining = CHITCHAT_MAX_MESSAGES - len(unconsolidated)
        kept_consolidated = consolidated[-slot_remaining:] if slot_remaining > 0 else []
        kept = unconsolidated + [line for _, line in kept_consolidated]
        if len(kept) < len(lines):
            pruned = consolidated[:max(0, len(consolidated) - slot_remaining)]
            for _, line in pruned:
                try:
                    pruned_ids.append(json.loads(line).get("id", ""))
                except json.JSONDecodeError:
                    pass
            all_pruned_ids.extend(pruned_ids)
            path.write_text("\n".join(kept) + "\n" if kept else "")
    if all_pruned_ids:
        try:
            db = sqlite3.connect(str(WORKDIR / "index.db"))
            for pid in all_pruned_ids:
                try:
                    db.execute("DELETE FROM chat_fts WHERE id = ?", (pid,))
                except Exception:
                    pass
            db.commit()
            db.close()
        except Exception:
            pass


def _consolidate_chitchat():
    global _chitchat_unconsolidated, _chitchat_consolidated_through
    if _chitchat_unconsolidated < CHITCHAT_CONSOLIDATE_THRESHOLD:
        return

    all_messages: list[dict] = []
    if not CHITCHAT_DIR.exists():
        return
    max_ingested_per_room: dict[str, float] = {}
    for room_dir in sorted(CHITCHAT_DIR.iterdir()):
        if not room_dir.is_dir():
            continue
        path = room_dir / "inbox.jsonl"
        if not path.exists():
            continue
        lines = path.read_text().strip().splitlines()
        for line in lines:
            try:
                msg = json.loads(line)
                all_messages.append(msg)
                ingested = msg.get("ingested_at", 0)
                room = msg.get("room", room_dir.name)
                if ingested > max_ingested_per_room.get(room, 0):
                    max_ingested_per_room[room] = ingested
            except json.JSONDecodeError:
                continue

    if not all_messages:
        _chitchat_unconsolidated = 0
        return

    keyword_counts: Counter = Counter()
    room_counts: Counter = Counter()
    from_counts: Counter = Counter()

    for msg in all_messages:
        text = msg.get("text", "")
        room_counts[msg.get("room", "?")] += 1
        from_counts[msg.get("from", "?")] += 1
        words = [w.lower() for w in text.split() if len(w) >= 4]
        keyword_counts.update(words)

    top_keywords = [w for w, c in keyword_counts.most_common(10) if c >= 3]
    if not top_keywords:
        _chitchat_unconsolidated = 0
        return

    suggested_topic = top_keywords[0]

    summary_parts = []
    summary_parts.append(f"Pattern across {len(room_counts)} room(s): {', '.join(sorted(room_counts))}")
    summary_parts.append(f"Participants: {', '.join(sorted(from_counts))}")
    summary_parts.append(f"Keywords: {', '.join(top_keywords)}")
    summary = " | ".join(summary_parts)

    proposal_text = (
        f"[chitchat consolidation] {summary[:400]}\n"
        f"Messages sampled: {len(all_messages)}"
    )

    # Interleaved replay: check existing topics before proposing
    existing_topics = _load_existing_topic_names()
    match = _find_matching_topic(top_keywords, existing_topics)
    if match:
        # Append to existing topic directly (not via proposal — already vetted)
        existing_facts = _load_topic_facts(match)
        if proposal_text not in existing_facts:
            existing_facts.append(proposal_text)
            content = "\n§\n".join(existing_facts) + "\n"
            (WORKDIR / "topics" / f"{match}.md").write_text(content)
            _notify_chitchat(
                f"[memoria] pattern '{suggested_topic}' appended to existing topic '{match}'"
                f" ({len(all_messages)} msgs)"
            )
    else:
        # Auto-accept flow: check if same topic already proposed
        existing_proposals = _load_proposals()
        existing = next((p for p in existing_proposals if p.get("topic") == suggested_topic[:50]), None)
        if existing:
            existing["hits"] = existing.get("hits", 1) + 1
            if existing["hits"] >= AUTO_ACCEPT_THRESHOLD:
                _add_fact_to_topic(existing["topic"], existing["text"])
                existing_proposals = [p for p in existing_proposals if p.get("id") != existing["id"]]
                _save_proposals(existing_proposals)
                _notify_chitchat(
                    f"[memoria] pattern '{suggested_topic}' auto-accepted"
                    f" (hits={existing['hits']}, msgs={len(all_messages)})"
                )
            else:
                _save_proposals(existing_proposals)
                _notify_chitchat(
                    f"[memoria] pattern '{suggested_topic}' hit {existing['hits']}/{AUTO_ACCEPT_THRESHOLD}"
                )
        else:
            path = WORKDIR / "proposals.jsonl"
            record = {
                "id": f"chat_consolidate_{int(time.time())}",
                "text": proposal_text,
                "topic": suggested_topic[:50],
                "proposed_at": time.time(),
                "source": "chitchat_consolidation",
                "hits": 1,
            }
            with open(path, "a") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
            _notify_chitchat(
                f"[memoria] pattern '{suggested_topic}' from chat "
                f"({len(all_messages)} msgs, {len(room_counts)} rooms) → proposed"
            )

    # Mark what's been consolidated
    for room, ingested in max_ingested_per_room.items():
        if ingested > _chitchat_consolidated_through.get(room, 0):
            _chitchat_consolidated_through[room] = ingested

    # Prune by slot limit (keeps unconsolidated messages)
    _prune_chitchat()

    _chitchat_unconsolidated = 0


def _prune_sessions():
    path = WORKDIR / "sessions.jsonl"
    if not path.exists():
        return
    lines = path.read_text().strip().splitlines()
    if len(lines) <= SESSION_MAX_RECORDS:
        return
    kept = lines[-SESSION_MAX_RECORDS:]
    pruned_ids: list[str] = []
    for line in lines[:-SESSION_MAX_RECORDS]:
        try:
            s = json.loads(line)
            pruned_ids.append(s.get("id", ""))
        except json.JSONDecodeError:
            pass
    path.write_text("\n".join(kept) + "\n" if kept else "")
    if pruned_ids:
        try:
            db = sqlite3.connect(str(WORKDIR / "index.db"))
            for sid in pruned_ids:
                db.execute("DELETE FROM sessions_fts WHERE id = ?", (sid,))
            db.commit()
            db.close()
        except Exception:
            pass


def _deep_consolidate():
    """Cross-layer consolidation: sessions → topics + chat → topics."""
    # 0. Prune old sessions first
    _prune_sessions()

    # 1. Force chitchat consolidation
    global _chitchat_unconsolidated
    _chitchat_unconsolidated = CHITCHAT_CONSOLIDATE_THRESHOLD
    _consolidate_chitchat()

    # 2. Pull recent sessions and check if any new topic patterns emerge
    path = WORKDIR / "sessions.jsonl"
    if path.exists():
        lines = path.read_text().strip().splitlines()
        recent = lines[-5:]
        session_keywords: Counter = Counter()
        for line in recent:
            try:
                s = json.loads(line)
                title = s.get("title", "")
                task = s.get("task", "")
                txt = f"{title} {task}"
                words = [w.lower() for w in txt.split() if len(w) >= 4]
                session_keywords.update(words)
            except json.JSONDecodeError:
                continue
        top = [w for w, c in session_keywords.most_common(5) if c >= 2]
        if top:
            existing = _load_existing_topic_names()
            if not _find_matching_topic(top, existing):
                prop_text = f"[deep consolidation] Recent session keywords: {', '.join(top)}"
                prop_path = WORKDIR / "proposals.jsonl"
                record = {
                    "id": f"deep_consolidate_{int(time.time())}",
                    "text": prop_text,
                    "topic": top[0][:50],
                    "proposed_at": time.time(),
                    "source": "deep_consolidation",
                }
                with open(prop_path, "a") as f:
                    f.write(json.dumps(record) + "\n")


# ── FastAPI app ──────────────────────────────────────────────


_poll_task: asyncio.Task | None = None
_chitchat_poll_task: asyncio.Task | None = None
_sleep_cycle_task: asyncio.Task | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    _init_index()
    global _poll_task, _chitchat_poll_task, _sleep_cycle_task
    _poll_task = asyncio.create_task(_poll_loop())
    _chitchat_poll_task = asyncio.create_task(_chitchat_poll_loop())
    _sleep_cycle_task = asyncio.create_task(_sleep_cycle_loop())
    yield
    for t in (_poll_task, _chitchat_poll_task, _sleep_cycle_task):
        if t:
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass


async def _poll_loop():
    while True:
        try:
            poll_sessions()
        except Exception:
            pass
        await asyncio.sleep(POLL_INTERVAL)


async def _chitchat_poll_loop():
    await asyncio.sleep(5)
    while True:
        try:
            poll_chitchat()
            _consolidate_chitchat()
        except Exception:
            pass
        await asyncio.sleep(CHITCHAT_POLL_INTERVAL)


async def _sleep_cycle_loop():
    await asyncio.sleep(300)
    while True:
        try:
            _deep_consolidate()
        except Exception:
            pass
        await asyncio.sleep(SLEEP_CYCLE_HOURS * 3600)


app = FastAPI(title="memoria", version="2.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    sess_path = WORKDIR / "sessions.jsonl"
    count = sum(1 for _ in sess_path.open()) if sess_path.exists() else 0
    topics_dir = WORKDIR / "topics"
    topics = (
        sorted(d.stem for d in topics_dir.iterdir() if d.suffix == ".md")
        if topics_dir.exists()
        else []
    )
    chitchat_rooms_list = sorted(d.name for d in CHITCHAT_DIR.iterdir() if d.is_dir()) if CHITCHAT_DIR.exists() else []
    return {
        "ok": True,
        "sessions_indexed": count,
        "topics": topics,
        "chitchat_rooms": chitchat_rooms_list,
        "db_exists": OPENCODE_DB.exists(),
        "memoria_version": "2.0.0",
        "sleep_cycle_hours": SLEEP_CYCLE_HOURS,
        "session_max_records": SESSION_MAX_RECORDS,
        "chitchat_max_messages": CHITCHAT_MAX_MESSAGES,
    }


# ── Recall ──────────────────────────────────────────────────


@app.get("/recall")
async def recall(
    q: str = Query(...),
    limit: int = 5,
    project: Optional[str] = None,
    source: Optional[str] = None,
):
    path = WORKDIR / "index.db"
    if not path.exists():
        raise HTTPException(404, "no index — wait for session extraction")
    db = sqlite3.connect(str(path))
    qs = " OR ".join(q.split())
    results = []

    if source in (None, "sessions"):
        try:
            rows = db.execute(
                "SELECT id, title, summary, rank FROM sessions_fts "
                "WHERE sessions_fts MATCH ? ORDER BY rank LIMIT ?",
                (qs, limit),
            ).fetchall()
            for r in rows:
                results.append({
                    "id": r[0],
                    "title": r[1],
                    "summary": (r[2] or "")[:300],
                    "source": "session",
                })
        except sqlite3.OperationalError:
            pass

    if source in (None, "chats"):
        try:
            rows = db.execute(
                "SELECT id, room, from_name, text, rank FROM chat_fts "
                "WHERE chat_fts MATCH ? ORDER BY rank LIMIT ?",
                (qs, limit),
            ).fetchall()
            for r in rows:
                results.append({
                    "id": r[0],
                    "title": f"[{r[1]}] {r[2]}",
                    "summary": (r[3] or "")[:300],
                    "source": "chat",
                    "room": r[1],
                    "from": r[2],
                })
        except sqlite3.OperationalError:
            pass

    db.close()
    return {"query": q, "count": len(results), "results": results[:limit]}


@app.get("/review")
async def review(n: int = 3, project: Optional[str] = None):
    path = WORKDIR / "sessions.jsonl"
    if not path.exists():
        return {"sessions": []}
    lines = path.read_text().strip().splitlines()
    selected = lines[-n:]
    sessions = []
    for line in selected:
        try:
            s = json.loads(line)
        except json.JSONDecodeError:
            continue
        sessions.append(
            {
                "id": s.get("id", ""),
                "title": s.get("title", ""),
                "task": s.get("task", "")[:200],
                "tools": s.get("tools", []),
                "tool_count": s.get("tool_count", 0),
                "created": s.get("created", 0),
                "summary": (s.get("summary", "") or "")[:300],
            }
        )
    return {"sessions": sessions, "count": len(sessions)}


# ── Topics ──────────────────────────────────────────────────


@app.get("/topics")
async def list_topics(detail: bool = False):
    topics_dir = WORKDIR / "topics"
    if not topics_dir.exists():
        return {"topics": [] if not detail else {}}
    if detail:
        result = {}
        for f in sorted(topics_dir.iterdir()):
            if f.suffix == ".md":
                entries = [e.strip() for e in f.read_text().split("§") if e.strip()]
                result[f.stem] = entries
        return {"topics": result}
    names = sorted(f.stem for f in topics_dir.iterdir() if f.suffix == ".md")
    return {"topics": names}


@app.get("/topics/{name}")
async def read_topic(name: str):
    path = WORKDIR / "topics" / f"{name}.md"
    if not path.exists():
        raise HTTPException(404, f"topic '{name}' not found")
    entries = [e.strip() for e in path.read_text().split("§") if e.strip()]
    return {"topic": name, "facts": entries}


@app.get("/topics/search")
async def topics_search(q: str = Query(...), limit: int = 3):
    keywords = {w.lower() for w in q.split() if len(w) >= 4}
    if not keywords:
        return {"query": q, "count": 0, "topics": []}
    scored: list[tuple[int, str, list[str]]] = []
    topics_dir = WORKDIR / "topics"
    if not topics_dir.exists():
        return {"query": q, "count": 0, "topics": []}
    for f in sorted(topics_dir.iterdir()):
        if f.suffix != ".md":
            continue
        facts = [e.strip() for e in f.read_text().split("§") if e.strip()]
        body = (f.stem + " " + " ".join(facts)).lower()
        overlap = sum(1 for k in keywords if k in body)
        if overlap:
            scored.append((overlap, f.stem, facts[:3]))
    scored.sort(key=lambda x: -x[0])
    topics = [{"name": t, "facts": f} for _, t, f in scored[:limit]]
    return {"query": q, "count": len(topics), "topics": topics}


class AddTopicFact(BaseModel):
    text: str


@app.post("/topics/{name}")
async def add_topic_fact(name: str, body: AddTopicFact):
    topics_dir = WORKDIR / "topics"
    topics_dir.mkdir(parents=True, exist_ok=True)
    path = topics_dir / f"{name}.md"
    entries = []
    if path.exists():
        entries = [e.strip() for e in path.read_text().split("§") if e.strip()]
    entries.append(body.text)
    content = "\n§\n".join(entries) + "\n"
    fd, tmp = tempfile.mkstemp(dir=str(topics_dir))
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return {"ok": True, "topic": name, "entries": len(entries)}


# ── Proposals ───────────────────────────────────────────────


def _load_proposals() -> list[dict]:
    path = WORKDIR / "proposals.jsonl"
    if not path.exists():
        return []
    lines = path.read_text().strip().splitlines()
    result = []
    for line in lines:
        try:
            result.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return result


def _save_proposals(proposals: list[dict]):
    path = WORKDIR / "proposals.jsonl"
    content = "\n".join(json.dumps(p, ensure_ascii=False) for p in proposals)
    path.write_text(content + "\n" if content else "")


def _add_fact_to_topic(topic: str, text: str):
    topics_dir = WORKDIR / "topics"
    topics_dir.mkdir(parents=True, exist_ok=True)
    tpath = topics_dir / f"{topic}.md"
    entries = []
    if tpath.exists():
        entries = [e.strip() for e in tpath.read_text().split("§") if e.strip()]
    entries.append(text)
    tpath.write_text("\n§\n".join(entries) + "\n")


class ProposeFact(BaseModel):
    text: str
    topic: str


@app.get("/proposals")
async def list_proposals():
    path = WORKDIR / "proposals.jsonl"
    if not path.exists():
        return {"proposals": []}
    lines = path.read_text().strip().splitlines()
    proposals = []
    for line in lines:
        try:
            proposals.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return {"proposals": proposals, "count": len(proposals)}


@app.post("/proposals")
async def propose_fact(body: ProposeFact):
    path = WORKDIR / "proposals.jsonl"
    record = {
        "id": f"prop_{int(time.time())}_{os.getpid()}",
        "text": body.text,
        "topic": body.topic,
        "proposed_at": time.time(),
    }
    with open(path, "a") as f:
        f.write(json.dumps(record) + "\n")
    return {"ok": True, "id": record["id"]}


@app.post("/proposals/{pid}/accept")
async def accept_proposal(pid: str):
    path = WORKDIR / "proposals.jsonl"
    if not path.exists():
        raise HTTPException(404, "no proposals")
    lines = path.read_text().strip().splitlines()
    kept = []
    accepted = None
    for line in lines:
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("id") == pid:
            accepted = rec
        else:
            kept.append(line)
    if accepted is None:
        raise HTTPException(404, f"proposal '{pid}' not found")
    with open(path, "w") as f:
        f.writelines(l + "\n" for l in kept)
    # Add to topic
    topics_dir = WORKDIR / "topics"
    topics_dir.mkdir(parents=True, exist_ok=True)
    tpath = topics_dir / f"{accepted['topic']}.md"
    entries = []
    if tpath.exists():
        entries = [e.strip() for e in tpath.read_text().split("§") if e.strip()]
    entries.append(accepted["text"])
    content = "\n§\n".join(entries) + "\n"
    tpath.write_text(content)
    return {"ok": True, "moved_to": accepted["topic"]}


@app.delete("/proposals/{pid}")
async def reject_proposal(pid: str):
    path = WORKDIR / "proposals.jsonl"
    if not path.exists():
        raise HTTPException(404, "no proposals")
    lines = path.read_text().strip().splitlines()
    kept = []
    found = False
    for line in lines:
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("id") == pid:
            found = True
        else:
            kept.append(line)
    if not found:
        raise HTTPException(404, f"proposal '{pid}' not found")
    with open(path, "w") as f:
        f.writelines(l + "\n" for l in kept)
    return {"ok": True, "rejected": pid}


@app.delete("/proposals")
async def clear_proposals(confirm: bool = False):
    if not confirm:
        raise HTTPException(400, "set ?confirm=true to clear all proposals")
    path = WORKDIR / "proposals.jsonl"
    if path.exists():
        path.write_text("")
    return {"ok": True}


@app.delete("/topics/{name}")
async def delete_topic(name: str):
    path = WORKDIR / "topics" / f"{name}.md"
    if not path.exists():
        raise HTTPException(404, f"topic '{name}' not found")
    path.unlink()
    return {"ok": True, "deleted": name}


class EditTopicFact(BaseModel):
    index: int
    text: str


@app.put("/topics/{name}")
async def edit_topic_fact(name: str, body: EditTopicFact):
    path = WORKDIR / "topics" / f"{name}.md"
    if not path.exists():
        raise HTTPException(404, f"topic '{name}' not found")
    entries = [e.strip() for e in path.read_text().split("§") if e.strip()]
    if body.index < 1 or body.index > len(entries):
        raise HTTPException(400, f"index {body.index} out of range (1-{len(entries)})")
    entries[body.index - 1] = body.text
    path.write_text("\n§\n".join(entries) + "\n")
    return {"ok": True, "topic": name, "entries": len(entries)}


@app.delete("/topics/{name}/{index}")
async def delete_topic_fact(name: str, index: int):
    path = WORKDIR / "topics" / f"{name}.md"
    if not path.exists():
        raise HTTPException(404, f"topic '{name}' not found")
    entries = [e.strip() for e in path.read_text().split("§") if e.strip()]
    if index < 1 or index > len(entries):
        raise HTTPException(400, f"index {index} out of range (1-{len(entries)})")
    removed = entries.pop(index - 1)
    path.write_text("\n§\n".join(entries) + "\n" if entries else "")
    return {"ok": True, "removed": removed, "entries": len(entries)}


# ── Context ─────────────────────────────────────────────────


@app.get("/context/{project}")
async def get_context(project: str):
    state = current_state(project)
    if state is None:
        raise HTTPException(404, "no active context")
    return state


class WriteContext(BaseModel):
    task: str = ""
    files: list[str] = []
    claims: dict[str, int] = {}


@app.post("/context/{project}")
async def post_context(project: str, body: WriteContext):
    state = write_state(project, body.task, body.files, body.claims)
    return {"ok": True, "state": state}


@app.delete("/context/{project}")
async def delete_context(project: str):
    ok = release_state(project)
    return {"ok": ok}


@app.post("/context/{project}/claim/{filename:path}")
async def claim_project_file(project: str, filename: str):
    ok, conflict = claim_file(project, filename)
    if not ok and conflict:
        raise HTTPException(409, f"file claimed by pid {conflict}")
    return {"ok": ok, "file": filename}


# ── Per-project memory ─────────────────────────────────────


@app.get("/memory/{project}")
async def get_memory(project: str):
    entries = _parse_memory(project)
    return {"project": project, "entries": entries, "count": len(entries)}


class AddMemory(BaseModel):
    text: str


@app.post("/memory/{project}")
async def add_memory(project: str, body: AddMemory):
    entries = _parse_memory(project)
    total = sum(len(e) for e in entries)
    if total + len(body.text) > MEMORY_LIMIT:
        raise HTTPException(413, f"memory limit {MEMORY_LIMIT} chars")
    entries.append(body.text)
    _write_memory(project, entries)
    return {"ok": True, "entries": len(entries), "chars": total + len(body.text)}


class ReplaceMemory(BaseModel):
    old: str
    new: str


@app.put("/memory/{project}")
async def replace_memory(project: str, body: ReplaceMemory):
    entries = _parse_memory(project)
    found = False
    for i, e in enumerate(entries):
        if body.old in e:
            entries[i] = body.new
            found = True
    if not found:
        raise HTTPException(404, f"no entry containing: {body.old}")
    _write_memory(project, entries)
    return {"ok": True, "entries": len(entries)}


# ── Compression ─────────────────────────────────────────────


class CompressRequest(BaseModel):
    text: str
    phase: int = 2


@app.post("/compress")
async def compress_text(body: CompressRequest):
    result = compress(body.text, phase=body.phase)
    return {
        "compressed": result,
        "original_chars": len(body.text),
        "compressed_chars": len(result),
    }


# ═══════════════════════════════════════════════════════════════
#  Agent Registry
# ═══════════════════════════════════════════════════════════════


def _agent_path(agent_id: str) -> Path:
    return AGENTS_DIR / f"{agent_id}.json"


def _load_agent(agent_id: str) -> dict | None:
    p = _agent_path(agent_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _save_agent(agent: dict):
    _agent_path(agent["id"]).write_text(json.dumps(agent, ensure_ascii=False, indent=2))


def _delete_agent(agent_id: str):
    p = _agent_path(agent_id)
    if p.exists():
        p.unlink()


def _list_agents(project: str | None = None) -> list[dict]:
    if not AGENTS_DIR.exists():
        return []
    agents = []
    now = time.time()
    for f in sorted(AGENTS_DIR.iterdir()):
        if f.suffix != ".json":
            continue
        try:
            a = json.loads(f.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        pid = a.get("pid", 0)
        if pid and now - a.get("last_heartbeat", 0) > AGENT_STALE_SEC:
            try:
                os.kill(pid, 0)
            except OSError:
                _delete_agent(a["id"])
                continue
        if project and a.get("project") != project:
            continue
        agents.append(a)
    return agents


def _check_file_conflicts(
    project: str, files: list[str], exclude_id: str = ""
) -> list[str]:
    conflicts = []
    for a in _list_agents(project):
        if a["id"] == exclude_id:
            continue
        for f in files:
            if f in a.get("files", []):
                conflicts.append(
                    f"'{f}' claimed by agent '{a['id']}' ({a.get('task', '?')})"
                )
    return conflicts


def _notify_chitchat(text: str):
    try:
        req = urllib.request.Request(
            f"{CHITCHAT_URL}/general/say",
            data=json.dumps({"text": text[:1000], "from_name": "agent-os"}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=3)
    except Exception:
        pass


class RegisterAgent(BaseModel):
    project: str
    task: str = ""
    files: list[str] = []
    chitchat_name: str = ""


class AgentHeartbeat(BaseModel):
    status: str = "active"
    commit_log: list[str] = []


@app.post("/agents")
async def register_agent(body: RegisterAgent):
    agent_id = f"agent_{uuid.uuid4().hex[:12]}"
    conflicts = _check_file_conflicts(body.project, body.files)
    agent = {
        "id": agent_id,
        "project": body.project,
        "task": body.task[:500],
        "files": body.files,
        "pid": os.getpid(),
        "started_at": time.time(),
        "last_heartbeat": time.time(),
        "status": "active",
        "commit_log": [],
        "chitchat_name": body.chitchat_name,
        "conflicts_warned": conflicts,
    }
    _save_agent(agent)
    _notify_chitchat(
        f"agent {agent_id[:12]} started on project '{body.project}': {body.task[:200]}"
        + (f" — conflicts: {'; '.join(conflicts)}" if conflicts else "")
    )
    return {"ok": True, "agent_id": agent_id, "conflicts": conflicts}


@app.get("/agents")
async def list_agents(project: Optional[str] = None):
    agents = _list_agents(project)
    return {"agents": agents, "count": len(agents)}


@app.get("/agents/{agent_id}")
async def get_agent(agent_id: str):
    a = _load_agent(agent_id)
    if a is None:
        raise HTTPException(404, "agent not found")
    return a


@app.patch("/agents/{agent_id}")
async def heartbeat(agent_id: str, body: AgentHeartbeat):
    a = _load_agent(agent_id)
    if a is None:
        raise HTTPException(404, "agent not found")
    a["last_heartbeat"] = time.time()
    a["status"] = body.status
    if body.commit_log:
        a["commit_log"].extend(body.commit_log)
    _save_agent(a)
    return {"ok": True}


@app.delete("/agents/{agent_id}")
async def deregister_agent(agent_id: str):
    a = _load_agent(agent_id)
    if a is None:
        raise HTTPException(404, "agent not found")
    _delete_agent(agent_id)
    _notify_chitchat(
        f"agent {agent_id[:12]} finished: {a.get('task', '?')[:200]}"
        + f" — {len(a.get('commit_log', []))} commits"
    )
    return {"ok": True, "commits": len(a.get("commit_log", []))}


# ═══════════════════════════════════════════════════════════════
#  Task Board
# ═══════════════════════════════════════════════════════════════


def _task_path(task_id: str) -> Path:
    return TASKS_DIR / f"{task_id}.json"


def _load_task(task_id: str) -> dict | None:
    p = _task_path(task_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _save_task(task: dict):
    _task_path(task["id"]).write_text(json.dumps(task, ensure_ascii=False, indent=2))


def _delete_task(task_id: str):
    p = _task_path(task_id)
    if p.exists():
        p.unlink()


def _list_tasks(project: str | None = None, status: str | None = None) -> list[dict]:
    if not TASKS_DIR.exists():
        return []
    tasks = []
    for f in sorted(TASKS_DIR.iterdir()):
        if f.suffix != ".json":
            continue
        try:
            t = json.loads(f.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if project and t.get("project") != project:
            continue
        if status and t.get("status") != status:
            continue
        tasks.append(t)
    return tasks


class CreateTask(BaseModel):
    project: str
    title: str
    description: str = ""
    assigned_to: str = ""
    depends_on: list[str] = []


class UpdateTask(BaseModel):
    status: str = ""
    assigned_to: str = ""
    result: str = ""
    error: str = ""
    rollback_commit: str = ""


@app.post("/tasks")
async def create_task(body: CreateTask):
    task_id = f"task_{uuid.uuid4().hex[:12]}"
    task = {
        "id": task_id,
        "project": body.project,
        "title": body.title[:200],
        "description": body.description[:2000],
        "status": "pending",
        "assigned_to": body.assigned_to,
        "depends_on": body.depends_on,
        "created_at": time.time(),
        "assigned_at": None,
        "result": "",
        "error": "",
        "rollback_commit": "",
    }
    _save_task(task)
    if body.assigned_to:
        _notify_chitchat(
            f"@{body.assigned_to} task assigned: {body.title[:200]} on '{body.project}'"
        )
    return {"ok": True, "task_id": task_id}


@app.get("/tasks")
async def list_tasks(
    project: Optional[str] = None,
    status: Optional[str] = None,
):
    tasks = _list_tasks(project, status)
    return {"tasks": tasks, "count": len(tasks)}


@app.get("/tasks/{task_id}")
async def get_task(task_id: str):
    t = _load_task(task_id)
    if t is None:
        raise HTTPException(404, "task not found")
    return t


@app.patch("/tasks/{task_id}")
async def update_task(task_id: str, body: UpdateTask):
    t = _load_task(task_id)
    if t is None:
        raise HTTPException(404, "task not found")
    if body.status:
        t["status"] = body.status
        if body.status == "assigned":
            t["assigned_at"] = time.time()
            t["assigned_to"] = body.assigned_to or t.get("assigned_to", "")
    if body.assigned_to:
        t["assigned_to"] = body.assigned_to
        t["assigned_at"] = time.time()
    if body.result:
        t["result"] = body.result[:2000]
    if body.error:
        t["error"] = body.error[:2000]
        t["status"] = "failed"
    if body.rollback_commit:
        t["rollback_commit"] = body.rollback_commit
    _save_task(t)
    return {"ok": True}


@app.delete("/tasks/{task_id}")
async def delete_task(task_id: str):
    t = _load_task(task_id)
    if t is None:
        raise HTTPException(404, "task not found")
    _delete_task(task_id)
    return {"ok": True}


# ═══════════════════════════════════════════════════════════════
#  Safety Layer — Git Snapshots & Rollback
# ═══════════════════════════════════════════════════════════════


def _safety_dir(project: str) -> Path:
    p = SAFETY_DIR / project
    p.mkdir(parents=True, exist_ok=True)
    return p


def _snapshot_path(project: str) -> Path:
    return _safety_dir(project) / "snapshots.jsonl"


def _run_git(args: list[str], cwd: str | None = None) -> tuple[str, str, int]:
    try:
        r = subprocess.run(
            ["git"] + args,
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=30,
        )
        return r.stdout.strip(), r.stderr.strip(), r.returncode
    except FileNotFoundError:
        return "", "git not found", -1
    except subprocess.TimeoutExpired:
        return "", "git timed out", -1


def _detect_project_dir(project: str) -> str | None:
    paths = [
        Path.cwd(),
        Path.home() / project,
        Path("/mnt/external-drive/code") / project,
    ]
    for p in paths:
        if (p / ".git").exists():
            return str(p)
    return None


class CreateSnapshot(BaseModel):
    message: str = "agent-os snapshot"
    agent_id: str = ""
    project_dir: str = ""


class RollbackRequest(BaseModel):
    project_dir: str = ""
    snapshot_id: str = ""


@app.post("/safety/snapshot/{project}")
async def create_snapshot(project: str, body: Optional[CreateSnapshot] = None):
    msg = body.message if body else "agent-os snapshot"
    aid = body.agent_id if body else ""
    prj_dir = (
        body.project_dir if body and body.project_dir else _detect_project_dir(project)
    )
    if not prj_dir:
        raise HTTPException(400, f"could not detect git repo for project '{project}'")
    # Stage everything (new + modified + deleted)
    _run_git(["add", "-A"], cwd=prj_dir)
    # Commit with inline identity — no global git config needed
    _run_git(
        [
            "-c",
            "user.name=agent-os",
            "-c",
            "user.email=agent@memoria.local",
            "commit",
            "-m",
            f"[agent-os][snapshot] {msg[:200]}",
        ],
        cwd=prj_dir,
    )
    # Record the current HEAD (the commit we just made, or existing one)
    stdout, stderr, code = _run_git(["rev-parse", "HEAD"], cwd=prj_dir)
    if code != 0:
        raise HTTPException(500, f"git rev-parse failed: {stderr}")
    current_hash = stdout
    sid = f"snap_{uuid.uuid4().hex[:12]}"
    snapshot = {
        "id": sid,
        "project": project,
        "project_dir": prj_dir,
        "commit_hash": current_hash,
        "message": msg[:200],
        "agent_id": aid,
        "created_at": time.time(),
    }
    path = _snapshot_path(project)
    with open(path, "a") as f:
        f.write(json.dumps(snapshot) + "\n")
    return {"ok": True, "snapshot_id": sid, "commit_hash": current_hash}


@app.get("/safety/{project}/snapshots")
async def list_snapshots(project: str):
    path = _snapshot_path(project)
    if not path.exists():
        return {"snapshots": [], "count": 0}
    lines = path.read_text().strip().splitlines()
    snaps = []
    for line in lines:
        try:
            snaps.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return {"snapshots": list(reversed(snaps)), "count": len(snaps)}


@app.post("/safety/rollback/{project}")
async def rollback_snapshot(project: str, body: RollbackRequest):
    path = _snapshot_path(project)
    if not path.exists():
        raise HTTPException(404, f"no snapshots for project '{project}'")
    lines = path.read_text().strip().splitlines()
    target = None
    if body.snapshot_id:
        for line in lines:
            try:
                s = json.loads(line)
                if s["id"] == body.snapshot_id:
                    target = s
                    break
            except json.JSONDecodeError:
                continue
        if not target:
            raise HTTPException(404, f"snapshot '{body.snapshot_id}' not found")
    else:
        try:
            target = json.loads(lines[-1])
        except (IndexError, json.JSONDecodeError):
            raise HTTPException(404, "no snapshots available")
    prj_dir = (
        body.project_dir or target.get("project_dir") or _detect_project_dir(project)
    )
    if not prj_dir:
        raise HTTPException(400, "could not detect git repo directory")
    # Git reset
    stdout, stderr, code = _run_git(
        ["reset", "--hard", target["commit_hash"]],
        cwd=prj_dir,
    )
    if code != 0:
        raise HTTPException(500, f"rollback failed: {stderr}")
    _run_git(["clean", "-fd"], cwd=prj_dir)
    _notify_chitchat(
        f"rollback on '{project}' to {target['commit_hash'][:12]} "
        f"({target.get('message', '?')})"
    )
    return {
        "ok": True,
        "rolled_back_to": target["commit_hash"],
        "snapshot_id": target["id"],
        "message": target.get("message", ""),
    }


# ═══════════════════════════════════════════════════════════════
#  Chitchat Endpoints
# ═══════════════════════════════════════════════════════════════


@app.get("/chitchat/rooms")
async def chitchat_rooms():
    if not CHITCHAT_DIR.exists():
        return {"rooms": []}
    rooms = []
    for d in sorted(CHITCHAT_DIR.iterdir()):
        if d.is_dir():
            path = d / "inbox.jsonl"
            count = sum(1 for _ in path.open()) if path.exists() else 0
            rooms.append({"room": d.name, "messages": count})
    return {"rooms": rooms, "count": len(rooms)}


@app.get("/chitchat/{room}")
async def chitchat_history(room: str, limit: int = 20):
    path = CHITCHAT_DIR / room / "inbox.jsonl"
    if not path.exists():
        raise HTTPException(404, f"no messages for room '{room}'")
    lines = path.read_text().strip().splitlines()
    messages = [json.loads(l) for l in lines[-limit:] if l.strip()]
    return {"room": room, "messages": messages, "count": len(messages)}


@app.get("/config")
async def get_config():
    return {
        "memory_limit": MEMORY_LIMIT,
        "poll_interval": POLL_INTERVAL,
        "agent_stale_sec": AGENT_STALE_SEC,
        "chitchat_poll_interval": CHITCHAT_POLL_INTERVAL,
        "chitchat_consolidate_threshold": CHITCHAT_CONSOLIDATE_THRESHOLD,
        "chitchat_max_messages": CHITCHAT_MAX_MESSAGES,
        "sleep_cycle_hours": SLEEP_CYCLE_HOURS,
        "session_max_records": SESSION_MAX_RECORDS,
        "auto_accept_threshold": AUTO_ACCEPT_THRESHOLD,
        "chitchat_url": CHITCHAT_URL,
        "port": int(os.environ.get("MEMORIA_PORT", "19998")),
        "host": os.environ.get("MEMORIA_HOST", "0.0.0.0"),
    }


class ConfigUpdate(BaseModel):
    memory_limit: Optional[int] = None
    poll_interval: Optional[int] = None
    agent_stale_sec: Optional[int] = None
    chitchat_poll_interval: Optional[int] = None
    chitchat_consolidate_threshold: Optional[int] = None
    chitchat_max_messages: Optional[int] = None
    sleep_cycle_hours: Optional[int] = None
    session_max_records: Optional[int] = None
    auto_accept_threshold: Optional[int] = None
    chitchat_url: Optional[str] = None


@app.patch("/config")
async def update_config(updates: ConfigUpdate):
    data = updates.model_dump(exclude_none=True)
    field_map = {
        "memory_limit": "MEMORY_LIMIT",
        "poll_interval": "POLL_INTERVAL",
        "agent_stale_sec": "AGENT_STALE_SEC",
        "chitchat_poll_interval": "CHITCHAT_POLL_INTERVAL",
        "chitchat_consolidate_threshold": "CHITCHAT_CONSOLIDATE_THRESHOLD",
        "chitchat_max_messages": "CHITCHAT_MAX_MESSAGES",
        "sleep_cycle_hours": "SLEEP_CYCLE_HOURS",
        "session_max_records": "SESSION_MAX_RECORDS",
        "auto_accept_threshold": "AUTO_ACCEPT_THRESHOLD",
        "chitchat_url": "CHITCHAT_URL",
    }
    for field, var_name in field_map.items():
        if field in data:
            globals()[var_name] = data[field]
    return {"ok": True, "updated": list(data.keys())}


@app.post("/chitchat/consolidate")
async def trigger_consolidation():
    global _chitchat_unconsolidated
    _chitchat_unconsolidated = CHITCHAT_CONSOLIDATE_THRESHOLD
    _consolidate_chitchat()
    return {"ok": True}


# ═══════════════════════════════════════════════════════════════
#  Dashboard — HTML
# ═══════════════════════════════════════════════════════════════


DASHBOARD_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AgentOS Dashboard</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0d1117; color: #c9d1d9; padding: 20px; }
  h1 { color: #58a6ff; margin-bottom: 20px; }
  h2 { color: #8b949e; font-size: 14px; text-transform: uppercase; letter-spacing: 1px; margin: 20px 0 10px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 16px; }
  .card { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 16px; }
  .card h3 { color: #58a6ff; font-size: 14px; margin-bottom: 8px; }
  .stat { display: flex; justify-content: space-between; padding: 4px 0; font-size: 13px; }
  .stat span:first-child { color: #8b949e; }
  .tag { display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 11px; font-weight: 600; }
  .tag-active { background: #1b3a1b; color: #3fb950; }
  .tag-idle { background: #3a2e1b; color: #d29922; }
  .tag-done { background: #1b283a; color: #58a6ff; }
  .tag-pending { background: #3a1b1b; color: #f85149; }
  pre { background: #0d1117; border-radius: 4px; padding: 8px; font-size: 12px; overflow-x: auto; margin-top: 8px; }
  .meta { font-size: 11px; color: #484f58; margin-top: 8px; }
  .refresh { float: right; color: #58a6ff; text-decoration: none; font-size: 13px; cursor: pointer; }
  .empty { color: #484f58; font-style: italic; font-size: 13px; padding: 12px; }
  .error { color: #f85149; }
</style>
</head>
<body>
<h1>AgentOS <span style="font-size:14px;color:#8b949e;font-weight:400">orchestration dashboard</span>
<a class="refresh" href="/" onclick="setTimeout(()=>location.reload(),200)">&#x21bb; refresh</a></h1>
<div class="grid" id="health"></div>
<h2>Active Agents</h2>
<div id="agents"></div>
<h2>Recent Tasks</h2>
<div id="tasks"></div>
<h2>Snapshots</h2>
<div id="snapshots"></div>
<script>
async function load() {
  try {
    const [health, agents, tasks] = await Promise.all([
      fetch('/health').then(r=>r.json()),
      fetch('/agents').then(r=>r.json()),
      fetch('/tasks').then(r=>r.json()),
    ]);
    document.getElementById('health').innerHTML = `
      <div class="card"><h3>Server</h3>
        <div class="stat"><span>Version</span><span>${health.memoria_version}</span></div>
        <div class="stat"><span>Sessions Indexed</span><span>${health.sessions_indexed}</span></div>
        <div class="stat"><span>DB Present</span><span>${health.db_exists ? '✓' : '✗'}</span></div>
        <div class="stat"><span>Topics</span><span>${(health.topics||[]).join(', ') || 'none'}</span></div>
      </div>
      <div class="card"><h3>Agent Activity</h3>
        <div class="stat"><span>Active Agents</span><span>${agents.count}</span></div>
        <div class="stat"><span>Total Tasks</span><span>${tasks.count}</span></div>
      </div>`;
    const agentsEl = document.getElementById('agents');
    agentsEl.innerHTML = (agents.agents || []).length
      ? (agents.agents || []).map(a => `
        <div class="card">
          <div class="stat"><span>${a.id.slice(0,16)}...</span><span class="tag tag-${a.status}">${a.status}</span></div>
          <div class="stat"><span>Project</span><span>${htmlEscape(a.project)}</span></div>
          <div class="stat"><span>Task</span><span>${htmlEscape((a.task||'').slice(0,100))}</span></div>
          <div class="stat"><span>Files</span><span>${(a.files||[]).length}</span></div>
          <div class="stat"><span>Commits</span><span>${(a.commit_log||[]).length}</span></div>
          <div class="meta">started ${new Date(a.started_at*1000).toLocaleString()}</div>
          ${a.conflicts_warned && a.conflicts_warned.length
            ? '<div class="error">⚠ ' + htmlEscape(a.conflicts_warned.join('; ')) + '</div>'
            : ''}
        </div>`).join('')
      : '<div class="empty">No active agents</div>';
    const tasksEl = document.getElementById('tasks');
    tasksEl.innerHTML = (tasks.tasks || []).length
      ? (tasks.tasks || []).slice(-10).reverse().map(t => `
        <div class="card">
          <div class="stat"><span>${htmlEscape(t.title)}</span><span class="tag tag-${t.status == 'pending' ? 'pending' : t.status == 'done' || t.status == 'completed' ? 'done' : 'active'}">${t.status}</span></div>
          <div class="stat"><span>Project</span><span>${htmlEscape(t.project)}</span></div>
          <div class="stat"><span>Assigned</span><span>${t.assigned_to || 'unassigned'}</span></div>
          ${t.result ? '<pre>' + htmlEscape(t.result.slice(0,200)) + '</pre>' : ''}
          ${t.error ? '<pre class="error">' + htmlEscape(t.error.slice(0,200)) + '</pre>' : ''}
          <div class="meta">${new Date(t.created_at*1000).toLocaleString()}${t.rollback_commit ? ' | rollback: ' + t.rollback_commit.slice(0,12) : ''}</div>
        </div>`).join('')
      : '<div class="empty">No tasks</div>';
    // Try to load snapshots
    try {
      const snaps = await fetch('/safety/' + ((health.topics||[])[0] || 'general') + '/snapshots').then(r=>r.json());
      document.getElementById('snapshots').innerHTML = snaps.count
        ? '<div class="card">' + snaps.snapshots.slice(0,10).map(s => `
          <div class="stat"><span>${s.commit_hash.slice(0,12)}</span><span>${s.message.slice(0,60)}</span></div>
          <div class="meta">${new Date(s.created_at*1000).toLocaleString()}${s.agent_id ? ' | by ' + s.agent_id.slice(0,16) : ''}</div>
        `).join('') + '</div>'
        : '<div class="empty">No snapshots yet</div>';
    } catch(e) {
      document.getElementById('snapshots').innerHTML = '<div class="empty">Snapshots not available for this project</div>';
    }
  } catch(e) {
    document.body.innerHTML = '<div class="error">Failed to load dashboard: ' + e.message + '</div>';
  }
}
function htmlEscape(s) { const d=document.createElement('div'); d.textContent=s; return d.innerHTML; }
load();
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    return HTMLResponse(DASHBOARD_HTML)


# ── Main ──────────────────────────────────────────────────────


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("MEMORIA_PORT", "19998"))
    host = os.environ.get("MEMORIA_HOST", "0.0.0.0")
    uvicorn.run(
        "memoriad_global:app", host=host, port=port, log_level="info", reload=False
    )
