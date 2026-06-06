# CORTEX: Brain-Inspired Multi-Agent Task Allocation
## Comprehensive Research Reference

## Overview

This document synthesizes findings from 17+ research papers on brain function
and multi-agent systems to inform the CORTEX (Coordinated Orchestration via
Reinforcement-learning Task Execution) architecture. Each brain region's
computational function is mapped to an agent-system analogue, with direct
implementation guidance for memoria.

---

## 1. Prefrontal Cortex (PFC) — Hierarchical Task Decomposition

### Key Papers

| Paper | Year | Key Insight |
|-------|------|-------------|
| LLM-PFC (arXiv 2310.00194) | 2023 | PFC modules: TaskDecomposer, Actor, Monitor, Predictor, Evaluator, Orchestrator |
| MAP (Nature Comms 63804) | 2025 | Modular Agentic Planner: error monitoring, action proposal, state prediction, evaluation, task decomposition, coordination |
| BMAS (OpenReview YqFLsI44vN) | 2025 | PFC-like module for hierarchical task decomposition + working memory |
| HiBerNAC (arXiv 2506.08296) | 2025 | Prefrontal Planner (PFP) at 10⁻² Hz for high-level reasoning |

### Neuroscientific Basis

The PFC is organized along a rostro-caudal axis:
- **Anterior PFC** (aPFC): Abstract goal representation, subgoal generation
- **Dorsolateral PFC** (dlPFC): Rule-based reasoning, working memory
- **Ventromedial PFC** (vmPFC): Value computation, arbitration

Botvinick (2008) formalized PFC as maintaining subgoal stacks — the direct
computational analogue of hierarchical task decomposition in multi-agent systems.

### Implementation in CORTEX

```
PrefrontalDecomposer:
  decompose(title, description) → list of subtasks
  priority(task_type, complexity, queue_length) → priority score 1-20
```

**Key equations** (from MAP/Botvinick):

```
Subgoal generation:  Z = TaskDecomposer(state, goal)
State evaluation:    V(s) = Evaluator(Predictor(s, a))
Goal verification:  achieved = Orchestrator(state, subgoal)
```

---

## 2. Basal Ganglia — Action Selection & Gating

### Key Papers

| Paper | Year | Key Insight |
|-------|------|-------------|
| Cortex-BG Loop (arXiv 2402.13275) | 2024 | Cortex predicts actions, BG uses RL for Go/NoGo gating |
| Adaptive Chunking (eLife 97894) | 2025 | PBWM: PFC-BG gating with dopamine RL, chunking for WM capacity |
| CBGT Decision Policies (PLOS Comp Bio) | 2025 | Three control ensembles: responsiveness, pliancy, choice |
| OpAL* (eLife 85107) | 2023 | Opponent D1/D2 pathways for Go/NoGo, environmental richness modulation |
| SPE Model (PLOS Comp Bio) | 2022 | Uncertainty-scaled prediction errors in BG |
| Striatal Action Selection (eLife 101747) | 2025 | Off-policy RL in striatum, efferent multiplexing |

### Neuroscientific Basis

The basal ganglia consist of:
- **Striatum** (input): D1 "Go" + D2 "NoGo" pathways
- **GPi/SNr** (output): Tonic inhibition of thalamus
- **GPe** (indirect): Modulates GPi via double inhibition
- **SNc/VTA** (modulator): Dopamine RPE signal

**Direct pathway** (Go): D1 → GPi(−) → Thalamus(+) → Cortex → action selected
**Indirect pathway** (NoGo): D2 → GPe(−) → GPi(+) → Thalamus(−) → action suppressed

### Key Equations

**Scaled Prediction Error** (SPE model):
```
δ = (r - m) / s
  where m = mean reward, s = std dev of reward
```

**OpAL* opponent learning**:
```
G(a) ← G(a) + α_G * δ * G(a)    (D1: Go)
N(a) ← N(a) + α_N * (-δ) * N(a) (D2: NoGo)
Action value:  Act(a) = β_g * G(a) - β_n * N(a)
```

**Dopaminergic RPE** (classic):
```
δ = r - Q(s, a)
Q(s, a) ← Q(s, a) + α * δ
```

### Implementation in CORTEX

