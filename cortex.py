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


# ── Meta-State Learner (Learnable Categories) ──────────────────


class MetaStateLearner:
    """Learnable state representation — the system learns what counts as a state.

    Replaces hardcoded complexity bins (state_key's ``complexity // 2``) with
    learned bin boundaries that adapt via meta-learning over the Q-table.
    This is the Copernican Revolution: the architecture questions its own
    transcendental categories (state, action, reward, complexity).

    Meta-learning loop (second-order):
      1. Learnable complexity bin boundaries (start equispaced, adapt via usage)
      2. State quality monitoring — within-state Q variance triggers splitting
      3. Cross-state Q-table cosine similarity → merge similar adjacent states
      4. Visit-count rebalancing → shift boundaries toward uniform distribution
      5. Variance-guided boundary adjustment → shrink high-variance bins

    Architecture:
      - Second-order loop reads the Q-table (first-order output) as its input
      - Modifies state representation → changes what first-order learner learns
      - This is true meta-learning: learning how to represent states, not just
        learning values within a fixed representation

    Compatible with: PFCBGGating, HippocampalReplay, AuctionCoordinator
    """

    def __init__(self, n_bins: int = 5, meta_interval: int = 50):
        self.n_bins = n_bins
        self.meta_interval = meta_interval

        # Normalized [0,1] bin boundaries; n_bins regions from n_bins-1 boundaries
        # Start equispaced (equivalent to //2 with 5 bins): [0.2, 0.4, 0.6, 0.8]
        self.boundaries: list[float] = [(i + 1) / n_bins for i in range(n_bins - 1)]

        # Per-state-action Q-value moment statistics (Welford online)
        self.q_means: dict[str, dict[str, float]] = {}
        self.q_vars: dict[str, dict[str, float]] = {}
        self.q_counts: dict[str, dict[str, int]] = {}

        # Meta-learning thresholds
        self.merge_threshold = 0.85  # cosine sim to merge adjacent bins
        self.split_threshold = 0.25  # within-state Q variance to split
        self.boundary_lr = 0.03  # adaptation rate for boundary shifts
        self.min_boundary_gap = 0.05  # min normalized distance between boundaries
        self.balance_target = 0.25  # min/max visit ratio triggering rebalance

        # Meta-learning state
        self.total_updates = 0
        self.last_meta_step = 0
        self.proposal_history: list[dict] = []
        self.warmup_steps = 100

    # ── State Key Generation ───────────────────────────────────

    def state_key(self, task_type: str, complexity: int) -> str:
        """Learnable state key — replaces hardcoded complexity // 2."""
        task_type = task_type or "generic"
        bin_idx = self._complexity_to_bin(complexity)
        return f"{task_type}_l{bin_idx}"

    def _complexity_to_bin(self, complexity: int) -> int:
        """Map complexity (0-10) to bin using learned boundaries."""
        c_norm = complexity / 10.0
        for i, b in enumerate(self.boundaries):
            if c_norm < b:
                return i
        return len(self.boundaries)

    # ── Q-Value Statistics Recording (Welford Online) ──────────

    def record(self, state: str, action: str, q_value: float):
        """Record Q-value for a state-action pair.

        Uses Welford's online algorithm for mean and variance:
          M_{n+1} = M_n + (x_n - M_n) / (n+1)
          S_{n+1} = S_n + (x_n - M_n) * (x_n - M_{n+1})
          Var = S_n / n

        This feeds the meta-evaluation: high variance → split candidate,
        low variance + similar to neighbor → merge candidate.
        """
        if state not in self.q_means:
            self.q_means[state] = {}
            self.q_vars[state] = {}
            self.q_counts[state] = {}

        if action not in self.q_means[state]:
            self.q_means[state][action] = q_value
            self.q_vars[state][action] = 0.0
            self.q_counts[state][action] = 1
        else:
            n = self.q_counts[state][action]
            old_mean = self.q_means[state][action]
            delta = q_value - old_mean
            new_mean = old_mean + delta / (n + 1)
            delta2 = q_value - new_mean
            self.q_vars[state][action] = (
                self.q_vars[state][action] * n + delta * delta2
            ) / (n + 1)
            self.q_means[state][action] = new_mean
            self.q_counts[state][action] = n + 1

        self.total_updates += 1

    # ── Meta-Evaluation Loop ───────────────────────────────────

    def meta_step(self, gating: "PFCBGGating") -> list[str]:
        """Run one meta-evaluation step.

        Second-order learning loop:
          1. Scan state pairs for merge candidates (adjacent bins, similar Q-tables)
          2. Scan states for split candidates (high within-action Q variance)
          3. Rebalance bin boundaries if visit distribution is very skewed
          4. Nudge boundaries toward low-variance regions

        Returns: list of change descriptions (empty if no changes).
        """
        if self.total_updates < self.warmup_steps:
            return []
        if self.total_updates - self.last_meta_step < self.meta_interval:
            return []

        self.last_meta_step = self.total_updates
        changes: list[str] = []

        # 1. Merge adjacent bins with similar Q-tables
        task_types = self._detect_task_types(gating)
        for tt in task_types:
            for b in range(len(self.boundaries)):
                s1 = f"{tt}_l{b}"
                s2 = f"{tt}_l{b + 1}"
                if s1 in gating.Go and s2 in gating.Go:
                    sim = self._state_cosine_sim(gating, s1, s2)
                    if sim >= self.merge_threshold:
                        self._merge_states(gating, s1, s2)
                        changes.append(f"merged {s2}→{s1} (sim={sim:.3f})")
                        break  # avoid cascading after merge

        # 2. Split high-variance states (mark for investigation)
        for state in list(gating.Go.keys()):
            if state in self.q_vars and self.q_vars[state]:
                vars_list = [v for v in self.q_vars[state].values() if v > 0]
                if vars_list:
                    avg_var = sum(vars_list) / len(vars_list)
                    if avg_var > self.split_threshold:
                        self._split_state(gating, state)
                        changes.append(f"split {state} (avg_var={avg_var:.3f})")

        # 3. Rebalance boundaries if visit distribution is too skewed
        visit_dist = self._visit_distribution(gating)
        if visit_dist and len(visit_dist) > 1:
            max_v = max(visit_dist)
            min_v = min(visit_dist)
            if max_v > 0 and min_v / max_v < self.balance_target:
                self._rebalance_boundaries(visit_dist)
                changes.append(
                    f"rebalanced boundaries: {[round(b, 3) for b in self.boundaries]}"
                )

        # 4. Adjust boundaries toward lower within-bin variance
        self._adjust_boundaries_by_variance(gating)

        if changes:
            self._cleanup(gating)
            self.proposal_history.append(
                {
                    "t": time.time(),
                    "changes": changes,
                    "boundaries": self.boundaries.copy(),
                }
            )

        return changes

    # ── Meta-Evaluation Subroutines ────────────────────────────

    @staticmethod
    def _detect_task_types(gating: "PFCBGGating") -> list[str]:
        """Extract unique task types from state keys (handles _cN and _lN)."""
        types: set[str] = set()
        for s in gating.Go:
            for sep in ("_l", "_c"):
                if sep in s:
                    types.add(s.rsplit(sep, 1)[0])
                    break
        return sorted(types)

    def _state_cosine_sim(self, gating: "PFCBGGating", s1: str, s2: str) -> float:
        """Cosine similarity between Q-value vectors of two states."""
        actions = set(gating.Go.get(s1, {})) | set(gating.Go.get(s2, {}))
        v1 = [gating.get_q(s1, a) for a in actions]
        v2 = [gating.get_q(s2, a) for a in actions]
        dot = sum(a * b for a, b in zip(v1, v2))
        n1 = sum(a * a for a in v1) ** 0.5 or 1.0
        n2 = sum(b * b for b in v2) ** 0.5 or 1.0
        return dot / (n1 * n2)

    def _merge_states(self, gating: "PFCBGGating", keep: str, drop: str):
        """Merge `drop` state into `keep` state (weighted by visit counts)."""
        if drop not in gating.Go:
            return
        all_actions = set(gating.Go.get(keep, {})) | set(gating.Go.get(drop, {}))
        for a in all_actions:
            gating._init_state_action(keep, a)
            kc = gating.N.get(keep, {}).get(a, 0)
            dc = gating.N.get(drop, {}).get(a, 0)
            total = kc + dc
            if total > 0:
                kw = kc / total
                dw = dc / total
                gating.Go[keep][a] = kw * gating.Go[keep].get(a, 0.5) + dw * gating.Go[
                    drop
                ].get(a, 0.5)
                gating.NoGo[keep][a] = kw * gating.NoGo[keep].get(
                    a, 0.5
                ) + dw * gating.NoGo[drop].get(a, 0.5)
                gating.N[keep][a] = total

        for d in (gating.Go, gating.NoGo, gating.N, gating.beta_g, gating.beta_n):
            d.pop(drop, None)
        for d in (self.q_means, self.q_vars, self.q_counts):
            d.pop(drop, None)

    def _split_state(self, gating: "PFCBGGating", state: str):
        """Add a new boundary to split a high-variance bin.

        Creates a new boundary within the bin's complexity range,
        then redistributes by updating the boundaries list.
        """
        for sep in ("_l", "_c"):
            if sep in state:
                try:
                    bin_idx = int(state.rsplit(sep, 1)[1])
                except (ValueError, IndexError):
                    return

                if bin_idx >= len(self.boundaries):
                    # If bin_idx == len(boundaries), it's the last bin
                    # Insert a boundary before it
                    insert_pos = bin_idx
                    left_edge = (
                        self.boundaries[insert_pos - 1] if insert_pos > 0 else 0.0
                    )
                    right_edge = (
                        self.boundaries[insert_pos]
                        if insert_pos < len(self.boundaries)
                        else 1.0
                    )
                else:
                    # It's an interior bin; split its range
                    left_edge = self.boundaries[bin_idx - 1] if bin_idx > 0 else 0.0
                    right_edge = (
                        self.boundaries[bin_idx]
                        if bin_idx < len(self.boundaries)
                        else 1.0
                    )
                    insert_pos = bin_idx

                new_boundary = (left_edge + right_edge) / 2

                # Ensure minimum gap from neighbors
                if insert_pos > 0:
                    new_boundary = max(
                        new_boundary,
                        self.boundaries[insert_pos - 1] + self.min_boundary_gap,
                    )
                if insert_pos < len(self.boundaries):
                    new_boundary = min(
                        new_boundary,
                        self.boundaries[insert_pos] - self.min_boundary_gap,
                    )

                if (
                    new_boundary > left_edge + self.min_boundary_gap
                    and new_boundary < right_edge - self.min_boundary_gap
                ):
                    self.boundaries.insert(insert_pos, new_boundary)

                return

    def _visit_distribution(self, gating: "PFCBGGating") -> list[int]:
        """Count total visits per complexity bin across all task types."""
        n_regions = len(self.boundaries) + 1
        counts = [0] * n_regions
        for state, actions in gating.N.items():
            for sep in ("_l", "_c"):
                if sep in state:
                    try:
                        b = int(state.rsplit(sep, 1)[1])
                        if 0 <= b < n_regions:
                            counts[b] += sum(actions.values())
                    except (ValueError, IndexError):
                        pass
                    break
        return counts

    def _rebalance_boundaries(self, visit_counts: list[int]):
        """Shift boundaries to equalize visit distribution across bins.

        Uses cumulative distribution of visits to set boundary positions,
        smoothed with momentum to avoid oscillation.
        """
        total = sum(visit_counts)
        if total == 0:
            return

        cumulative = 0
        for i in range(len(self.boundaries)):
            cumulative += visit_counts[i]
            desired = cumulative / total
            current = self.boundaries[i]
            self.boundaries[i] += self.boundary_lr * (desired - current)
            # Clamp
            min_pos = (i + 1) * self.min_boundary_gap
            max_pos = 1.0 - (len(self.boundaries) - i) * self.min_boundary_gap
            self.boundaries[i] = max(min_pos, min(max_pos, self.boundaries[i]))

        # Ensure monotonicity
        for i in range(1, len(self.boundaries)):
            if self.boundaries[i] <= self.boundaries[i - 1] + self.min_boundary_gap:
                self.boundaries[i] = self.boundaries[i - 1] + self.min_boundary_gap
        if self.boundaries[-1] > 1.0 - self.min_boundary_gap:
            self.boundaries[-1] = 1.0 - self.min_boundary_gap

    def _adjust_boundaries_by_variance(self, gating: "PFCBGGating"):
        """Nudge boundaries toward regions with lower Q-value variance.

        Intuition: high-variance bins lump together situations requiring
        different action values → shrink these bins by moving boundaries inward.
        Low-variance bins have stable action selection → expand them.
        """
        n_regions = len(self.boundaries) + 1
        per_bin_vars: list[list[float]] = [[] for _ in range(n_regions)]

        for state in gating.Go:
            for sep in ("_l", "_c"):
                if sep in state and state in self.q_vars:
                    try:
                        b = int(state.rsplit(sep, 1)[1])
                        if 0 <= b < n_regions:
                            for v in self.q_vars[state].values():
                                if v > 0:
                                    per_bin_vars[b].append(v)
                    except (ValueError, IndexError):
                        pass
                    break

        for i in range(len(self.boundaries)):
            left_vars = per_bin_vars[i]
            right_vars = per_bin_vars[i + 1]
            left_avg = sum(left_vars) / max(len(left_vars), 1)
            right_avg = sum(right_vars) / max(len(right_vars), 1)

            if left_avg + right_avg > 0:
                # Push boundary toward higher-variance bin (shrinking it)
                push = (
                    self.boundary_lr * (right_avg - left_avg) / (left_avg + right_avg)
                )
                self.boundaries[i] += push
                # Clamp
                min_pos = (i + 1) * self.min_boundary_gap
                max_pos = 1.0 - (len(self.boundaries) - i) * self.min_boundary_gap
                self.boundaries[i] = max(min_pos, min(max_pos, self.boundaries[i]))

        # Re-ensure monotonicity
        for i in range(1, len(self.boundaries)):
            if self.boundaries[i] <= self.boundaries[i - 1] + self.min_boundary_gap:
                self.boundaries[i] = self.boundaries[i - 1] + self.min_boundary_gap

    def _cleanup(self, gating: "PFCBGGating"):
        """Remove orphaned meta-statistics for states no longer in Go."""
        for d in (self.q_means, self.q_vars, self.q_counts):
            for state in list(d.keys()):
                if state not in gating.Go:
                    d.pop(state, None)

    # ── Serialization ──────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "boundaries": self.boundaries,
            "proposal_history": self.proposal_history[-50:],
            "total_updates": self.total_updates,
        }

    def from_dict(self, data: dict):
        self.boundaries = data.get("boundaries", self.boundaries)
        self.proposal_history = data.get("proposal_history", [])
        self.total_updates = data.get("total_updates", 0)


