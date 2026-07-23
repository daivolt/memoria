"""
memoria — CLI for opencode session memory + AgentOS orchestration.

Memory:
  memoria init                  Verify server connectivity
  memoria add [--red|--important] <text>  Save fact (with optional priority)
  memoria list                  Show all facts
  memoria list-full             Show all facts with priority/type/strength
  memoria replace <old> <new>   Replace matching entry
  memoria recall <query>        Search past sessions (SIRA-enriched)
  memoria review [N]            Summarize last N sessions
  memoria learnings             Show accumulated project knowledge
  memoria context <query>       Unified search across ALL surfaces
  memoria reindex               Re-enqueue all records for enrichment
  memoria papers rescan         Force rescan of papers/ directory
  memoria compress              Compress tool outputs (stdin) via REST
  memoria status                Server health + project memory
  memoria red-ink               List all critical (red ink) entries
  memoria promote <idx>         Promote entry to critical priority
  memoria demote <idx>          Demote entry to normal priority
  memoria type <idx> <type>     Set memory type (red|concept|procedural|temporal|relation)
  memoria classify              Auto-classify temporal entries via LLM
  memoria decay                 Show entries with fading strength
  memoria boost <idx>          Boost an entry's strength (recall)
  memoria briefing <task>       Assemble task-specific briefing card
  memoria procedure list        List procedures for current project
  memoria procedure add <pattern> <step1> <step2>...  Add procedure
  memoria procedure search <q>  Search matching procedures
  memoria consolidation [status|trigger]  Memory tier status
  memoria anchors                 Show environmental anchors
  memoria costs [days]          Memory cost analytics
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

Clients:
  memoria clients                      List registered clients
  memoria client <name> <host> [key] [user]  Register client
  memoria client remove <name>         Remove client
  memoria push-clients                 Push updates to all clients

Federation:
  memoria peers                        List federation peers
  memoria peer <name> <url> [key]      Register a federation peer
  memoria peer remove <name>           Remove a federation peer
  memoria sync pull <peer> [types]     Pull changes from peer
  memoria sync push <peer> [types]     Push changes to peer
  memoria sync full <peer> [types]     Bidirectional sync with peer
  memoria sync all [types]             Sync with all peers

CORTEX (autonomous agent task allocation):
  memoria cortex status                Show Q-table, reputation, epsilon
  memoria cortex learnings [n]         Show last N task outcomes
  memoria cortex bid                   Trigger scan + bid on pending tasks
  memoria cortex assign <title> [type] [complexity]  Create + auto-assign task
  memoria cortex complete <task-id> [reward]         Mark done with reward signal
  memoria cortex policy                Show learned Q-values per state

Social / Cultural Learning (Tomasello 1999):
  memoria teach <project> <title> <topic> <facts>...  Create a lesson
  memoria lessons [topic] [project] [min_score]        List available lessons
  memoria lesson <lesson-id>                           Get lesson details
  memoria outcome <lesson-id> <student> <success>      Record student outcome
  memoria curriculum <project> [capabilities]          Build agent curriculum
  memoria culture <project>                            Show cultural memory
  memoria consolidate <project>                        Consolidate culture
  memoria evolve <project> [topic]                     Run cultural evolution
  memoria diversity <project>                          Topic diversity metrics
"""

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

SERVER = os.environ.get("MEMORIA_SERVER", "http://100.126.64.13:19998")


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


def cmd_add(text: str, priority: str = "normal", memory_type: str | None = None):
    payload: dict = {"text": text, "priority": priority}
    if memory_type:
        payload["memory_type"] = memory_type
    r = _req("POST", f"/memory/{project_name()}", payload)
    if "error" in r:
        print(r["error"], file=sys.stderr)
        sys.exit(1)
    print(f"added ({r['entries']} entries, {r['chars']} chars)")


def cmd_list():
    r = _req("GET", f"/memory/{project_name()}/full")
    if "error" in r:
        print(r["error"], file=sys.stderr)
        sys.exit(1)
    entries = r.get("entries", [])
    if not entries:
        print("MEMORY.md is empty")
        return
    type_icons = {
        "red": "🔴",
        "concept": "💡",
        "procedural": "🔧",
        "temporal": "🕐",
        "relation": "🔗",
    }
    for i, e in enumerate(entries, 1):
        mt = e.get("memory_type", "temporal")
        icon = type_icons.get(mt, "🕐")
        text = e.get("entry", str(e)) if isinstance(e, dict) else e
        print(f"{i}. {icon} {text}")


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


def cmd_context(query: str, limit: int = 10):
    r = _req("POST", "/context", {"query": query, "limit": limit})
    if "error" in r:
        print(r["error"], file=sys.stderr)
        sys.exit(1)
    results = r.get("results", [])
    if not results:
        print(f"No matches for: {query}")
        return
    print(f"Found {len(results)} results:\n")
    for s in results:
        src = s.get("source", "?")
        print(f"  [{src}] {s.get('title', '?')}")
        print(f"  {s.get('text', '')[:200]}\n")


def cmd_reindex():
    r = _req("POST", "/enrichment/reindex")
    if not r.get("ok"):
        print(r.get("error", "reindex failed"), file=sys.stderr)
        sys.exit(1)
    print(f"Enqueued {r.get('enqueued', 0)} items for re-enrichment")