```
PFCBGGating:
  state_key(task_type, complexity) → "tasktype_cN"
  select_action(state, available_actions) → best_action (ε-greedy)
  should_gate(state, action, threshold) → bool
  update(state, action, reward) → RPE
```

Three control ensembles for the auction (per CBGT paper):
1. **Responsiveness**: How quickly agents bid (response time)
2. **Pliancy**: How much agents adapt bid score to task type (flexibility)
3. **Choice**: How strongly agents prefer certain task types (specialization)

---

## 3. Dopamine System — Reward Prediction Error

### Key Papers

| Paper | Year | Key Insight |
|-------|------|-------------|
| Dopamine role in learning (PMC 7392608) | 2020 | DopAct: DA encodes errors in both reward prediction AND action prediction |
| SPE Model (PLOS Comp Bio) | 2022 | Uncertainty-scaled DA responses |
| OpAL* (eLife 85107) | 2023 | DA state modulates exploration/exploitation balance |

### Neuroscientific Basis

Dopamine neurons fire in two modes:
- **Phasic burst**: Positive RPE (outcome better than expected)
- **Phasic dip**: Negative RPE (outcome worse than expected)
- **Tonic**: Baseline encoding of average reward rate ("environmental richness")

Three distinct DA prediction errors (DopAct framework):
1. **δ_v** (valuation system): r - expected_value
2. **δ_g** (goal-directed system): r - goal_prediction  
3. **δ_h** (habit system): chosen_action - habitual_action

### Implementation in CORTEX

The gating module's `update()` computes dopaminergic RPE:
```
RPE = reward - Q_old
Q_new = Q_old + α/(1 + 0.02*N) * RPE  (decaying learning rate)
```

Scaled by uncertainty (future enhancement):
```
δ_scaled = (reward - Q_old) / (reward_variance + ε)
```

Adaptive ε-greedy (DA tonic analogue):
```
epsilon = max(0.05, epsilon * 0.999)  // decays as dopamine tonic
```

---

## 4. Hippocampus — Episodic Memory & Replay

### Key Papers

| Paper | Year | Key Insight |
|-------|------|-------------|
| Memory Consolidation from RL (Frontiers) | 2025 | Hippocampus = Dyna-style offline RL: CA3 simulates, CA1 evaluates |
| Hippocampal Replay Compositional (Nature Neuro) | 2025 | Replay builds compositional state spaces for policy generalization |
| Generative Memory Model (Nature Human Behaviour) | 2024 | Replay trains generative models (VAEs) for memory construction |
| Replay & Ripples in Humans (Ann Review Neuro) | 2025 | SPW-Rs prioritize experiences for consolidation |
| Awake Replay (Trends Neurosci) | 2025 | Replay performs fictive learning, not online planning |
| Context-Driven Replay (Penn) | 2025 | Replay = context-guided memory reactivation, not RL |

### Neuroscientific Basis

- **CA3**: Recurrent collaterals → pattern completion, simulation generation
- **CA1**: Value encoding, selects high-value replays
- **Sharp-Wave Ripples (SWRs)**: 150-250 Hz oscillations during replay
- **Replay types**: Forward (planning) + Reverse (credit assignment) + Novel (composition)

### Key Equations

**Hippocampal Dyna** (Memory Consolidation paper):
```
Simulated experience:  (s', r) = Model(s, a)
Q-learning update:     Q(s, a) ← Q(s, a) + α[r + γ max_a' Q(s', a') - Q(s, a)]
```

**EVB-based replay prioritization** (expected value of backup):
```
EVB(s, a) = |TD_error(s, a)| * need(s, a)
  where need = expected future encounter probability
```

### Implementation in CORTEX

```
HippocampalMemory:
  store(task_id, agent_id, title, outcome, reward, meta)
  recall_similar(task_type, complexity, top_k) → [past_episodes]
  avg_reward_for_agent(agent_id) → float
```

Memory consolidation loop (background, every 15s):
```
For each completed task:
  1. Store episode in hippocampus
  2. Compute similarity with past episodes
  3. Update gating Q-values (RPE-driven)
  4. Propagate reputation update
```

---

## 5. Anterior Cingulate Cortex (ACC) — Conflict Monitoring & Control

### Key Papers

