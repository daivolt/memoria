"""
federation — multi-server sync for memoria.

Allows memoriad instances to exchange topics, memory, sessions, tasks,
and proposals with peer servers. Supports incremental sync via timestamps,
conflict resolution (last-writer-wins with merge), and optional full replication.

Architecture:
  - Each server maintains a peer registry in WORKDIR/federation/peers.json
  - Sync state (last_sync timestamps per peer) in WORKDIR/federation/sync_state.json
  - Records carry updated_at timestamps for incremental sync
  - Conflict resolution: newer timestamp wins; for topics, facts missing from both
    sides are merged (union merge)
  - Pull: fetch peer's changelog since last sync, apply locally
  - Push: send local changelog to peer; peer applies
  - Full: bidirectional pull+push
"""

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

# ── Peer Registry ──────────────────────────────────────────

FEDERATION_DIR: Path | None = None
WORKDIR: Path | None = None
SYNC_TIMEOUT = 30  # seconds per HTTP call


def _init_federation(workdir: Path):
    global FEDERATION_DIR, WORKDIR
    WORKDIR = workdir
    FEDERATION_DIR = workdir / "federation"
    FEDERATION_DIR.mkdir(parents=True, exist_ok=True)
    (FEDERATION_DIR / "peers.json").touch(exist_ok=True)
    (FEDERATION_DIR / "sync_state.json").touch(exist_ok=True)


def _peers_path() -> Path:
    return FEDERATION_DIR / "peers.json"


def _sync_state_path() -> Path:
    return FEDERATION_DIR / "sync_state.json"


def _load_peers() -> list[dict]:
    p = _peers_path()
    if not p.exists() or p.stat().st_size == 0:
        return []
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return []


def _save_peers(peers: list[dict]):
    _peers_path().write_text(json.dumps(peers, ensure_ascii=False, indent=2))


def _load_sync_state() -> dict:
    p = _sync_state_path()
    if not p.exists() or p.stat().st_size == 0:
        return {}
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save_sync_state(state: dict):
    _sync_state_path().write_text(json.dumps(state, ensure_ascii=False, indent=2))


# ── Peer Registration ──────────────────────────────────────

def register_peer(name: str, url: str, api_key: str = "", adapter: str = "") -> dict:
    peers = _load_peers()
    existing = next((p for p in peers if p["name"] == name), None)
    if existing:
        existing["url"] = url.rstrip("/")
        existing["api_key"] = api_key
        existing["adapter"] = adapter
        existing["updated_at"] = time.time()
        action = "updated"
    else:
        peers.append({
            "name": name,
            "url": url.rstrip("/"),
            "api_key": api_key,
            "adapter": adapter,
            "created_at": time.time(),
            "updated_at": time.time(),
        })
        action = "registered"
    _save_peers(peers)
    return {"ok": True, "name": name, "action": action}


def remove_peer(name: str) -> bool:
    peers = _load_peers()
    before = len(peers)
    peers = [p for p in peers if p["name"] != name]
    if len(peers) == before:
        return False
    _save_peers(peers)
    # clean sync state
    state = _load_sync_state()
    state.pop(name, None)
    _save_sync_state(state)
    return True


def list_peers() -> list[dict]:
    return _load_peers()


# ── HTTP helpers for peer communication ────────────────────

def _peer_req(peer: dict, method: str, path: str, data: dict | None = None) -> dict | None:
    url = f"{peer['url']}{path}"
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Content-Type", "application/json")
    if peer.get("api_key"):
        req.add_header("X-Api-Key", peer["api_key"])
    try:
        with urllib.request.urlopen(req, timeout=SYNC_TIMEOUT) as resp:
            return json.loads(resp.read())
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, json.JSONDecodeError) as e:
        return None


# ── Changelog Generation ───────────────────────────────────

def _ts() -> float:
    return time.time()


def _load_jsonl(path: Path, max_records: int = 0) -> list[dict]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    lines = path.read_text().strip().splitlines()
    if max_records and len(lines) > max_records:
        lines = lines[-max_records:]
    result = []
    for line in lines:
        try:
            result.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return result


def _append_jsonl(path: Path, record: dict):
    with open(path, "a") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _load_topics(base_dir: Path) -> dict[str, list[dict]]:
    """Load all topics with per-fact metadata."""
    topics_dir = base_dir / "topics"
    if not topics_dir.exists():
        return {}
    result = {}
    for f in sorted(topics_dir.iterdir()):
        if f.suffix != ".md":
            continue
        raw = f.read_text()
        entries = [e.strip() for e in raw.split("§") if e.strip()]
        facts = []
        for i, e in enumerate(entries):
            facts.append({
                "index": i + 1,
                "text": e,
                "updated_at": f.stat().st_mtime,
            })
        result[f.stem] = facts
    return result