def cmd_papers_rescan():
    r = _req("POST", "/papers/rescan")
    if not r.get("ok"):
        print(r.get("error", "rescan failed"), file=sys.stderr)
        sys.exit(1)
    print(r.get("message", "Paper rescan triggered"))


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
    r = _req(
        "PUT", f"/topics/{urllib.parse.quote(name)}", {"index": index, "text": text}
    )
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


def cmd_cortex_status():
    r = _req(
        "GET", f"/topics/cortex_state?project={urllib.parse.quote(project_name())}"
    )
    if "error" in r or not r.get("facts"):
        print("No CORTEX state found")
        return
    raw = r["facts"][-1] if isinstance(r["facts"], list) else r.get("text", "")
    try:
        data = json.loads(raw if isinstance(raw, str) else raw)
    except (json.JSONDecodeError, TypeError):
        print("Could not parse CORTEX state")
        return
    g = data.get("gating", {})
    b = data.get("bidding", {})
    m = data.get("memory", {})
    q = g.get("Q", {})
    eps = g.get("epsilon", "?")
    rep = b.get("reputation", "?")
    episodes = len(m.get("episodes", []))
    print(f"epsilon:     {eps}")
    print(f"reputation:  {rep}")
    print(f"episodes:    {episodes}")
    print(f"Q-table:     {len(q)} states")
    for state, vals in sorted(q.items())[:10]:
        best = max(vals, key=vals.get) if vals else "?"
        print(f"  {state}: best={best} Q={vals.get(best, 0):.3f}")
    if len(q) > 10:
        print(f"  ... and {len(q) - 10} more states")


def cmd_cortex_learnings(n: int = 5):
    r = _req(
        "GET", f"/topics/cortex_state?project={urllib.parse.quote(project_name())}"
    )
    if "error" in r or not r.get("facts"):
        print("No CORTEX state found")
        return
    raw = r["facts"][-1] if isinstance(r["facts"], list) else r.get("text", "")
    try:
        data = json.loads(raw if isinstance(raw, str) else raw)
    except (json.JSONDecodeError, TypeError):
        print("Could not parse CORTEX state")
        return
    episodes = data.get("memory", {}).get("episodes", [])
    if not episodes:
        print("No episodes yet")
        return
    for e in episodes[-n:]:
        ts = time.strftime("%H:%M:%S", time.localtime(e.get("timestamp", 0) / 1000))
        print(
            f"  [{ts}] {e.get('taskTitle', '?')[:40]:40s} agent={e.get('agentId', '?')[:12]} reward={e.get('reward', 0.5):.2f} outcome={e.get('outcome', '?')}"
        )


def cmd_cortex_bid():
    print("Trigger CORTEX bid scan via plugin tool: !cortex_bid")


def cmd_cortex_assign(title: str, task_type: str = "generic", complexity: int = 5):
    r = _req(
        "POST",
        "/tasks",
        {
            "project": project_name(),
            "title": title,
            "description": "",
            "payload": {"type": task_type, "complexity": complexity, "threshold": 0.3},
        },
    )
    if "error" in r:
        print(r["error"], file=sys.stderr)
        sys.exit(1)
    print(f"task created: {r['task_id']}")
    print("Use !cortex_bid tool in opencode to auto-assign")


def cmd_cortex_complete(task_id: str, reward: str = "0.8"):
    reward_f = float(reward)
    r = _req(
        "PATCH",
        f"/tasks/{task_id}",
        {"status": "completed", "payload": {"cortex_reward": reward_f}},
    )
    if "error" in r:
        print(r["error"], file=sys.stderr)
        sys.exit(1)
    print(f"{task_id} completed with reward {reward_f}")


def cmd_cortex_policy():
    r = _req(
        "GET", f"/topics/cortex_state?project={urllib.parse.quote(project_name())}"
    )
    if "error" in r or not r.get("facts"):
        print("No CORTEX state found")
        return
    raw = r["facts"][-1] if isinstance(r["facts"], list) else r.get("text", "")
    try:
        data = json.loads(raw if isinstance(raw, str) else raw)
    except (json.JSONDecodeError, TypeError):
        print("Could not parse CORTEX state")
        return
    q = data.get("gating", {}).get("Q", {})
    if not q:
        print("No learned policy yet")
        return
    print("Learned Q-values (state → action → value):")
    for state, vals in sorted(q.items()):
        sorted_actions = sorted(vals.items(), key=lambda x: -x[1])
        actions_str = ", ".join(f"{a}={v:.3f}" for a, v in sorted_actions)
        print(f"  {state:20s}  →  {actions_str}")


# ── Social / Cultural Learning (CLI) ─────────────────────────


def cmd_teach(project: str, title: str, topic: str, facts: list[str]):
    examples = [f for f in facts if f.startswith("eg:")]
    exercises = [f for f in facts if f.startswith("ex:" or f.startswith("exercise:"))]
    core_facts = [f for f in facts if not f.startswith(("eg:", "ex:", "exercise:"))]
    r = _req(
        "POST",
        "/teach/lesson",
        {
            "teacher_agent": project_name(),
            "project": project,
            "title": title,
            "topic": topic,
            "facts": core_facts,
            "examples": examples,
            "exercises": exercises,
        },
    )
    if "error" in r:
        print(r["error"], file=sys.stderr)
        sys.exit(1)
    lesson = r.get("lesson", {})
    print(f"lesson created: {lesson.get('lesson_id', '?')}")
    print(f"  title: {lesson.get('title', '?')}")
    print(f"  topic: {lesson.get('topic', '?')}")
    print(f"  facts: {len(lesson.get('facts', []))}")
    print(f"  generation: {lesson.get('generation', 0)}")


