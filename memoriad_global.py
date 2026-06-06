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
from cortex import get_engine, CortexEngine

# ── Paths ──────────────────────────────────────────────────────

WORKDIR = Path("/var/tmp/memoria")
AGENTS_DIR = WORKDIR / "agents"
TASKS_DIR = WORKDIR / "tasks"
SAFETY_DIR = WORKDIR / "safety"
OPENCODE_DB = Path.home() / ".local/share/opencode/opencode.db"
MEMORY_LIMIT = 5000
POLL_INTERVAL = 30
AGENT_STALE_SEC = 300
CHITCHAT_URL = os.environ.get("CHITCHAT_URL", "http://100.121.245.69:19999")
CHITCHAT_POLL_INTERVAL = 3
CHITCHAT_CONSOLIDATE_THRESHOLD = 20
CHITCHAT_DIR = WORKDIR / "chitchat"
CHITCHAT_MAX_MESSAGES = 10000          # per-room slot limit (hippocampal capacity)
CHITCHAT_ROOMS = [r.strip() for r in os.environ.get("CHITCHAT_ROOMS", "general").split(",")]
SLEEP_CYCLE_HOURS = int(os.environ.get("SLEEP_CYCLE_HOURS", "6"))
SESSION_MAX_RECORDS = int(os.environ.get("SESSION_MAX_RECORDS", "5000"))  # total session slot limit
AUTO_ACCEPT_THRESHOLD = int(os.environ.get("AUTO_ACCEPT_THRESHOLD", "3"))
CLIENTS_DIR = WORKDIR / "clients"
CLIENTS_CONF = Path(os.environ.get("MEMORIA_CLIENTS_CONF", "/mnt/external-drive/code/memoria/opencode-integration/clients.conf"))

# ── Background task references ────────────────────────────────

_poll_task: asyncio.Task | None = None
_chitchat_poll_task: asyncio.Task | None = None
_sleep_cycle_task: asyncio.Task | None = None
_cortex_task: asyncio.Task | None = None
_skill_watch_task: asyncio.Task | None = None

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
    """Cross-layer consolidation: sessions → topics + chat → topics.
    
    Enhanced with:
      - Hippocampal episode consolidation with similarity clustering
      - Offline Q-learning via replay
      - Reward-based pruning (keep episodes with reward >= 0.2)
      - Episode → topic extraction bridge
    """
    _prune_sessions()

    # 1. Force chitchat consolidation
    global _chitchat_unconsolidated
    _chitchat_unconsolidated = CHITCHAT_CONSOLIDATE_THRESHOLD
    _consolidate_chitchat()

    # 2. Consolidate hippocampal episodes: cluster by similarity
    try:
        from cortex import get_engine
        projects = set()
        tasks = _list_tasks(None, None)
        for t in tasks:
            p = t.get("project", "")
            if p:
                projects.add(p)
        for project in projects:
            engine = get_engine(project)
            episodes = engine.hippocampus.episodes
            if len(episodes) < 3:
                continue
            # Simple k-means-like clustering by task_type
            clusters: dict[str, list[dict]] = {}
            for ep in episodes:
                meta = ep.get("meta", {}) or {}
                ttype = meta.get("type", "generic")
                clusters.setdefault(ttype, []).append(ep)
            # Offline Q-learning: for each cluster, run replay steps
            for ttype, cluster in clusters.items():
                for _ in range(min(len(cluster), 10)):
                    engine.replay.replay_step(lr_multiplier=0.3)
            # Reward-based pruning: remove episodes with reward < 0.2
            before = len(episodes)
            engine.hippocampus.episodes = [e for e in episodes if e.get("reward", 0) >= 0.2]
            pruned = before - len(engine.hippocampus.episodes)
            if pruned:
                engine.hippocampus.save()
            # Episodic → semantic: extract topic proposals from high-value clusters
            for ttype, cluster in clusters.items():
                high_val = [e for e in cluster if e.get("reward", 0) >= 0.7]
                if len(high_val) >= 3:
                    kw = Counter()
                    for e in high_val:
                        title = e.get("task_title", "")
                        for w in title.split():
                            if len(w) >= 4:
                                kw[w.lower()] += 1
                    top_kw = [w for w, c in kw.most_common(3)]
                    if top_kw:
                        existing = _load_existing_topic_names()
                        if not _find_matching_topic(top_kw, existing):
                            prop_text = (f"[hippocampal consolidation] Cluster '{ttype}': "
                                         f"{len(high_val)} high-reward episodes, "
                                         f"keywords: {', '.join(top_kw)}")
                            prop_path = WORKDIR / "proposals.jsonl"
                            record = {
                                "id": f"hip_consolidate_{int(time.time())}",
                                "text": prop_text,
                                "topic": top_kw[0][:50],
                                "proposed_at": time.time(),
                                "source": "hippocampal_consolidation",
                            }
                            with open(prop_path, "a") as f:
                                f.write(json.dumps(record) + "\n")
    except Exception:
        pass

    # 3. Pull recent sessions and check if any new topic patterns emerge
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
_cortex_task: asyncio.Task | None = None
_skill_watch_task: asyncio.Task | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    _init_index()
    global _poll_task, _chitchat_poll_task, _sleep_cycle_task, _cortex_task, _skill_watch_task
    _poll_task = asyncio.create_task(_poll_loop())
    _chitchat_poll_task = asyncio.create_task(_chitchat_poll_loop())
    _sleep_cycle_task = asyncio.create_task(_sleep_cycle_loop())
    _cortex_task = asyncio.create_task(_cortex_auction_loop())
    _skill_watch_task = asyncio.create_task(_skill_watch_loop())
    yield
    for t in (_poll_task, _chitchat_poll_task, _sleep_cycle_task, _cortex_task, _skill_watch_task):
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
    capabilities: list[str] = ["general"]


class AgentHeartbeat(BaseModel):
    status: str = "active"
    activity: str = ""
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
        "capabilities": body.capabilities,
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
    if body.activity:
        a["activity"] = body.activity
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
#  CORTEX — Brain-Inspired Autonomous Task Allocation
# ═══════════════════════════════════════════════════════════════


class CortexBidRequest(BaseModel):
    agent_id: str = ""
    project: str = ""


class CortexCompleteRequest(BaseModel):
    agent_id: str
    task_id: str
    reward: float = 0.8
    task_type: str = "generic"
    complexity: int = 5
    result: str = "completed"


@app.get("/cortex/status")
async def cortex_status(project: Optional[str] = None):
    p = project or "unknown"
    engine = get_engine(p)
    return {"ok": True, "cortex": engine.get_status()}


@app.post("/cortex/bid")
async def cortex_bid(body: CortexBidRequest):
    project = body.project or "unknown"
    engine = get_engine(project)
    pending = _list_tasks(project, "pending")
    agents = _list_agents(project)
    results = []
    for task in pending:
        assignment = engine.process_task_assignment(task, agents)
        if assignment:
            task["status"] = "assigned"
            task["assigned_to"] = assignment["winner"]["agent_id"]
            task["assigned_at"] = time.time()
            payload = task.get("payload", {}) or {}
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except (json.JSONDecodeError, TypeError):
                    payload = {}
            payload["cortex_bid"] = assignment
            task["payload"] = payload
            _save_task(task)
            results.append(assignment)
    return {
        "ok": True,
        "project": project,
        "tasks_scanned": len(pending),
        "assignments": len(results),
        "details": results,
    }


@app.post("/cortex/complete")
async def cortex_complete(body: CortexCompleteRequest):
    engine = get_engine("_global")
    t = _load_task(body.task_id)
    if t is None:
        raise HTTPException(404, "task not found")
    project = t.get("project", "unknown")
    engine = get_engine(project)
    t["status"] = "completed"
    t["result"] = body.result[:2000] if body.result else "completed"
    payload = t.get("payload", {}) or {}
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            payload = {}
    payload["cortex_reward"] = body.reward
    t["payload"] = payload
    _save_task(t)
    da_signals = engine.record_outcome(
        body.task_id, body.agent_id,
        t.get("title", "?"),
        body.task_type, body.complexity, body.reward,
    )
    return {
        "ok": True,
        "task_id": body.task_id,
        "reward": body.reward,
        "da_signals": {k: round(v, 4) for k, v in da_signals.items()} if isinstance(da_signals, dict) else {"rpe": round(da_signals, 4)},
        "status": "completed",
    }


@app.get("/cortex/learnings")
async def cortex_learnings(project: Optional[str] = None, n: int = 5):
    p = project or "unknown"
    engine = get_engine(p)
    recent = engine.hippocampus.recent(n)
    return {
        "ok": True,
        "project": p,
        "total_episodes": len(engine.hippocampus.episodes),
        "recent": reversed(recent),
    }


@app.get("/cortex/policy")
async def cortex_policy(project: Optional[str] = None):
    p = project or "unknown"
    engine = get_engine(p)
    policy = {}
    for state in engine.gating.Go:
        go_vals = engine.gating.Go.get(state, {})
        nogo_vals = engine.gating.NoGo.get(state, {})
        all_a = set(go_vals) | set(nogo_vals)
        net = {}
        for a in all_a:
            bg = engine.gating.beta_g.get(state, {}).get(a, 1.0)
            bn = engine.gating.beta_n.get(state, {}).get(a, 1.0)
            gv = go_vals.get(a, 0.5)
            nv = nogo_vals.get(a, 0.5)
            net[a] = round(bg * gv - bn * nv, 3)
        sorted_a = sorted(net.items(), key=lambda x: -x[1])
        policy[state] = dict(sorted_a)
    return {
        "ok": True,
        "project": p,
        "epsilon": engine.gating.epsilon,
        "policy": policy,
        "ensembles": {
            "agents": len(engine.auction.reputation),
            "avg_responsiveness": round(sum(engine.auction.responsiveness.values()) / max(len(engine.auction.responsiveness), 1), 3),
            "avg_choice": round(sum(engine.auction.choice.values()) / max(len(engine.auction.choice), 1), 3),
        },
    }