def _load_proposals_jsonl(base_dir: Path) -> list[dict]:
    path = base_dir / "proposals.jsonl"
    return _load_jsonl(path)


def _load_memory_facts(base_dir: Path, project: str) -> list[dict]:
    p = base_dir / project / "MEMORY.md"
    if not p.exists():
        return []
    raw = p.read_text().strip()
    if not raw:
        return []
    entries = [e.strip() for e in raw.split("§") if e.strip()]
    return [{"text": e, "updated_at": p.stat().st_mtime} for e in entries]


def _load_sessions(base_dir: Path) -> list[dict]:
    path = base_dir / "sessions.jsonl"
    return _load_jsonl(path)


def _load_tasks(base_dir: Path) -> list[dict]:
    tasks_dir = base_dir / "tasks"
    if not tasks_dir.exists():
        return []
    tasks = []
    for f in sorted(tasks_dir.iterdir()):
        if f.suffix != ".json":
            continue
        try:
            t = json.loads(f.read_text())
            tasks.append(t)
        except (json.JSONDecodeError, OSError):
            continue
    return tasks


def get_changelog(since: float = 0.0, types: list[str] | None = None) -> dict:
    """Return records changed since timestamp.

    types: subset of ['topic','memory','session','task','proposal']
           None means all types.
    """
    if WORKDIR is None:
        return {"error": "federation not initialized"}
    result: dict[str, Any] = {
        "server_ts": _ts(),
        "since": since,
        "topics": {},
        "memory": {},
        "sessions": [],
        "tasks": [],
        "proposals": [],
    }

    if types is None or "topic" in types:
        topics = _load_topics(WORKDIR)
        for topic_name, facts in topics.items():
            changed = [f for f in facts if f["updated_at"] > since]
            if changed:
                result["topics"][topic_name] = changed

    if types is None or "memory" in types:
        memory_dir = WORKDIR
        projects = set()
        # discover projects from memory dirs
        for entry in memory_dir.iterdir():
            if entry.is_dir() and (entry / "MEMORY.md").exists():
                projects.add(entry.name)
        for proj in projects:
            facts = _load_memory_facts(WORKDIR, proj)
            changed = [f for f in facts if f["updated_at"] > since]
            if changed:
                result["memory"][proj] = changed

    if types is None or "session" in types:
        sessions = _load_sessions(WORKDIR)
        result["sessions"] = [
            s for s in sessions
            if s.get("created", 0) / 1000 > since
        ]

    if types is None or "task" in types:
        tasks = _load_tasks(WORKDIR)
        result["tasks"] = [
            t for t in tasks
            if t.get("created_at", 0) > since
        ]

    if types is None or "proposal" in types:
        proposals = _load_proposals_jsonl(WORKDIR)
        result["proposals"] = [
            p for p in proposals
            if p.get("proposed_at", 0) > since
        ]

    return result


# ── Apply Changes (Pull Integration) ────────────────────────

def _add_fact_to_topic(base_dir: Path, topic: str, text: str):
    topics_dir = base_dir / "topics"
    topics_dir.mkdir(parents=True, exist_ok=True)
    tpath = topics_dir / f"{topic}.md"
    entries = []
    if tpath.exists():
        entries = [e.strip() for e in tpath.read_text().split("§") if e.strip()]
    if text not in entries:
        entries.append(text)
        tpath.write_text("\n§\n".join(entries) + "\n")


def _merge_topic_facts(base_dir: Path, topic: str, incoming_facts: list[dict]):
    """Merge incoming facts with local facts. Union merge: dedup by text."""
    topics_dir = base_dir / "topics"
    topics_dir.mkdir(parents=True, exist_ok=True)
    tpath = topics_dir / f"{topic}.md"
    local = []
    if tpath.exists():
        local = [e.strip() for e in tpath.read_text().split("§") if e.strip()]

    local_texts = set(local)
    for f in incoming_facts:
        text = f.get("text", "").strip()
        if text and text not in local_texts:
            local.append(text)
            local_texts.add(text)

    tpath.write_text("\n§\n".join(local) + "\n")