# ── Predictive Coding Hierarchy (Friston 2010) ─────────────────


class HierarchicalLevel:
    """A single level in the predictive coding hierarchy.

    Each level maintains a generative model of the level below it.
    Per Friston (2010) free energy principle:

      prediction (μ):  top-down expectation of input at this level
      error (ε):       bottom-up prediction error from level below
      precision (π):   attention weight = 1/σ² (inverse variance)

    Free energy at each level:  F = π·ε²/2 − ln(π)/2
    """

    def __init__(self, name: str, precision: float = 1.0):
        self.name = name
        self.prediction = 0.5
        self.error = 0.0
        self.precision = precision
        self.precision_history: list[float] = []
        self.error_history: list[float] = []
        self.prediction_history: list[float] = []
        self.precision_lr = 0.02

    def compute_error(self, observation: float) -> float:
        """Bottom-up prediction error: ε = observation − prediction."""
        self.error = observation - self.prediction
        self.error_history.append(self.error)
        if len(self.error_history) > 200:
            self.error_history = self.error_history[-200:]
        return self.error

    def update_prediction(
        self,
        top_down_prediction: float | None = None,
        bottom_up_error: float | None = None,
    ) -> float:
        """Precision-weighted belief update.

        Receives either a top-down prior (prediction from higher level)
        or a bottom-up prediction error (from level below), or both.

        Belief update:  μ_new = μ_old + π · ε
        """
        if top_down_prediction is not None:
            self.prediction = top_down_prediction
        if bottom_up_error is not None:
            self.prediction += self.precision * bottom_up_error
        self.prediction = max(0.0, min(1.0, self.prediction))
        self.prediction_history.append(self.prediction)
        if len(self.prediction_history) > 200:
            self.prediction_history = self.prediction_history[-200:]
        return self.prediction

    def update_precision(self, error: float):
        """Adapt precision to minimize free energy.

        High sustained error → precision decreases (attentional disengagement).
        Low error → precision increases (sharpen predictions).

        Friston: precision is updated by gradient descent on free energy.
        """
        err_mag = abs(error)
        target_precision = 1.0 / max(err_mag, 0.1)
        self.precision += self.precision_lr * (target_precision - self.precision)
        self.precision = max(0.1, min(5.0, self.precision))
        self.precision_history.append(self.precision)
        if len(self.precision_history) > 200:
            self.precision_history = self.precision_history[-200:]

    def free_energy(self) -> float:
        return 0.5 * self.precision * (self.error**2) - 0.5 * math.log(
            max(self.precision, 0.1)
        )

    def to_dict(self) -> dict:
        return {
            "prediction": round(self.prediction, 4),
            "error": round(self.error, 4),
            "precision": round(self.precision, 4),
            "free_energy": round(self.free_energy(), 4),
        }


