"""
memoria — CLI for opencode session memory + AgentOS orchestration.

Memory:
  memoria init                  Verify server connectivity
  memoria add <text>            Save durable fact to MEMORY.md
  memoria list                  Show all facts
  memoria replace <old> <new>   Replace matching entry
  memoria recall <query>        Search past sessions via FTS5
  memoria review [N]            Summarize last N sessions
  memoria learnings             Show accumulated project knowledge
  memoria compress              Compress tool outputs (stdin) via REST
  memoria status                Server health + project memory
   memoria topics                List cross-project topics
   memoria topic <name> [text]   Show topic facts or add one
   memoria topic delete <name>   Delete entire topic
   memoria topic edit <n> <i> <t> Edit fact at index i
   memoria topic remove <n> <i>  Remove fact at index i
   memoria propose <topic> <txt> Propose cross-project fact
   memoria proposals             List pending proposals
   memoria proposals clear       Clear all pending proposals
   memoria accept <id>           Accept proposal
  memoria reject <id>           Reject proposal

Orchestration:
  memoria task <project> <title>    Create task on board
  memoria claim <task-id>           Claim a pending task
  memoria done <task-id> [result]   Mark task complete
  memoria fail <task-id> [error]    Mark task failed
  memoria tasks [project]           List tasks
  memoria agents [project]          List active agents
  memoria snap <project> [msg]      Create git snapshot
  memoria rollback <project> [id]   Rollback to snapshot

Chitchat (episodic chat memory):
  memoria chitchat rooms               List tracked chat rooms
  memoria chitchat history <room>      Show recent chat messages
  memoria chitchat consolidate         Trigger hippocampal replay → topics
"""

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

SERVER = os.environ.get("MEMORIA_SERVER", "http://100.121.245.69:19998")


def project_name() -> str:
    return os.environ.get("MEMORIA_PROJECT") or Path.cwd().name


def _req(method: str, path: str, data: dict | None = None) -> dict:
    url = f"{SERVER}{path}"
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        err = e.read().decode()
        try:
            return json.loads(err)
        except json.JSONDecodeError:
            return {"error": f"HTTP {e.code}: {err}"}
    except urllib.error.URLError as e:
        return {"error": f"server unreachable: {e.reason}"}


def cmd_init():
    h = _req("GET", "/health")
    if "error" in h:
        print(h["error"], file=sys.stderr)
        sys.exit(1)
    s = h.get("sessions_indexed", 0)
    t = ", ".join(h.get("topics", []))
    print(f"memoria v{h.get('memoria_version', '?')} — {s} sessions indexed")
    if t:
        print(f"topics: {t}")


def cmd_add(text: str):
    r = _req("POST", f"/memory/{project_name()}", {"text": text})
    if "error" in r:
        print(r["error"], file=sys.stderr)
        sys.exit(1)
    print(f"added ({r['entries']} entries, {r['chars']} chars)")


def cmd_list():
    r = _req("GET", f"/memory/{project_name()}")
    if "error" in r:
        print(r["error"], file=sys.stderr)
        sys.exit(1)
    entries = r.get("entries", [])
    if not entries:
        print("MEMORY.md is empty")
        return
    for i, e in enumerate(entries, 1):
        print(f"{i}. {e}")


def cmd_replace(old: str, new: str):
    r = _req("PUT", f"/memory/{project_name()}", {"old": old, "new": new})
    if "error" in r:
        print(r["error"], file=sys.stderr)
        sys.exit(1)
    print(f"replaced ({r['entries']} entries)")


def cmd_recall(query: str, limit: int = 5):
    r = _req("GET", f"/recall?q={urllib.parse.quote(query)}&limit={limit}")
    if "error" in r:
        print(r["error"], file=sys.stderr)
        sys.exit(1)
    results = r.get("results", [])
    if not results:
        print(f"No matches for: {query}")
        return
    print(f"Found {len(results)} relevant sessions:\n")
    for s in results:
        print(f"  [{s['id'][:12]}...]")
        print(f"  title: {s['title']}")
        print(f"  summary: {s['summary']}\n")


def cmd_review(n: int = 3):
    r = _req("GET", f"/review?n={n}")
    if "error" in r:
        print(r["error"], file=sys.stderr)
        sys.exit(1)
    sessions = r.get("sessions", [])
    if not sessions:
        print("No sessions data yet.")
        return
    for s in sessions:
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
    r = _req("GET", "/review?n=20")
    if "error" in r:
        print(r["error"], file=sys.stderr)
        sys.exit(1)
    sessions = r.get("sessions", [])
    if not sessions:
        print("No sessions data yet.")
        return
    parts = [s.get("summary", "") for s in sessions if s.get("summary")]
    print("\n\n".join(p for p in parts if p))