def _merge_memory_facts(base_dir: Path, project: str, incoming_facts: list[dict]):
    """Merge incoming memory facts. Dedup by text."""
    p = base_dir / project / "MEMORY.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    local = []
    if p.exists():
        raw = p.read_text().strip()
        if raw:
            local = [e.strip() for e in raw.split("§") if e.strip()]

    local_texts = set(local)
    for f in incoming_facts:
        text = f.get("text", "").strip()
        if text and text not in local_texts:
            local.append(text)
            local_texts.add(text)

    p.write_text("\n§\n".join(local) + "\n" if local else "")


def _merge_sessions(base_dir: Path, sessions: list[dict]):
    """Append sessions not already present (dedup by id)."""
    path = base_dir / "sessions.jsonl"
    existing_ids: set[str] = set()
    if path.exists():
        for line in path.read_text().strip().splitlines():
            try:
                s = json.loads(line)
                existing_ids.add(s.get("id", ""))
            except json.JSONDecodeError:
                continue
    for s in sessions:
        if s.get("id") and s["id"] not in existing_ids:
            _append_jsonl(path, s)
            existing_ids.add(s["id"])
            # also index the session
            _index_session_internal(s)


def _index_session_internal(rec: dict):
    """Index a session into the FTS5 table."""
    try:
        import sqlite3
        db_path = WORKDIR / "index.db"
        if not db_path.exists():
            return
        db = sqlite3.connect(str(db_path))
        db.execute(
            "INSERT OR IGNORE INTO sessions_fts(id, title, summary) VALUES (?, ?, ?)",
            (rec.get("id", ""), rec.get("title", "")[:200], rec.get("summary", "")[:500]),
        )
        db.commit()
        db.close()
    except Exception:
        pass


def _merge_tasks(base_dir: Path, tasks: list[dict]):
    """Merge tasks. For conflicts: newer created_at wins."""
    tasks_dir = base_dir / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)
    existing: dict[str, dict] = {}
    for f in tasks_dir.iterdir():
        if f.suffix != ".json":
            continue
        try:
            t = json.loads(f.read_text())
            existing[t["id"]] = t
        except (json.JSONDecodeError, OSError):
            continue

    for t in tasks:
        tid = t.get("id", "")
        if not tid:
            continue
        if tid in existing:
            # last-writer-wins: keep the one with newer created_at
            if t.get("created_at", 0) > existing[tid].get("created_at", 0):
                (tasks_dir / f"{tid}.json").write_text(
                    json.dumps(t, ensure_ascii=False, indent=2)
                )
                existing[tid] = t
        else:
            (tasks_dir / f"{tid}.json").write_text(
                json.dumps(t, ensure_ascii=False, indent=2)
            )
            existing[tid] = t


def _merge_proposals(base_dir: Path, proposals: list[dict]):
    """Merge proposals. Dedup by id."""
    path = base_dir / "proposals.jsonl"
    existing_ids: set[str] = set()
    if path.exists():
        for line in path.read_text().strip().splitlines():
            try:
                p = json.loads(line)
                existing_ids.add(p.get("id", ""))
            except json.JSONDecodeError:
                continue
    for p in proposals:
        if p.get("id") and p["id"] not in existing_ids:
            _append_jsonl(path, p)
            existing_ids.add(p["id"])


def apply_changelog(changelog: dict) -> dict:
    """Apply incoming changelog to local storage.

    Returns counts of what was applied.
    """
    if WORKDIR is None:
        return {"error": "federation not initialized"}

    counts = {
        "topics": 0,
        "memory": 0,
        "sessions": 0,
        "tasks": 0,
        "proposals": 0,
    }

    # Topics — union merge
    for topic_name, facts in changelog.get("topics", {}).items():
        _merge_topic_facts(WORKDIR, topic_name, facts)
        counts["topics"] += len(facts)

    # Memory — union merge
    for project, facts in changelog.get("memory", {}).items():
        _merge_memory_facts(WORKDIR, project, facts)
        counts["memory"] += len(facts)

    # Sessions — append new
    sessions = changelog.get("sessions", [])
    if sessions:
        _merge_sessions(WORKDIR, sessions)
        counts["sessions"] = len(sessions)

    # Tasks — last-writer-wins
    tasks = changelog.get("tasks", [])
    if tasks:
        _merge_tasks(WORKDIR, tasks)
        counts["tasks"] = len(tasks)

    # Proposals — dedup by id
    proposals = changelog.get("proposals", [])
    if proposals:
        _merge_proposals(WORKDIR, proposals)
        counts["proposals"] = len(proposals)

    return counts