class PredictiveCodingHierarchy:
    """Hierarchical predictive coding per Friston (2010) free energy principle.

    Three-level cortical hierarchy — each level predicts the level below:

      Level 2 — PFC (Prefrontal Cortex): abstract goal/context predictions
        ↓ predictions (top-down)
      Level 1 — BG (Basal Ganglia): value/action-outcome predictions
        ↓ predictions (top-down)
      Level 0 — Sensory: raw outcome/reward predictions
        ↑ prediction errors (bottom-up)
      Level 1 — BG
        ↑ prediction errors (bottom-up)
      Level 2 — PFC

    Each level has precision weighting (attention) that modulates how
    much bottom-up error influences belief updating at the level above.

    Free energy is minimized at every level simultaneously.
    """

    def __init__(self, initial_precision: float = 1.0):
        self.levels: dict[str, HierarchicalLevel] = {
            "sensory": HierarchicalLevel("sensory", precision=initial_precision),
            "bg": HierarchicalLevel("bg", precision=initial_precision),
            "pfc": HierarchicalLevel("pfc", precision=initial_precision * 0.8),
        }
        self.total_free_energy: list[float] = []
        self.history: list[dict] = []

    def observe(self, observation: float, pfc_goal: float | None = None) -> dict:
        """Process a sensory observation through the hierarchy.

        1. Set PFC goal prediction if provided (top-down prior)
        2. Propagate predictions downward: PFC → BG → Sensory
        3. Compute prediction errors bottom-up: Sensory → BG → PFC
        4. Update precisions at every level

        Each level's prediction error (ε) represents the mismatch between
        its top-down prior and the updated belief after observing data.
        This is the core of hierarchical free energy minimization.

        Returns dict with all level states and total free energy.
        """
        if pfc_goal is not None:
            self.levels["pfc"].prediction = pfc_goal

        # Save priors before bottom-up influence for error computation
        pfc_prior = self.levels["pfc"].prediction
        bg_prior = self.levels["bg"].prediction

        # ── Top-down prediction propagation ──
        self.levels["bg"].update_prediction(
            top_down_prediction=self.levels["pfc"].prediction
        )
        self.levels["sensory"].update_prediction(
            top_down_prediction=self.levels["bg"].prediction
        )

        # ── Bottom-up error propagation ──
        # Level 0 (Sensory): observation vs prediction
        sensory_err = self.levels["sensory"].compute_error(observation)

        # Level 1 (BG): precision-weighted sensory error updates BG belief
        bg_bottom_up = self.levels["sensory"].precision * sensory_err
        self.levels["bg"].update_prediction(bottom_up_error=bg_bottom_up)
        # BG prediction error = deviation from PFC top-down prior
        bg_err = self.levels["bg"].prediction - pfc_prior
        self.levels["bg"].error = bg_err
        self.levels["bg"].error_history.append(bg_err)
        if len(self.levels["bg"].error_history) > 200:
            self.levels["bg"].error_history = self.levels["bg"].error_history[-200:]

        # Level 2 (PFC): precision-weighted BG error updates PFC context
        pfc_bottom_up = self.levels["bg"].precision * bg_err
        self.levels["pfc"].update_prediction(bottom_up_error=pfc_bottom_up)
        # PFC prediction error = deviation from its own prior (context shift)
        pfc_err = self.levels["pfc"].prediction - pfc_prior
        self.levels["pfc"].error = pfc_err
        self.levels["pfc"].error_history.append(pfc_err)
        if len(self.levels["pfc"].error_history) > 200:
            self.levels["pfc"].error_history = self.levels["pfc"].error_history[-200:]

        # ── Precision (attention) updates ──
        # Each level adjusts its precision to minimize free energy
        self.levels["sensory"].update_precision(sensory_err)
        self.levels["bg"].update_precision(bg_err)
        self.levels["pfc"].update_precision(pfc_err)

        # ── Free energy ──
        fe = sum(l.free_energy() for l in self.levels.values())
        self.total_free_energy.append(fe)
        if len(self.total_free_energy) > 200:
            self.total_free_energy = self.total_free_energy[-200:]

        result = {
            "sensory": self.levels["sensory"].to_dict(),
            "bg": self.levels["bg"].to_dict(),
            "pfc": self.levels["pfc"].to_dict(),
            "total_free_energy": round(fe, 4),
        }
        self.history.append(result)
        if len(self.history) > 200:
            self.history = self.history[-200:]
        return result

    def to_dict(self) -> dict:
        return {
            "levels": {k: v.to_dict() for k, v in self.levels.items()},
            "total_free_energy_trace": [
                round(f, 4) for f in self.total_free_energy[-20:]
            ],
        }