def cmd_lessons(topic: str = "", project: str = "", min_score: float = 0.0):
    params = []
    if topic:
        params.append(f"topic={urllib.parse.quote(topic)}")
    if project:
        params.append(f"project={urllib.parse.quote(project)}")
    if min_score:
        params.append(f"min_score={min_score}")
    qs = "&".join(params)
    r = _req("GET", f"/teach/lessons{'?' + qs if qs else ''}")
    if "error" in r:
        print(r["error"], file=sys.stderr)
        sys.exit(1)
    lessons = r.get("lessons", [])
    if not lessons:
        print("No lessons found")
        return
    for l in lessons:
        score_bar = "█" * int(l.get("score", 0) * 10) + "░" * (
            10 - int(l.get("score", 0) * 10)
        )
        print(
            f"  {l['lesson_id'][:20]}  gen={l.get('generation', 0)}  [{score_bar}]  {l.get('score', 0):.2f}"
        )
        print(f"    {l.get('title', '?')[:70]}")
        print(f"    topic={l.get('topic', '?')}  students={l.get('n_students', 0)}")
        if l.get("facts"):
            for f in l["facts"][:3]:
                print(f"      • {f[:100]}")
            if len(l["facts"]) > 3:
                print(f"      ... and {len(l['facts']) - 3} more")
        print()


def cmd_lesson(lesson_id: str):
    r = _req("GET", f"/teach/lessons/{urllib.parse.quote(lesson_id)}")
    if "error" in r:
        print(r["error"], file=sys.stderr)
        sys.exit(1)
    l = r.get("lesson", {})
    if not l:
        print("Lesson not found")
        return
    print(f"ID:       {l.get('lesson_id', '?')}")
    print(f"Title:    {l.get('title', '?')}")
    print(f"Topic:    {l.get('topic', '?')}")
    print(f"Gen:      {l.get('generation', 0)}")
    print(f"Score:    {l.get('score', 0):.3f} ({l.get('n_students', 0)} students)")
    print(f"Teacher:  {l.get('teacher_agent', '?')}")
    print(f"Prereqs:  {', '.join(l.get('prerequisites', []))[:80] or 'none'}")
    if l.get("facts"):
        print(f"\nFacts ({len(l['facts'])}):")
        for f in l["facts"]:
            print(f"  • {f[:150]}")
    if l.get("examples"):
        print(f"\nExamples ({len(l['examples'])}):")
        for e in l["examples"]:
            print(f"  • {e[:150]}")
    if l.get("exercises"):
        print(f"\nExercises ({len(l['exercises'])}):")
        for e in l["exercises"]:
            print(f"  • {e[:150]}")
    if l.get("parent_id"):
        print(f"\nParent:   {l['parent_id']}")


def cmd_outcome(lesson_id: str, student: str, success: str):
    succ = success.lower() in ("true", "1", "yes", "pass", "success")
    r = _req(
        "POST",
        f"/teach/lessons/{urllib.parse.quote(lesson_id)}/outcome",
        {
            "student_agent": student,
            "success": succ,
        },
    )
    if "error" in r:
        print(r["error"], file=sys.stderr)
        sys.exit(1)
    print(f"outcome recorded for {lesson_id[:20]}")
    print(f"  score: {r.get('score', 0):.3f}")
    print(f"  students: {r.get('n_students', 0)}")


def cmd_curriculum(project: str, capabilities: str = ""):
    qs = f"project={urllib.parse.quote(project)}"
    if capabilities:
        qs += f"&capabilities={urllib.parse.quote(capabilities)}"
    r = _req("GET", f"/teach/curriculum?{qs}")
    if "error" in r:
        print(r["error"], file=sys.stderr)
        sys.exit(1)
    curriculum = r.get("curriculum", [])
    inherited = r.get("inherited_facts", [])
    print(f"Curriculum for '{project}' ({len(curriculum)} lessons):")
    for i, l in enumerate(curriculum, 1):
        print(
            f"  {i}. [{l.get('topic', '?')}] {l.get('title', '?')[:60]} "
            f"(score={l.get('score', 0):.2f}, gen={l.get('generation', 0)})"
        )
    if inherited:
        print(f"\nInherited facts ({len(inherited)}):")
        for f in inherited:
            print(f"  • {f[:120]}")


def cmd_culture(project: str):
    r = _req("GET", f"/culture/memory?project={urllib.parse.quote(project)}")
    if "error" in r:
        print(r["error"], file=sys.stderr)
        sys.exit(1)
    facts = r.get("facts", [])
    gen = r.get("generation", 0)
    print(f"Cultural memory for '{project}'")
    print(f"  generation: {gen}")
    print(f"  facts:      {len(facts)}")
    for i, f in enumerate(facts, 1):
        topic = f.get("topic", "?")
        imp = f.get("importance", 0)
        agent = f.get("source_agent", "")[:12]
        bar = "█" * int(imp * 10) + "░" * (10 - int(imp * 10))
        print(f"  {i:3d}. [{bar}] [{topic}] {f.get('text', '')[:100]}")
        print(f"       agent={agent} gen={f.get('generation', 0)}")