| Paper | Year | Key Insight |
|-------|------|-------------|
| Meta-RL in ACC (PLOS Comp Bio 1013025) | 2025 | ACC = meta-learner: Bayesian surprise → cognitive control optimization |
| EVC Theory (Nature Neuro 4384) | 2016 | Expected Value of Control: ACC computes cost/benefit of control allocation |
| PRO Model (Topics Cog Sci) | 2019 | ACC predicts outcomes, signals unsigned prediction error (surprise) |
| ACC Value (PMC 7116891) | 2020 | ACC encodes search value + engage value for persist/switch decisions |

### Neuroscientific Basis

ACC integrates:
- **Conflict monitoring**: Response competition (Botvinick 2001)
- **Error detection**: Unexpected outcomes (Gehring 1993)
- **Surprise tracking**: Unsigned prediction error (PRO model)
- **Control allocation**: Expected Value of Control (EVC theory)
- **Meta-learning**: Optimizing control policies via Bayesian surprise (meta-RL)

Three ACC control ensembles:
1. **Responsiveness**: Reaction time modulation
2. **Pliancy**: Adaptation rate to feedback
3. **Choice**: Action preference stability

### Key Equations

**PRO model** (unsigned prediction error):
```
Negative surprise:  α_j(t) = max(0, p_j(t-1) - o_j(t))  // predicted but didn't occur
Positive surprise:  β_j(t) = max(0, o_j(t) - p_j(t-1))  // occurred but not predicted
ACC_activity = α + β  (non-valence-specific surprise)
```

**EVC theory**:
```
EVC(s) = max_control [ Σ_i V(i|s, control) * P(i|s, control) - Cost(control) ]
```

**Meta-RL (RML) model**:
```
Boost_selection = f(Bayesian_surprise, state)
Action_value = g(Q_MB, Q_MF, boost_level)
```

### Implementation in CORTEX

ACC analogue in auction coordinator:
- **Conflict detection**: When two agents bid equally → ACC flags conflict
- **Surprise signal**: When winning agent performs unexpectedly → ACC adjusts
- **Control allocation**: When task complexity > threshold → increase gate threshold
- **Persist/switch**: When agent reputation drops below threshold → switch agent

```
ACC_ensembles:
  responsiveness = 1 / (avg_bid_time_ms + 1)
  pliancy = variance(bid_scores_across_task_types)
  choice = max(bid_score) - mean(bid_scores)
```

---

## 6. Thalamus — Gating & Relay

### Key Papers

| Paper | Year | Key Insight |
|-------|------|-------------|
| Cortex-BG Loop (arXiv 2402.13275) | 2024 | Thalamus: inhibitory from BG (NoGo) → disinhibition (Go) → cortical activation |

### Neuroscientific Basis

- **Thalamus**: Relay station, receives BG inhibition, gates cortical output
- **BG → Thalamus**: GPi tonically inhibits thalamus
- **Go signal**: D1 pathway suppresses GPi → thalamus disinhibited → cortex activates
- **NoGo signal**: D2 pathway excites GPi → thalamus inhibited → cortex suppressed

### Implementation in CORTEX

The task board acts as the thalamus:
- Task goes to "pending" (thalamus inhibited) → no agent acts
- Task goes to "assigned" (thalamus disinhibited) → agent receives Go signal
- Task goes to "completed" (thalamus reset) → agent output gated to environment

---

## 7. Basal Ganglia Microcircuits — Fine-Grained Implementation

### Key Pathways

```
Cortex → Striatum (D1) → GPi(-) → Thalamus(+) → Cortex (Go)
Cortex → Striatum (D2) → GPe(-) → GPi(+) → Thalamus(-) → Cortex (NoGo)
        ↑                   ↑
    SNc (DA)           STN (hyperdirect)
```

### Action Selection Dynamics (per Striatal Action Selection paper)

1. **Cortex proposes**: Multiple action candidates activated
2. **Striatum evaluates**: D1 pushes Go, D2 pushes NoGo
3. **Winner-take-all**: Thalamus disinhibited for highest-value action
4. **Efferent feedback**: After selection, both D1+D2 of chosen action fire (learning mode)

### Implementation in CORTEX

The gating module implements a simplified BG:
- **D1 (Go)**: Q-values for task-agent pairs (positive RPE strengthens)
- **D2 (NoGo)**: Inverse Q-values (negative RPE strengthens D2, suppressing bad actions)
- **Winner-take-all**: Softmax action selection based on Q(s,a)
- **Efferent feedback**: After task completion, update both Go + NoGo weights