async def _cortex_auction_loop():
    """Background task: scan for pending tasks and run CORTEX auction."""
    await asyncio.sleep(30)
    while True:
        try:
            pending = _list_tasks(None, "pending")
            projects = set(t.get("project", "unknown") for t in pending)
            for project in projects:
                engine = get_engine(project)
                agents = _list_agents(project)
                project_tasks = [t for t in pending if t.get("project") == project]
                for task in project_tasks:
                    assignment = engine.process_task_assignment(task, agents)
                    if assignment:
                        task["status"] = "assigned"
                        task["assigned_to"] = assignment["winner"]["agent_id"]
                        task["assigned_at"] = time.time()
                        _save_task(task)
        except Exception:
            pass
        await asyncio.sleep(15)


async def _skill_watch_loop():
    """Background task: watch skills dir for changes, auto-push to clients."""
    skills_dir = CLIENTS_CONF.parent / "skills"
    if not skills_dir.exists():
        return
    last_mtimes: dict[str, float] = {}
    debounce = 0.0
    print(f"[skill-watch] watching {skills_dir} for changes")
    while True:
        await asyncio.sleep(10)
        try:
            changed = False
            for f in skills_dir.rglob("SKILL.md"):
                mtime = f.stat().st_mtime
                prev = last_mtimes.get(str(f))
                if prev is not None and mtime != prev:
                    changed = True
                last_mtimes[str(f)] = mtime
            if not last_mtimes:
                for f in skills_dir.rglob("SKILL.md"):
                    last_mtimes[str(f)] = f.stat().st_mtime
                continue
            now = time.time()
            if changed and now - debounce > 30:
                debounce = now
                print(f"[skill-watch] change detected in skills/, pushing to clients")
                await asyncio.to_thread(_push_to_clients)
        except Exception:
            pass


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


class ChitchatSay(BaseModel):
    text: str
    from_name: str = "dashboard"


@app.post("/chitchat/{room}/say")
async def chitchat_say(room: str, body: ChitchatSay):
    url = f"{CHITCHAT_URL}/{urllib.parse.quote(room)}/say"
    payload = {"text": body.text, "from_name": body.from_name}
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except Exception as e:
        raise HTTPException(502, f"chitchat proxy error: {e}")


@app.post("/chitchat/consolidate")
async def trigger_consolidation():
    global _chitchat_unconsolidated
    _chitchat_unconsolidated = CHITCHAT_CONSOLIDATE_THRESHOLD
    _consolidate_chitchat()
    return {"ok": True}


# ═══════════════════════════════════════════════════════════════
#  Client Registry
# ═══════════════════════════════════════════════════════════════

class RegisterClient(BaseModel):
    name: str
    host: str
    ssh_key: str = "~/.ssh/id_memoria"
    user: str = "daivolt"


def _load_clients() -> list[dict]:
    CLIENTS_DIR.mkdir(parents=True, exist_ok=True)
    clients_file = CLIENTS_DIR / "clients.json"
    if clients_file.exists():
        try:
            return json.loads(clients_file.read_text())
        except (json.JSONDecodeError, ValueError):
            pass
    if CLIENTS_CONF.exists():
        clients = []
        for line in CLIENTS_CONF.read_text().strip().splitlines():
            parts = line.strip().split()
            if not parts or parts[0].startswith("#"):
                continue
            clients.append({
                "name": parts[0],
                "host": parts[1] if len(parts) > 1 else "",
                "ssh_key": parts[2] if len(parts) > 2 else "~/.ssh/id_memoria",
                "user": parts[3] if len(parts) > 3 else "daivolt",
                "source": "conf",
            })
        clients_file.write_text(json.dumps(clients, ensure_ascii=False, indent=2))
        return clients
    return []


def _save_clients(clients: list[dict]):
    CLIENTS_DIR.mkdir(parents=True, exist_ok=True)
    (CLIENTS_DIR / "clients.json").write_text(json.dumps(clients, ensure_ascii=False, indent=2))


@app.get("/clients")
async def list_clients():
    clients = _load_clients()
    return {"clients": clients, "count": len(clients)}


@app.post("/clients")
async def register_client(body: RegisterClient):
    clients = _load_clients()
    existing = next((c for c in clients if c["name"] == body.name), None)
    if existing:
        existing["host"] = body.host
        existing["ssh_key"] = body.ssh_key
        existing["user"] = body.user
    else:
        clients.append(body.model_dump())
    _save_clients(clients)
    return {"ok": True, "name": body.name, "action": "updated" if existing else "registered"}


@app.delete("/clients/{name}")
async def remove_client(name: str):
    clients = _load_clients()
    before = len(clients)
    clients = [c for c in clients if c["name"] != name]
    if len(clients) == before:
        raise HTTPException(404, f"client '{name}' not found")
    _save_clients(clients)
    return {"ok": True, "removed": name}


def _push_to_clients() -> dict:
    """Sync push to all registered clients. Returns result dict."""
    clients = _load_clients()
    if not clients:
        return {"ok": True, "pushed": 0, "message": "no clients registered"}

    source_dir = str(CLIENTS_CONF.parent)
    results = []

    for client in clients:
        name = client["name"]
        host = client["host"]
        key = os.path.expanduser(client.get("ssh_key", "~/.ssh/id_memoria"))
        user = client.get("user", "daivolt")

        if not os.path.isfile(key):
            results.append({"name": name, "status": "skip", "error": "key not found"})
            continue

        try:
            r_sync = subprocess.run(
                ["rsync", "-avz", "--delete",
                 "-e", f"ssh -i {key} -o StrictHostKeyChecking=accept-new -o ConnectTimeout=5",
                 f"{source_dir}/", f"{user}@{host}:/tmp/memoria-update/"],
                capture_output=True, text=True, timeout=30,
            )
            if r_sync.returncode != 0:
                results.append({"name": name, "status": "fail", "error": r_sync.stderr[:200]})
                continue

            r_install = subprocess.run(
                ["ssh", "-i", key, "-o", "StrictHostKeyChecking=accept-new",
                 "-o", "ConnectTimeout=5",
                 f"{user}@{host}", "bash /tmp/memoria-update/install.sh"],
                capture_output=True, text=True, timeout=30,
            )
            if r_install.returncode != 0:
                results.append({"name": name, "status": "fail", "error": r_install.stderr[:200]})
            else:
                results.append({"name": name, "status": "ok"})
        except subprocess.TimeoutExpired:
            results.append({"name": name, "status": "timeout"})
        except Exception as e:
            results.append({"name": name, "status": "error", "error": str(e)[:200]})

    ok_count = sum(1 for r in results if r["status"] == "ok")
    return {"ok": True, "pushed": ok_count, "total": len(clients), "results": results}


@app.post("/clients/push")
async def push_to_clients():
    return await asyncio.to_thread(_push_to_clients)


# ═══════════════════════════════════════════════════════════════
#  Dashboard — HTML
# ═══════════════════════════════════════════════════════════════


DASHBOARD_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Memoria Dashboard</title>
<style>
:root {
  --bg-primary: #0d0f14;
  --bg-surface: #13151c;
  --bg-elevated: #1a1d27;
  --bg-hover: #222536;
  --border: #2a2d3a;
  --border-light: #353847;
  --accent: #6c5ce7;
  --accent-glow: rgba(108, 92, 231, 0.15);
  --accent-secondary: #00cec9;
  --success: #00b894;
  --warning: #fdcb6e;
  --error: #e17055;
  --text-primary: #e8e8f0;
  --text-secondary: #8b8da0;
  --text-muted: #5a5c6a;
  --font: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  --mono: 'JetBrains Mono', 'Fira Code', 'Cascadia Code', monospace;
  --radius: 12px;
  --radius-sm: 8px;
  --radius-xs: 6px;
  --shadow: 0 4px 24px rgba(0,0,0,0.4);
  --sidebar-w: 200px;
  --topbar-h: 52px;
}
[data-theme="catppuccin"] {
  --bg-primary: #1e1e2e; --bg-surface: #252540; --bg-elevated: #2e2e4a;
  --border: #363654; --accent: #cba6f7; --accent-secondary: #89dceb;
  --success: #a6e3a1; --warning: #f9e2af; --error: #f38ba8;
  --text-primary: #cdd6f4; --text-secondary: #a6adc8; --text-muted: #6c7086;
}
[data-theme="nord"] {
  --bg-primary: #2e3440; --bg-surface: #3b4252; --bg-elevated: #434c5e;
  --border: #4c566a; --accent: #88c0d0; --accent-secondary: #8fbcbb;
  --success: #a3be8c; --warning: #ebcb8b; --error: #bf616a;
  --text-primary: #eceff4; --text-secondary: #d8dee9; --text-muted: #7b88a1;
}
[data-theme="cyberpunk"] {
  --bg-primary: #0a0a0f; --bg-surface: #14141f; --bg-elevated: #1c1c30;
  --border: #2a2a45; --accent: #ff00ff; --accent-secondary: #00ffff;
  --success: #00ff41; --warning: #ffff00; --error: #ff0040;
  --text-primary: #e0e0ff; --text-secondary: #8888aa; --text-muted: #555577;
}
[data-theme="gruvbox"] {
  --bg-primary: #1d2021; --bg-surface: #282828; --bg-elevated: #32302f;
  --border: #3c3836; --accent: #d79921; --accent-secondary: #689d6a;
  --success: #98971a; --warning: #d79921; --error: #cc241d;
  --text-primary: #ebdbb2; --text-secondary: #a89984; --text-muted: #7c6f64;
}
[data-theme="light"] {
  --bg-primary: #f5f5f5; --bg-surface: #ffffff; --bg-elevated: #f0f0f0;
  --border: #ddd; --accent: #6c5ce7; --accent-secondary: #00cec9;
  --success: #00b894; --warning: #fdcb6e; --error: #e17055;
  --text-primary: #1a1a2e; --text-secondary: #555; --text-muted: #999;
}
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
html, body { height: 100%; overflow: hidden; }
body {
  font-family: var(--font);
  background: var(--bg-primary);
  color: var(--text-primary);
  display: flex;
  flex-direction: column;
  font-size: 14px;
  line-height: 1.5;
  -webkit-font-smoothing: antialiased;
}