def cmd_consolidate_culture(project: str):
    r = _req("POST", "/culture/consolidate", {"project": project})
    if "error" in r:
        print(r["error"], file=sys.stderr)
        sys.exit(1)
    print(f"Cultural consolidation for '{project}':")
    print(f"  facts added:   {r.get('facts_added', 0)}")
    print(f"  variations:    {r.get('variations', 0)}")
    print(f"  pruned:        {r.get('pruned', 0)}")
    print(f"  emerged topics: {r.get('emerged', 0)}")
    print(f"  generation:    {r.get('generation', 0)}")


def cmd_evolve(project: str, topic: str = ""):
    body = {"project": project}
    if topic:
        body["topic"] = topic
    r = _req("POST", "/culture/evolve", body)
    if "error" in r:
        print(r["error"], file=sys.stderr)
        sys.exit(1)
    print(f"Cultural evolution for '{project}':")
    print(f"  variations: {r.get('variations', 0)}")
    print(f"  pruned:     {r.get('pruned', 0)}")
    print(f"  emerged:    {r.get('emerged', 0)}")


def cmd_diversity(project: str):
    r = _req("GET", f"/culture/diversity?project={urllib.parse.quote(project)}")
    if "error" in r:
        print(r["error"], file=sys.stderr)
        sys.exit(1)
    print(f"Cultural diversity for '{project}':")
    print(f"  total lessons:   {r.get('total_lessons', 0)}")
    print(f"  unique topics:   {r.get('unique_topics', 0)}")
    print(f"  avg score:       {r.get('avg_score', 0):.3f}")
    print(f"  max generation:  {r.get('generation_max', 0)}")
    dist = r.get("topic_distribution", {})
    if dist:
        print(f"  topic distribution:")
        for t, c in sorted(dist.items(), key=lambda x: -x[1])[:10]:
            print(f"    {t[:30]:30s}  {c} lessons")


def cmd_red_ink():
    r = _req("GET", f"/red-ink/{project_name()}")
    if "error" in r:
        print(r["error"], file=sys.stderr)
        sys.exit(1)
    entries = r.get("entries", [])
    if not entries:
        print("No critical (red ink) entries")
        return
    for e in entries:
        print(f"  [{e.get('id', '?')}] {e['entry']}")


def cmd_promote(index: int):
    r = _req(
        "PUT",
        f"/memory/{project_name()}/priority",
        {"index": index, "priority": "critical"},
    )
    if "error" in r:
        print(r["error"], file=sys.stderr)
        sys.exit(1)
    print(f"entry {index} promoted to critical")


def cmd_demote(index: int):
    r = _req(
        "PUT",
        f"/memory/{project_name()}/priority",
        {"index": index, "priority": "normal"},
    )
    if "error" in r:
        print(r["error"], file=sys.stderr)
        sys.exit(1)
    print(f"entry {index} demoted to normal")


def cmd_set_type(index: int, memory_type: str):
    r = _req(
        "PUT",
        f"/memory/{project_name()}/type",
        {"index": index, "memory_type": memory_type},
    )
    if "error" in r:
        print(r["error"], file=sys.stderr)
        sys.exit(1)
    print(f"entry {index} type set to {memory_type}")


def cmd_classify():
    r = _req("POST", f"/memory/{project_name()}/classify")
    if "error" in r:
        print(r["error"], file=sys.stderr)
        sys.exit(1)
    classified = r.get("classified", 0)
    skipped = r.get("skipped", 0)
    total = r.get("total", 0)
    print(f"classified {classified}/{total} entries ({skipped} kept as temporal)")


def cmd_touch(index: int):
    r = _req(
        "POST",
        f"/memory/{project_name()}/touch",
        {"index": index},
    )
    if "error" in r:
        print(r["error"], file=sys.stderr)
        sys.exit(1)
    print(f"entry {index} touched (strength +0.3)")


def cmd_list_full():
    r = _req("GET", f"/memory/{project_name()}/full")
    if "error" in r:
        print(r["error"], file=sys.stderr)
        sys.exit(1)
    entries = r.get("entries", [])
    if not entries:
        print("MEMORY.md is empty")
        return
    icons = {"critical": "🔴", "important": "🟡", "normal": "⚪"}
    type_icons = {
        "red": "🔴",
        "concept": "💡",
        "procedural": "🔧",
        "temporal": "🕐",
        "relation": "🔗",
    }
    for i, e in enumerate(entries, 1):
        pri = e.get("priority", "normal")
        mt = e.get("memory_type", "temporal")
        pri_icon = icons.get(pri, "⚪")
        type_icon = type_icons.get(mt, "🕐")
        strength = e.get("strength", 1.0)
        text = e.get("entry", "")
        print(f"{i}. {pri_icon}{type_icon} [{pri}/{mt}] s={strength:.2f} {text[:100]}")