# ── Sync Operations ────────────────────────────────────────

def pull_from_peer(peer_name: str, types: list[str] | None = None) -> dict:
    """Pull changes from a peer since last sync."""
    peers = _load_peers()
    peer = next((p for p in peers if p["name"] == peer_name), None)
    if peer is None:
        return {"error": f"peer '{peer_name}' not found"}

    state = _load_sync_state()
    last_sync = state.get(peer_name, {}).get("last_sync", 0.0)

    # Build query params
    params = f"?since={last_sync}"
    if types:
        params += "&types=" + ",".join(types)

    result = _peer_req(peer, "GET", f"/sync/changelog{params}")
    if result is None:
        return {"error": f"failed to fetch changelog from peer '{peer_name}'"}

    if "error" in result:
        return result

    counts = apply_changelog(result)

    # Update sync state
    peer_state = state.setdefault(peer_name, {})
    peer_state["last_sync"] = result.get("server_ts", _ts())
    peer_state["last_pull_at"] = _ts()
    _save_sync_state(state)

    return {
        "ok": True,
        "peer": peer_name,
        "action": "pull",
        "applied": counts,
        "server_ts": result.get("server_ts", 0),
    }


def push_to_peer(peer_name: str, types: list[str] | None = None) -> dict:
    """Push local changes to a peer."""
    peers = _load_peers()
    peer = next((p for p in peers if p["name"] == peer_name), None)
    if peer is None:
        return {"error": f"peer '{peer_name}' not found"}

    state = _load_sync_state()
    last_sync = state.get(peer_name, {}).get("last_sync", 0.0)

    changelog = get_changelog(since=last_sync, types=types)
    if "error" in changelog:
        return changelog

    result = _peer_req(peer, "POST", "/sync/push", changelog)
    if result is None:
        return {"error": f"failed to push to peer '{peer_name}'"}

    # Update sync state
    peer_state = state.setdefault(peer_name, {})
    peer_state["last_sync"] = changelog.get("server_ts", _ts())
    peer_state["last_push_at"] = _ts()
    _save_sync_state(state)

    return {
        "ok": True,
        "peer": peer_name,
        "action": "push",
        "applied": result.get("applied", {}),
        "sent": {
            "topics": sum(len(v) for v in changelog.get("topics", {}).values()),
            "memory": sum(len(v) for v in changelog.get("memory", {}).values()),
            "sessions": len(changelog.get("sessions", [])),
            "tasks": len(changelog.get("tasks", [])),
            "proposals": len(changelog.get("proposals", [])),
        },
    }


def sync_full(peer_name: str, types: list[str] | None = None) -> dict:
    """Bidirectional sync: pull then push. Uses PneuralAdapter for pneural peers."""
    peers = _load_peers()
    peer = next((p for p in peers if p["name"] == peer_name), None)
    if peer is None:
        return {"error": f"peer '{peer_name}' not found"}

    if peer.get("adapter") == "pneural":
        adapter = PneuralAdapter(url=peer["url"], api_key=peer.get("api_key", ""))
        result = adapter.sync_with_pneural()
        return {"ok": True, "peer": peer_name, "action": "full_pneural", "result": result}

    pull_result = pull_from_peer(peer_name, types)
    if "error" in pull_result:
        return pull_result

    push_result = push_to_peer(peer_name, types)
    if "error" in push_result:
        return {"ok": True, "peer": peer_name, "action": "full_partial",
                "pull": pull_result, "push_error": push_result["error"]}

    return {
        "ok": True,
        "peer": peer_name,
        "action": "full",
        "pull": pull_result.get("applied", {}),
        "push": push_result.get("applied", {}),
    }


def sync_all(types: list[str] | None = None) -> list[dict]:
    """Sync with all registered peers."""
    peers = _load_peers()
    results = []
    for peer in peers:
        result = sync_full(peer["name"], types)
        results.append({"peer": peer["name"], "result": result})
    return results


# ── Pneural-Context Adapter ──────────────────────────────────