# ── PFC-BG Gating (Q-learning with ε-greedy) ──────────────────


class PFCBGGating:
    """Prefrontal Cortex - Basal Ganglia gating mechanism v2.

    Upgrades from research:
      1. Opponent D1/D2 pathways (Go/NoGo) — OpAL* (eLife 85107)
      2. Scaled Prediction Error — SPE model (PLOS Comp Bio 2022)
      3. ACC meta-learning via Bayesian surprise — RML model (PLOS Comp Bio 1013025)
      4. MetaStateLearner — Learnable state categories (Copernican Revolution)

    State:  (task_type, complexity_bin)  — with optional MetaStateLearner
    Action: agent_id or capability_name
    """

    def __init__(self, project: str, enable_meta_learner: bool = True):
        self.project = project
        # Meta-state learner (learnable categories)
        self.meta_learner = MetaStateLearner() if enable_meta_learner else None
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
        # Predictive coding hierarchy (Friston 2010)
        self.pc_hierarchy = PredictiveCodingHierarchy(initial_precision=1.0)
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
                # Load meta-learner state
                if self.meta_learner and "meta_learner" in data:
                    self.meta_learner.from_dict(data["meta_learner"])
                # Load predictive coding hierarchy state
                if "pc_hierarchy" in data:
                    pc_data = data["pc_hierarchy"]
                    for name, ld in pc_data.get("levels", {}).items():
                        if name in self.pc_hierarchy.levels:
                            self.pc_hierarchy.levels[name].prediction = ld.get(
                                "prediction", 0.5
                            )
                            self.pc_hierarchy.levels[name].precision = ld.get(
                                "precision", 1.0
                            )
                    self.pc_hierarchy.total_free_energy = pc_data.get(
                        "total_free_energy", []
                    )
                # Convert old _cN state keys to _lN (learned bin) format
                if self.meta_learner:
                    for d in (self.Go, self.NoGo, self.N, self.beta_g, self.beta_n):
                        for k in list(d.keys()):
                            if "_c" in k:
                                new_k = k.replace("_c", "_l")
                                d[new_k] = d.pop(k)
            except (json.JSONDecodeError, OSError):
                pass

    def save(self):
        data = {
            "Go": self.Go,
            "NoGo": self.NoGo,
            "N": self.N,
            "reward_mean": self.reward_mean,
            "reward_var": self.reward_var,
            "rpe_history": self.rpe_history[-200:],
            "alpha_surprise": self.alpha_surprise[-200:],
            "beta_surprise": self.beta_surprise[-200:],
            "epsilon": self.epsilon,
            "alpha": self.alpha,
            "beta_g": self.beta_g,
            "beta_n": self.beta_n,
        }
        if self.meta_learner:
            data["meta_learner"] = self.meta_learner.to_dict()
        data["pc_hierarchy"] = {
            "levels": {
                name: {"prediction": l.prediction, "precision": l.precision}
                for name, l in self.pc_hierarchy.levels.items()
            },
            "total_free_energy": self.pc_hierarchy.total_free_energy[-100:],
        }
        self._path().write_text(json.dumps(data, indent=2))

    def state_key(self, task_type: str, complexity: int) -> str:
        if self.meta_learner:
            return self.meta_learner.state_key(task_type, complexity)
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
        q = bg * go - bn * nogo
        if self.meta_learner:
            self.meta_learner.record(state, action, q)
        return q

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
        prediction = (
            sum(reward_window[:-1]) / len(reward_window[:-1])
            if len(reward_window) > 1
            else 0.5
        )
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

    def update(
        self,
        state: str,
        action: str,
        reward: float,
        goal_prediction: float | None = None,
        habitual_action: str | None = None,
    ):
        self._init_state_action(state, action)
        count = self.N[state][action]
        lr = self.alpha / (1 + 0.02 * count)

        # ── Hierarchical Predictive Coding ──────────────────────────
        # Process reward through the 3-level hierarchy (Friston 2010)
        pc_result = self.pc_hierarchy.observe(
            observation=reward,
            pfc_goal=goal_prediction
            if goal_prediction is not None
            else self.pc_hierarchy.levels["pfc"].prediction,
        )

        # Extract hierarchical prediction errors and precision weights
        sensory_err = pc_result["sensory"]["error"]  # Level 0: outcome PE
        bg_err = pc_result["bg"]["error"]  # Level 1: value PE
        pfc_err = pc_result["pfc"]["error"]  # Level 2: context PE
        bg_precision = pc_result["bg"]["precision"]  # BG attention weight
        pfc_precision = pc_result["pfc"]["precision"]  # PFC attention weight

        # ── SPE tracking (kept for backward compat / warm-up) ──────
        k = self._key(state, action)
        m = self.reward_mean.get(k, 0.5)
        s_sq = self.reward_var.get(k, 0.25)
        if count < 10:
            rpe_raw = reward - m
        else:
            s = max(s_sq**0.5, 0.1)
            rpe_raw = (reward - m) / s

        # Update SPE statistics (Welford's online algorithm)
        n = count + 1
        delta = reward - m
        self.reward_mean[k] = m + delta / n
        delta2 = reward - self.reward_mean[k]
        self.reward_var[k] = s_sq + (delta * delta2 - s_sq) / n

        # ── Triple DA errors via hierarchical precision weighting ───
        # δ_v (valuation): sensory prediction error × BG precision
        #   BG precision modulates how much sensory surprise drives Go/NoGo
        delta_v = sensory_err * bg_precision

        # δ_g (goal-directed): BG prediction error × PFC precision
        #   PFC precision modulates how much goal mismatch updates β weights
        delta_g = bg_err * pfc_precision

        # δ_h (habit): context error (PFC-level prediction deviation)
        delta_h = (
            1.0 if habitual_action is not None and action == habitual_action else -0.5
        )

        # ── OpAL* opponent update using δ_v (precision-weighted) ────
        self.Go[state][action] += lr * delta_v * self.Go[state][action]
        self.NoGo[state][action] += lr * (-delta_v) * self.NoGo[state][action]

        self.Go[state][action] = max(0.01, min(0.99, self.Go[state][action]))
        self.NoGo[state][action] = max(0.01, min(0.99, self.NoGo[state][action]))

        # Update β_g/β_n via δ_g (hierarchical goal error) and δ_h (habit)
        if state not in self.beta_g:
            self.beta_g[state] = {}
            self.beta_n[state] = {}
        old_bg = self.beta_g[state].get(action, 1.0)
        old_bn = self.beta_n[state].get(action, 1.0)
        self.beta_g[state][action] = max(0.1, min(3.0, old_bg + 0.05 * delta_g))
        self.beta_n[state][action] = max(0.1, min(3.0, old_bn + 0.05 * (-delta_h)))

        self.N[state][action] = count + 1

        # ACC: log sensory error as the primary RPE for surprise computation
        self.rpe_history.append(sensory_err)
        if len(self.rpe_history) > 200:
            self.rpe_history = self.rpe_history[-200:]

        self.epsilon = self._acc_surprise_epsilon(reward)

        self.efferent_feedback(state, action, delta_v)

        self.alpha = max(0.01, self.alpha * self.alpha_decay)

        meta_changes = []
        if self.meta_learner:
            self.meta_learner.record(state, action, self.get_q(state, action))
            meta_changes = self.meta_learner.meta_step(self)
        self.save()

        result = {
            "delta_v": round(delta_v, 4),
            "delta_g": round(delta_g, 4),
            "delta_h": round(delta_h, 4),
            "hierarchy": pc_result,
        }
        if meta_changes:
            result["meta_changes"] = meta_changes
        return result

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
        cleaned = re.sub(r"[^a-z0-9\s]", "", text.lower())
        words = cleaned.split()
        ngrams: list[str] = []
        for word in words:
            padded = f"  {word} " if len(word) > 2 else word
            for i in range(len(padded) - cls.N + 1):
                ngrams.append(padded[i : i + cls.N])
        ngrams.extend(f"w_{w}" for w in words)
        return collections.Counter(ngrams)

    @classmethod
    def embed(cls, text: str) -> dict[str, float]:
        total = 0
        counter = cls._ngrams(text)
        for v in counter.values():
            total += v * v
        mag = total**0.5
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

    @classmethod
    def grounded_similarity(
        cls,
        text_a: str,
        text_b: str,
        concept: str | None = None,
        visual_weight: float = 0.3,
    ) -> float:
        text_sim = cls.cosine_similarity(cls.embed(text_a), cls.embed(text_b))
        if concept is None or visual_weight <= 0:
            return text_sim

        try:
            from grounded_cognition import get_grounded_engine

            engine = get_grounded_engine()
            engine.ensure_backbones()
            embeddings = engine.visual_encoder.load_embeddings(concept)
            if not embeddings:
                return text_sim

            has_imagebind = (
                engine.concept_grounder._imagebind is not None
                and engine.concept_grounder._imagebind._imagebind_available
            )

            visual_sim = 0.0
            if has_imagebind:
                try:
                    visual_sim = engine.concept_grounder.get_cross_modal_similarity(
                        concept, text_b
                    )
                except Exception:
                    visual_sim = 0.0
            if visual_sim <= 0:
                visual_sim = text_sim

            combined = (1.0 - visual_weight) * text_sim + visual_weight * visual_sim
            return combined
        except ImportError:
            return text_sim
        except Exception:
            return text_sim


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
        self._path().write_text(
            json.dumps(
                {
                    "episodes": self.episodes[-self.max_episodes :],
                },
                indent=2,
            )
        )

    def store(
        self,
        task_id: str,
        agent_id: str,
        task_title: str,
        outcome: str,
        reward: float,
        meta: dict | None = None,
    ):
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
            self.episodes = self.episodes[-self.max_episodes :]
        self.save()

    def context_for_task(
        self, task_title: str, task_type: str = "", complexity: int = 5, top_k: int = 3
    ) -> list[dict]:
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

    def recall_similar(
        self, task_type: str, complexity: int, top_k: int = 3
    ) -> list[dict]:
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
        self._path().write_text(
            json.dumps(
                {
                    "buffer": self.buffer[-self.max_buffer :],
                },
                indent=2,
            )
        )

    def add_experience(
        self, state: str, action: str, reward: float, next_state: str = ""
    ):
        q_old = self.gating.get_q(state, action)
        td_error = abs(reward - q_old)
        need = (
            1.0 + 0.5 * (1.0 - len(self.buffer) / self.max_buffer)
            if self.buffer
            else 1.0
        )
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
            self.buffer = self.buffer[-self.max_buffer :]
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
            updates.append(
                {
                    "state": exp["state"],
                    "action": exp["action"],
                    "old_q": round(old_q, 3),
                    "new_q": round(new_q, 3),
                    "evb": exp["evb"],
                }
            )
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
                all_updates.append(
                    {
                        "state": exp["state"],
                        "action": exp["action"],
                        "old_q": round(old_q, 3),
                        "new_q": round(new_q, 3),
                        "evb": exp["evb"],
                        "signal_urgency": round(urgency, 3),
                        "learning_rate": round(lr, 3),
                    }
                )
        return all_updates

    def get_consolidate_candidates(self, min_reward: float = 0.7) -> list[dict]:
        """Consolidate phase: return top-K high-reward episodes for topic proposal."""
        return [e for e in self.buffer if e.get("reward", 0) >= min_reward]

    def get_prune_candidates(
        self, old_days: int = 30, ancient_days: int = 90, low_reward: float = 0.3
    ) -> tuple[list[dict], list[dict]]:
        """Prune phase: return (old_low_reward, ancient) episode tuples.

        old_low_reward: episodes older than `old_days` with reward < `low_reward`
        ancient: episodes older than `ancient_days` (regardless of reward)
        Both are returned as lists of (index, episode) tuples from self.buffer.
        """
        now = time.time()
        old_cutoff = now - old_days * 86400
        ancient_cutoff = now - ancient_days * 86400
        old_low = []
        ancient = []
        for i, ep in enumerate(self.buffer):
            ts = ep.get("timestamp", 0)
            if ts < ancient_cutoff:
                if ep.get("meta", {}).get("priority") != "critical":
                    ancient.append((i, ep))
            elif ts < old_cutoff and ep.get("reward", 0.5) < low_reward:
                old_low.append((i, ep))
        return old_low, ancient

    def prune_episodes(self, indices: list[int]) -> int:
        """Remove episodes at given indices from the buffer. Returns count removed."""
        if not indices:
            return 0
        to_remove = set(indices)
        before = len(self.buffer)
        self.buffer = [ep for i, ep in enumerate(self.buffer) if i not in to_remove]
        removed = before - len(self.buffer)
        if removed:
            self.save()
        return removed


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
        self._path().write_text(
            json.dumps(
                {
                    "reputation": self.reputation,
                    "responsiveness": self.responsiveness,
                    "pliancy": self.pliancy,
                    "choice": self.choice,
                    "_bid_history": {k: v[-50:] for k, v in self._bid_history.items()},
                },
                indent=2,
            )
        )

    def update_ensembles(
        self, agent_id: str, bid_time_ms: float, bid_score: float, task_type: str
    ):
        if agent_id not in self._bid_history:
            self._bid_history[agent_id] = []
        self._bid_history[agent_id].append(
            {
                "time_ms": bid_time_ms,
                "score": bid_score,
                "task_type": task_type,
                "ts": time.time(),
            }
        )
        recent = self._bid_history[agent_id][-20:]
        if not recent:
            return
        speeds = [b["time_ms"] for b in recent if b["time_ms"] > 0]
        self.responsiveness[agent_id] = (
            1.0 / (sum(speeds) / len(speeds) + 1) if speeds else 0.5
        )
        scores = [b["score"] for b in recent]
        mean_s = sum(scores) / len(scores)
        self.pliancy[agent_id] = (
            sum((s - mean_s) ** 2 for s in scores) / len(scores)
            if len(scores) > 1
            else 0.0
        )
        self.choice[agent_id] = max(scores) - mean_s

    def get_reputation(self, agent_id: str) -> float:
        return self.reputation.get(agent_id, 0.5)

    def compute_bid(
        self, agent_id: str, capabilities: list[str], task_type: str, complexity: int
    ) -> dict:
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
        score = (
            0.4 * confidence
            + 0.2 * rep
            + 0.2 * ens_resp
            + 0.1 * ens_choice
            + 0.1 * random.random()
        )
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
        self._path().write_text(
            json.dumps(
                {"subgoal_stack": self.subgoal_stack[-self.max_depth :]}, indent=2
            )
        )

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

    def decompose(
        self, title: str, description: str, complexity: int = 5
    ) -> list[dict]:
        """Hierarchical decomposition with recursive subgoal generation."""
        lines = [l for l in description.split("\n") if l.strip()]
        if len(lines) <= 1:
            subtask = {
                "step": 1,
                "description": title,
                "depends_on": [],
                "subtasks": [],
                "depth": 0,
            }
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
                    subtasks[-1].setdefault("subtasks", []).append(
                        {
                            "step": len(subtasks[-1].get("subtasks", [])) + 1,
                            "description": line.strip().lstrip("- ").lstrip("* "),
                            "depends_on": [len(subtasks)],
                            "subtasks": [],
                            "depth": depth + 1,
                        }
                    )
                else:
                    subtasks.append(
                        {
                            "step": i + 1,
                            "description": line.strip().lstrip("- ").lstrip("* "),
                            "depends_on": [i] if i > 0 else [],
                            "subtasks": [],
                            "depth": depth,
                        }
                    )
            return subtasks

        subtasks = _build_tree(lines)
        if self.wm:
            for st in subtasks:
                self.wm.push(st["description"], title)
        return subtasks

    @staticmethod
    def priority(task_type: str, complexity: int, queue_length: int) -> int:
        base = {
            "bugfix": 10,
            "critical": 9,
            "deploy": 8,
            "coding": 6,
            "research": 4,
            "review": 3,
            "documentation": 2,
            "generic": 5,
        }
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
        self._path().write_text(
            json.dumps(
                {
                    "assumptions": self.assumptions[-100:],
                    "dialectic_records": self.dialectic_records[-50:],
                },
                indent=2,
            )
        )

    def question_goal(self, title: str, description: str) -> list[dict]:
        """Generate Socratic questions for a task goal.

        Returns structured questions with category and Socratic target.
        """
        goal = title or description[:60]
        questions = []
        for i, q_template in enumerate(self.SOCRATIC_QUESTIONS):
            q = q_template.replace("{goal}", goal)
            category = (
                "definition"
                if i == 0
                else "measurement"
                if i == 1
                else "assumption"
                if i in (2, 3)
                else "motivation"
                if i in (4, 5)
                else "knowledge"
                if i == 6
                else "consequence"
                if i == 7
                else "blindspot"
                if i == 8
                else "reframing"
            )
            questions.append(
                {
                    "id": i,
                    "question": q,
                    "category": category,
                    "target": goal,
                    "answer": None,
                    "assumption_exposed": None,
                }
            )
        return questions

    def record_answer(
        self, question_id: int, answer: str, assumption: str | None = None
    ):
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

    def refine_task(
        self, title: str, description: str, answers: list[dict] | None = None
    ) -> dict:
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
                "\n\n[Assumptions exposed by Socratic questioning]\n"
                + "\n".join(f"- {a}" for a in refined["exposed_assumptions"])
            )
        if refined["success_criteria"]:
            refined["refined_title"] = refined["refined_title"]

        return refined

    def detect_dialectic(
        self, task_id: str, agent_a: str, agent_b: str, position_a: str, position_b: str
    ) -> dict | None:
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

    def __init__(self, project: str, enable_meta_learner: bool = True):
        self.project = project
        self.gating = PFCBGGating(project, enable_meta_learner=enable_meta_learner)
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
        similar = self.hippocampus.context_for_task(
            meta.get("title", ""), task_type, complexity
        )
        bids = []
        for agent in agents:
            aid = agent.get("id", "")
            caps = agent.get("capabilities", ["general"])
            if not isinstance(caps, list):
                caps = ["general"]
            bid = self.auction.compute_bid(aid, caps, task_type, complexity)
            # Cold-start: if state has never been visited, skip gate
            n_visits = sum(self.gating.N.get(state, {}).values())
            if n_visits == 0:
                bids.append(bid)
                continue
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
            "gate_confidence": max(
                (self.gating.get_q(state, a) for a in self.gating.Go.get(state, {})),
                default=0.5,
            ),
        }

    def record_outcome(
        self,
        task_id: str,
        agent_id: str,
        task_title: str,
        task_type: str,
        complexity: int,
        reward: float,
    ):
        state = self.gating.state_key(task_type, complexity)
        da_signals = self.gating.update(state, agent_id, reward)
        self.auction.update_reputation(agent_id, reward)
        self.hippocampus.store(
            task_id,
            agent_id,
            task_title,
            "completed" if reward >= 0.5 else "failed",
            reward,
            {"type": task_type, "complexity": complexity},
        )
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
        status = {
            "project": self.project,
            "epsilon": round(self.gating.epsilon, 4),
            "q_table_size": len(self.gating.Go),
            "q_summary": q_summary,
            "episodes": len(self.hippocampus.episodes),
            "wm_stack_depth": len(self.wm.subgoal_stack) if self.wm else 0,
            "wm_active_goals": [
                sg["goal"][:40]
                for sg in (self.wm.subgoal_stack if self.wm else [])
                if sg.get("status") == "active"
            ],
            "replay_buffer": len(self.replay.buffer),
            "replay_evb_top": round(max(e["evb"] for e in self.replay.buffer), 3)
            if self.replay.buffer
            else 0.0,
            "agents_tracked": len(self.auction.reputation),
            "avg_reputations": {
                a: round(r, 3) for a, r in self.auction.reputation.items()
            },
            "alpha_surprise": round(self.gating.alpha_surprise[-1], 4)
            if self.gating.alpha_surprise
            else 0,
            "beta_surprise": round(self.gating.beta_surprise[-1], 4)
            if self.gating.beta_surprise
            else 0,
            "avg_responsiveness": round(
                sum(self.auction.responsiveness.values())
                / max(len(self.auction.responsiveness), 1),
                3,
            )
            if self.auction.responsiveness
            else 0,
            "avg_choice": round(
                sum(self.auction.choice.values()) / max(len(self.auction.choice), 1), 3
            )
            if self.auction.choice
            else 0,
            "socratic_assumptions": len(self.socrates.assumptions),
            "socratic_dialectics": len(self.socrates.dialectic_records),
            "meta_learner_enabled": self.gating.meta_learner is not None,
            "pc_hierarchy": self.gating.pc_hierarchy.to_dict(),
        }
        if self.gating.meta_learner:
            status["meta_learner"] = {
                "boundaries": [
                    round(b, 3) for b in self.gating.meta_learner.boundaries
                ],
                "total_updates": self.gating.meta_learner.total_updates,
                "last_meta_step": self.gating.meta_learner.last_meta_step,
                "proposals": len(self.gating.meta_learner.proposal_history),
                "recent_changes": self.gating.meta_learner.proposal_history[-3:]
                if self.gating.meta_learner.proposal_history
                else [],
            }
        return status


# ── Global registry of engines ────────────────────────────────

_engines: dict[str, CortexEngine] = {}


def get_engine(project: str) -> CortexEngine:
    if project not in _engines:
        _engines[project] = CortexEngine(project)
    return _engines[project]