def cmd_briefing(task_description: str):
    r = _req(
        "POST",
        "/briefing",
        {"task_description": task_description, "project": project_name()},
    )
    if "error" in r:
        print(r["error"], file=sys.stderr)
        sys.exit(1)
    print(r.get("briefing", ""))
    print(f"\n--- Sources: {r.get('sources', {})}")
    print(f"--- Token estimate: {r.get('token_estimate', 0)}")


def cmd_procedure_list(show_retired: bool = False):
    url = f"/procedural/{project_name()}"
    if show_retired:
        url += "?retired=true"
    r = _req("GET", url)
    if "error" in r:
        print(r["error"], file=sys.stderr)
        sys.exit(1)
    procedures = r.get("procedures", [])
    if not procedures:
        print("No procedures recorded")
        return
    for p in procedures:
        steps = p.get("steps", [])
        if isinstance(steps, str):
            try:
                steps = json.loads(steps)
            except (json.JSONDecodeError, TypeError):
                steps = [steps]
        score = p.get("reinforcement_score", 0)
        succ = p.get("success_count", 0)
        fail = p.get("fail_count", 0)
        retired = " [RETIRED]" if p.get("retired") else ""
        print(
            f"  [{p['id']}] {p.get('task_pattern', '?')} (score={score:.2f}, {succ}✓/{fail}✗){retired}"
        )
        for s in steps[:5]:
            print(f"    → {s}")
        if len(steps) > 5:
            print(f"    ... and {len(steps) - 5} more steps")


def cmd_procedure_add(task_pattern: str, steps: list[str], task_type: str = ""):
    r = _req(
        "POST",
        f"/procedural/{project_name()}",
        {
            "task_pattern": task_pattern,
            "task_type": task_type,
            "steps": steps,
        },
    )
    if "error" in r:
        print(r["error"], file=sys.stderr)
        sys.exit(1)
    print(f"procedure added (id={r.get('id', '?')})")


def cmd_procedure_search(query: str):
    r = _req("POST", f"/procedural/{project_name()}/search", {"query": query})
    if "error" in r:
        print(r["error"], file=sys.stderr)
        sys.exit(1)
    results = r.get("results", [])
    if not results:
        print(f"No matching procedures for: {query}")
        return
    for p in results:
        steps = p.get("steps", [])
        if isinstance(steps, str):
            try:
                steps = json.loads(steps)
            except (json.JSONDecodeError, TypeError):
                steps = [steps]
        score = p.get("reinforcement_score", 0)
        print(f"  [{p['id']}] {p.get('task_pattern', '?')} (score={score:.2f})")
        for s in steps[:5]:
            print(f"    → {s}")


def cmd_consolidation_status():
    r = _req("GET", f"/consolidation/{project_name()}/status")
    if "error" in r:
        print(r["error"], file=sys.stderr)
        sys.exit(1)
    print(f"Consolidation status for '{r.get('project', '?')}':")
    tiers = r.get("tiers", {})
    for tier, count in tiers.items():
        print(f"  {tier}: {count} entries")
    print(f"  total: {r.get('total', 0)}")


def cmd_consolidate(project: str | None = None):
    proj = project or project_name()
    r = _req("POST", f"/consolidation/{urllib.parse.quote(proj)}/trigger")
    if "error" in r:
        print(r["error"], file=sys.stderr)
        sys.exit(1)
    print(f"Consolidation complete for '{r.get('project', '?')}':")
    print(f"  insights created: {r.get('insights_created', 0)}")
    print(f"  consolidated → timeless: {r.get('consolidated_to_timeless', 0)}")
    print(f"  archived temporal: {r.get('archived_temporal', 0)}")
    print(f"  errors: {r.get('errors', 0)}")
    tiers = r.get("tiers", {})
    if tiers:
        print("  tier counts:")
        for tier, count in tiers.items():
            print(f"    {tier}: {count}")


def cmd_costs(days: int = 30):
    r = _req(
        "GET",
        f"/costs/analysis?project={urllib.parse.quote(project_name())}&days={days}",
    )
    if "error" in r:
        print(r["error"], file=sys.stderr)
        sys.exit(1)
    print(f"Memory cost analysis for '{r.get('project', '?')}' (last {days} days):")
    print(f"  records:            {r.get('records', 0)}")
    print(f"  total injected:     {r.get('total_injected', 0)} tokens")
    print(f"  saved (injection):  {r.get('total_saved_injection', 0)} tokens")
    print(f"  saved (forgetting):  {r.get('total_saved_forgetting', 0)} tokens")
    summary = r.get("summary", {})
    if summary:
        print(
            f"  avg injected:       {summary.get('avg_injected', 0):.0f} tokens/session"
        )
    eff = r.get("effectiveness", {})
    if eff:
        print(f"\nEffectiveness:")
        print(f"  outcomes:           {eff.get('total_outcomes', 0)}")
        print(f"  success:            {eff.get('success_count', 0)}")
        print(f"  fail:               {eff.get('fail_count', 0)}")
        print(f"  partial:            {eff.get('partial_count', 0)}")
        print(f"  success rate:       {eff.get('success_rate', 0):.1%}")
        print(
            f"  avg ctx/success:    {eff.get('avg_context_per_success', 0):.0f} tokens"
        )
        print(f"  avg ctx/fail:       {eff.get('avg_context_per_fail', 0):.0f} tokens")
    by_type = r.get("by_context_type", {})
    if by_type:
        print(f"\nBy context type:")
        for ct, stats in by_type.items():
            print(
                f"  {ct}: {stats['count']} records, {stats['tokens_injected']} tokens injected"
            )