def cmd_compress():
    text = sys.stdin.read()
    if not text:
        print("No input (stdin is empty)")
        return
    r = _req("POST", "/compress", {"text": text, "phase": 2})
    if "error" in r:
        print(r["error"], file=sys.stderr)
        sys.exit(1)
    sys.stdout.write(r.get("compressed", ""))


def cmd_status():
    h = _req("GET", "/health")
    if "error" in h:
        print(h["error"], file=sys.stderr)
        sys.exit(1)
    m = _req("GET", f"/memory/{project_name()}")
    entries = m.get("entries", [])
    char_total = sum(len(e) for e in entries)
    print(f"project:    {project_name()}")
    print(f"server:     {SERVER}")
    print(f"daemon:     running (REST server)")
    print(f"memory:     {len(entries)} entries, {char_total} chars")
    print(f"sessions:   {h.get('sessions_indexed', 0)} indexed")
    print(f"db:         {'present' if h.get('db_exists') else 'missing'}")
    topics = h.get("topics", [])
    if topics:
        print(f"topics:     {', '.join(topics)}")


def cmd_stop():
    print("memoria server is persistent — no stop needed")
    print(f"server: {SERVER}")


def cmd_topics():
    r = _req("GET", "/topics?detail=true")
    if "error" in r:
        print(r["error"], file=sys.stderr)
        sys.exit(1)
    topics = r.get("topics", {})
    if not topics:
        print("No topics yet. Use: memoria propose <topic> <text>")
        return
    for name, facts in sorted(topics.items()):
        print(f"\n## {name}")
        for i, f in enumerate(facts, 1):
            ws = " " * (len(name) + 4)
            print(f"  {i}. {f}" if i == 1 else f"{ws}{i}. {f}")
    print()


def cmd_topic(name: str, text: str | None = None):
    if text:
        r = _req("POST", f"/topics/{name}", {"text": text})
        if "error" in r:
            print(r["error"], file=sys.stderr)
            sys.exit(1)
        print(f"added to topic '{name}' ({r['entries']} entries)")
        return
    r = _req("GET", f"/topics/{name}")
    if "error" in r:
        print(r["error"], file=sys.stderr)
        sys.exit(1)
    facts = r.get("facts", [])
    if not facts:
        print(f"Topic '{name}' is empty")
        return
    print(f"## {name}")
    for i, f in enumerate(facts, 1):
        print(f"  {i}. {f}")


def cmd_propose(topic: str, text: str):
    r = _req("POST", "/proposals", {"topic": topic, "text": text})
    if "error" in r:
        print(r["error"], file=sys.stderr)
        sys.exit(1)
    print(f"proposed as {r['id']}")


def cmd_proposals():
    r = _req("GET", "/proposals")
    if "error" in r:
        print(r["error"], file=sys.stderr)
        sys.exit(1)
    props = r.get("proposals", [])
    if not props:
        print("No pending proposals")
        return
    for p in props:
        ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(p.get("proposed_at", 0)))
        hits = p.get("hits", 1)
        print(f"  {p['id']}  [{p['topic']}]  hits={hits}  {ts}")
        print(f"    {p['text'][:200]}")


def cmd_accept(pid: str):
    r = _req("POST", f"/proposals/{pid}/accept")
    if "error" in r:
        print(r["error"], file=sys.stderr)
        sys.exit(1)
    print(f"accepted, moved to topic '{r['moved_to']}'")


def cmd_reject(pid: str):
    r = _req("DELETE", f"/proposals/{pid}")
    if "error" in r:
        print(r["error"], file=sys.stderr)
        sys.exit(1)
    print(f"rejected {pid}")


def cmd_topic_delete(name: str):
    r = _req("DELETE", f"/topics/{urllib.parse.quote(name)}")
    if "error" in r:
        print(r["error"], file=sys.stderr)
        sys.exit(1)
    print(f"topic '{name}' deleted")


def cmd_topic_edit(name: str, index: int, text: str):
    r = _req("PUT", f"/topics/{urllib.parse.quote(name)}", {"index": index, "text": text})
    if "error" in r:
        print(r["error"], file=sys.stderr)
        sys.exit(1)
    print(f"fact {index} updated in topic '{name}'")