---

## 8. Memory Consolidation & Sleep Cycles

### Key Papers

| Paper | Year | Key Insight |
|-------|------|-------------|
| Generative Model of Memory (Nature Hum Behav) | 2024 | Replay trains generative models: hippocampus → VAE → neocortex |
| Memory Consolidation from RL (Frontiers) | 2025 | Consolidation = offline RL (Dyna-style) |
| Replay & Ripples (Ann Review Neuro) | 2025 | SPW-Rs = cognitive biomarker for episodic memory |

### Implementation Guidance

Memoria already has `SLEEP_CYCLE_HOURS` (default 6) which triggers
`_deep_consolidate()`. This maps to hippocampal replay during sleep:

```
Sleep cycle (every 6 hours):
  1. Collect all completed task episodes
  2. Compute similarity clusters
  3. For each cluster, run offline Q-learning updates
  4. Generate topic proposals from consolidated patterns
  5. Prune low-value memories (episodes with reward < 0.2)
```

---

## 9. Multi-Agent System Integration Patterns

### Brain-Inspired Architectures

| System | Brain Mapping | Agent Architecture |
|--------|---------------|-------------------|
| BMAS | PFC + Hippocampus | Task decomposer + dual memory |
| MAP | PFC subregions | 6 specialized modules |
| HiBerNAC | PFC + Hippocampus + Motor | Prefrontal Planner + Specialist Agents |
| Meta-Dyna | PFC meta-control | Model-based/Model-free arbitration |
| Diagon | Market economy | Trader + Worker split |
| MemMA | Memory cycle | Meta-Thinker + Memory Manager + Query Reasoner |

### CORTEX-Specific Architecture

```
                   ┌─────────────────────┐
                   │   External Input     │
                   │  (tasks from agents) │
                   └──────────┬──────────┘
                              │
                   ┌──────────▼──────────┐
                   │  ACC (Meta-RL)      │
                   │  surprise tracking  │
                   │  control allocation │
                   └──────────┬──────────┘
                              │
              ┌───────────────┼───────────────┐
              │               │               │
   ┌──────────▼──────┐  ┌────▼────┐  ┌───────▼───────┐
   │ PFC (Decomposer)│  │ BG Gate │  │ Hippocampus    │
   │ task breakdown  │  │ select  │  │ episodic store │
   │ subgoal gen     │  │ Go/NoGo│  │ similarity     │
   └─────────────────┘  └────┬────┘  └───────┬───────┘
                              │               │
                   ┌──────────▼───────────────▼──┐
                   │  Auction Coordinator        │
                   │  (Basal Ganglia output)     │
                   │  bid computation + winner   │
                   └──────────┬──────────────────┘
                              │
                   ┌──────────▼──────────┐
                   │  Thalamus (Task     │
                   │  Board relay)       │
                   └──────────┬──────────┘
                              │
                   ┌──────────▼──────────┐
                   │  Motor (Agent       │
                   │  execution)         │
                   └─────────────────────┘
```

### Loop Frequencies (per HiBerNAC)

| Loop | Frequency | Function | Brain Region |
|------|-----------|----------|-------------|
| Deliberative | 10⁻² Hz (every 100s) | Task decomposition, goal setting | PFC |
| Learning | 10⁻¹ Hz (every 10s) | Q-learning updates, reputation | BG + DA |
| Reactive | 10⁰ Hz (every 1s) | Bid computation, task polling | Striatum |
| Consolidation | 1 / 6 hours | Deep replay, memory pruning | Hippocampus |

---

## 10. Papers Directory — /home/daivolt/memoria/papers/

### Downloaded Papers (17 total)