def cmd_decay():
    r = _req("GET", f"/memory/{urllib.parse.quote(project_name())}/decay")
    if "error" in r:
        print(r["error"], file=sys.stderr)
        sys.exit(1)
    total = r.get("total", 0)
    fading = r.get("fading", [])
    fading_count = r.get("fading_count", 0)
    stable_count = r.get("stable_count", 0)
    print(f"Decay status: {total} total, {stable_count} stable, {fading_count} fading")
    if fading:
        print("\n  Fading entries:")
        for e in fading:
            text = e.get("entry", "")[:60]
            s = e.get("strength", 1.0)
            p = e.get("priority", "normal")
            print(f"    [{e.get('id', '?')}] s={s:.2f} p={p} {text}")


def cmd_boost(idx: int):
    r = _req("POST", f"/memory/{urllib.parse.quote(project_name())}/boost/{idx}")
    if "error" in r:
        print(r["error"], file=sys.stderr)
        sys.exit(1)
    print(f"Entry {idx} strength boosted")


def cmd_anchors():
    r = _req("GET", f"/anchors/{urllib.parse.quote(project_name())}")
    if "error" in r:
        print(r["error"], file=sys.stderr)
        sys.exit(1)
    print(f"Environmental anchors for '{r.get('project', '?')}':")
    completed = r.get("completed_tasks", [])
    if completed:
        print("\n  Completed tasks:")
        for t in completed:
            print(f"    ✓ {t.get('title', '?')[:80]}")
            if t.get("result"):
                print(f"      result: {t['result'][:100]}")
    active = r.get("active_tasks", [])
    if active:
        print("\n  Active tasks:")
        for t in active:
            assigned = t.get("assigned_to", "unassigned")[:20]
            print(
                f"    → [{t.get('status', '?')}] {t.get('title', '?')[:80]} ({assigned})"
            )
    red_ink = r.get("red_ink", [])
    if red_ink:
        print("\n  Red ink reminders:")
        for entry in red_ink:
            print(f"    🔴 {entry[:100]}")
    commits = r.get("commit_log", [])
    if commits:
        print("\n  Recent commits:")
        for c in commits:
            print(f"    {c[:100]}")
    if not completed and not active and not red_ink and not commits:
        print("  (no anchors yet)")


