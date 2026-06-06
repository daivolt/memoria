"""
cortex — Brain-inspired autonomous task allocation engine for memoria.

Architecture (neuroscience mapping):
  PFC-BG Gating       → Q-learning with ε-greedy arbitration
  Basal Ganglia        → Input/output gating for task selection
  Dopaminergic RPE     → Reward Prediction Error updates
  Hippocampus          → Episodic memory + similarity recall
  Prefrontal Cortex    → Task decomposition + priority arbitration
"""

import json
import math
import random
import time
from pathlib import Path
from typing import Any

CORTEX_DIR = Path("/var/tmp/memoria/cortex")
CORTEX_DIR.mkdir(parents=True, exist_ok=True)


# ── PFC-BG Gating (Q-learning with ε-greedy) ──────────────────

class PFCBGGating:
    """Prefrontal Cortex - Basal Ganglia gating mechanism.
    
    State:  (task_type, complexity_bin)
    Action: agent_id or capability_name
    """
    
    def __init__(self, project: str):
        self.project = project
        self.Q: dict[str, dict[str, float]] = {}
        self.N: dict[str, dict[str, int]] = {}
        self.epsilon = 0.3          # exploration rate
        self.alpha = 0.15           # learning rate
        self.gamma = 0.9            # discount factor
        self.alpha_decay = 0.995    # per-update decay
        self.epsilon_decay = 0.999  # per-update decay
        self.min_epsilon = 0.05
        self._load()
    
    def _path(self) -> Path:
        return CORTEX_DIR / f"gating_{self.project}.json"
    
    def _load(self):
        p = self._path()
        if p.exists():
            try:
                data = json.loads(p.read_text())
                self.Q = data.get("Q", {})
                self.N = data.get("N", {})
                self.epsilon = data.get("epsilon", 0.3)
                self.alpha = data.get("alpha", 0.15)
            except (json.JSONDecodeError, OSError):
                pass
    
    def save(self):
        self._path().write_text(json.dumps({
            "Q": self.Q,
            "N": self.N,
            "epsilon": self.epsilon,
            "alpha": self.alpha,
        }, indent=2))
    
    def state_key(self, task_type: str, complexity: int) -> str:
        bin_idx = min(complexity // 2, 4)
        return f"{task_type or 'generic'}_c{bin_idx}"
    
    def get_q(self, state: str, action: str) -> float:
        return self.Q.get(state, {}).get(action, 0.5)
    
    def set_q(self, state: str, action: str, value: float):
        if state not in self.Q:
            self.Q[state] = {}
            self.N[state] = {}
        self.Q[state][action] = value
        if action not in self.N[state]:
            self.N[state][action] = 0
        self.N[state][action] += 1
    
    def select_action(self, state: str, available_actions: list[str]) -> str | None:
        if not available_actions:
            return None
        q = self.Q.get(state, {})
        for a in available_actions:
            if a not in q:
                q[a] = 0.5
                if a not in self.N.get(state, {}):
                    self.N.setdefault(state, {})[a] = 0
        if random.random() < self.epsilon:
            return random.choice(available_actions)
        best = max(available_actions, key=lambda a: q.get(a, 0.5))
        return best
    
    def should_gate(self, state: str, action: str, threshold: float = 0.3) -> bool:
        q_val = self.get_q(state, action)
        return q_val >= threshold
    
    def update(self, state: str, action: str, reward: float):
        q_old = self.get_q(state, action)
        count = self.N.get(state, {}).get(action, 0)
        lr = self.alpha / (1 + 0.02 * count)  # decaying learning rate
        rpe = reward - q_old  # Reward Prediction Error (dopaminergic)
        q_new = q_old + lr * rpe
        self.set_q(state, action, q_new)
        self.epsilon = max(self.min_epsilon, self.epsilon * self.epsilon_decay)
        self.alpha = max(0.01, self.alpha * self.alpha_decay)
        self.save()
        return rpe


# ── Hippocampal Episodic Memory ────────────────────────────────

class HippocampalMemory:
    """Episodic memory with similarity recall (hippocampal replay)."""
    
    def __init__(self, project: str):
        self.project = project
        self.episodes: list[dict] = []
        self.max_episodes = 500
        self._load()
    
    def _path(self) -> Path:
        return CORTEX_DIR / f"hippocampus_{self.project}.json"
    
    def _load(self):
        p = self._path()
        if p.exists():
            try:
                data = json.loads(p.read_text())
                self.episodes = data.get("episodes", [])
            except (json.JSONDecodeError, OSError):
                pass
    
    def save(self):
        self._path().write_text(json.dumps({
            "episodes": self.episodes[-self.max_episodes:],
        }, indent=2))
    
    def store(self, task_id: str, agent_id: str, task_title: str,
              outcome: str, reward: float, meta: dict | None = None):
        entry = {
            "task_id": task_id,
            "agent_id": agent_id,
            "task_title": task_title,
            "outcome": outcome,
            "reward": reward,
            "meta": meta or {},
            "timestamp": time.time(),
        }
        self.episodes.append(entry)
        if len(self.episodes) > self.max_episodes:
            self.episodes = self.episodes[-self.max_episodes:]
        self.save()
    
    def recall_similar(self, task_type: str, complexity: int, top_k: int = 3) -> list[dict]:
        scored = []
        for ep in self.episodes:
            meta = ep.get("meta", {}) or {}
            score = 0.0
            if meta.get("type") == task_type:
                score += 0.5
            c = meta.get("complexity", 5)
            score += 0.3 * (1 - abs(c - complexity) / 10)
            score += 0.2 * ep.get("reward", 0.5)
            scored.append((score, ep))
        scored.sort(key=lambda x: -x[0])
        return [ep for _, ep in scored[:top_k]]
    
    def avg_reward_for_agent(self, agent_id: str) -> float:
        matched = [e for e in self.episodes if e.get("agent_id") == agent_id]
        if not matched:
            return 0.5
        return sum(e.get("reward", 0.5) for e in matched) / len(matched)
    
    def recent(self, n: int = 5) -> list[dict]:
        return self.episodes[-n:]


# ── Auction Coordinator (Basal Ganglia gating) ────────────────

class AuctionCoordinator:
    """Market-based task allocation with sealed-bid auction."""
    
    def __init__(self, project: str):
        self.project = project
        self.reputation: dict[str, float] = {}  # agent_id → reputation
        self._load()
    
    def _path(self) -> Path:
        return CORTEX_DIR / f"auction_{self.project}.json"
    
    def _load(self):
        p = self._path()
        if p.exists():
            try:
                data = json.loads(p.read_text())
                self.reputation = data.get("reputation", {})
            except (json.JSONDecodeError, OSError):
                pass
    
    def save(self):
        self._path().write_text(json.dumps({
            "reputation": self.reputation,
        }, indent=2))
    
    def get_reputation(self, agent_id: str) -> float:
        return self.reputation.get(agent_id, 0.5)
    
    def compute_bid(self, agent_id: str, capabilities: list[str],
                    task_type: str, complexity: int) -> dict:
        task_type_lower = (task_type or "generic").lower()
        capability_match = 0.3
        for cap in capabilities:
            cl = cap.lower()
            if task_type_lower in cl or cl in task_type_lower:
                capability_match = 0.9
                break
        confidence = capability_match * (1 - abs(complexity - 5) / 10)
        rep = self.get_reputation(agent_id)
        score = 0.6 * confidence + 0.3 * rep + 0.1 * random.random()
        return {
            "agent_id": agent_id,
            "score": round(score, 3),
            "capability_match": round(capability_match, 3),
            "reputation": round(rep, 3),
            "confidence": round(confidence, 3),
        }
    
    def select_winner(self, bids: list[dict]) -> dict | None:
        if not bids:
            return None
        bids.sort(key=lambda b: -b["score"])
        winner = bids[0]
        if winner["score"] >= 0.3:
            return winner
        return None
    
    def update_reputation(self, agent_id: str, reward: float):
        old = self.get_reputation(agent_id)
        self.reputation[agent_id] = min(1.0, max(0.0, old + 0.05 * (reward - 0.5)))
        self.save()


# ── Prefrontal Task Decomposer ────────────────────────────────

class PrefrontalDecomposer:
    """Task decomposition and priority arbitration."""
    
    @staticmethod
    def decompose(title: str, description: str) -> list[dict]:
        lines = [l for l in description.split("\n") if l.strip()]
        if len(lines) <= 1:
            return [{"step": 1, "description": title, "depends_on": []}]
        subtasks = []
        for i, line in enumerate(lines):
            subtasks.append({
                "step": i + 1,
                "description": line.strip().lstrip("- ").lstrip("* "),
                "depends_on": [i] if i > 0 else [],
            })
        return subtasks
    
    @staticmethod
    def priority(task_type: str, complexity: int, queue_length: int) -> int:
        base = {"bugfix": 10, "critical": 9, "deploy": 8,
                "coding": 6, "research": 4, "review": 3,
                "documentation": 2, "generic": 5}
        base_p = base.get(task_type, 5)
        complexity_bonus = complexity // 2
        queue_penalty = min(queue_length, 5)
        return max(1, min(20, base_p + complexity_bonus - queue_penalty))


# ── Main CORTEX Engine ────────────────────────────────────────

class CortexEngine:
    """Brain-inspired autonomous task allocation engine."""
    
    def __init__(self, project: str):
        self.project = project
        self.gating = PFCBGGating(project)
        self.hippocampus = HippocampalMemory(project)
        self.auction = AuctionCoordinator(project)
        self.decomposer = PrefrontalDecomposer()
    
    def process_task_assignment(self, task: dict, agents: list[dict]) -> dict | None:
        meta = task.get("payload", {}) or {}
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except (json.JSONDecodeError, TypeError):
                meta = {}
        task_type = meta.get("type", "generic")
        complexity = int(meta.get("complexity", 5))
        state = self.gating.state_key(task_type, complexity)
        similar = self.hippocampus.recall_similar(task_type, complexity)
        bids = []
        for agent in agents:
            aid = agent.get("id", "")
            caps = agent.get("capabilities", ["general"])
            if not isinstance(caps, list):
                caps = ["general"]
            bid = self.auction.compute_bid(aid, caps, task_type, complexity)
            action_scores = [self.gating.get_q(state, c) for c in caps]
            gate_signal = max(action_scores) if action_scores else 0.5
            if gate_signal >= 0.2:
                bids.append(bid)
        if not bids:
            return None
        winner = self.auction.select_winner(bids)
        if winner is None:
            return None
        return {
            "task_id": task["id"],
            "winner": winner,
            "state": state,
            "similar_episodes": len(similar),
            "gate_confidence": max(self.gating.get_q(state, a) for a in self.gating.Q.get(state, {})),
        }
    
    def record_outcome(self, task_id: str, agent_id: str, task_title: str,
                       task_type: str, complexity: int, reward: float):
        state = self.gating.state_key(task_type, complexity)
        rpe = self.gating.update(state, agent_id, reward)
        self.auction.update_reputation(agent_id, reward)
        self.hippocampus.store(task_id, agent_id, task_title,
                               "completed" if reward >= 0.5 else "failed",
                               reward, {"type": task_type, "complexity": complexity})
        return rpe
    
    def get_status(self) -> dict:
        q_summary = {}
        for state, vals in self.gating.Q.items():
            if vals:
                best = max(vals, key=vals.get)
                q_summary[state] = {
                    "best_action": best,
                    "best_q": round(vals[best], 3),
                    "visits": sum(self.gating.N.get(state, {}).values()),
                }
        return {
            "project": self.project,
            "epsilon": round(self.gating.epsilon, 4),
            "q_table_size": len(self.gating.Q),
            "q_summary": q_summary,
            "episodes": len(self.hippocampus.episodes),
            "agents_tracked": len(self.auction.reputation),
            "avg_reputations": {a: round(r, 3) for a, r in self.auction.reputation.items()},
        }


# ── Global registry of engines ────────────────────────────────

_engines: dict[str, CortexEngine] = {}

def get_engine(project: str) -> CortexEngine:
    if project not in _engines:
        _engines[project] = CortexEngine(project)
    return _engines[project]
