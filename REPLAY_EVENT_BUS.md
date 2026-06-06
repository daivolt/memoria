# Replay Event Bus Interface — Audit & Design

## Current Replay Components

| Component | File:Line | Mechanism | Trigger |
|-----------|-----------|-----------|---------|
| `HippocampalReplay` | `cortex.py:411` | EVB-prioritized buffer → Dyna Q-learning | Manual `replay_step()` call |
| `_awake_replay_loop()` | `memoriad_global.py:751` | 60s timer → replay_step on all projects | Timer (60s) |
| `_deep_consolidate()` | `memoriad_global.py:600` | Offline clustering + replay + pruning | Timer (6h) |
| `_consolidate_chitchat()` | `memoriad_global.py:452` | Keyword extraction → topic proposals | Counter threshold (20 msgs) |
| `record_outcome()` | `cortex.py:951` | Adds experience to replay buffer | Task completion with reward |

## Existing Event Sources

1. **Kill audit** (`/tmp/memoria_kill_audit.jsonl`): agent death events
2. **Task board** (`/var/tmp/memoria/tasks/*.json`): task status transitions
3. **Agent registry** (`/var/tmp/memoria/agents/*.json`): agent lifecycle (register/heartbeat/deregister)
4. **Chitchat inbox** (`/var/tmp/memoria/chitchat/*/inbox.jsonl`): chat message arrival
5. **CORTEX auctions**: assignment outcomes (`/cortex/complete`)

## Event Bus Interface (Proposed)

```python
# memoriad_global.py — add EventBus class

class ReplayEvent:
    """All replay-triggering events normalize to this shape."""
    source: str        # e.g. "kill_audit", "task_complete", "chitchat", "idle"
    timestamp: float
    payload: dict      # event-specific data
    priority: float    # 0.0–1.0, for EVB-weighted scheduling

class EventDrivenReplayScheduler:
    """
    Watches event sources and feeds into HippocampalReplay.
    
    Event → Replay mapping:
      kill_audit line appended
        → create replay exp: state="{agent}_killed", reward=-0.3
        → if 3+ kills in 60s: create high-priority consolidation task
    
      task completed with reward
        → replay.add_experience(state, agent, reward)
        → if reward >= 0.8: immediate replay_step (hot learning)
    
      chitchat topic consolidation
        → replay.add_experience("chitchat_{topic}", "consolidate", 0.5)
    
      idle (no tasks for 30s, agents online)
        → trigger awake replay across all buffers
    """

    def __init__(self):
        self._kill_pos = 0          # file watch cursor
        self._last_task_activity = time.time()
        self._watchers: list[asyncio.Task] = []

    async def watch_kill_audit(self, path="/tmp/memoria_kill_audit.jsonl"):
        """Tail JSONL → emit ReplayEvent on new lines."""
        ...

    async def watch_task_board(self, interval=2):
        """Poll for status transitions → emit on complete/fail."""
        ...

    async def watch_idle(self, idle_threshold=30):
        """If no task activity > threshold and agents exist → emit idle event."""
        ...
```

## Integration Points

| Hook | Location | What to add |
|------|----------|-------------|
| `_awake_replay_loop()` | `memoriad_global.py:751` | Replace with `EventDrivenReplayScheduler` instance |
| `record_outcome()` | `cortex.py:959` | After `add_experience()`, if reward >= 0.8 call `replay_step()` immediately |
| Incoming kill audit | `/tmp/memoria_kill_audit.jsonl` | Watch with `inotify` or polling tail |
| `_cortex_auction_loop()` | `memoriad_global.py:718` | Track `_last_task_activity` timestamp |

## Implementation Order

1. Add `EventDrivenReplayScheduler` class to `memoriad_global.py`
2. Wire it into the lifespan (replace `_awake_replay_loop`)
3. Add `/cortex/replay` REST endpoint for manual/ad-hoc replay triggers
4. Add kill audit watcher as an asyncio task
5. Add task board watcher as an asyncio task
6. Add idle detection
7. Deploy: `sudo systemctl restart memoria`

## Dependencies

- `pyinotify` or `asyncio` polling (no new deps needed — polling is already the pattern)
- File: `/tmp/memoria_kill_audit.jsonl` (already exists, 3 entries)
- REST API: `POST /cortex/complete` (existing) + `POST /cortex/bid` (existing)