def cmd_topic_remove(name: str, index: int):
    r = _req("DELETE", f"/topics/{urllib.parse.quote(name)}/{index}")
    if "error" in r:
        print(r["error"], file=sys.stderr)
        sys.exit(1)
    print(f"fact {index} removed from topic '{name}'")


def cmd_proposals_clear():
    r = _req("DELETE", "/proposals?confirm=true")
    if "error" in r:
        print(r["error"], file=sys.stderr)
        sys.exit(1)
    print("all proposals cleared")


def cmd_chitchat_history(room: str, limit: int = 20):
    r = _req("GET", f"/chitchat/{urllib.parse.quote(room)}?limit={limit}")
    if "error" in r:
        print(r["error"], file=sys.stderr)
        sys.exit(1)
    msgs = r.get("messages", [])
    if not msgs:
        print(f"No messages in room '{room}'")
        return
    for m in msgs:
        ts = m.get("ts", "")[:19] if m.get("ts") else "?"
        print(f"  [{ts}] {m.get('from', '?')}: {m.get('text', '')[:200]}")


def cmd_chitchat_rooms():
    r = _req("GET", "/chitchat/rooms")
    if "error" in r:
        print(r["error"], file=sys.stderr)
        sys.exit(1)
    rooms = r.get("rooms", [])
    if not rooms:
        print("No chitchat rooms tracked yet")
        return
    for rm in rooms:
        print(f"  {rm['room']} — {rm['messages']} messages")


def cmd_chitchat_consolidate():
    r = _req("POST", "/chitchat/consolidate")
    if "error" in r:
        print(r["error"], file=sys.stderr)
        sys.exit(1)
    print("Consolidation triggered (chat replay → topics proposals)")


def cmd_task(project: str, title: str, description: str = ""):
    r = _req(
        "POST",
        "/tasks",
        {"project": project, "title": title, "description": description},
    )
    if "error" in r:
        print(r["error"], file=sys.stderr)
        sys.exit(1)
    print(f"task created: {r['task_id']}")


def cmd_claim(task_id: str):
    r = _req(
        "PATCH",
        f"/tasks/{task_id}",
        {"status": "assigned", "assigned_to": project_name()},
    )
    if "error" in r:
        print(r["error"], file=sys.stderr)
        sys.exit(1)
    print(f"claimed {task_id}")


def cmd_done(task_id: str, result: str = ""):
    r = _req("PATCH", f"/tasks/{task_id}", {"status": "completed", "result": result})
    if "error" in r:
        print(r["error"], file=sys.stderr)
        sys.exit(1)
    print(f"{task_id} completed")


def cmd_fail(task_id: str, error: str = ""):
    r = _req("PATCH", f"/tasks/{task_id}", {"error": error})
    if "error" in r:
        print(r["error"], file=sys.stderr)
        sys.exit(1)
    print(f"{task_id} failed: {error}")


def cmd_tasks(project: str | None = None):
    path = "/tasks"
    if project:
        path += f"?project={urllib.parse.quote(project)}"
    r = _req("GET", path)
    if "error" in r:
        print(r["error"], file=sys.stderr)
        sys.exit(1)
    tasks = r.get("tasks", [])
    if not tasks:
        print("No tasks")
        return
    for t in tasks:
        status = t.get("status", "?")
        assigned = t.get("assigned_to", "") or "unassigned"
        print(f"  {t['id'][:20]}  [{status}]  {assigned}")
        print(f"    {t.get('title', '?')[:100]}")
        if t.get("result"):
            print(f"    result: {t['result'][:100]}")
        if t.get("error"):
            print(f"    error: {t['error'][:100]}")
        print()


def cmd_agents(project: str | None = None):
    path = "/agents"
    if project:
        path += f"?project={urllib.parse.quote(project)}"
    r = _req("GET", path)
    if "error" in r:
        print(r["error"], file=sys.stderr)
        sys.exit(1)
    agents = r.get("agents", [])
    if not agents:
        print("No active agents")
        return
    for a in agents:
        status = a.get("status", "?")
        print(f"  {a['id'][:20]}  [{status}]  {a.get('project', '?')}")
        print(f"    task: {a.get('task', '?')[:100]}")
        if a.get("files"):
            print(f"    files: {', '.join(a['files'][:5])}")
        if a.get("conflicts_warned"):
            print(f"    ⚠ conflicts: {'; '.join(a['conflicts_warned'])}")
        print()


def cmd_snap(project: str, message: str = "agent-os snapshot"):
    r = _req(
        "POST", f"/safety/snapshot/{urllib.parse.quote(project)}", {"message": message}
    )
    if "error" in r:
        print(r["error"], file=sys.stderr)
        sys.exit(1)
    print(f"snapshot {r['snapshot_id']} at {r['commit_hash'][:12]}")


