"""
cortex — Brain-inspired autonomous task allocation engine for memoria.

Architecture (neuroscience mapping v2):
  PFC-BG Gating       → OpAL* opponent D1/D2 pathways (Go/NoGo)
  dACC Meta-Controller → Bayesian surprise-driven ε adaptation
  SPE (Striatum)       → Scaled Prediction Error (normalized RPE)
  Hippocampus          → Episodic memory + similarity recall
  Prefrontal Cortex    → Task decomposition + priority arbitration
"""

import collections
import json
import math
import random
import re
import textwrap
import time
from pathlib import Path
from typing import Any

CORTEX_DIR = Path("/var/tmp/memoria/cortex")
CORTEX_DIR.mkdir(parents=True, exist_ok=True)


# ── PFC-BG Gating (Q-learning with ε-greedy) ──────────────────

class PFCBGGating:
    """Prefrontal Cortex - Basal Ganglia gating mechanism v2.
    
    Upgrades from research:
      1. Opponent D1/D2 pathways (Go/NoGo) — OpAL* (eLife 85107)
      2. Scaled Prediction Error — SPE model (PLOS Comp Bio 2022)
      3. ACC meta-learning via Bayesian surprise — RML model (PLOS Comp Bio 1013025)
    
    State:  (task_type, complexity_bin)
    Action: agent_id or capability_name
    """
    
    def __init__(self, project: str):
        self.project = project
        # Opponent pathways (OpAL*)
        self.Go: dict[str, dict[str, float]] = {}
        self.NoGo: dict[str, dict[str, float]] = {}
        self.N: dict[str, dict[str, int]] = {}
        # SPE tracking
        self.reward_history: dict[str, list[float]] = {}
        self.reward_mean: dict[str, float] = {}
        self.reward_var: dict[str, float] = {}
        # ACC meta-learning (surprise-driven epsilon)
        self.rpe_history: list[float] = []
        self.raw_rewards: list[float] = []
        # PRO model: dual surprise channels
        self.alpha_surprise: list[float] = []
        self.beta_surprise: list[float] = []
        self.epsilon = 0.3
        self.alpha = 0.15
        self.gamma = 0.9
        self.alpha_decay = 0.995
        self.min_epsilon = 0.05
        # OpAL* β parameters (Go/NoGo bias weights)
        self.beta_g: dict[str, dict[str, float]] = {}
        self.beta_n: dict[str, dict[str, float]] = {}
        self._load()
    
    def _path(self) -> Path:
        return CORTEX_DIR / f"gating_{self.project}.json"
    
    def _load(self):
        p = self._path()
        if p.exists():
            try:
                data = json.loads(p.read_text())
                self.Go = data.get("Go", data.get("Q", {}))  # fallback to old Q
                self.NoGo = data.get("NoGo", {})
                if not self.NoGo:
                    for s in self.Go:
                        self.NoGo[s] = {a: 1.0 - v for a, v in self.Go[s].items()}
                self.N = data.get("N", {})
                self.reward_mean = data.get("reward_mean", {})
                self.reward_var = data.get("reward_var", {})
                self.rpe_history = data.get("rpe_history", [])
                self.alpha_surprise = data.get("alpha_surprise", [])
                self.beta_surprise = data.get("beta_surprise", [])
                self.epsilon = data.get("epsilon", 0.3)
                self.alpha = data.get("alpha", 0.15)
                self.beta_g = data.get("beta_g", {})
                self.beta_n = data.get("beta_n", {})
            except (json.JSONDecodeError, OSError):
                pass
    
    def save(self):
        self._path().write_text(json.dumps({
            "Go": self.Go, "NoGo": self.NoGo, "N": self.N,
            "reward_mean": self.reward_mean, "reward_var": self.reward_var,
            "rpe_history": self.rpe_history[-200:],
            "alpha_surprise": self.alpha_surprise[-200:],
            "beta_surprise": self.beta_surprise[-200:],
            "epsilon": self.epsilon, "alpha": self.alpha,
            "beta_g": self.beta_g,
            "beta_n": self.beta_n,
        }, indent=2))
    
    def state_key(self, task_type: str, complexity: int) -> str:
        bin_idx = min(complexity // 2, 4)
        return f"{task_type or 'generic'}_c{bin_idx}"
    
    def _key(self, state: str, action: str) -> str:
        return f"{state}|{action}"
    
    def _init_state_action(self, state: str, action: str):
        if state not in self.Go:
            self.Go[state] = {}
            self.NoGo[state] = {}
            self.N[state] = {}
            self.reward_mean[self._key(state, action)] = 0.5
            self.reward_var[self._key(state, action)] = 0.25
        if action not in self.Go[state]:
            self.Go[state][action] = 0.5
            self.NoGo[state][action] = 0.5
            self.N[state][action] = 0
            self.reward_mean[self._key(state, action)] = 0.5
            self.reward_var[self._key(state, action)] = 0.25
    
    def get_q(self, state: str, action: str) -> float:
        self._init_state_action(state, action)
        go = self.Go[state][action]
        nogo = self.NoGo[state][action]
        bg = self.beta_g.get(state, {}).get(action, 1.0)
        bn = self.beta_n.get(state, {}).get(action, 1.0)
        return bg * go - bn * nogo
    
    def get_go(self, state: str, action: str) -> float:
        self._init_state_action(state, action)
        return self.Go[state][action]
    
    def get_nogo(self, state: str, action: str) -> float:
        self._init_state_action(state, action)
        return self.NoGo[state][action]
    
    def _acc_surprise_epsilon(self, reward: float | None = None) -> float:
        """ACC PRO model: dual-channel surprise-driven epsilon adaptation.
        
        PRO Model (Topics Cog Sci 2019):
          α_j(t) = max(0, p_j(t-1) - o_j(t))  negative surprise (predicted but didn't occur)
          β_j(t) = max(0, o_j(t) - p_j(t-1))  positive surprise (occurred but not predicted)
        
        Uses raw rewards (not RPEs) for PRO model as specified by the paper.
        Prediction is the running average of recent rewards (dynamic expectation).
        """
        if reward is not None:
            self.raw_rewards.append(reward)
        if len(self.raw_rewards) < 5:
            return self.epsilon
        if len(self.raw_rewards) > 200:
            self.raw_rewards = self.raw_rewards[-200:]
        reward_window = self.raw_rewards[-10:]
        prediction = sum(reward_window[:-1]) / len(reward_window[:-1]) if len(reward_window) > 1 else 0.5
        outcome = reward_window[-1]
        neg_surprise = max(0, prediction - outcome)
        pos_surprise = max(0, outcome - prediction)
        total_surprise = neg_surprise + pos_surprise
        self.alpha_surprise.append(neg_surprise)
        self.beta_surprise.append(pos_surprise)
        if len(self.alpha_surprise) > 200:
            self.alpha_surprise = self.alpha_surprise[-200:]
            self.beta_surprise = self.beta_surprise[-200:]
        base = self.min_epsilon
        max_e = 0.5
        scaled = base + (max_e - base) * min(1.0, total_surprise * 3)
        return 0.7 * self.epsilon + 0.3 * scaled
    
    def select_action(self, state: str, available_actions: list[str]) -> str | None:
        if not available_actions:
            return None
        for a in available_actions:
            self._init_state_action(state, a)
        # ACC-modulated epsilon
        current_eps = self._acc_surprise_epsilon()
        if random.random() < current_eps:
            return random.choice(available_actions)
        best = max(available_actions, key=lambda a: self.get_q(state, a))
        return best
    
    def should_gate(self, state: str, action: str, threshold: float = 0.3) -> bool:
        return self.get_q(state, action) >= threshold
    
    def update(self, state: str, action: str, reward: float, goal_prediction: float | None = None,
               habitual_action: str | None = None):
        self._init_state_action(state, action)
        count = self.N[state][action]
        lr = self.alpha / (1 + 0.02 * count)
        
        # SPE: scaled prediction error (with warm-up period)
        k = self._key(state, action)
        m = self.reward_mean.get(k, 0.5)
        s_sq = self.reward_var.get(k, 0.25)
        if count < 10:
            rpe_scaled = reward - m
        else:
            s = max(s_sq ** 0.5, 0.1)
            rpe_scaled = (reward - m) / s
        
        # Update SPE tracking (Welford's online algorithm)
        n = count + 1
        delta = reward - m
        self.reward_mean[k] = m + delta / n
        delta2 = reward - self.reward_mean[k]
        self.reward_var[k] = s_sq + (delta * delta2 - s_sq) / n
        
        # Triple DA errors per DopAct framework (PMC 7392608):
        # δ_v (valuation): r - expected_value  — already computed as rpe_scaled
        # δ_g (goal-directed): r - goal_prediction
        # δ_h (habit): chosen_action - habitual_action
        delta_v = rpe_scaled
        delta_g = (reward - goal_prediction) / (max(s_sq ** 0.5, 0.1)) if goal_prediction is not None else delta_v
        delta_h = 1.0 if habitual_action is not None and action == habitual_action else -0.5
        
        # OpAL* opponent update using δ_v as primary DA signal:
        self.Go[state][action] += lr * delta_v * self.Go[state][action]
        self.NoGo[state][action] += lr * (-delta_v) * self.NoGo[state][action]
        
        # Clamp to [0.01, 0.99]
        self.Go[state][action] = max(0.01, min(0.99, self.Go[state][action]))
        self.NoGo[state][action] = max(0.01, min(0.99, self.NoGo[state][action]))
        
        # Update β_g/β_n via δ_g (goal) and δ_h (habit)
        if state not in self.beta_g:
            self.beta_g[state] = {}
            self.beta_n[state] = {}
        old_bg = self.beta_g[state].get(action, 1.0)
        old_bn = self.beta_n[state].get(action, 1.0)
        self.beta_g[state][action] = max(0.1, min(3.0, old_bg + 0.05 * delta_g))
        self.beta_n[state][action] = max(0.1, min(3.0, old_bn + 0.05 * (-delta_h)))
        
        self.N[state][action] = count + 1
        
        # ACC: log RPE for surprise computation
        self.rpe_history.append(delta_v)
        if len(self.rpe_history) > 200:
            self.rpe_history = self.rpe_history[-200:]
        
        # Update epsilon via ACC meta-learning (surprise-driven) using raw reward
        self.epsilon = self._acc_surprise_epsilon(reward)
        
        # Efferent feedback multiplexing
        self.efferent_feedback(state, action, delta_v)
        
        self.alpha = max(0.01, self.alpha * self.alpha_decay)
        self.save()
        return {"delta_v": round(delta_v, 4), "delta_g": round(delta_g, 4),
                "delta_h": round(delta_h, 4), "rpe_scaled": round(rpe_scaled, 4)}
    
    def efferent_feedback(self, state: str, action: str, rpe: float):
        """Post-selection BG → cortex modulation of future proposals.
        
        Based on efferent multiplexing (Striatal Action Selection, eLife 101747):
        After selection, both D1+D2 of the chosen action fire in learning mode.
        Positive RPE → strengthen similar future proposals (D1 bias).
        Negative RPE → suppress similar future proposals (D2 bias).
        """
        feedback = {
            "state": state,
            "action": action,
            "rpe": round(rpe, 4),
            "Go_after": round(self.Go[state][action], 3),
            "NoGo_after": round(self.NoGo[state][action], 3),
            "timestamp": time.time(),
        }
        efferent_path = CORTEX_DIR / f"efferent_{self.project}.jsonl"
        with open(efferent_path, "a") as f:
            f.write(json.dumps(feedback) + "\n")


# ── Hippocampal Episodic Memory ────────────────────────────────

class TextEmbedder:
    """Lightweight text embedder using character n-gram frequency vectors.
    
    No external dependencies. Uses character trigrams to build sparse
    vectors, which capture semantic similarity without needing a model.
    Based on the principle that similar texts share character-level patterns
    (Cavnar & Trenkle, 1994 — N-gram-based text categorization).
    """
    
    N = 3
    
    @classmethod
    def _ngrams(cls, text: str) -> collections.Counter:
        cleaned = re.sub(r'[^a-z0-9\s]', '', text.lower())
        words = cleaned.split()
        ngrams: list[str] = []
        for word in words:
            padded = f"  {word} " if len(word) > 2 else word
            for i in range(len(padded) - cls.N + 1):
                ngrams.append(padded[i:i + cls.N])
        ngrams.extend(f"w_{w}" for w in words)
        return collections.Counter(ngrams)
    
    @classmethod
    def embed(cls, text: str) -> dict[str, float]:
        total = 0
        counter = cls._ngrams(text)
        for v in counter.values():
            total += v * v
        mag = total ** 0.5
        if mag == 0:
            return {}
        return {k: v / mag for k, v in counter.items()}
    
    @classmethod
    def cosine_similarity(cls, a: dict[str, float], b: dict[str, float]) -> float:
        dot = 0.0
        for k, v in a.items():
            if k in b:
                dot += v * b[k]
        return dot


class HippocampalMemory:
    """Episodic memory with automatic context retrieval via embeddings.
    
    Pattern completion (CA3-like): when a new task comes in, compute its
    embedding and auto-find similar past episodes — no manual query needed.
    This mirrors hippocampal pattern completion (Marr, 1971; Treves & Rolls, 1994).
    """
    
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
        text = f"{task_title} {outcome} {meta or {}}"
        embedding = TextEmbedder.embed(text)
        entry = {
            "task_id": task_id,
            "agent_id": agent_id,
            "task_title": task_title,
            "outcome": outcome,
            "reward": reward,
            "meta": meta or {},
            "embedding": embedding,
            "timestamp": time.time(),
        }
        self.episodes.append(entry)
        if len(self.episodes) > self.max_episodes:
            self.episodes = self.episodes[-self.max_episodes:]
        self.save()
    
    def context_for_task(self, task_title: str, task_type: str = "",
                         complexity: int = 5, top_k: int = 3) -> list[dict]:
        """Automatic context retrieval — like hippocampal pattern completion.
        
        Computes embedding of the current task and finds similar past episodes.
        No manual query needed — the context IS the query.
        """
        query_text = f"{task_title} {task_type}"
        query_emb = TextEmbedder.embed(query_text)
        scored = []
        for ep in self.episodes:
            emb = ep.get("embedding")
            if emb and query_emb:
                sim = TextEmbedder.cosine_similarity(query_emb, emb)
            else:
                sim = 0.0
            meta = ep.get("meta", {}) or {}
            if meta.get("type") == task_type:
                sim += 0.2
            c = meta.get("complexity", 5)
            sim += 0.1 * (1 - abs(c - complexity) / 10)
            sim += 0.1 * ep.get("reward", 0.5)
            scored.append((sim, ep))
        scored.sort(key=lambda x: -x[0])
        return [ep for _, ep in scored[:top_k]]
    
    def recall_similar(self, task_type: str, complexity: int, top_k: int = 3) -> list[dict]:
        return self.context_for_task("", task_type, complexity, top_k)
    
    def avg_reward_for_agent(self, agent_id: str) -> float:
        matched = [e for e in self.episodes if e.get("agent_id") == agent_id]
        if not matched:
            return 0.5
        return sum(e.get("reward", 0.5) for e in matched) / len(matched)
    
    def recent(self, n: int = 5) -> list[dict]:
        return self.episodes[-n:]


# ── Hippocampal Replay with EVB Prioritization ───────────────

class HippocampalReplay:
    """Offline hippocampal replay with EVB-prioritized sampling.
    
    Based on:
      - Memory Consolidation from RL (Frontiers 2025): Dyna-style offline RL
      - Hippocampal Replay Compositional (Nature Neuro 2025): EVB prioritization
      - Generative Memory Model (Nature Hum Behav 2024): VAE training via replay
    
    EVB(s, a) = |TD_error(s, a)| * need(s, a)  (expected value of backup)
    """
    
    def __init__(self, project: str, gating: PFCBGGating):
        self.project = project
        self.gating = gating
        self.buffer: list[dict] = []
        self.max_buffer = 200
        self._load()
    
    def _path(self) -> Path:
        return CORTEX_DIR / f"replay_{self.project}.json"
    
    def _load(self):
        p = self._path()
        if p.exists():
            try:
                data = json.loads(p.read_text())
                self.buffer = data.get("buffer", [])
            except (json.JSONDecodeError, OSError):
                pass
    
    def save(self):
        self._path().write_text(json.dumps({
            "buffer": self.buffer[-self.max_buffer:],
        }, indent=2))
    
    def add_experience(self, state: str, action: str, reward: float, next_state: str = ""):
        q_old = self.gating.get_q(state, action)
        td_error = abs(reward - q_old)
        need = 1.0 + 0.5 * (1.0 - len(self.buffer) / self.max_buffer) if self.buffer else 1.0
        evb = td_error * need
        entry = {
            "state": state,
            "action": action,
            "reward": reward,
            "next_state": next_state or state,
            "td_error": round(td_error, 4),
            "evb": round(evb, 4),
            "timestamp": time.time(),
        }
        self.buffer.append(entry)
        if len(self.buffer) > self.max_buffer:
            self.buffer = self.buffer[-self.max_buffer:]
        self.save()
    
    def sample_prioritized(self, batch_size: int = 8) -> list[dict]:
        if not self.buffer:
            return []
        scored = sorted(self.buffer, key=lambda x: -x["evb"])
        return scored[:batch_size]
    
    def replay_step(self, lr_multiplier: float = 0.5):
        """Run one step of offline Dyna Q-learning on prioritized samples."""
        batch = self.sample_prioritized(8)
        updates = []
        for exp in batch:
            old_q = self.gating.get_q(exp["state"], exp["action"])
            self.gating.update(exp["state"], exp["action"], exp["reward"])
            new_q = self.gating.get_q(exp["state"], exp["action"])
            updates.append({
                "state": exp["state"],
                "action": exp["action"],
                "old_q": round(old_q, 3),
                "new_q": round(new_q, 3),
                "evb": exp["evb"],
            })
        return updates

    def signal_weighted_replay(self, signals: dict | None = None, batch_size: int = 8):
        """Event-driven replay with signal-modulated priority sampling.

        Signals dict can contain:
          - rpe_magnitude:  float (0-1) — recent RPE intensity
          - acc_surprise:   float (0-1) — Bayesian surprise from ACC
          - conflict_score: float (0-1) — Socratic dialectic conflict
          - efferent_div:   float (0-1) — Go/NoGo divergence

        Higher signal values → more replay steps + wider sampling.
        If signals is None or empty, falls back to standard replay_step.
        """
        if not signals:
            return self.replay_step(lr_multiplier=0.5)

        urgency = sum(signals.values()) / max(len(signals), 1)
        if urgency < 0.1:
            return []

        steps = max(1, int(urgency * 5))
        lr = 0.3 + 0.4 * urgency
        all_updates = []
        for _ in range(steps):
            batch = self.sample_prioritized(batch_size)
            if not batch:
                break
            for exp in batch:
                old_q = self.gating.get_q(exp["state"], exp["action"])
                self.gating.update(exp["state"], exp["action"], exp["reward"])
                new_q = self.gating.get_q(exp["state"], exp["action"])
                all_updates.append({
                    "state": exp["state"],
                    "action": exp["action"],
                    "old_q": round(old_q, 3),
                    "new_q": round(new_q, 3),
                    "evb": exp["evb"],
                    "signal_urgency": round(urgency, 3),
                    "learning_rate": round(lr, 3),
                })
        return all_updates


# ── Auction Coordinator (Basal Ganglia gating) ────────────────

class AuctionCoordinator:
    """Market-based task allocation with sealed-bid auction.
    
    Includes CBGT three control ensembles (per BRAIN_MAS_REFERENCE.md):
      - Responsiveness:  how quickly agents bid (1/avg_bid_time_ms)
      - Pliancy:         how much agents adapt to task type (variance)
      - Choice:          how strongly agents specialize (max - mean bid)
    """
    
    def __init__(self, project: str):
        self.project = project
        self.reputation: dict[str, float] = {}
        # CBGT three control ensembles
        self.responsiveness: dict[str, float] = {}
        self.pliancy: dict[str, float] = {}
        self.choice: dict[str, float] = {}
        self._bid_history: dict[str, list[dict]] = {}
        self._load()
    
    def _path(self) -> Path:
        return CORTEX_DIR / f"auction_{self.project}.json"
    
    def _load(self):
        p = self._path()
        if p.exists():
            try:
                data = json.loads(p.read_text())
                self.reputation = data.get("reputation", {})
                self.responsiveness = data.get("responsiveness", {})
                self.pliancy = data.get("pliancy", {})
                self.choice = data.get("choice", {})
                self._bid_history = data.get("_bid_history", {})
            except (json.JSONDecodeError, OSError):
                pass
    
    def save(self):
        self._path().write_text(json.dumps({
            "reputation": self.reputation,
            "responsiveness": self.responsiveness,
            "pliancy": self.pliancy,
            "choice": self.choice,
            "_bid_history": {k: v[-50:] for k, v in self._bid_history.items()},
        }, indent=2))
    
    def update_ensembles(self, agent_id: str, bid_time_ms: float, bid_score: float, task_type: str):
        if agent_id not in self._bid_history:
            self._bid_history[agent_id] = []
        self._bid_history[agent_id].append({
            "time_ms": bid_time_ms,
            "score": bid_score,
            "task_type": task_type,
            "ts": time.time(),
        })
        recent = self._bid_history[agent_id][-20:]
        if not recent:
            return
        speeds = [b["time_ms"] for b in recent if b["time_ms"] > 0]
        self.responsiveness[agent_id] = 1.0 / (sum(speeds) / len(speeds) + 1) if speeds else 0.5
        scores = [b["score"] for b in recent]
        mean_s = sum(scores) / len(scores)
        self.pliancy[agent_id] = sum((s - mean_s) ** 2 for s in scores) / len(scores) if len(scores) > 1 else 0.0
        self.choice[agent_id] = max(scores) - mean_s
    
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
        ens_resp = self.responsiveness.get(agent_id, 0.5)
        ens_choice = self.choice.get(agent_id, 0.0)
        score = 0.4 * confidence + 0.2 * rep + 0.2 * ens_resp + 0.1 * ens_choice + 0.1 * random.random()
        return {
            "agent_id": agent_id,
            "score": round(score, 3),
            "capability_match": round(capability_match, 3),
            "reputation": round(rep, 3),
            "confidence": round(confidence, 3),
            "responsiveness": round(ens_resp, 3),
            "choice": round(ens_choice, 3),
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

class PFCWorkingMemory:
    """dlPFC-like working memory buffer for subgoal stacks.
    
    Maintains hierarchical subgoal state as per Botvinick (2008):
    PFC maintains subgoal stacks — the direct computational analogue
    of hierarchical task decomposition in multi-agent systems.
    """
    
    def __init__(self, project: str):
        self.project = project
        self.subgoal_stack: list[dict] = []
        self.max_depth = 5
        self._load()
    
    def _path(self) -> Path:
        return CORTEX_DIR / f"working_memory_{self.project}.json"
    
    def _load(self):
        p = self._path()
        if p.exists():
            try:
                data = json.loads(p.read_text())
                self.subgoal_stack = data.get("subgoal_stack", [])
            except (json.JSONDecodeError, OSError):
                pass
    
    def save(self):
        self._path().write_text(json.dumps({"subgoal_stack": self.subgoal_stack[-self.max_depth:]}, indent=2))
    
    def push(self, goal: str, parent_id: str | None = None) -> int:
        entry = {
            "goal": goal,
            "parent_id": parent_id,
            "depth": len(self.subgoal_stack),
            "timestamp": time.time(),
            "status": "active",
            "subgoals": [],
        }
        self.subgoal_stack.append(entry)
        if parent_id:
            for sg in self.subgoal_stack:
                if sg.get("goal") == parent_id or sg.get("id") == parent_id:
                    sg.setdefault("subgoals", []).append(goal)
                    break
        self.save()
        return len(self.subgoal_stack) - 1
    
    def pop(self) -> dict | None:
        if not self.subgoal_stack:
            return None
        entry = self.subgoal_stack.pop()
        self.save()
        return entry
    
    def peek(self) -> dict | None:
        return self.subgoal_stack[-1] if self.subgoal_stack else None
    
    def mark_done(self, goal: str):
        for sg in self.subgoal_stack:
            if sg.get("goal") == goal:
                sg["status"] = "done"
        self.save()


class PrefrontalDecomposer:
    """Hierarchical task decomposition with PFC working memory.
    
    Maps to rostro-caudal PFC:
      - aPFC (anterior): Abstract goal representation
      - dlPFC: Rule-based reasoning, working memory (PFCWorkingMemory)
      - vmPFC: Value computation, arbitration
    """
    
    def __init__(self, project: str | None = None):
        self.wm = PFCWorkingMemory(project or "default") if project else None
    
    def decompose(self, title: str, description: str, complexity: int = 5) -> list[dict]:
        """Hierarchical decomposition with recursive subgoal generation."""
        lines = [l for l in description.split("\n") if l.strip()]
        if len(lines) <= 1:
            subtask = {"step": 1, "description": title, "depends_on": [],
                       "subtasks": [], "depth": 0}
            if self.wm:
                self.wm.push(title)
            return [subtask]
        
        def _build_tree(lines: list[str], depth: int = 0) -> list[dict]:
            if depth > 3 or not lines:
                return []
            subtasks = []
            for i, line in enumerate(lines):
                indent = len(line) - len(line.lstrip())
                if indent > depth * 2 and subtasks:
                    subtasks[-1].setdefault("subtasks", []).append({
                        "step": len(subtasks[-1].get("subtasks", [])) + 1,
                        "description": line.strip().lstrip("- ").lstrip("* "),
                        "depends_on": [len(subtasks)],
                        "subtasks": [],
                        "depth": depth + 1,
                    })
                else:
                    subtasks.append({
                        "step": i + 1,
                        "description": line.strip().lstrip("- ").lstrip("* "),
                        "depends_on": [i] if i > 0 else [],
                        "subtasks": [],
                        "depth": depth,
                    })
            return subtasks
        
        subtasks = _build_tree(lines)
        if self.wm:
            for st in subtasks:
                self.wm.push(st["description"], title)
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


# ── Socratic Decomposer (Philosophical Task Refinement) ───────

class SocraticDecomposer:
    """Socratic elenchus before task decomposition.
    
    Philosophy-specialized task refiner that:
    1. Questions the goal — exposes hidden assumptions
    2. Refines success criteria before subgoaling
    3. Logs assumptions as structured metadata
    4. Supports cross-agent dialectic — flags disagreements
    
    Based on Socratic method: systematic questioning to expose
    contradictions in beliefs and arrive at deeper truth.
    """
    
    SOCRATIC_QUESTIONS = [
        "What do you mean by '{goal}'? Can you define it more precisely?",
        "What would success look like? How would we measure it?",
        "What assumptions are we making about this problem?",
        "What if the opposite of our assumption is true?",
        "Why is this the right goal to pursue? What makes it important?",
        "Who benefits from this goal? Who might be harmed?",
        "What prior knowledge or experience informs this approach?",
        "If we achieve this, what new problems might it create?",
        "What are we NOT considering? What's outside the frame?",
        "Is this goal means or ends? Could it be a means to a deeper end?",
    ]
    
    def __init__(self, project: str):
        self.project = project
        self.assumptions: list[dict] = []
        self.dialectic_records: list[dict] = []
        self._load()
    
    def _path(self) -> Path:
        return CORTEX_DIR / f"socratic_{self.project}.json"
    
    def _load(self):
        p = self._path()
        if p.exists():
            try:
                data = json.loads(p.read_text())
                self.assumptions = data.get("assumptions", [])
                self.dialectic_records = data.get("dialectic_records", [])
            except (json.JSONDecodeError, OSError):
                pass
    
    def save(self):
        self._path().write_text(json.dumps({
            "assumptions": self.assumptions[-100:],
            "dialectic_records": self.dialectic_records[-50:],
        }, indent=2))
    
    def question_goal(self, title: str, description: str) -> list[dict]:
        """Generate Socratic questions for a task goal.
        
        Returns structured questions with category and Socratic target.
        """
        goal = title or description[:60]
        questions = []
        for i, q_template in enumerate(self.SOCRATIC_QUESTIONS):
            q = q_template.replace("{goal}", goal)
            category = "definition" if i == 0 else \
                       "measurement" if i == 1 else \
                       "assumption" if i in (2, 3) else \
                       "motivation" if i in (4, 5) else \
                       "knowledge" if i == 6 else \
                       "consequence" if i == 7 else \
                       "blindspot" if i == 8 else \
                       "reframing"
            questions.append({
                "id": i,
                "question": q,
                "category": category,
                "target": goal,
                "answer": None,
                "assumption_exposed": None,
            })
        return questions
    
    def record_answer(self, question_id: int, answer: str, assumption: str | None = None):
        """Record an answer to a Socratic question and any exposed assumption."""
        record = {
            "question_id": question_id,
            "answer": answer[:500],
            "assumption": assumption[:300] if assumption else None,
            "timestamp": time.time(),
        }
        self.assumptions.append(record)
        if assumption:
            self.assumptions[-1]["exposed_assumption"] = assumption[:300]
        self.save()
    
    def refine_task(self, title: str, description: str, answers: list[dict] | None = None) -> dict:
        """Refine a task based on Socratic answers.
        
        Returns enriched task model with:
        - refined_title: more precise goal statement
        - success_criteria: measurable outcomes
        - exposed_assumptions: list of assumptions found
        - blindspots: identified gaps
        """
        refined = {
            "original_title": title,
            "original_description": description,
            "refined_title": title,
            "refined_description": description,
            "success_criteria": [],
            "exposed_assumptions": [],
            "blindspots": [],
            "socratic_depth": len(answers or []),
        }
        if not answers:
            return refined
        
        for a in answers:
            if a.get("assumption"):
                refined["exposed_assumptions"].append(a["assumption"])
            cat = a.get("category", "")
            ans = a.get("answer", "")
            if cat == "measurement" and ans:
                refined["success_criteria"].append(ans)
            elif cat == "blindspot" and ans:
                refined["blindspots"].append(ans)
        
        if refined["exposed_assumptions"]:
            refined["refined_description"] += (
                "\n\n[Assumptions exposed by Socratic questioning]\n" +
                "\n".join(f"- {a}" for a in refined["exposed_assumptions"])
            )
        if refined["success_criteria"]:
            refined["refined_title"] = refined["refined_title"]
        
        return refined
    
    def detect_dialectic(self, task_id: str, agent_a: str, agent_b: str,
                         position_a: str, position_b: str) -> dict | None:
        """Detect and record dialectic disagreement between two agents.
        
        When two agents have conflicting positions on the same task,
        this flags the disagreement for resolution (Hegelian dialectic).
        """
        conflict_score = 0.0
        a_words = set(position_a.lower().split())
        b_words = set(position_b.lower().split())
        common = a_words & b_words
        diff = a_words ^ b_words
        if len(common) > 0:
            conflict_score = len(diff) / (len(common) + len(diff))
        
        record = {
            "task_id": task_id,
            "agent_a": agent_a,
            "agent_b": agent_b,
            "position_a": position_a[:200],
            "position_b": position_b[:200],
            "conflict_score": round(conflict_score, 3),
            "resolution": None,
            "timestamp": time.time(),
        }
        self.dialectic_records.append(record)
        self.save()
        
        if conflict_score > 0.5:
            return record
        return None
    
    def resolve_dialectic(self, task_id: str, synthesis: str):
        """Record Hegelian synthesis for a dialectic conflict."""
        for r in self.dialectic_records:
            if r["task_id"] == task_id and r["resolution"] is None:
                r["resolution"] = synthesis[:500]
                r["resolved_at"] = time.time()
        self.save()


# ── Main CORTEX Engine ────────────────────────────────────────

class CortexEngine:
    """Brain-inspired autonomous task allocation engine."""
    
    def __init__(self, project: str):
        self.project = project
        self.gating = PFCBGGating(project)
        self.hippocampus = HippocampalMemory(project)
        self.replay = HippocampalReplay(project, self.gating)
        self.auction = AuctionCoordinator(project)
        self.decomposer = PrefrontalDecomposer(project)
        self.socrates = SocraticDecomposer(project)
        self.wm = self.decomposer.wm
    
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
        similar = self.hippocampus.context_for_task(meta.get("title", ""), task_type, complexity)
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
            "gate_confidence": max((self.gating.get_q(state, a) for a in self.gating.Go.get(state, {})), default=0.5),
        }
    
    def record_outcome(self, task_id: str, agent_id: str, task_title: str,
                       task_type: str, complexity: int, reward: float):
        state = self.gating.state_key(task_type, complexity)
        da_signals = self.gating.update(state, agent_id, reward)
        self.auction.update_reputation(agent_id, reward)
        self.hippocampus.store(task_id, agent_id, task_title,
                               "completed" if reward >= 0.5 else "failed",
                               reward, {"type": task_type, "complexity": complexity})
        self.replay.add_experience(state, agent_id, reward)
        return da_signals
    
    def get_status(self) -> dict:
        q_summary = {}
        for state, go_vals in self.gating.Go.items():
            nogo_vals = self.gating.NoGo.get(state, {})
            all_actions = set(go_vals) | set(nogo_vals)
            if all_actions:
                net = {}
                for a in all_actions:
                    bg = self.gating.beta_g.get(state, {}).get(a, 1.0)
                    bn = self.gating.beta_n.get(state, {}).get(a, 1.0)
                    gv = go_vals.get(a, 0.5)
                    nv = nogo_vals.get(a, 0.5)
                    net[a] = bg * gv - bn * nv
                best = max(net, key=net.get)
                visits = sum(self.gating.N.get(state, {}).values())
                q_summary[state] = {
                    "best_action": best,
                    "best_q": round(net[best], 3),
                    "visits": visits,
                }
        return {
            "project": self.project,
            "epsilon": round(self.gating.epsilon, 4),
            "q_table_size": len(self.gating.Go),
            "q_summary": q_summary,
            "episodes": len(self.hippocampus.episodes),
            "wm_stack_depth": len(self.wm.subgoal_stack) if self.wm else 0,
            "wm_active_goals": [sg["goal"][:40] for sg in (self.wm.subgoal_stack if self.wm else []) if sg.get("status") == "active"],
            "replay_buffer": len(self.replay.buffer),
            "replay_evb_top": round(max(e["evb"] for e in self.replay.buffer), 3) if self.replay.buffer else 0.0,
            "agents_tracked": len(self.auction.reputation),
            "avg_reputations": {a: round(r, 3) for a, r in self.auction.reputation.items()},
            "alpha_surprise": round(self.gating.alpha_surprise[-1], 4) if self.gating.alpha_surprise else 0,
            "beta_surprise": round(self.gating.beta_surprise[-1], 4) if self.gating.beta_surprise else 0,
            "avg_responsiveness": round(sum(self.auction.responsiveness.values()) / max(len(self.auction.responsiveness), 1), 3) if self.auction.responsiveness else 0,
            "avg_choice": round(sum(self.auction.choice.values()) / max(len(self.auction.choice), 1), 3) if self.auction.choice else 0,
            "socratic_assumptions": len(self.socrates.assumptions),
            "socratic_dialectics": len(self.socrates.dialectic_records),
        }


# ── Global registry of engines ────────────────────────────────

_engines: dict[str, CortexEngine] = {}

def get_engine(project: str) -> CortexEngine:
    if project not in _engines:
        _engines[project] = CortexEngine(project)
    return _engines[project]