| # | File | Topic | Source |
|---|------|-------|--------|
| 1 | BMAS_brain_inspired_multi_agent.pdf | PFC-guided MAS + dual memory | OpenReview 2025 |
| 2 | MAP_modular_agentic_planner.pdf | PFC-inspired LLM modules | Nature Comms 2025 |
| 3 | HiBerNAC_brain_emulated_robotic.pdf | Hierarchical brain-emulated agents | arXiv 2025 |
| 4 | PFC_architecture_planning_LLMs.pdf | PFC modules for planning | arXiv 2023 |
| 5 | cortex_basal_ganglia_loop.pdf | Cortex-BG-thalamus model | arXiv 2024 |
| 6 | adaptive_chunking_PFC_BG_WM.pdf | PBWM chunking, DA gating | eLife 2025 |
| 7 | meta_RL_ACC_surprise_value_control.pdf | Meta-RL in anterior cingulate | PLOS 2025 |
| 8 | CBGT_decision_policies.pdf | CBGT subnetworks, control ensembles | PLOS 2025 |
| 9 | hippocampal_replay_compositional.pdf | Compositional replay | Nature Neuro 2025 |
| 10 | memory_consolidation_RL_perspective.pdf | Hippocampal Dyna | Frontiers 2025 |
| 11 | generative_memory_construction.pdf | VAE + replay for memory | Nature Hum Behav 2024 |
| 12 | striatal_action_selection_RL.pdf | Off-policy RL in striatum | eLife 2025 |
| 13 | dino.pdf | Vision foundation model | Existing |
| 14 | dinov2.pdf | Vision foundation model | Existing |
| 15 | ijepa.pdf | Self-supervised vision | Existing |
| 16 | vjepa.pdf | Video joint embedding | Existing |
| 17 | vjepa2.pdf | Video joint embedding v2 | Existing |

---

## 11. CORTEX Implementation Status & Future Directions

### Currently Implemented

| Component | Brain Region | Status |
|-----------|-------------|--------|
| Q-learning with ε-greedy | BG (D1/D2) | ✅ Core |
| Reward Prediction Error | SNc/VTA (DA) | ✅ Core |
| Decaying learning rate α/(1+0.02N) | Striatal plasticity | ✅ Core |
| Episodic memory store | Hippocampus (CA3) | ✅ Core |
| Similarity-based recall | Hippocampus (pattern completion) | ✅ Core |
| Auction protocol | Thalamus (winner-take-all) | ✅ Core |
| Reputation tracking | ACC (value signals) | ✅ Core |
| Background polling loop | BG (tonic) | ✅ Server |

### Planned Enhancements

| Enhancement | Brain Region | Priority |
|-------------|-------------|----------|
| Model-based planning (task decomposition) | PFC (aPFC) | High |
| Scaled prediction error (δ = (r-m)/s) | SNc + Striatum | Medium |
| OpAL* opponent D1/D2 weights | Striatum | Medium |
| ACC meta-learning for ε adaptation | ACC (RML) | Medium |
| Sleep cycle consolidation (Dyna replay) | Hippocampus (SWRs) | Medium |
| Hierarchical RL (options framework) | PFC-BG hierarchy | Low |
| Efferent feedback multiplexing | Striatum (learning mode) | Low |

---

## References

1. BMAS: Brain-inspired Multi-Agent System (2025). OpenReview YqFLsI44vN.
2. MAP: Modular Agentic Planner. Nature Communications 16, 8633 (2025).
3. HiBerNAC: Hierarchical Brain-emulated Robotic Neural Agent Collective. arXiv 2506.08296 (2025).
4. LLM-PFC: Prefrontal Cortex-inspired Architecture for Planning. arXiv 2310.00194 (2023).
5. Cortex-BG Loop Model. arXiv 2402.13275 (2024).
6. Adaptive Chunking in PFC-BG. eLife 97894 (2025).
7. Meta-RL in ACC. PLOS Computational Biology 1013025 (2025).
8. CBGT Decision Policies. PLOS Computational Biology 1013712 (2025).
9. Hippocampal Replay Compositional. Nature Neuroscience (2025).
10. Memory Consolidation from RL. Frontiers Comp Neurosci 18:1538741 (2025).
11. Generative Model of Memory. Nature Human Behaviour (2024).
12. Striatal Action Selection. eLife 101747 (2025).
13. OpAL*: Dopamine and Striatal Opponency. eLife 85107 (2023).
14. SPE Model: Uncertainty-Guided Learning. PLOS Comp Bio (2022).
15. DopAct: Dopamine Role in Learning. PMC 7392608 (2020).
16. EVC Theory: ACC Value of Control. Nature Neuroscience 4384 (2016).
17. PRO Model: ACC Prediction & Surprise. Topics Cog Sci (2019).