def cmd_recall_boost(query: str, limit: int = 5):
    r = _req("GET", f"/recall?q={urllib.parse.quote(query)}&limit={limit}")
    if "error" in r:
        print(r["error"], file=sys.stderr)
        sys.exit(1)
    results = r.get("results", [])
    if not results:
        print(f"No matches for: {query}")
        return
    print(f"Found {len(results)} relevant sessions (strength boosted):\n")
    for s in results:
        print(f"  [{s['id'][:12]}...]")
        print(f"  title: {s['title']}")
        print(f"  summary: {s['summary']}\n")


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print((__doc__ or "").strip())
        return
    cmd = args[0]
    if cmd == "init":
        cmd_init()
    elif cmd == "add":
        priority = "normal"
        memory_type = None
        text_args = args[1:]
        while text_args:
            if text_args[0] == "--red":
                priority = "critical"
                text_args = text_args[1:]
            elif text_args[0] == "--important":
                priority = "important"
                text_args = text_args[1:]
            elif text_args[0] == "--type" and len(text_args) > 1:
                memory_type = text_args[1]
                text_args = text_args[2:]
            else:
                break
        if not text_args:
            print(
                "usage: memoria add [--red|--important] [--type <red|concept|procedural|temporal|relation>] <text>",
                file=sys.stderr,
            )
            sys.exit(1)
        cmd_add(" ".join(text_args), priority=priority, memory_type=memory_type)
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
    elif cmd == "context":
        if len(args) < 2:
            print("usage: memoria context <query>", file=sys.stderr)
            sys.exit(1)
        cmd_context(" ".join(args[1:]))
    elif cmd == "reindex":
        cmd_reindex()
    elif cmd == "papers":
        if len(args) > 1 and args[1] == "rescan":
            cmd_papers_rescan()
        else:
            print("usage: memoria papers rescan", file=sys.stderr)
            sys.exit(1)
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
                print(
                    "usage: memoria topic edit <name> <index> <text>", file=sys.stderr
                )
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
            print(
                "usage: memoria chitchat <history|rooms|consolidate> [...]",
                file=sys.stderr,
            )
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
    elif cmd == "clients":
        r = _req("GET", "/clients")
        if "error" in r:
            print(r["error"], file=sys.stderr)
            sys.exit(1)
        clients = r.get("clients", [])
        if not clients:
            print("No clients registered")
            return
        for c in clients:
            print(
                f"  {c['name']:<15} {c['host']:<20} {c.get('user', 'daivolt'):<10} {c.get('ssh_key', '~/.ssh/id_memoria')}"
            )
    elif cmd == "client":
        if len(args) < 2:
            print(
                "usage: memoria client <name> <host> [ssh_key] [user]", file=sys.stderr
            )
            print("       memoria client remove <name>", file=sys.stderr)
            sys.exit(1)
        if args[1] == "remove":
            if len(args) < 3:
                print("usage: memoria client remove <name>", file=sys.stderr)
                sys.exit(1)
            r = _req("DELETE", f"/clients/{urllib.parse.quote(args[2])}")
            if "error" in r:
                print(r["error"], file=sys.stderr)
                sys.exit(1)
            print(f"client '{args[2]}' removed")
        else:
            name = args[1]
            host = args[2] if len(args) > 2 else ""
            ssh_key = args[3] if len(args) > 3 else "~/.ssh/id_memoria"
            user = args[4] if len(args) > 4 else "daivolt"
            r = _req(
                "POST",
                "/clients",
                {"name": name, "host": host, "ssh_key": ssh_key, "user": user},
            )
            if "error" in r:
                print(r["error"], file=sys.stderr)
                sys.exit(1)
            print(f"client '{name}' {r.get('action', 'registered')}")
    elif cmd == "push-clients":
        print("Pushing updates to all clients ...")
        r = _req("POST", "/clients/push")
        if "error" in r:
            print(r["error"], file=sys.stderr)
            sys.exit(1)
        for result in r.get("results", []):
            status = result["status"]
            name = result["name"]
            if status == "ok":
                print(f"  {name}: OK")
            elif status == "skip":
                print(f"  {name}: SKIP ({result.get('error', '')})")
            else:
                print(f"  {name}: {status.upper()} — {result.get('error', '')}")
        print(f"Pushed {r.get('pushed', 0)}/{r.get('total', 0)} clients")
    elif cmd == "peers":
        r = _req("GET", "/federation/peers")
        if "error" in r:
            print(r["error"], file=sys.stderr)
            sys.exit(1)
        peers = r.get("peers", [])
        if not peers:
            print("No federation peers registered")
            return
        for p in peers:
            key = " (key)" if p.get("api_key") else ""
            print(f"  {p['name']:<15} {p['url']:<30}{key}")
    elif cmd == "peer":
        if len(args) < 2:
            print("usage: memoria peer <name> <url> [api_key]", file=sys.stderr)
            print("       memoria peer remove <name>", file=sys.stderr)
            sys.exit(1)
        if args[1] == "remove":
            if len(args) < 3:
                print("usage: memoria peer remove <name>", file=sys.stderr)
                sys.exit(1)
            r = _req("DELETE", f"/federation/peers/{urllib.parse.quote(args[2])}")
            if "error" in r:
                print(r["error"], file=sys.stderr)
                sys.exit(1)
            print(f"peer '{args[2]}' removed")
        else:
            name = args[1]
            url = args[2] if len(args) > 2 else ""
            api_key = args[3] if len(args) > 3 else ""
            r = _req(
                "POST",
                "/federation/peers",
                {"name": name, "url": url, "api_key": api_key},
            )
            if "error" in r:
                print(r["error"], file=sys.stderr)
                sys.exit(1)
            print(f"peer '{name}' {r.get('action', 'registered')}")
    elif cmd == "sync":
        if len(args) < 3:
            print(
                "usage: memoria sync <pull|push|full|all> [peer] [types]",
                file=sys.stderr,
            )
            sys.exit(1)
        sub = args[1]
        peer_name = args[2] if len(args) > 2 else ""
        types = args[3].split(",") if len(args) > 3 else []
        if sub == "all":
            r = _req("POST", "/sync/all", {"peer": "", "types": types})
        elif sub in ("pull", "push", "full"):
            if not peer_name:
                print(f"usage: memoria sync {sub} <peer> [types]", file=sys.stderr)
                sys.exit(1)
            r = _req(
                "POST",
                f"/sync/{sub}",
                {"peer": peer_name, "types": types, "full": sub == "full"},
            )
        else:
            print(f"unknown sync subcommand: {sub}", file=sys.stderr)
            sys.exit(1)
        if "error" in r:
            print(r["error"], file=sys.stderr)
            sys.exit(1)
        if sub == "all":
            for res in r.get("results", []):
                peer_res = res.get("result", {})
                status = "OK" if peer_res.get("ok") else "FAIL"
                print(f"  {res['peer']}: {status}")
            print(f"Synced with {len(r.get('results', []))} peers")
        elif sub == "full":
            print(f"Sync with '{peer_name}' complete")
            print(f"  pulled: {r.get('pull', {})}")
            print(f"  pushed: {r.get('push', {})}")
        else:
            print(f"{sub.capitalize()} from '{peer_name}' complete")
            print(f"  applied: {r.get('applied', {})}")
    elif cmd == "cortex":
        if len(args) < 2:
            print(
                "usage: memoria cortex <status|learnings|bid|assign|complete|policy> [...]",
                file=sys.stderr,
            )
            sys.exit(1)
        sub = args[1]
        if sub == "status":
            cmd_cortex_status()
        elif sub == "learnings":
            cmd_cortex_learnings(int(args[2]) if len(args) > 2 else 5)
        elif sub == "bid":
            cmd_cortex_bid()
        elif sub == "assign":
            if len(args) < 3:
                print(
                    "usage: memoria cortex assign <title> [type] [complexity]",
                    file=sys.stderr,
                )
                sys.exit(1)
            t = args[2]
            typ = args[3] if len(args) > 3 else "generic"
            comp = int(args[4]) if len(args) > 4 else 5
            cmd_cortex_assign(t, typ, comp)
        elif sub == "complete":
            if len(args) < 3:
                print(
                    "usage: memoria cortex complete <task-id> [reward]", file=sys.stderr
                )
                sys.exit(1)
            cmd_cortex_complete(args[2], args[3] if len(args) > 3 else "0.8")
        elif sub == "policy":
            cmd_cortex_policy()
        else:
            print(f"unknown cortex subcommand: {sub}", file=sys.stderr)
            sys.exit(1)
    elif cmd == "teach":
        if len(args) < 5:
            print(
                "usage: memoria teach <project> <title> <topic> <fact> [...]",
                file=sys.stderr,
            )
            sys.exit(1)
        cmd_teach(args[1], args[2], args[3], args[4:])
    elif cmd == "lessons":
        topic = args[1] if len(args) > 1 else ""
        project = args[2] if len(args) > 2 else ""
        min_score = float(args[3]) if len(args) > 3 else 0.0
        cmd_lessons(topic, project, min_score)
    elif cmd == "lesson":
        if len(args) < 2:
            print("usage: memoria lesson <lesson-id>", file=sys.stderr)
            sys.exit(1)
        cmd_lesson(args[1])
    elif cmd == "outcome":
        if len(args) < 4:
            print(
                "usage: memoria outcome <lesson-id> <student> <success>",
                file=sys.stderr,
            )
            sys.exit(1)
        cmd_outcome(args[1], args[2], args[3])
    elif cmd == "curriculum":
        if len(args) < 2:
            print("usage: memoria curriculum <project> [capabilities]", file=sys.stderr)
            sys.exit(1)
        cmd_curriculum(args[1], args[2] if len(args) > 2 else "")
    elif cmd == "culture":
        if len(args) < 2:
            print("usage: memoria culture <project>", file=sys.stderr)
            sys.exit(1)
        cmd_culture(args[1])
    elif cmd == "consolidate":
        if len(args) >= 2:
            cmd_consolidate(args[1])
        else:
            cmd_consolidate()
    elif cmd == "evolve":
        if len(args) < 2:
            print("usage: memoria evolve <project> [topic]", file=sys.stderr)
            sys.exit(1)
        cmd_evolve(args[1], args[2] if len(args) > 2 else "")
    elif cmd == "diversity":
        if len(args) < 2:
            print("usage: memoria diversity <project>", file=sys.stderr)
            sys.exit(1)
        cmd_diversity(args[1])
    elif cmd == "red-ink":
        cmd_red_ink()
    elif cmd == "promote":
        if len(args) < 2:
            print("usage: memoria promote <index>", file=sys.stderr)
            sys.exit(1)
        cmd_promote(int(args[1]))
    elif cmd == "demote":
        if len(args) < 2:
            print("usage: memoria demote <index>", file=sys.stderr)
            sys.exit(1)
        cmd_demote(int(args[1]))
    elif cmd == "type":
        if len(args) < 3:
            print(
                "usage: memoria type <index> <red|concept|procedural|temporal|relation>",
                file=sys.stderr,
            )
            sys.exit(1)
        cmd_set_type(int(args[1]), args[2])
    elif cmd == "classify":
        cmd_classify()
    elif cmd == "list-full":
        cmd_list_full()
    elif cmd == "touch":
        if len(args) < 2:
            print("usage: memoria touch <index>", file=sys.stderr)
            sys.exit(1)
        cmd_touch(int(args[1]))
    elif cmd == "briefing":
        if len(args) < 2:
            print("usage: memoria briefing <task description>", file=sys.stderr)
            sys.exit(1)
        cmd_briefing(" ".join(args[1:]))
    elif cmd == "procedure":
        if len(args) < 2:
            print("usage: memoria procedure <list|add|search> ...", file=sys.stderr)
            sys.exit(1)
        sub = args[1]
        if sub == "list":
            show_retired = "--retired" in args or "-r" in args
            cmd_procedure_list(show_retired=show_retired)
        elif sub == "add":
            if len(args) < 4:
                print(
                    "usage: memoria procedure add <task_pattern> <step1> <step2> ...",
                    file=sys.stderr,
                )
                sys.exit(1)
            cmd_procedure_add(args[2], args[3:])
        elif sub == "search":
            if len(args) < 3:
                print("usage: memoria procedure search <query>", file=sys.stderr)
                sys.exit(1)
            cmd_procedure_search(" ".join(args[2:]))
        else:
            print(f"unknown procedure subcommand: {sub}", file=sys.stderr)
            sys.exit(1)
    elif cmd == "consolidation":
        if len(args) < 2:
            cmd_consolidation_status()
        elif args[1] == "status":
            cmd_consolidation_status()
        elif args[1] == "trigger":
            cmd_consolidate()
        else:
            print(f"unknown consolidation subcommand: {args[1]}", file=sys.stderr)
            sys.exit(1)
    elif cmd == "costs":
        cmd_costs(int(args[1]) if len(args) > 1 else 30)
    elif cmd == "decay":
        cmd_decay()
    elif cmd == "boost":
        if len(args) < 2:
            print("usage: memoria boost <index>", file=sys.stderr)
            sys.exit(1)
        cmd_boost(int(args[1]))
    elif cmd == "anchors":
        cmd_anchors()
    else:
        print(f"unknown: {cmd}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