/* Top bar */
.topbar {
  height: var(--topbar-h);
  display: flex;
  align-items: center;
  padding: 0 20px;
  background: var(--bg-surface);
  border-bottom: 1px solid var(--border);
  gap: 16px;
  flex-shrink: 0;
  z-index: 10;
}
.topbar-logo {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 700;
  font-size: 16px;
  color: var(--accent);
  white-space: nowrap;
}
.topbar-logo .dot { width: 10px; height: 10px; border-radius: 50%; background: var(--success); transition: background 0.3s; }
.topbar-logo .dot.error { background: var(--error); }
.topbar-logo .dot.pulse { animation: pulse-dot 1.5s infinite; }
@keyframes pulse-dot { 0%,100% { opacity: 1; } 50% { opacity: 0.4; } }
.topbar-version { font-size: 11px; color: var(--text-muted); font-weight: 400; }
.topbar-spacer { flex: 1; }
.topbar-search {
  position: relative;
  display: flex;
  align-items: center;
}
.topbar-search input {
  background: var(--bg-elevated);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text-primary);
  padding: 6px 12px 6px 32px;
  font-size: 13px;
  width: 220px;
  outline: none;
  font-family: var(--font);
  transition: border-color 0.2s, width 0.2s;
}
.topbar-search input:focus { border-color: var(--accent); width: 280px; }
.topbar-search input::placeholder { color: var(--text-muted); }
.topbar-search .icon { position: absolute; left: 10px; color: var(--text-muted); font-size: 14px; pointer-events: none; }
.topbar-actions { display: flex; gap: 8px; align-items: center; }
.theme-btn {
  background: var(--bg-elevated);
  border: 1px solid var(--border);
  color: var(--text-secondary);
  border-radius: var(--radius-xs);
  padding: 5px 10px;
  font-size: 12px;
  cursor: pointer;
  transition: all 0.15s;
  font-family: var(--font);
}
.theme-btn:hover { background: var(--bg-hover); color: var(--text-primary); border-color: var(--accent); }
.help-btn {
  background: none;
  border: none;
  color: var(--text-muted);
  cursor: pointer;
  font-size: 18px;
  padding: 4px;
  border-radius: var(--radius-xs);
  transition: color 0.15s;
}
.help-btn:hover { color: var(--text-primary); }

/* Layout */
.layout {
  display: flex;
  flex: 1;
  overflow: hidden;
}

/* Sidebar */
.sidebar {
  width: var(--sidebar-w);
  background: var(--bg-surface);
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
  overflow-y: auto;
  padding: 12px 0;
}
.sidebar-label {
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 1.5px;
  color: var(--text-muted);
  padding: 16px 16px 8px;
  font-weight: 600;
}
.nav-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 16px;
  color: var(--text-secondary);
  cursor: pointer;
  font-size: 13px;
  border-left: 3px solid transparent;
  transition: all 0.12s;
  user-select: none;
}
.nav-item:hover { background: var(--bg-hover); color: var(--text-primary); }
.nav-item.active { background: var(--accent-glow); color: var(--accent); border-left-color: var(--accent); font-weight: 600; }
.nav-item .icon { width: 18px; text-align: center; font-size: 14px; flex-shrink: 0; }
.nav-item .badge {
  margin-left: auto;
  background: var(--accent);
  color: var(--bg-primary);
  font-size: 10px;
  font-weight: 700;
  padding: 1px 7px;
  border-radius: 10px;
  line-height: 1.4;
}

/* Main content */
.main {
  flex: 1;
  overflow-y: auto;
  padding: 20px 24px;
  background: var(--bg-primary);
}
.tab-content { display: none; }
.tab-content.active { display: block; }
.tab-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 20px;
  gap: 12px;
}
.tab-title { font-size: 18px; font-weight: 700; }
.tab-actions { display: flex; gap: 8px; align-items: center; }

/* Cards */
.card {
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 16px;
  transition: border-color 0.15s;
}
.card:hover { border-color: var(--border-light); }
.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
  gap: 8px;
}
.card-title { font-size: 13px; font-weight: 600; color: var(--text-secondary); text-transform: uppercase; letter-spacing: 0.5px; }

/* Stats grid */
.stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin-bottom: 20px; }
.stat-card { background: var(--bg-surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 16px; }
.stat-card .value { font-size: 28px; font-weight: 700; color: var(--text-primary); line-height: 1.2; }
.stat-card .label { font-size: 12px; color: var(--text-muted); margin-top: 4px; text-transform: uppercase; letter-spacing: 0.5px; }
.stat-card .sub { font-size: 12px; color: var(--text-secondary); margin-top: 2px; }
.stat-card .accent { color: var(--accent); }
.stat-card .success { color: var(--success); }
.stat-card .warning { color: var(--warning); }
.stat-card .error { color: var(--error); }

/* Progress bars */
.progress-bar {
  display: flex;
  height: 6px;
  border-radius: 3px;
  background: var(--bg-elevated);
  overflow: hidden;
  margin-top: 8px;
}
.progress-bar .fill {
  height: 100%;
  border-radius: 3px;
  background: var(--accent);
  transition: width 0.3s;
}
.progress-bar .fill.success { background: var(--success); }
.progress-bar .fill.warning { background: var(--warning); }
.progress-bar .fill.error { background: var(--error); }

/* Badges */
.badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 600;
  white-space: nowrap;
  line-height: 1.4;
}
.badge-default { background: var(--bg-elevated); color: var(--text-secondary); }
.badge-accent { background: var(--accent-glow); color: var(--accent); }
.badge-success { background: rgba(0,184,148,0.12); color: var(--success); }
.badge-warning { background: rgba(253,203,110,0.12); color: var(--warning); }
.badge-error { background: rgba(225,112,85,0.12); color: var(--error); }
.badge-dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; flex-shrink: 0; }
.badge-dot.active { background: var(--success); }
.badge-dot.idle { background: var(--warning); }
.badge-dot.error { background: var(--error); }
.badge-dot.pending { background: var(--text-muted); }

/* Agent/Task cards list */
.item-list { display: flex; flex-direction: column; gap: 8px; }
.item-card {
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 12px 16px;
  cursor: pointer;
  transition: all 0.12s;
}
.item-card:hover { border-color: var(--accent); background: var(--bg-hover); }
.item-card .top {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 4px;
}
.item-card .id { font-family: var(--mono); font-size: 12px; color: var(--text-muted); }
.item-card .title { font-weight: 600; font-size: 14px; flex: 1; }
.item-card .meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  font-size: 12px;
  color: var(--text-secondary);
  margin-top: 4px;
}
.item-card .meta span { display: flex; align-items: center; gap: 4px; }
.item-card .detail {
  display: none;
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px solid var(--border);
  font-size: 13px;
  color: var(--text-secondary);
}
.item-card .detail.open { display: block; }
.item-card .detail pre {
  background: var(--bg-elevated);
  border-radius: var(--radius-xs);
  padding: 8px 10px;
  font-family: var(--mono);
  font-size: 12px;
  overflow-x: auto;
  margin-top: 6px;
  color: var(--text-secondary);
  white-space: pre-wrap;
  word-break: break-word;
}

/* Memory entries */
.memory-list { display: flex; flex-direction: column; gap: 6px; }
.memory-entry {
  display: flex;
  gap: 10px;
  padding: 8px 0;
  border-bottom: 1px solid var(--border);
  font-size: 13px;
  line-height: 1.5;
}
.memory-entry:last-child { border-bottom: none; }
.memory-rail { width: 3px; border-radius: 2px; flex-shrink: 0; background: var(--accent); opacity: 0.4; }

/* Chat */
.chat-layout { display: flex; gap: 16px; height: calc(100vh - var(--topbar-h) - 80px); }
.chat-rooms {
  width: 200px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.chat-room {
  padding: 8px 12px;
  border-radius: var(--radius-xs);
  cursor: pointer;
  font-size: 13px;
  color: var(--text-secondary);
  display: flex;
  align-items: center;
  justify-content: space-between;
  transition: background 0.12s;
}
.chat-room:hover { background: var(--bg-hover); color: var(--text-primary); }
.chat-room.active { background: var(--accent-glow); color: var(--accent); font-weight: 600; }
.chat-room .count { font-size: 11px; color: var(--text-muted); background: var(--bg-elevated); padding: 1px 6px; border-radius: 8px; }
.chat-main { flex: 1; display: flex; flex-direction: column; min-width: 0; }
.chat-messages {
  flex: 1;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 12px;
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  margin-bottom: 10px;
}
.msg {
  padding: 6px 10px;
  border-radius: var(--radius-xs);
  font-size: 13px;
  line-height: 1.5;
}
.msg .from { font-weight: 600; font-size: 12px; margin-bottom: 2px; }
.msg .from.agent-os { color: var(--error); }
.msg .from.mini, .msg .from.mini-participant { color: var(--accent-secondary); }
.msg .from.notebookLM { color: var(--warning); }
.msg .ts { font-size: 10px; color: var(--text-muted); float: right; margin-left: 8px; }
.msg.system { background: var(--bg-elevated); border-left: 3px solid var(--text-muted); }
.msg.system .from { color: var(--text-muted); }
.chat-input-row {
  display: flex;
  gap: 8px;
}
.chat-input-row input {
  flex: 1;
  background: var(--bg-elevated);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text-primary);
  padding: 8px 12px;
  font-size: 13px;
  outline: none;
  font-family: var(--font);
}
.chat-input-row input:focus { border-color: var(--accent); }
.chat-input-row button {
  background: var(--accent);
  color: #fff;
  border: none;
  border-radius: var(--radius-sm);
  padding: 8px 18px;
  font-weight: 600;
  font-size: 13px;
  cursor: pointer;
  transition: opacity 0.15s;
  font-family: var(--font);
}
.chat-input-row button:hover { opacity: 0.85; }

