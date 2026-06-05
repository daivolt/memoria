"""
memoria — CLI for opencode session memory and context compression.

Usage:
  memoria init                  Start daemon + create MEMORY.md
  memoria add <text>            Save durable fact to MEMORY.md
  memoria list                  Show all facts
  memoria replace <old> <new>   Replace matching entry
  memoria recall <query>        Search past sessions via FTS5
  memoria review [N]            Summarize last N sessions
  memoria learnings             Show accumulated project knowledge
  memoria compress              Compress tool outputs to 1-liners (stdin)
  memoria status                Check daemon health
  memoria stop                  Stop daemon
"""

import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

WORKDIR = Path("/var/tmp/memoria")
MEMORY_LIMIT = 5000
HERMES_HOME = Path(os.environ.get("HERMES_HOME", "~/.hermes")).expanduser()


def project_name() -> str:
    return os.environ.get("MEMORIA_PROJECT") or Path.cwd().name


def wd() -> Path:
    p = WORKDIR / project_name()
    p.mkdir(parents=True, exist_ok=True)
    return p


def _memory_path() -> Path:
    return wd() / "MEMORY.md"


def _parse_memory() -> list[str]:
    p = _memory_path()
    if not p.exists():
        return []
    raw = p.read_text().strip()
    if not raw:
        return []
    return [e.strip() for e in raw.split("§") if e.strip()]


def _write_memory(entries: list[str]):
    p = _memory_path()
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


def cmd_add(text: str):
    entries = _parse_memory()
    total_chars = sum(len(e) for e in entries)
    if total_chars + len(text) > MEMORY_LIMIT:
        print(
            f"Memory at {total_chars}/{MEMORY_LIMIT} chars. Cannot add.",
            file=sys.stderr,
        )
        sys.exit(1)
    entries.append(text)
    _write_memory(entries)
    print(
        f"added ({len(entries)} entries, {total_chars + len(text)}/{MEMORY_LIMIT} chars)"
    )


def cmd_list():
    entries = _parse_memory()
    if not entries:
        print("MEMORY.md is empty")
        return
    for i, e in enumerate(entries, 1):
        print(f"{i}. {e}")


def cmd_replace(old: str, new: str):
    entries = _parse_memory()
    found = False
    for i, e in enumerate(entries):
        if old in e:
            entries[i] = new
            found = True
    if not found:
        print(f"No entry containing: {old}", file=sys.stderr)
        sys.exit(1)
    _write_memory(entries)
    print(f"replaced ({len(entries)} entries)")


def daemon_pid() -> int | None:
    p = wd() / "daemon.pid"
    if not p.exists():
        return None
    pid = int(p.read_text().strip())
    try:
        os.kill(pid, 0)
        return pid
    except OSError:
        return None


def cmd_init():
    pid = daemon_pid()
    if pid:
        print(f"daemon already running (pid {pid})")
        return
    if not _memory_path().exists():
        _memory_path().write_text("")
        print(f"created {_memory_path()}")
    script = str(Path(__file__).resolve().parent / "memoriad.py")
    env = os.environ.copy()
    env["MEMORIA_PROJECT"] = project_name()
    subprocess.Popen(
        [sys.executable, script],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
        start_new_session=True,
    )
    for _ in range(10):
        time.sleep(0.3)
        pid = daemon_pid()
        if pid:
            print(f"daemon started (pid {pid})")
            _maybe_import_hermes()
            return
    print("daemon start timed out", file=sys.stderr)
    sys.exit(1)


def cmd_stop():
    pid = daemon_pid()
    if not pid:
        print("daemon not running")
        return
    os.kill(pid, 15)
    for _ in range(10):
        time.sleep(0.3)
        if not daemon_pid():
            print(f"daemon (pid {pid}) stopped")
            return
    os.kill(pid, 9)
    print("forced kill")


def cmd_status():
    pid = daemon_pid()
    entries = _parse_memory()
    index_path = wd() / "index.db"
    sess_path = wd() / "sessions.jsonl"
    print(f"project:    {project_name()}")
    print(f"daemon:     {'running (pid ' + str(pid) + ')' if pid else 'stopped'}")
    print(
        f"memory:     {len(entries)} entries, {sum(len(e) for e in entries)}/{MEMORY_LIMIT} chars"
    )
    print(f"index.db:   {'exists' if index_path.exists() else 'missing'}")
    sess_count = sum(1 for _ in sess_path.open()) if sess_path.exists() else 0
    print(f"sessions:   {sess_count} indexed")
    print(f"workdir:    {wd()}")


def _open_index() -> sqlite3.Connection:
    path = wd() / "index.db"
    db = sqlite3.connect(str(path))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=OFF")
    db.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS sessions_fts "
        "USING fts5(id UNINDEXED, title, summary, tokenize='porter unicode61')"
    )
    return db


def cmd_recall(query: str, limit: int = 5):
    db = _open_index()
    count = db.execute("SELECT count(*) FROM sessions_fts").fetchone()[0]
    if count == 0:
        print("No indexed sessions yet. Run 'memoria review' or wait for daemon.")
        db.close()
        return
    q = " OR ".join(query.split())
    try:
        rows = db.execute(
            "SELECT id, title, summary, rank "
            "FROM sessions_fts "
            "WHERE sessions_fts MATCH ? "
            "ORDER BY rank LIMIT ?",
            (q, limit),
        ).fetchall()
    except sqlite3.OperationalError:
        print(f"No matches for: {query}")
        db.close()
        return
    db.close()
    if not rows:
        print(f"No matches for: {query}")
        return
    print(f"Found {len(rows)} relevant sessions:\n")
    for r in rows:
        summary = (r["summary"] or "")[:300]
        print(f"  [{r['id'][:12]}...]")
        print(f"  title: {r['title']}")
        print(f"  summary: {summary}\n")