def cmd_rollback(project: str, snapshot_id: str | None = None):
    body = {} if not snapshot_id else {"snapshot_id": snapshot_id}
    r = _req("POST", f"/safety/rollback/{urllib.parse.quote(project)}", body)
    if "error" in r:
        print(r["error"], file=sys.stderr)
        sys.exit(1)
    print(f"rolled back to {r['rolled_back_to'][:12]} ({r.get('message', '?')})")


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
    elif cmd == "topics":
        cmd_topics()
    elif cmd == "topic":
        if len(args) < 2:
            print("usage: memoria topic <name> [text]", file=sys.stderr)
            sys.exit(1)
        sub = args[1]
        if sub == "delete":
            if len(args) < 3:
                print("usage: memoria topic delete <name>", file=sys.stderr)
                sys.exit(1)
            cmd_topic_delete(args[2])
        elif sub == "edit":
            if len(args) < 5:
                print("usage: memoria topic edit <name> <index> <text>", file=sys.stderr)
                sys.exit(1)
            cmd_topic_edit(args[2], int(args[3]), " ".join(args[4:]))
        elif sub == "remove":
            if len(args) < 4:
                print("usage: memoria topic remove <name> <index>", file=sys.stderr)
                sys.exit(1)
            cmd_topic_remove(args[2], int(args[3]))
        else:
            cmd_topic(args[1], " ".join(args[2:]) if len(args) > 2 else None)
    elif cmd == "propose":
        if len(args) < 3:
            print("usage: memoria propose <topic> <text>", file=sys.stderr)
            sys.exit(1)
        cmd_propose(args[1], " ".join(args[2:]))
    elif cmd == "proposals":
        if len(args) > 1 and args[1] == "clear":
            cmd_proposals_clear()
        else:
            cmd_proposals()
    elif cmd == "accept":
        if len(args) < 2:
            print("usage: memoria accept <id>", file=sys.stderr)
            sys.exit(1)
        cmd_accept(args[1])
    elif cmd == "reject":
        if len(args) < 2:
            print("usage: memoria reject <id>", file=sys.stderr)
            sys.exit(1)
        cmd_reject(args[1])
    elif cmd == "task":
        if len(args) < 3:
            print("usage: memoria task <project> <title>", file=sys.stderr)
            sys.exit(1)
        cmd_task(args[1], " ".join(args[2:]))
    elif cmd == "claim":
        if len(args) < 2:
            print("usage: memoria claim <task-id>", file=sys.stderr)
            sys.exit(1)
        cmd_claim(args[1])
    elif cmd == "done":
        if len(args) < 2:
            print("usage: memoria done <task-id> [result]", file=sys.stderr)
            sys.exit(1)
        cmd_done(args[1], " ".join(args[2:]))
    elif cmd == "fail":
        if len(args) < 2:
            print("usage: memoria fail <task-id> [error]", file=sys.stderr)
            sys.exit(1)
        cmd_fail(args[1], " ".join(args[2:]))
    elif cmd == "tasks":
        cmd_tasks(args[1] if len(args) > 1 else None)
    elif cmd == "agents":
        cmd_agents(args[1] if len(args) > 1 else None)
    elif cmd == "snap":
        if len(args) < 2:
            print("usage: memoria snap <project> [message]", file=sys.stderr)
            sys.exit(1)
        cmd_snap(args[1], " ".join(args[2:]) if len(args) > 2 else "agent-os snapshot")
    elif cmd == "rollback":
        if len(args) < 2:
            print("usage: memoria rollback <project> [snapshot-id]", file=sys.stderr)
            sys.exit(1)
        cmd_rollback(args[1], args[2] if len(args) > 2 else None)
    elif cmd == "chitchat":
        if len(args) < 2:
            print("usage: memoria chitchat <history|rooms|consolidate> [...]", file=sys.stderr)
            sys.exit(1)
        sub = args[1]
        if sub == "history":
            if len(args) < 3:
                print("usage: memoria chitchat history <room> [limit]", file=sys.stderr)
                sys.exit(1)
            cmd_chitchat_history(args[2], int(args[3]) if len(args) > 3 else 20)
        elif sub == "rooms":
            cmd_chitchat_rooms()
        elif sub == "consolidate":
            cmd_chitchat_consolidate()
        else:
            print(f"unknown chitchat subcommand: {sub}", file=sys.stderr)
            sys.exit(1)
    else:
        print(f"unknown: {cmd}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