/* Settings */
.settings-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 12px; }
.setting-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 12px;
  background: var(--bg-elevated);
  border-radius: var(--radius-xs);
  gap: 12px;
}
.setting-row .label { font-size: 12px; color: var(--text-secondary); flex-shrink: 0; }
.setting-row input {
  background: var(--bg-primary);
  border: 1px solid var(--border);
  border-radius: 4px;
  color: var(--text-primary);
  padding: 4px 8px;
  font-size: 12px;
  font-family: var(--mono);
  width: 120px;
  text-align: right;
  outline: none;
}
.setting-row input:focus { border-color: var(--accent); }

/* Safety */
.safety-list { display: flex; flex-direction: column; gap: 8px; }
.snapshot-card {
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 12px 16px;
}
.snapshot-card .top { display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px; }
.snapshot-card .hash { font-family: var(--mono); font-size: 12px; color: var(--accent); }
.snapshot-card .msg { font-size: 13px; }
.snapshot-card .ts { font-size: 11px; color: var(--text-muted); }

/* Proposal cards */
.proposal-card {
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 12px 16px;
  margin-bottom: 6px;
}
.proposal-card .top { display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px; gap: 8px; }
.proposal-card .id { font-family: var(--mono); font-size: 11px; color: var(--text-muted); }
.proposal-card .topic { font-weight: 600; }
.proposal-card .src { font-size: 11px; color: var(--text-muted); }
.proposal-card .text { font-size: 12px; color: var(--text-secondary); margin-top: 4px; }