class PneuralAdapter:
    """Adapter for syncing with a pneural-context instance.

    Translates between memoria's file-based format and pneural-context's
    relational API (GET /api/memory/full, POST /api/memory).
    """

    def __init__(self, url: str = "http://localhost:8777", api_key: str = ""):
        self.url = url.rstrip("/")
        self.api_key = api_key

    def _req(self, method: str, path: str, data: dict | None = None) -> dict | None:
        url = f"{self.url}{path}"
        body = json.dumps(data).encode() if data is not None else None
        req = urllib.request.Request(url, data=body, method=method)
        req.add_header("Content-Type", "application/json")
        if self.api_key:
            req.add_header("X-Api-Key", self.api_key)
        try:
            with urllib.request.urlopen(req, timeout=SYNC_TIMEOUT) as resp:
                return json.loads(resp.read())
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, json.JSONDecodeError):
            return None

    def pull_from_pneural(self, project: str) -> list[dict]:
        """Pull memory entries from pneural-context for a project."""
        result = self._req("GET", f"/api/memory/full?project={urllib.parse.quote(project)}")
        if result is None or not isinstance(result, list):
            return []
        entries = []
        for row in result:
            entries.append({
                "text": row.get("entry", ""),
                "priority": row.get("priority", "normal"),
                "memory_type": row.get("memory_type", "temporal"),
                "source_system": row.get("pb_sync_source", "local"),
            })
        return entries

    def push_to_pneural(self, project: str, text: str, priority: str = "normal",
                         memory_type: str | None = None) -> dict | None:
        """Push a single memory entry to pneural-context."""
        payload: dict[str, Any] = {
            "project": project,
            "text": text,
            "priority": priority,
        }
        if memory_type:
            payload["memory_type"] = memory_type
        return self._req("POST", "/api/memory", payload)

    def get_changelog_for_pneural(self, since: float = 0.0) -> dict[str, list[dict]]:
        """Convert memoria's file-based changelog into pneural-context API format.

        Returns dict of project -> list of entries suitable for POST /api/memory.
        Only includes 'memory' type (no topics, sessions, tasks, proposals).
        """
        if WORKDIR is None:
            return {}
        result: dict[str, list[dict]] = {}
        memory_dir = WORKDIR
        for entry in memory_dir.iterdir():
            if entry.is_dir() and (entry / "MEMORY.md").exists():
                project = entry.name
                facts = _load_memory_facts(WORKDIR, project)
                changed = [f for f in facts if f["updated_at"] > since]
                if changed:
                    result[project] = [
                        {
                            "text": f["text"],
                            "priority": "normal",
                            "memory_type": "temporal",
                        }
                        for f in changed
                    ]
        return result

    def apply_pneural_changelog(self, entries_by_project: dict[str, list[dict]]) -> dict:
        """Apply pneural-context memory entries to memoria's file-based storage.

        Each entry: {"text": str, "priority": str, "memory_type": str}.
        Dedup by normalized text.
        """
        if WORKDIR is None:
            return {"error": "federation not initialized"}
        applied = 0
        for project, entries in entries_by_project.items():
            p = WORKDIR / project / "MEMORY.md"
            p.parent.mkdir(parents=True, exist_ok=True)
            local = []
            if p.exists():
                raw = p.read_text().strip()
                if raw:
                    local = [e.strip() for e in raw.split("§") if e.strip()]
            local_set = set(local)
            for entry in entries:
                text = entry.get("text", "").strip()
                if text and text not in local_set:
                    local.append(text)
                    local_set.add(text)
                    applied += 1
            p.write_text("\n§\n".join(local) + "\n" if local else "")
        return {"applied": applied}

    def sync_with_pneural(self) -> dict:
        """Full sync: pull from pneural + push to pneural (memory type only)."""
        pull_result = {"projects_pulled": 0, "entries_pulled": 0}
        push_result = {"projects_pushed": 0, "entries_pushed": 0}

        if WORKDIR is None:
            return {"error": "federation not initialized"}

        # Discover projects
        projects = set()
        for entry in WORKDIR.iterdir():
            if entry.is_dir() and (entry / "MEMORY.md").exists():
                projects.add(entry.name)

        # Pull from pneural for each project
        for project in projects:
            entries = self.pull_from_pneural(project)
            if entries:
                self.apply_pneural_changelog({project: entries})
                pull_result["projects_pulled"] += 1
                pull_result["entries_pulled"] += len(entries)

        # Push memoria's changelog to pneural
        changelog = self.get_changelog_for_pneural()
        for project, entries in changelog.items():
            for entry in entries:
                self.push_to_pneural(
                    project,
                    entry["text"],
                    entry.get("priority", "normal"),
                    entry.get("memory_type"),
                )
                push_result["entries_pushed"] += 1
            if entries:
                push_result["projects_pushed"] += 1

        return {"pull": pull_result, "push": push_result}