def cmd_review(n: int = 3):
    p = wd() / "sessions.jsonl"
    if not p.exists():
        print("No sessions data yet.")
        return
    lines = p.read_text().strip().splitlines()
    for line in lines[-n:]:
        try:
            s = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(s.get("created", 0) / 1000))
        print(f"[{s.get('id', '?')[:12]}...]  {ts}")
        print(f"  title: {s.get('title', 'untitled')}")
        print(f"  task:  {s.get('task', '?')[:200]}")
        tools = s.get("tools", [])
        if tools:
            print(f"  tools: {', '.join(tools[:8])}")
            if len(tools) > 8:
                print(f"         ... and {len(tools) - 8} more")
        print()


def cmd_learnings():
    p = wd() / "sessions.jsonl"
    if not p.exists():
        print("No sessions data yet.")
        return
    parts = []
    for line in p.read_text().strip().splitlines()[-20:]:
        try:
            parts.append(json.loads(line).get("summary", ""))
        except json.JSONDecodeError:
            continue
    print("\n\n".join(p for p in parts if p))


def cmd_compress():
    text = sys.stdin.read()
    if not text:
        print("No input (stdin is empty)")
        return
    lines = text.splitlines(keepends=True)
    result = []
    buf = []
    in_tool = False
    for line in lines:
        m = re.match(r"^\[(\w+)\]\s+(ran `[^`]+`|executed|returned)", line)
        if m:
            if buf:
                result.extend(_compress_block(buf))
            buf = [line]
            in_tool = True
            continue
        if in_tool:
            if re.match(r"^\[(\w+)\]", line):
                result.extend(_compress_block(buf))
                result.append(line)
                buf = []
                in_tool = False
            elif line.strip() == "" and len(buf) > 30:
                result.extend(_compress_block(buf))
                result.append(line)
                buf = []
                in_tool = False
            else:
                buf.append(line)
            continue
        result.append(line)
    if buf:
        result.extend(_compress_block(buf))
    sys.stdout.write("".join(result))


_TOOL = re.compile(r"^\[(\w+)\]\s+(.+)")
_EXIT = re.compile(r"exit (\d+)")


def _compress_block(lines: list[str]) -> list[str]:
    if not lines:
        return []
    first = lines[0].strip()
    m = _TOOL.match(first)
    if not m:
        return lines
    tool = m.group(1)
    action = m.group(2)
    code = "?"
    count = len(lines) - 1
    err = any("Error:" in l for l in lines)
    for l in lines:
        x = _EXIT.search(l)
        if x:
            code = x.group(1)
    status = f"exit {code}{' (error)' if err else ''}"
    for l in lines[1:6]:
        f = re.search(r"Found (\d+) matches", l)
        if f:
            return [f"[{tool}] {action} → {status}, {f.group(1)} matches\n"]
        c = re.search(r"(\d+) lines?", l)
        if c:
            return [f"[{tool}] {action} → {status}, {c.group(1)}\n"]
    return [
        f"[{tool}] {action} → {status}, {count} lines{' (errors)' if err else ''}\n"
    ]


def _maybe_import_hermes():
    p = _memory_path()
    if p.exists() and p.stat().st_size > 0:
        return
    hm = HERMES_HOME / "memories" / "MEMORY.md"
    hu = HERMES_HOME / "memories" / "USER.md"
    entries = []
    if hm.exists():
        for e in hm.read_text().split("§"):
            e = e.strip()
            if e:
                entries.append(f"[hermes] {e}")
    if hu.exists():
        for e in hu.read_text().split("§"):
            e = e.strip()
            if e:
                entries.append(f"[hermes:user] {e}")
    if entries:
        _write_memory(entries[:20])
        print(f"imported {len(entries[:20])} entries from Hermes")
    else:
        p.write_text("")


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print((__doc__ or "").strip())
        return
    cmd = args[0]
    if cmd == "init":
        cmd_init()
    elif cmd == "add":
        if len(args) < 2:
            print("usage: memoria add <text>", file=sys.stderr)
            sys.exit(1)
        cmd_add(" ".join(args[1:]))
    elif cmd == "list":
        cmd_list()
    elif cmd == "replace":
        if len(args) < 3:
            print("usage: memoria replace <old> <new>", file=sys.stderr)
            sys.exit(1)
        cmd_replace(args[1], " ".join(args[2:]))
    elif cmd == "recall":
        if len(args) < 2:
            print("usage: memoria recall <query>", file=sys.stderr)
            sys.exit(1)
        cmd_recall(" ".join(args[1:]))
    elif cmd == "review":
        cmd_review(int(args[1]) if len(args) > 1 else 3)
    elif cmd == "learnings":
        cmd_learnings()
    elif cmd == "compress":
        cmd_compress()
    elif cmd == "status":
        cmd_status()
    elif cmd == "stop":
        cmd_stop()
    else:
        print(f"unknown: {cmd}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