/* Buttons */
.btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 14px;
  border-radius: var(--radius-xs);
  font-size: 12px;
  font-weight: 600;
  font-family: var(--font);
  border: 1px solid var(--border);
  background: var(--bg-elevated);
  color: var(--text-primary);
  cursor: pointer;
  transition: all 0.12s;
  white-space: nowrap;
}
.btn:hover { background: var(--bg-hover); border-color: var(--accent); }
.btn-accent { background: var(--accent); color: #fff; border-color: var(--accent); }
.btn-accent:hover { opacity: 0.85; }
.btn-success { background: var(--success); color: #fff; border-color: var(--success); }
.btn-warning { background: var(--warning); color: #1a1a2e; border-color: var(--warning); }
.btn-error { background: var(--error); color: #fff; border-color: var(--error); }
.btn-sm { padding: 4px 10px; font-size: 11px; }

/* Filter buttons group */
.filter-group { display: flex; gap: 4px; }
.filter-btn {
  padding: 4px 12px;
  border-radius: var(--radius-xs);
  font-size: 11px;
  font-weight: 600;
  border: 1px solid var(--border);
  background: var(--bg-elevated);
  color: var(--text-secondary);
  cursor: pointer;
  transition: all 0.12s;
  font-family: var(--font);
}
.filter-btn:hover { background: var(--bg-hover); color: var(--text-primary); }
.filter-btn.active { background: var(--accent-glow); color: var(--accent); border-color: var(--accent); }

/* Project selector */
.project-select {
  background: var(--bg-elevated);
  border: 1px solid var(--border);
  border-radius: var(--radius-xs);
  color: var(--text-primary);
  padding: 6px 10px;
  font-size: 13px;
  outline: none;
  cursor: pointer;
  font-family: var(--font);
}
.project-select:focus { border-color: var(--accent); }

/* Toast */
.toast-container {
  position: fixed;
  bottom: 20px;
  right: 20px;
  z-index: 1000;
  display: flex;
  flex-direction: column;
  gap: 8px;
  pointer-events: none;
}
.toast {
  background: var(--bg-elevated);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 10px 16px;
  font-size: 13px;
  color: var(--text-primary);
  pointer-events: auto;
  animation: slide-in 0.2s ease-out;
  box-shadow: var(--shadow);
  max-width: 360px;
}
.toast.error { border-left: 3px solid var(--error); }
.toast.success { border-left: 3px solid var(--success); }
@keyframes slide-in { from { transform: translateX(100%); opacity: 0; } to { transform: translateX(0); opacity: 1; } }

/* Help modal */
.modal-overlay {
  display: none;
  position: fixed;
  inset: 0;
  background: rgba(0,0,0,0.6);
  z-index: 500;
  align-items: center;
  justify-content: center;
}
.modal-overlay.open { display: flex; }
.modal {
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 24px;
  max-width: 480px;
  width: 90%;
  box-shadow: var(--shadow);
}
.modal h2 { font-size: 18px; margin-bottom: 16px; }
.modal kbd {
  display: inline-block;
  background: var(--bg-elevated);
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 2px 7px;
  font-family: var(--mono);
  font-size: 12px;
  color: var(--text-primary);
  margin: 0 2px;
}
.modal .shortcut { display: flex; justify-content: space-between; padding: 6px 0; font-size: 13px; color: var(--text-secondary); }
.modal .shortcut .key { color: var(--text-primary); }
.modal-close {
  float: right;
  background: none;
  border: none;
  color: var(--text-muted);
  cursor: pointer;
  font-size: 20px;
  padding: 0 4px;
}
.modal-close:hover { color: var(--text-primary); }

/* Search results overlay */
.search-overlay {
  display: none;
  position: fixed;
  top: var(--topbar-h);
  right: 20px;
  width: 480px;
  max-height: 400px;
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  z-index: 100;
  overflow-y: auto;
  padding: 12px;
}
.search-overlay.open { display: block; }
.search-result {
  padding: 8px 10px;
  border-radius: var(--radius-xs);
  cursor: pointer;
  font-size: 13px;
}
.search-result:hover { background: var(--bg-hover); }
.search-result .title { font-weight: 600; margin-bottom: 2px; }
.search-result .summary { font-size: 12px; color: var(--text-secondary); display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.search-result .tags { display: flex; gap: 6px; margin-top: 4px; flex-wrap: wrap; }
.search-result .tags span { font-size: 10px; padding: 1px 6px; border-radius: 3px; background: var(--bg-elevated); color: var(--text-muted); }

/* Empty state */
.empty-state {
  padding: 32px;
  text-align: center;
  color: var(--text-muted);
  font-size: 14px;
}
.empty-state .icon { font-size: 28px; margin-bottom: 8px; display: block; }

/* Utility */
.flex { display: flex; }
.gap-2 { gap: 8px; }
.gap-4 { gap: 16px; }
.items-center { align-items: center; }
.mt-2 { margin-top: 8px; }
.mt-4 { margin-top: 16px; }
.mb-2 { margin-bottom: 8px; }
.mb-4 { margin-bottom: 16px; }
.text-muted { color: var(--text-muted); }
.text-secondary { color: var(--text-secondary); }
.text-sm { font-size: 12px; }
.w-full { width: 100%; }
.color-accent { color: var(--accent); }
.color-success { color: var(--success); }
.color-warning { color: var(--warning); }
.color-error { color: var(--error); }

/* Scrollbar */
::-webkit-scrollbar { width: 6px; height: 6px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }
::-webkit-scrollbar-thumb:hover { background: var(--border-light); }

/* Responsive */
@media (max-width: 768px) {
  .sidebar { display: none; }
  .sidebar.open { display: flex; position: fixed; inset: var(--topbar-h) auto 0 0; z-index: 50; width: 240px; }
  .hamburger { display: flex !important; }
  .main { padding: 16px; }
  .stats-grid { grid-template-columns: 1fr 1fr; }
  .chat-layout { flex-direction: column; }
  .chat-rooms { width: 100%; flex-direction: row; overflow-x: auto; }
  .settings-grid { grid-template-columns: 1fr; }
  .search-overlay { width: calc(100% - 40px); right: 20px; }
}
.hamburger { display: none; background: none; border: none; color: var(--text-primary); font-size: 20px; cursor: pointer; padding: 4px; }

/* Markdown in chat */
.msg .body { white-space: pre-wrap; word-break: break-word; font-family: var(--font); line-height: 1.6; }
.msg .body strong { font-weight: 700; color: var(--text-primary); }
.msg .body em { font-style: italic; }
.msg .body code {
  background: var(--bg-elevated);
  padding: 1px 5px;
  border-radius: 3px;
  font-family: var(--mono);
  font-size: 12px;
  color: var(--accent);
}
.msg .body pre {
  background: var(--bg-elevated);
  border: 1px solid var(--border);
  border-radius: var(--radius-xs);
  padding: 8px 12px;
  margin: 6px 0;
  overflow-x: auto;
}
.msg .body pre code {
  background: none;
  padding: 0;
  border-radius: 0;
  color: var(--text-primary);
  font-size: 12px;
}
.msg .body a { color: var(--accent); text-decoration: underline; }
.msg .body a:hover { opacity: 0.8; }

/* Agent activity spinner */
.activity-indicator {
  display: inline-flex; align-items: center; gap: 4px;
  font-size: 11px; color: var(--text-muted); margin-left: 6px;
}
.activity-indicator .dot {
  width: 6px; height: 6px; border-radius: 50%;
  background: var(--accent);
  animation: pulse-dot 1.2s ease-in-out infinite;
}
.activity-indicator .dot.thinking { background: var(--warning); }
.activity-indicator .dot.writing { background: var(--accent); }
.activity-indicator .dot.reading { background: var(--info, #5dade2); }
.activity-indicator .dot.researching { background: var(--warning); }
.activity-indicator .dot.idle { background: var(--text-muted); animation: none; }
.activity-indicator .dot.error { background: var(--error); animation: none; }
@keyframes pulse-dot {
  0%, 100% { opacity: 0.4; transform: scale(0.8); }
  50% { opacity: 1; transform: scale(1.2); }
}
</style>
</head>
<body>

<!-- Top Bar -->
<div class="topbar">
  <button class="hamburger" onclick="toggleSidebar()">☰</button>
  <div class="topbar-logo">
    <span class="dot" id="connDot"></span>
    Memoria
    <span class="topbar-version" id="versionLabel">v2.0.0</span>
  </div>
  <div class="topbar-spacer"></div>
  <div class="topbar-search">
    <span class="icon">🔍</span>
    <input type="text" id="searchInput" placeholder="Search recall..." onkeydown="if(event.key==='Enter')doSearch()" onblur="setTimeout(()=>document.getElementById('searchOverlay').classList.remove('open'),200)">
  </div>
  <div class="topbar-actions">
    <select class="theme-btn" id="themeSelect" onchange="setTheme(this.value)">
      <option value="default">🌙 Memory</option>
      <option value="catppuccin">🌺 Catppuccin</option>
      <option value="nord">❄️ Nord</option>
      <option value="cyberpunk">💿 Cyberpunk</option>
      <option value="gruvbox">🪵 Gruvbox</option>
      <option value="light">☀️ Light</option>
    </select>
    <button class="help-btn" onclick="toggleHelp()">?</button>
  </div>
</div>

<div class="layout">
  <!-- Sidebar -->
  <div class="sidebar" id="sidebar">
    <div class="sidebar-label">Dashboard</div>
    <div class="nav-item active" data-tab="0" onclick="switchTab(0)"><span class="icon">◉</span> Overview</div>
    <div class="nav-item" data-tab="1" onclick="switchTab(1)"><span class="icon">●</span> Agents <span class="badge" id="agentBadge">0</span></div>
    <div class="nav-item" data-tab="2" onclick="switchTab(2)"><span class="icon">☰</span> Tasks <span class="badge" id="taskBadge">0</span></div>
    <div class="nav-item" data-tab="3" onclick="switchTab(3)"><span class="icon">◈</span> Memory <span class="badge" id="memoryBadge">0</span></div>
    <div class="nav-item" data-tab="4" onclick="switchTab(4)"><span class="icon">◎</span> Recall</div>
    <div class="nav-item" data-tab="5" onclick="switchTab(5)"><span class="icon">💬</span> Chat <span class="badge" id="chatBadge">0</span></div>
    <div class="nav-item" data-tab="6" onclick="switchTab(6)"><span class="icon">🛡</span> Safety</div>
    <div class="nav-item" data-tab="7" onclick="switchTab(7)"><span class="icon">⚙</span> Settings</div>
  </div>

  <!-- Main Content -->
  <div class="main" id="mainContent">
    <!-- Tab 0: Overview -->
    <div class="tab-content active" id="tab0">
      <div class="tab-header"><div class="tab-title">Overview</div><div class="tab-actions"><span class="text-sm text-muted" id="overviewTs"></span></div></div>
      <div class="stats-grid" id="overviewStats"></div>
      <div class="card mb-4"><div class="card-title">Agents</div><div id="overviewAgents"></div></div>
      <div class="card mb-4"><div class="card-title">Recent Tasks</div><div id="overviewTasks"></div></div>
      <div class="card"><div class="card-title">Chitchat Rooms</div><div id="overviewRooms"></div></div>
    </div>

    <!-- Tab 1: Agents -->
    <div class="tab-content" id="tab1">
      <div class="tab-header">
        <div class="tab-title">Agents</div>
        <div class="tab-actions">
          <div class="filter-group" id="agentFilters">
            <button class="filter-btn active" data-filter="all" onclick="setAgentFilter('all')">All</button>
            <button class="filter-btn" data-filter="active" onclick="setAgentFilter('active')">Active</button>
            <button class="filter-btn" data-filter="idle" onclick="setAgentFilter('idle')">Idle</button>
            <button class="filter-btn" data-filter="error" onclick="setAgentFilter('error')">Error</button>
          </div>
        </div>
      </div>
      <div class="item-list" id="agentList"></div>
    </div>

    <!-- Tab 2: Tasks -->
    <div class="tab-content" id="tab2">
      <div class="tab-header">
        <div class="tab-title">Tasks</div>
        <div class="tab-actions">
          <div class="filter-group" id="taskFilters">
            <button class="filter-btn active" data-filter="all" onclick="setTaskFilter('all')">All</button>
            <button class="filter-btn" data-filter="pending" onclick="setTaskFilter('pending')">Pending</button>
            <button class="filter-btn" data-filter="running" onclick="setTaskFilter('running')">Running</button>
            <button class="filter-btn" data-filter="completed" onclick="setTaskFilter('completed')">Done</button>
            <button class="filter-btn" data-filter="failed" onclick="setTaskFilter('failed')">Failed</button>
          </div>
        </div>
      </div>
      <div class="item-list" id="taskList"></div>
    </div>

    <!-- Tab 3: Memory -->
    <div class="tab-content" id="tab3">
      <div class="tab-header">
        <div class="tab-title">Memory</div>
        <div class="tab-actions flex gap-2 items-center">
          <select class="project-select" id="memoryProject" onchange="loadMemory()"></select>
          <button class="btn btn-sm" onclick="document.getElementById('memoryInput').style.display=document.getElementById('memoryInput').style.display==='none'?'flex':'none'">+ Add</button>
        </div>
      </div>
      <div id="memoryInput" style="display:none;gap:8px;margin-bottom:16px;" class="flex">
        <input type="text" id="memoryText" placeholder="Enter a fact to remember..." style="flex:1;background:var(--bg-elevated);border:1px solid var(--border);border-radius:var(--radius-xs);color:var(--text-primary);padding:8px 12px;font-size:13px;outline:none;font-family:var(--font);">
        <button class="btn btn-accent btn-sm" onclick="addMemory()">Save</button>
      </div>
      <div class="memory-list" id="memoryList"></div>
    </div>

    <!-- Tab 4: Recall -->
    <div class="tab-content" id="tab4">
      <div class="tab-header">
        <div class="tab-title">Recall</div>
        <div class="tab-actions flex gap-2 items-center">
          <input type="text" id="recallInput" placeholder="Search memory..." style="background:var(--bg-elevated);border:1px solid var(--border);border-radius:var(--radius-xs);color:var(--text-primary);padding:6px 12px;font-size:13px;outline:none;width:250px;font-family:var(--font);" onkeydown="if(event.key==='Enter')doRecall()">
          <button class="btn btn-accent btn-sm" onclick="doRecall()">Search</button>
        </div>
      </div>
      <div id="recallResults"></div>
    </div>

    <!-- Tab 5: Chat -->
    <div class="tab-content" id="tab5">
      <div class="tab-header"><div class="tab-title">Chitchat</div></div>
      <div class="chat-layout">
        <div class="chat-rooms" id="chatRoomList"></div>
        <div class="chat-main">
          <div class="chat-messages" id="chatMessages"></div>
          <div class="chat-input-row">
            <input type="text" id="chatInput" placeholder="Type a message..." onkeydown="if(event.key==='Enter')sendChat()">
            <button onclick="sendChat()">Send</button>
          </div>
        </div>
      </div>
    </div>

    <!-- Tab 6: Safety -->
    <div class="tab-content" id="tab6">
      <div class="tab-header">
        <div class="tab-title">Safety</div>
        <div class="tab-actions flex gap-2 items-center">
          <select class="project-select" id="safetyProject" onchange="loadSafety()"></select>
          <button class="btn btn-accent btn-sm" onclick="createSnapshot()">📸 Snapshot</button>
        </div>
      </div>
      <div id="safetyContent"></div>
    </div>

    <!-- Tab 7: Settings -->
    <div class="tab-content" id="tab7">
      <div class="tab-header">
        <div class="tab-title">Settings</div>
        <div class="tab-actions">
          <button class="btn btn-accent btn-sm" onclick="saveConfig()">💾 Save</button>
        </div>
      </div>
      <div class="settings-grid" id="configGrid"></div>
      <div class="card mt-4">
        <div class="card-title">Actions</div>
        <div class="flex gap-2 mt-2">
          <button class="btn btn-warning btn-sm" onclick="consolidateChat()">🔄 Consolidate</button>
          <button class="btn btn-error btn-sm" onclick="clearProposals()">🗑 Clear Proposals</button>
        </div>
      </div>
      <div class="card mt-4">
        <div class="card-title">Proposals</div>
        <div id="proposalsList"></div>
      </div>
    </div>
  </div>
</div>

<!-- Search Overlay -->
<div class="search-overlay" id="searchOverlay"></div>

<!-- Help Modal -->
<div class="modal-overlay" id="helpModal">
  <div class="modal">
    <button class="modal-close" onclick="toggleHelp()">×</button>
    <h2>Keyboard Shortcuts</h2>
    <div class="shortcut"><span>Switch tab</span><span class="key"><kbd>1</kbd>–<kbd>8</kbd></span></div>
    <div class="shortcut"><span>Search recall</span><span class="key"><kbd>/</kbd></span></div>
    <div class="shortcut"><span>Close modal / blur</span><span class="key"><kbd>Esc</kbd></span></div>
    <div class="shortcut"><span>Toggle help</span><span class="key"><kbd>?</kbd></span></div>
    <div class="shortcut"><span>Select next nav</span><span class="key"><kbd>↓</kbd> <kbd>↑</kbd></span></div>
    <div class="shortcut"><span>Expand detail</span><span class="key"><kbd>Enter</kbd> on card</span></div>
  </div>
</div>

<!-- Toast Container -->
<div class="toast-container" id="toastContainer"></div>

<script>
// -- State --
const BASE = '';
const state = {
  theme: localStorage.getItem('memoria-theme') || 'default',
  tab: 0,
  chatRooms: [],
  chatRoom: 'general',
  projects: [],
  agentFilter: 'all',
  taskFilter: 'all',
  config: {},
  proposals: []
};

// -- Theme --
function setTheme(name) {
  state.theme = name;
  document.documentElement.setAttribute('data-theme', name === 'default' ? '' : name);
  localStorage.setItem('memoria-theme', name);
  document.getElementById('themeSelect').value = name;
}
(function() {
  const t = localStorage.getItem('memoria-theme') || 'default';
  if (t !== 'default') document.documentElement.setAttribute('data-theme', t);
  document.getElementById('themeSelect').value = t;
})();

// -- Sidebar --
function toggleSidebar() {
  document.getElementById('sidebar').classList.toggle('open');
}

// -- Tab Switching --
function switchTab(n) {
  state.tab = n;
  document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
  document.getElementById('tab' + n).classList.add('active');
  document.querySelectorAll('.nav-item').forEach(el => el.classList.remove('active'));
  document.querySelector('.nav-item[data-tab="' + n + '"]')?.classList.add('active');
  document.getElementById('sidebar').classList.remove('open');
  const loaders = [loadOverview, loadAgents, loadTasks, loadMemory, null, loadChat, loadSafety, loadSettings];
  if (loaders[n]) loaders[n]();
}

// -- Help Modal --
function toggleHelp() {
  document.getElementById('helpModal').classList.toggle('open');
}

// -- Toast --
function toast(msg, type) {
  const el = document.createElement('div');
  el.className = 'toast' + (type ? ' ' + type : '');
  el.textContent = msg;
  document.getElementById('toastContainer').appendChild(el);
  setTimeout(() => { el.style.opacity = '0'; el.style.transition = 'opacity 0.3s'; setTimeout(() => el.remove(), 300); }, 3000);
}

// -- HTML Escape --
function esc(s) {
  const d = document.createElement('div');
  d.textContent = s == null ? '' : String(s);
  return d.innerHTML;
}

// -- Time Helpers --
function fmtTime(ts) {
  if (!ts) return '';
  const d = typeof ts === 'number' ? new Date(ts * 1000) : new Date(ts);
  return d.toLocaleString();
}
function ago(ts) {
  if (!ts) return '';
  const sec = Math.floor((Date.now() - (typeof ts === 'number' ? ts * 1000 : new Date(ts).getTime())) / 1000);
  if (sec < 5) return 'just now';
  if (sec < 60) return sec + 's ago';
  if (sec < 3600) return Math.floor(sec / 60) + 'm ago';
  if (sec < 86400) return Math.floor(sec / 3600) + 'h ago';
  return Math.floor(sec / 86400) + 'd ago';
}

// -- Fetch Wrapper --
async function api(path) {
  const r = await fetch(BASE + path);
  if (!r.ok) throw new Error(r.status + ' ' + r.statusText);
  return r.json();
}

// -- Progress Bar --
function renderBar(pct, cls) {
  const c = Math.min(100, Math.max(0, pct));
  return '<div class="progress-bar"><div class="fill' + (cls ? ' ' + cls : '') + '" style="width:' + c + '%"></div></div>';
}

// -- Status Dot --
function dot(status) {
  return '<span class="badge-dot ' + (status || 'pending') + '"></span>';
}

// -- Badge --
function tag(text, cls) {
  return '<span class="badge badge-' + (cls || 'default') + '">' + esc(text) + '</span>';
}

// -- Keyboard Shortcuts --
document.addEventListener('keydown', function(e) {
  if (e.key >= '1' && e.key <= '8' && !e.ctrlKey && !e.metaKey) {
    switchTab(parseInt(e.key) - 1);
    return;
  }
  if (e.key === '/' && !['INPUT', 'TEXTAREA', 'SELECT'].includes(e.target.tagName)) {
    e.preventDefault();
    document.getElementById('searchInput').focus();
    return;
  }
  if (e.key === '?' && !['INPUT', 'TEXTAREA'].includes(e.target.tagName)) {
    toggleHelp();
    return;
  }
  if (e.key === 'Escape') {
    document.getElementById('helpModal').classList.remove('open');
    document.getElementById('searchOverlay').classList.remove('open');
    document.activeElement?.blur();
  }
});

// -- Search --
async function doSearch() {
  const q = document.getElementById('searchInput').value.trim();
  if (!q) return;
  try {
    const data = await api('/recall?q=' + encodeURIComponent(q));
    const el = document.getElementById('searchOverlay');
    const results = data.results || [];
    if (!results.length) {
      el.innerHTML = '<div class="empty-state">No results for "' + esc(q) + '"</div>';
    } else {
      el.innerHTML = results.map(r => '<div class="search-result">' +
        '<div class="title">' + esc(r.title || r.id || 'Result') + '</div>' +
        '<div class="summary">' + esc(r.summary || r.text || '').slice(0, 200) + '</div>' +
        '<div class="tags">' +
          (r.project ? '<span>' + esc(r.project) + '</span>' : '') +
          (r.room ? '<span>' + esc(r.room) + '</span>' : '') +
          (r.source ? '<span>' + esc(r.source).slice(0, 20) + '</span>' : '') +
        '</div></div>').join('');
      el.onclick = function(e) {
        if (e.target.closest('.search-result')) {
          switchTab(0);
          document.getElementById('searchOverlay').classList.remove('open');
        }
      };
    }
    el.classList.add('open');
  } catch(e) {
    toast('Search failed: ' + e.message, 'error');
  }
}

// -- Connection Indicator --
let connOk = true;
function setConn(ok) {
  const dot = el('connDot');
  if (ok) {
    dot.className = 'dot';
    connOk = true;
  } else {
    dot.className = 'dot error pulse';
    connOk = false;
  }
}

// ============================================
// TAB 0: OVERVIEW
// ============================================
async function loadOverview() {
  try {
    const [health, agents, tasks, rooms] = await Promise.all([
      api('/health'),
      api('/agents'),
      api('/tasks'),
      api('/chitchat/rooms').catch(() => ({ rooms: [] }))
    ]);
    el('overviewTs').textContent = 'updated ' + ago(Date.now());

    const agentCount = agents.agents ? agents.agents.length : 0;
    const taskTotal = tasks.tasks ? tasks.tasks.length : 0;
    const taskDone = tasks.tasks ? tasks.tasks.filter(t => t.status === 'completed' || t.status === 'done').length : 0;
    const roomCount = rooms.rooms ? rooms.rooms.length : 0;
    const memCount = health.sessions_indexed || 0;

    el('overviewStats').innerHTML =
      '<div class="stat-card"><div class="value accent">' + agentCount + '</div><div class="label">Agents</div><div class="sub">' + dot('active') + ' active</div></div>' +
      '<div class="stat-card"><div class="value">' + taskTotal + '</div><div class="label">Tasks</div><div class="sub success">' + taskDone + ' done</div>' + renderBar(taskTotal ? (taskDone / taskTotal * 100) : 0, 'success') + '</div>' +
      '<div class="stat-card"><div class="value success">' + memCount + '</div><div class="label">Sessions</div><div class="sub"><span class="success">&check;</span> DB ready</div></div>' +
      '<div class="stat-card"><div class="value warning">' + roomCount + '</div><div class="label">Rooms</div><div class="sub">' + (health.memoria_version || '') + '</div></div>';

    const agentsEl = el('overviewAgents');
    if (agentCount === 0) {
      agentsEl.innerHTML = '<div class="empty-state">No agents running</div>';
    } else {
      agentsEl.innerHTML = agents.agents.map(a => '<div class="item-card">' +
        '<div class="top">' + dot(a.status) + '<span class="id">' + esc(a.id).slice(0, 20) + '</span>' + tag(a.project, 'accent') + tag(a.status, a.status === 'active' ? 'success' : a.status === 'idle' ? 'warning' : 'error') + '</div>' +
        '<div class="meta"><span>&#128193; ' + (a.files || []).length + ' files</span><span>&#10084;&#65039; ' + ago(a.last_heartbeat) + '</span></div>' +
        '</div>').join('');
    }

    const tasksEl = el('overviewTasks');
    if (taskTotal === 0) {
      tasksEl.innerHTML = '<div class="empty-state">No tasks</div>';
    } else {
      const recent = tasks.tasks.slice(-5).reverse();
      tasksEl.innerHTML = recent.map(t => '<div class="item-card">' +
        '<div class="top">' + dot(t.status === 'completed' || t.status === 'done' ? 'active' : t.status === 'running' ? 'active' : t.status === 'failed' ? 'error' : 'pending') +
        '<span class="title">' + esc(t.title).slice(0, 60) + '</span>' + tag(t.status, t.status === 'completed' || t.status === 'done' ? 'success' : t.status === 'running' ? 'accent' : t.status === 'failed' ? 'error' : 'warning') + '</div>' +
        '<div class="meta"><span>&#128230; ' + esc(t.project) + '</span><span>' + ago(t.created_at) + '</span></div>' +
        '</div>').join('');
    }

    const roomsEl = el('overviewRooms');
    if (roomCount === 0) {
      roomsEl.innerHTML = '<div class="empty-state">No rooms</div>';
    } else {
      roomsEl.innerHTML = '<div class="flex gap-2" style="flex-wrap:wrap">' +
        rooms.rooms.map(r => '<span class="badge badge-accent"># ' + esc(r.room) + ' <span class="text-muted">(' + r.messages + ')</span></span>').join('') +
        '</div>';
    }

    setConn(true);
    el('agentBadge').textContent = agentCount;
    el('taskBadge').textContent = taskTotal;
    el('chatBadge').textContent = roomCount;
  } catch(e) {
    setConn(false);
    el('overviewStats').innerHTML = '<div class="stat-card"><div class="value error">!</div><div class="label">Connection Error</div><div class="sub">' + esc(e.message) + '</div></div>';
  }
}

// ============================================
// TAB 1: AGENTS
// ============================================
let agentsData = [];

async function loadAgents() {
  try {
    agentsData = (await api('/agents')).agents || [];
    renderAgents();
    setConn(true);
  } catch(e) {
    setConn(false);
    el('agentList').innerHTML = '<div class="empty-state error">Failed to load agents: ' + esc(e.message) + '</div>';
  }
}

function setAgentFilter(f) {
  state.agentFilter = f;
  document.querySelectorAll('#agentFilters .filter-btn').forEach(b => b.classList.toggle('active', b.dataset.filter === f));
  renderAgents();
}

function renderAgents() {
  const list = state.agentFilter === 'all' ? agentsData : agentsData.filter(a => a.status === state.agentFilter);
  const container = el('agentList');
  if (!list.length) {
    container.innerHTML = '<div class="empty-state"><span class="icon">&#9679;</span>No ' + (state.agentFilter === 'all' ? '' : state.agentFilter + ' ') + 'agents</div>';
    return;
  }
  container.innerHTML = list.map(a => '<div class="item-card" onclick="toggleDetail(this)">' +
    '<div class="top">' + dot(a.status) + '<span class="id">' + esc(a.id) + '</span>' + tag(a.project, 'accent') + tag(a.status, a.status === 'active' ? 'success' : a.status === 'idle' ? 'warning' : 'error') + '</div>' +
    '<div class="title">' + esc(a.task || (a.chitchat_name || '')) +
      (a.activity ? '<span class="activity-indicator"><span class="dot ' + esc(a.activity) + '"></span>' + esc(a.activity) + '</span>' : '') +
    '</div>' +
    '<div class="meta">' +
      '<span>&#128193; ' + (a.files || []).length + ' files</span>' +
      '<span>&#128190; ' + (a.commit_log || []).length + ' commits</span>' +
      '<span>&#10084;&#65039; ' + ago(a.last_heartbeat) + '</span>' +
      '<span>&#9654; ' + ago(a.started_at) + '</span>' +
    '</div>' +
    '<div class="detail">' +
      '<div><strong>PID:</strong> ' + (a.pid || '-') + '</div>' +
      '<div><strong>Chitchat:</strong> ' + esc(a.chitchat_name || '-') + '</div>' +
      '<div><strong>Activity:</strong> ' + esc(a.activity || '-') + '</div>' +
      '<div><strong>Started:</strong> ' + fmtTime(a.started_at) + '</div>' +
      '<div><strong>Heartbeat:</strong> ' + fmtTime(a.last_heartbeat) + '</div>' +
      (a.conflicts_warned && a.conflicts_warned.length ? '<div class="mt-2"><strong>&#9888; Conflicts:</strong> ' + esc(a.conflicts_warned.join('; ')) + '</div>' : '') +
      (a.commit_log && a.commit_log.length ? '<div class="mt-2"><strong>Recent Commits:</strong></div><pre>' + esc(a.commit_log.slice(-5).join(String.fromCharCode(10))) + '</pre>' : '') +
    '</div>' +
    '</div>').join('');
}

// ============================================
// TAB 2: TASKS
// ============================================
let tasksData = [];

async function loadTasks() {
  try {
    tasksData = (await api('/tasks')).tasks || [];
    renderTasks();
    setConn(true);
  } catch(e) {
    setConn(false);
    el('taskList').innerHTML = '<div class="empty-state error">Failed to load tasks: ' + esc(e.message) + '</div>';
  }
}

function setTaskFilter(f) {
  state.taskFilter = f;
  document.querySelectorAll('#taskFilters .filter-btn').forEach(b => b.classList.toggle('active', b.dataset.filter === f));
  renderTasks();
}

function renderTasks() {
  let list = tasksData;
  if (state.taskFilter !== 'all') {
    list = list.filter(t => t.status === state.taskFilter || (state.taskFilter === 'completed' && (t.status === 'done' || t.status === 'completed')));
  }
  const container = el('taskList');
  if (!list.length) {
    container.innerHTML = '<div class="empty-state"><span class="icon">&#9776;</span>No tasks</div>';
    return;
  }
  const order = { running: 0, pending: 1, active: 0, completed: 2, done: 2, failed: 3 };
  list.sort((a, b) => (order[a.status] || 9) - (order[b.status] || 9));

  container.innerHTML = list.map(t => {
    const s = t.status === 'completed' || t.status === 'done' ? 'active' : t.status === 'failed' ? 'error' : t.status === 'running' ? 'active' : 'pending';
    return '<div class="item-card" onclick="toggleDetail(this)">' +
      '<div class="top">' + dot(s) + '<span class="title">' + esc(t.title) + '</span>' + tag(t.status, t.status === 'completed' || t.status === 'done' ? 'success' : t.status === 'running' ? 'accent' : t.status === 'failed' ? 'error' : 'warning') + '</div>' +
      '<div class="meta">' +
        '<span>&#128230; ' + esc(t.project) + '</span>' +
        (t.assigned_to ? '<span>&#128100; ' + esc(t.assigned_to).slice(0, 20) + '</span>' : '') +
        '<span>&#128197; ' + ago(t.created_at) + '</span>' +
      '</div>' +
      '<div class="detail">' +
        '<div><strong>ID:</strong> ' + esc(t.id) + '</div>' +
        (t.description ? '<div class="mt-2"><strong>Description:</strong><pre>' + esc(t.description) + '</pre></div>' : '') +
        (t.result ? '<div class="mt-2"><strong>Result:</strong><pre>' + esc(t.result).slice(0, 500) + '</pre></div>' : '') +
        (t.error ? '<div class="mt-2"><strong style="color:var(--error)">Error:</strong><pre style="color:var(--error)">' + esc(t.error).slice(0, 500) + '</pre></div>' : '') +
        (t.rollback_commit ? '<div class="mt-2"><strong>Rollback:</strong> ' + esc(t.rollback_commit).slice(0, 16) + '</div>' : '') +
        '<div class="mt-2"><strong>Created:</strong> ' + fmtTime(t.created_at) + '</div>' +
        (t.assigned_at ? '<div><strong>Assigned:</strong> ' + fmtTime(t.assigned_at) + '</div>' : '') +
      '</div>' +
      '</div>';
  }).join('');
}

// -- Toggle Detail --
function toggleDetail(el) {
  const d = el.querySelector('.detail');
  if (d) d.classList.toggle('open');
}

// ============================================
// TAB 3: MEMORY
// ============================================
async function loadMemory() {
  try {
    const projects = await getProjects();
    const sel = el('memoryProject');
    const current = sel.value || projects[0] || 'memoria';
    sel.innerHTML = projects.map(p => '<option value="' + esc(p) + '"' + (p === current ? ' selected' : '') + '>' + esc(p) + '</option>').join('');

    const data = await api('/memory/' + encodeURIComponent(current));
    const entries = data.entries || [];
    el('memoryBadge').textContent = entries.length;
    const container = el('memoryList');
    if (!entries.length) {
      container.innerHTML = '<div class="empty-state"><span class="icon">&#9672;</span>No memory entries for <strong>' + esc(current) + '</strong></div>';
    } else {
      container.innerHTML = entries.map(e => '<div class="memory-entry"><div class="memory-rail"></div><div>' + esc(e.content || e) + '</div></div>').join('');
    }
    setConn(true);
  } catch(e) {
    setConn(false);
    el('memoryList').innerHTML = '<div class="empty-state error">' + esc(e.message) + '</div>';
  }
}

async function addMemory() {
  const project = el('memoryProject').value;
  const text = el('memoryText').value.trim();
  if (!text) return;
  try {
    await fetch(BASE + '/memory/' + encodeURIComponent(project), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: text })
    });
    el('memoryText').value = '';
    el('memoryInput').style.display = 'none';
    toast('Memory saved', 'success');
    loadMemory();
  } catch(e) {
    toast('Failed: ' + e.message, 'error');
  }
}

async function getProjects() {
  try {
    const agents = (await api('/agents')).agents || [];
    const projects = new Set();
    agents.forEach(a => { if (a.project) projects.add(a.project); });
    const health = await api('/health');
    if (health.topics) health.topics.forEach(t => projects.add(t));
    projects.add('memoria');
    return Array.from(projects).sort();
  } catch(e) {
    return ['memoria'];
  }
}

// ============================================
// TAB 4: RECALL
// ============================================
async function doRecall() {
  const q = el('recallInput').value.trim();
  if (!q) return;
  try {
    const data = await api('/recall?q=' + encodeURIComponent(q));
    const results = data.results || [];
    const container = el('recallResults');
    if (!results.length) {
      container.innerHTML = '<div class="empty-state">No results for "' + esc(q) + '"</div>';
    } else {
      container.innerHTML = '<div class="text-sm text-muted mb-2">' + results.length + ' result' + (results.length > 1 ? 's' : '') + ' for "' + esc(q) + '"</div>' +
        '<div class="item-list">' +
        results.map(r => '<div class="item-card">' +
          '<div class="top">' + tag(r.source || r.project || 'memoria', 'accent') + (r.room ? tag('# ' + r.room, 'default') : '') + (r.from ? tag(r.from, 'success') : '') + '</div>' +
          '<div class="title">' + esc(r.title || r.id || 'Entry') + '</div>' +
          (r.summary ? '<div class="text-sm text-secondary mt-2">' + esc(r.summary).slice(0, 300) + '</div>' : '') +
          (r.text ? '<div class="text-sm text-secondary mt-2">' + esc(r.text).slice(0, 300) + '</div>' : '') +
          '<div class="meta"><span>&#128368; ' + ago(r.ts) + '</span></div>' +
          '</div>').join('') +
        '</div>';
    }
    setConn(true);
  } catch(e) {
    setConn(false);
    el('recallResults').innerHTML = '<div class="empty-state error">' + esc(e.message) + '</div>';
  }
}

// ============================================
// TAB 5: CHAT
// ============================================

async function loadChat() {
  try {
    const data = await api('/chitchat/rooms');
    state.chatRooms = (data.rooms || []).map(r => r.room);
    const sel = el('chatRoomList');
    if (!state.chatRooms.length) {
      sel.innerHTML = '<div class="text-muted text-sm" style="padding:8px">No rooms</div>';
      return;
    }
    if (!state.chatRooms.includes(state.chatRoom)) state.chatRoom = state.chatRooms[0];
    sel.innerHTML = state.chatRooms.map(r =>
      '<div class="chat-room' + (r === state.chatRoom ? ' active' : '') + '" data-room="' + esc(r) + '">' +
        esc(r) + ' <span class="count">' + (data.rooms.find(rr => rr.room === r)?.messages || 0) + '</span>' +
      '</div>'
    ).join('');
    sel.onclick = function(e) {
      const room = e.target.closest('.chat-room');
      if (room) switchChatRoom(room.dataset.room);
    };
    setConn(true);
    loadChatMessages();
  } catch(e) {
    setConn(false);
    el('chatRoomList').innerHTML = '<div class="text-muted text-sm" style="padding:8px">Error loading rooms</div>';
  }
}

function switchChatRoom(room) {
  state.chatRoom = room;
  loadChat();
}

function renderMarkdown(text) {
  if (!text) return '';
  let s = text.replace(/<script\\b[^<]*(?:(?!<\\/script>)<[^<]*)*<\\/script>/gi, '');
  s = s.replace(/```([\s\S]*?)```/g, '<pre><code>$1</code></pre>');
  s = s.replace(/`([^`]+)`/g, '<code>$1</code>');
  s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  s = s.replace(/\*([^*]+)\*/g, '<em>$1</em>');
  s = s.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank">$1</a>');
  s = s.replace(/\\n/g, '<br>');
  return s;
}

async function loadChatMessages() {
  if (!state.chatRoom) return;
  try {
    const limit = window.__memoriaMaxMessages || 100;
    const data = await api('/chitchat/' + encodeURIComponent(state.chatRoom) + '?limit=' + limit);
    const msgs = data.messages || [];
    const container = el('chatMessages');
    container.innerHTML = msgs.slice(-100).map(m => {
      const from = m.from || 'system';
      const isSys = m.type === 'system' || from === 'agent-os' || from === 'system';
      const cls = isSys ? ' system' : '';
      const fromCls = from === 'agent-os' ? 'agent-os' : from === 'mini' || from === 'mini-participant' ? 'mini' : from === 'notebookLM' ? 'notebookLM' : '';
      const prefix = isSys ? '\u25c9' : from === 'user' || from === 'you' ? '\u25b6' : '\u25c8';
      return '<div class="msg' + cls + '">' +
        '<div class="from' + (fromCls ? ' ' + fromCls : '') + '">' + prefix + ' ' + esc(from) + ' <span class="ts">' + fmtTime(m.ts) + '</span></div>' +
        '<div class="body">' + renderMarkdown(m.text) + '</div>' +
        '</div>';
    }).join('');
    container.scrollTop = container.scrollHeight;
    setConn(true);
  } catch(e) {
    setConn(false);
  }
}

async function sendChat() {
  const text = el('chatInput').value.trim();
  if (!text || !state.chatRoom) return;
  try {
    await fetch(BASE + '/chitchat/' + encodeURIComponent(state.chatRoom) + '/say', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text, from_name: 'dashboard' })
    });
    el('chatInput').value = '';
    setTimeout(loadChatMessages, 500);
  } catch(e) {
    toast('Send failed: ' + e.message, 'error');
  }
}

// ============================================
// TAB 6: SAFETY
// ============================================
async function loadSafety() {
  try {
    const projects = await getProjects();
    const sel = el('safetyProject');
    const current = sel.value || projects[0] || 'memoria';
    sel.innerHTML = projects.map(p => '<option value="' + esc(p) + '"' + (p === current ? ' selected' : '') + '>' + esc(p) + '</option>').join('');

    const data = await api('/safety/' + encodeURIComponent(current) + '/snapshots');
    const snaps = data.snapshots || [];
    const container = el('safetyContent');
    if (!snaps.length) {
      container.innerHTML = '<div class="empty-state"><span class="icon">&#x1f6e1;</span>No snapshots for <strong>' + esc(current) + '</strong></div>';
    } else {
      container.innerHTML = '<div class="text-sm text-muted mb-2">' + snaps.length + ' snapshot' + (snaps.length > 1 ? 's' : '') + '</div>' +
        '<div class="safety-list">' +
        snaps.map(s => '<div class="snapshot-card">' +
          '<div class="top"><span class="hash">' + esc(s.commit_hash || '').slice(0, 16) + '</span>' + tag(ago(s.created_at), 'default') + '</div>' +
          '<div class="msg">' + esc(s.message || 'Snapshot') + '</div>' +
          '<div class="ts">' + (s.agent_id ? 'by ' + esc(s.agent_id).slice(0, 20) + ' &middot; ' : '') + fmtTime(s.created_at) + '</div>' +
          '</div>').join('') +
        '</div>';
    }
    setConn(true);
  } catch(e) {
    setConn(false);
    el('safetyContent').innerHTML = '<div class="empty-state error">' + esc(e.message) + '</div>';
  }
}

async function createSnapshot() {
  const project = el('safetyProject').value;
  try {
    await fetch(BASE + '/safety/snapshot/' + encodeURIComponent(project), { method: 'POST' });
    toast('Snapshot created', 'success');
    loadSafety();
  } catch(e) {
    toast('Failed: ' + e.message, 'error');
  }
}

// ============================================
// TAB 7: SETTINGS
// ============================================
async function loadSettings() {
  try {
    state.config = await api('/config');
    renderConfig();
    setConn(true);
    loadProposals();
  } catch(e) {
    setConn(false);
    el('configGrid').innerHTML = '<div class="empty-state error">' + esc(e.message) + '</div>';
  }
}

function renderConfig() {
  const container = el('configGrid');
  const fields = [
    ['memory_limit', 'Memory Limit', 'number'],
    ['poll_interval', 'Poll Interval (s)', 'number'],
    ['agent_stale_sec', 'Agent Stale (s)', 'number'],
    ['chitchat_poll_interval', 'Chat Poll (s)', 'number'],
    ['chitchat_consolidate_threshold', 'Consolidate Threshold', 'number'],
    ['chitchat_max_messages', 'Max Messages', 'number'],
    ['sleep_cycle_hours', 'Sleep Cycle (h)', 'number'],
    ['session_max_records', 'Max Records', 'number'],
    ['auto_accept_threshold', 'Auto Accept', 'number'],
    ['port', 'Port', 'number'],
  ];
  container.innerHTML = fields.map(([key, label, type]) =>
    '<div class="setting-row">' +
      '<span class="label">' + esc(label) + '</span>' +
      '<input type="' + type + '" id="cfg_' + key + '" value="' + esc(state.config[key] ?? '') + '" data-key="' + key + '">' +
    '</div>'
  ).join('');
}

async function saveConfig() {
  const updates = {};
  document.querySelectorAll('#configGrid input[data-key]').forEach(inp => {
    const key = inp.dataset.key;
    const val = inp.value;
    if (val !== '' && !isNaN(val)) updates[key] = parseFloat(val);
    else updates[key] = val;
  });
  try {
    await fetch(BASE + '/config', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(updates)
    });
    toast('Config saved', 'success');
  } catch(e) {
    toast('Save failed: ' + e.message, 'error');
  }
}

async function loadProposals() {
  try {
    const data = await api('/proposals');
    state.proposals = data.proposals || [];
    const container = el('proposalsList');
    if (!state.proposals.length) {
      container.innerHTML = '<div class="text-muted text-sm">No pending proposals</div>';
    } else {
      container.innerHTML = state.proposals.map(p =>
        '<div class="proposal-card">' +
          '<div class="top"><span class="id">' + esc(p.id).slice(0, 30) + '</span><span class="topic">' + tag(p.topic, 'warning') + '</span><span class="src">' + esc(p.source || '') + '</span></div>' +
          '<div class="text">' + esc(p.text || '').slice(0, 200) + '</div>' +
          '<div class="flex gap-2 mt-2">' +
            '<button class="btn btn-sm btn-success proposal-accept" data-id="' + esc(p.id) + '">Accept</button>' +
            '<button class="btn btn-sm btn-error proposal-delete" data-id="' + esc(p.id) + '">Delete</button>' +
          '</div>' +
        '</div>'
      ).join('');
      container.onclick = function(e) {
        const accept = e.target.closest('.proposal-accept');
        if (accept) acceptProposal(accept.dataset.id);
        const del = e.target.closest('.proposal-delete');
        if (del) deleteProposal(del.dataset.id);
      };
    }
  } catch(e) {
    el('proposalsList').innerHTML = '<div class="text-muted text-sm">Error: ' + esc(e.message) + '</div>';
  }
}

async function acceptProposal(id) {
  try {
    await fetch(BASE + '/proposals/' + encodeURIComponent(id) + '/accept', { method: 'POST' });
    toast('Proposal accepted', 'success');
    loadProposals();
  } catch(e) { toast('Failed: ' + e.message, 'error'); }
}

async function deleteProposal(id) {
  try {
    await fetch(BASE + '/proposals/' + encodeURIComponent(id), { method: 'DELETE' });
    toast('Proposal deleted', 'success');
    loadProposals();
  } catch(e) { toast('Failed: ' + e.message, 'error'); }
}

async function consolidateChat() {
  try {
    await fetch(BASE + '/chitchat/consolidate', { method: 'POST' });
    toast('Consolidation triggered', 'success');
  } catch(e) { toast('Failed: ' + e.message, 'error'); }
}

async function clearProposals() {
  try {
    await fetch(BASE + '/proposals?confirm=true', { method: 'DELETE' });
    toast('Proposals cleared', 'success');
    loadProposals();
  } catch(e) { toast('Failed: ' + e.message, 'error'); }
}

// -- Helper: document.getElementById shorthand --
function el(id) { return document.getElementById(id); }

// ============================================
// INIT
// ============================================
loadOverview();

setInterval(() => {
  if (state.tab === 0) loadOverview();
  else if (state.tab === 1) loadAgents();
  else if (state.tab === 2) loadTasks();
  else if (state.tab === 3) loadMemory();
  else if (state.tab === 5) { loadChat(); }
  else if (state.tab === 6) loadSafety();
  else if (state.tab === 7) { loadSettings(); }
}, 5000);

setInterval(() => {
  if (state.tab === 5 && state.chatRoom) loadChatMessages();
}, 3000);
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
