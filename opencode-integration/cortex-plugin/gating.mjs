const MEMORIA_URL = "http://localhost:19998";

async function fetchJSON(path, opts) {
  const resp = await fetch(`${MEMORIA_URL}${path}`, {
    signal: AbortSignal.timeout(3000),
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (!resp.ok) {
    const text = await resp.text().catch(() => "");
    throw new Error(`HTTP ${resp.status}: ${text.slice(0, 200)}`);
  }
  return resp.json();
}

export class MetaStateLearner {
  constructor(nBins = 5, metaInterval = 50) {
    this.nBins = nBins;
    this.metaInterval = metaInterval;

    // Normalized [0,1] bin boundaries
    this.boundaries = Array.from({ length: nBins - 1 }, (_, i) => (i + 1) / nBins);

    // Per-state-action Q-value statistics (Welford online)
    this.qMeans = {};
    this.qVars = {};
    this.qCounts = {};

    // Meta-learning thresholds
    this.mergeThreshold = 0.85;
    this.splitThreshold = 0.25;
    this.boundaryLr = 0.03;
    this.minBoundaryGap = 0.05;
    this.balanceTarget = 0.25;

    this.totalUpdates = 0;
    this.lastMetaStep = 0;
    this.proposalHistory = [];
    this.warmupSteps = 100;
  }

  stateKey(taskType, complexity) {
    const tt = taskType || "generic";
    const binIdx = this._complexityToBin(complexity);
    return `${tt}_l${binIdx}`;
  }

  _complexityToBin(complexity) {
    const cNorm = complexity / 10.0;
    for (let i = 0; i < this.boundaries.length; i++) {
      if (cNorm < this.boundaries[i]) return i;
    }
    return this.boundaries.length;
  }

  record(state, action, qValue) {
    if (!this.qMeans[state]) {
      this.qMeans[state] = {};
      this.qVars[state] = {};
      this.qCounts[state] = {};
    }
    if (this.qMeans[state][action] === undefined) {
      this.qMeans[state][action] = qValue;
      this.qVars[state][action] = 0.0;
      this.qCounts[state][action] = 1;
    } else {
      const n = this.qCounts[state][action];
      const oldMean = this.qMeans[state][action];
      const delta = qValue - oldMean;
      const newMean = oldMean + delta / (n + 1);
      const delta2 = qValue - newMean;
      this.qVars[state][action] = (this.qVars[state][action] * n + delta * delta2) / (n + 1);
      this.qMeans[state][action] = newMean;
      this.qCounts[state][action] = n + 1;
    }
    this.totalUpdates++;
  }

  metaStep(gating) {
    if (this.totalUpdates < this.warmupSteps) return [];
    if (this.totalUpdates - this.lastMetaStep < this.metaInterval) return [];

    this.lastMetaStep = this.totalUpdates;
    const changes = [];

    // 1. Merge adjacent bins with similar Q-tables
    const taskTypes = this._detectTaskTypes(gating);
    for (const tt of taskTypes) {
      for (let b = 0; b < this.boundaries.length; b++) {
        const s1 = `${tt}_l${b}`;
        const s2 = `${tt}_l${b + 1}`;
        if (gating.Go[s1] && gating.Go[s2]) {
          const sim = this._stateCosineSim(gating, s1, s2);
          if (sim >= this.mergeThreshold) {
            this._mergeStates(gating, s1, s2);
            changes.push(`merged ${s2}→${s1} (sim=${sim.toFixed(3)})`);
            break;
          }
        }
      }
    }

    // 2. Split high-variance states
    for (const state of Object.keys(gating.Go)) {
      if (this.qVars[state]) {
        const varsList = Object.values(this.qVars[state]).filter(v => v > 0);
        if (varsList.length > 0) {
          const avgVar = varsList.reduce((a, b) => a + b, 0) / varsList.length;
          if (avgVar > this.splitThreshold) {
            this._splitState(gating, state);
            changes.push(`split ${state} (avg_var=${avgVar.toFixed(3)})`);
          }
        }
      }
    }

    // 3. Rebalance boundaries
    const visitDist = this._visitDistribution(gating);
    if (visitDist && visitDist.length > 1) {
      const maxV = Math.max(...visitDist);
      const minV = Math.min(...visitDist);
      if (maxV > 0 && minV / maxV < this.balanceTarget) {
        this._rebalanceBoundaries(visitDist);
        changes.push(`rebalanced boundaries: ${this.boundaries.map(b => b.toFixed(3)).join(", ")}`);
      }
    }

    // 4. Adjust boundaries by variance
    this._adjustBoundariesByVariance(gating);

    if (changes.length > 0) {
      this._cleanup(gating);
      this.proposalHistory.push({
        t: Date.now() / 1000,
        changes: [...changes],
        boundaries: [...this.boundaries],
      });
    }

    return changes;
  }

  _detectTaskTypes(gating) {
    const types = new Set();
    for (const s of Object.keys(gating.Go)) {
      for (const sep of ["_l", "_c"]) {
        const idx = s.lastIndexOf(sep);
        if (idx !== -1) {
          types.add(s.substring(0, idx));
          break;
        }
      }
    }
    return [...types].sort();
  }

  _stateCosineSim(gating, s1, s2) {
    const actions = new Set([
      ...Object.keys(gating.Go[s1] || {}),
      ...Object.keys(gating.Go[s2] || {}),
    ]);
    const v1 = [];
    const v2 = [];
    const getQ = (state, action) => {
      const go = gating.Go[state]?.[action] || 0.5;
      const no = gating.NoGo[state]?.[action] || 0.5;
      const bg = gating.betaG[state]?.[action] ?? 1.0;
      const bn = gating.betaN[state]?.[action] ?? 1.0;
      return bg * go - bn * no;
    };
    for (const a of actions) {
      v1.push(getQ(s1, a));
      v2.push(getQ(s2, a));
    }
    const dot = v1.reduce((s, v, i) => s + v * v2[i], 0);
    const n1 = Math.sqrt(v1.reduce((s, v) => s + v * v, 0)) || 1;
    const n2 = Math.sqrt(v2.reduce((s, v) => s + v * v, 0)) || 1;
    return dot / (n1 * n2);
  }

  _mergeStates(gating, keep, drop) {
    if (!gating.Go[drop]) return;
    const allActions = new Set([
      ...Object.keys(gating.Go[keep] || {}),
      ...Object.keys(gating.Go[drop] || {}),
    ]);
    for (const a of allActions) {
      if (!gating.Go[keep]) gating.Go[keep] = {};
      if (!gating.NoGo[keep]) gating.NoGo[keep] = {};
      if (!gating.N[keep]) gating.N[keep] = {};
      if (gating.Go[keep][a] === undefined) gating.Go[keep][a] = 0.5;
      if (gating.NoGo[keep][a] === undefined) gating.NoGo[keep][a] = 0.5;
      if (gating.N[keep][a] === undefined) gating.N[keep][a] = 0;

      const kc = gating.N[keep]?.[a] || 0;
      const dc = gating.N[drop]?.[a] || 0;
      const total = kc + dc;
      if (total > 0) {
        const kw = kc / total;
        const dw = dc / total;
        gating.Go[keep][a] = kw * (gating.Go[keep]?.[a] || 0.5) + dw * (gating.Go[drop]?.[a] || 0.5);
        gating.NoGo[keep][a] = kw * (gating.NoGo[keep]?.[a] || 0.5) + dw * (gating.NoGo[drop]?.[a] || 0.5);
        gating.N[keep][a] = total;
      }
    }
    for (const d of ["Go", "NoGo", "N", "betaG", "betaN"]) {
      delete gating[d][drop];
    }
    for (const d of ["qMeans", "qVars", "qCounts"]) {
      delete this[d][drop];
    }
  }

  _splitState(gating, state) {
    for (const sep of ["_l", "_c"]) {
      const idx = state.lastIndexOf(sep);
      if (idx === -1) continue;
      const binIdx = parseInt(state.substring(idx + 2), 10);
      if (isNaN(binIdx)) return;

      let leftEdge, rightEdge, insertPos;
      if (binIdx >= this.boundaries.length) {
        insertPos = this.boundaries.length;
        leftEdge = insertPos > 0 ? this.boundaries[insertPos - 1] : 0.0;
        rightEdge = 1.0;
      } else {
        insertPos = binIdx;
        leftEdge = insertPos > 0 ? this.boundaries[insertPos - 1] : 0.0;
        rightEdge = this.boundaries[insertPos];
      }

      let newBoundary = (leftEdge + rightEdge) / 2;

      if (insertPos > 0) {
        newBoundary = Math.max(newBoundary, this.boundaries[insertPos - 1] + this.minBoundaryGap);
      }
      if (insertPos < this.boundaries.length) {
        newBoundary = Math.min(newBoundary, this.boundaries[insertPos] - this.minBoundaryGap);
      }

      if (newBoundary > leftEdge + this.minBoundaryGap && newBoundary < rightEdge - this.minBoundaryGap) {
        this.boundaries.splice(insertPos, 0, newBoundary);
      }
      return;
    }
  }

  _visitDistribution(gating) {
    const nRegions = this.boundaries.length + 1;
    const counts = new Array(nRegions).fill(0);
    for (const [state, actions] of Object.entries(gating.N)) {
      for (const sep of ["_l", "_c"]) {
        const idx = state.lastIndexOf(sep);
        if (idx !== -1) {
          const b = parseInt(state.substring(idx + 2), 10);
          if (!isNaN(b) && b >= 0 && b < nRegions) {
            counts[b] += Object.values(actions).reduce((s, v) => s + v, 0);
          }
          break;
        }
      }
    }
    return counts;
  }

  _rebalanceBoundaries(visitCounts) {
    const total = visitCounts.reduce((s, v) => s + v, 0);
    if (total === 0) return;

    let cumulative = 0;
    for (let i = 0; i < this.boundaries.length; i++) {
      cumulative += visitCounts[i];
      const desired = cumulative / total;
      const current = this.boundaries[i];
      this.boundaries[i] += this.boundaryLr * (desired - current);

      const minPos = (i + 1) * this.minBoundaryGap;
      const maxPos = 1.0 - (this.boundaries.length - i) * this.minBoundaryGap;
      this.boundaries[i] = Math.max(minPos, Math.min(maxPos, this.boundaries[i]));
    }

    for (let i = 1; i < this.boundaries.length; i++) {
      if (this.boundaries[i] <= this.boundaries[i - 1] + this.minBoundaryGap) {
        this.boundaries[i] = this.boundaries[i - 1] + this.minBoundaryGap;
      }
    }
    if (this.boundaries[this.boundaries.length - 1] > 1.0 - this.minBoundaryGap) {
      this.boundaries[this.boundaries.length - 1] = 1.0 - this.minBoundaryGap;
    }
  }

  _adjustBoundariesByVariance(gating) {
    const nRegions = this.boundaries.length + 1;
    const perBinVars = Array.from({ length: nRegions }, () => []);

    for (const state of Object.keys(gating.Go)) {
      for (const sep of ["_l", "_c"]) {
        const idx = state.lastIndexOf(sep);
        if (idx !== -1 && this.qVars[state]) {
          const b = parseInt(state.substring(idx + 2), 10);
          if (!isNaN(b) && b >= 0 && b < nRegions) {
            for (const v of Object.values(this.qVars[state])) {
              if (v > 0) perBinVars[b].push(v);
            }
          }
          break;
        }
      }
    }

    for (let i = 0; i < this.boundaries.length; i++) {
      const leftAvg = perBinVars[i].length > 0
        ? perBinVars[i].reduce((s, v) => s + v, 0) / perBinVars[i].length
        : 0;
      const rightAvg = perBinVars[i + 1].length > 0
        ? perBinVars[i + 1].reduce((s, v) => s + v, 0) / perBinVars[i + 1].length
        : 0;

      if (leftAvg + rightAvg > 0) {
        const push = this.boundaryLr * (rightAvg - leftAvg) / (leftAvg + rightAvg);
        this.boundaries[i] += push;

        const minPos = (i + 1) * this.minBoundaryGap;
        const maxPos = 1.0 - (this.boundaries.length - i) * this.minBoundaryGap;
        this.boundaries[i] = Math.max(minPos, Math.min(maxPos, this.boundaries[i]));
      }
    }

    for (let i = 1; i < this.boundaries.length; i++) {
      if (this.boundaries[i] <= this.boundaries[i - 1] + this.minBoundaryGap) {
        this.boundaries[i] = this.boundaries[i - 1] + this.minBoundaryGap;
      }
    }
  }

  _cleanup(gating) {
    const activeStates = new Set(Object.keys(gating.Go));
    for (const d of [this.qMeans, this.qVars, this.qCounts]) {
      for (const state of Object.keys(d)) {
        if (!activeStates.has(state)) delete d[state];
      }
    }
  }

  toDict() {
    return {
      boundaries: [...this.boundaries],
      proposalHistory: this.proposalHistory.slice(-50),
      totalUpdates: this.totalUpdates,
    };
  }

  fromDict(data) {
    if (data.boundaries) this.boundaries = data.boundaries;
    if (data.proposalHistory) this.proposalHistory = data.proposalHistory;
    if (data.totalUpdates !== undefined) this.totalUpdates = data.totalUpdates;
  }
}

// ── Predictive Coding Hierarchy (Friston 2010) ──────────────────

export class HierarchicalLevel {
  constructor(name, precision = 1.0) {
    this.name = name;
    this.prediction = 0.5;
    this.error = 0.0;
    this.precision = precision;
    this.precisionHistory = [];
    this.errorHistory = [];
    this.predictionHistory = [];
    this.precisionLr = 0.02;
  }

  computeError(observation) {
    this.error = observation - this.prediction;
    this.errorHistory.push(this.error);
    if (this.errorHistory.length > 200) this.errorHistory = this.errorHistory.slice(-200);
    return this.error;
  }

  updatePrediction(topDownPrediction = null, bottomUpError = null) {
    if (topDownPrediction !== null) this.prediction = topDownPrediction;
    if (bottomUpError !== null) this.prediction += this.precision * bottomUpError;
    this.prediction = Math.max(0.0, Math.min(1.0, this.prediction));
    this.predictionHistory.push(this.prediction);
    if (this.predictionHistory.length > 200) this.predictionHistory = this.predictionHistory.slice(-200);
    return this.prediction;
  }

  updatePrecision(error) {
    const errMag = Math.abs(error);
    const targetPrecision = 1.0 / Math.max(errMag, 0.1);
    this.precision += this.precisionLr * (targetPrecision - this.precision);
    this.precision = Math.max(0.1, Math.min(5.0, this.precision));
    this.precisionHistory.push(this.precision);
    if (this.precisionHistory.length > 200) this.precisionHistory = this.precisionHistory.slice(-200);
  }

  freeEnergy() {
    return 0.5 * this.precision * (this.error * this.error) - 0.5 * Math.log(Math.max(this.precision, 0.1));
  }

  toDict() {
    return {
      prediction: Math.round(this.prediction * 1e4) / 1e4,
      error: Math.round(this.error * 1e4) / 1e4,
      precision: Math.round(this.precision * 1e4) / 1e4,
      freeEnergy: Math.round(this.freeEnergy() * 1e4) / 1e4,
    };
  }
}

export class PredictiveCodingHierarchy {
  constructor(initialPrecision = 1.0) {
    this.levels = {
      sensory: new HierarchicalLevel("sensory", initialPrecision),
      bg: new HierarchicalLevel("bg", initialPrecision),
      pfc: new HierarchicalLevel("pfc", initialPrecision * 0.8),
    };
    this.totalFreeEnergy = [];
    this.history = [];
  }

  observe(observation, pfcGoal = null) {
    if (pfcGoal !== null) this.levels.pfc.prediction = pfcGoal;

    // Save priors before bottom-up influence
    const pfcPrior = this.levels.pfc.prediction;
    const bgPrior = this.levels.bg.prediction;

    // Top-down prediction propagation
    this.levels.bg.updatePrediction(this.levels.pfc.prediction, null);
    this.levels.sensory.updatePrediction(this.levels.bg.prediction, null);

    // Bottom-up error propagation
    const sensoryErr = this.levels.sensory.computeError(observation);

    // Level 1 (BG): update belief from sensory error, compute deviation from PFC prior
    const bgBottomUp = this.levels.sensory.precision * sensoryErr;
    this.levels.bg.updatePrediction(null, bgBottomUp);
    const bgErr = this.levels.bg.prediction - pfcPrior;
    this.levels.bg.error = bgErr;
    this.levels.bg.errorHistory.push(bgErr);
    if (this.levels.bg.errorHistory.length > 200) this.levels.bg.errorHistory = this.levels.bg.errorHistory.slice(-200);

    // Level 2 (PFC): update context from BG error, compute context shift
    const pfcBottomUp = this.levels.bg.precision * bgErr;
    this.levels.pfc.updatePrediction(null, pfcBottomUp);
    const pfcErr = this.levels.pfc.prediction - pfcPrior;
    this.levels.pfc.error = pfcErr;
    this.levels.pfc.errorHistory.push(pfcErr);
    if (this.levels.pfc.errorHistory.length > 200) this.levels.pfc.errorHistory = this.levels.pfc.errorHistory.slice(-200);

    // Precision updates
    this.levels.sensory.updatePrecision(sensoryErr);
    this.levels.bg.updatePrecision(bgErr);
    this.levels.pfc.updatePrecision(pfcErr);

    // Free energy
    const fe = Object.values(this.levels).reduce((s, l) => s + l.freeEnergy(), 0);
    this.totalFreeEnergy.push(fe);
    if (this.totalFreeEnergy.length > 200) this.totalFreeEnergy = this.totalFreeEnergy.slice(-200);

    const result = {
      sensory: this.levels.sensory.toDict(),
      bg: this.levels.bg.toDict(),
      pfc: this.levels.pfc.toDict(),
      total_free_energy: Math.round(fe * 1e4) / 1e4,
    };
    this.history.push(result);
    if (this.history.length > 200) this.history = this.history.slice(-200);
    return result;
  }

  toDict() {
    return {
      levels: Object.fromEntries(
        Object.entries(this.levels).map(([k, v]) => [k, v.toDict()])
      ),
      totalFreeEnergyTrace: this.totalFreeEnergy.slice(-20).map(f => Math.round(f * 1e4) / 1e4),
    };
  }
}

export class PFCBG_Gating {
  constructor(projectName, agentId, capabilities, enableMetaLearner = true) {
    this.projectName = projectName;
    this.agentId = agentId;
    this.capabilities = capabilities || [];
    this.metaLearner = enableMetaLearner ? new MetaStateLearner() : null;
    this.Q = {};
    this.Go = {};
    this.NoGo = {};
    this.N = {};
    this.alpha = 0.15;
    this.gamma = 0.9;
    this.epsilon = 0.3;
    this.betaG = {};
    this.betaN = {};
    this.pcHierarchy = new PredictiveCodingHierarchy(1.0);
    this._useServer = true;
    this._pendingActions = {};
  }

  stateKey(task) {
    const t = (task.type || "generic").toLowerCase();
    const c = task.complexity || 5;
    if (this.metaLearner) {
      return this.metaLearner.stateKey(t, c);
    }
    const bin = Math.min(Math.floor(c / 2), 4);
    return `${t}_c${bin}`;
  }

  getQ(state, action) {
    const go = this.Go[state]?.[action] ?? 0.5;
    const no = this.NoGo[state]?.[action] ?? 0.5;
    const bg = this.betaG[state]?.[action] ?? 1.0;
    const bn = this.betaN[state]?.[action] ?? 1.0;
    const q = bg * go - bn * no;
    if (this.metaLearner) {
      this.metaLearner.record(state, action, q);
    }
    return q;
  }

  async _serverStatus() {
    try {
      return await fetchJSON(`/cortex/status?project=${encodeURIComponent(this.projectName)}`);
    } catch {
      return null;
    }
  }

  async _syncFromServer() {
    try {
      const data = await fetchJSON(`/cortex/policy?project=${encodeURIComponent(this.projectName)}`);
      if (data && data.policy) {
        this.epsilon = data.epsilon ?? this.epsilon;
        for (const [state, actions] of Object.entries(data.policy)) {
          this.Q[state] = actions;
        }
        return true;
      }
    } catch {}
    return false;
  }

  async _sendComplete(task, reward) {
    try {
      await fetchJSON("/cortex/complete", {
        method: "POST",
        body: JSON.stringify({
          agent_id: this.agentId,
          task_id: task.id || "local",
          reward: reward,
          task_type: task.type || "generic",
          complexity: task.complexity || 5,
          result: "completed",
        }),
      });
      return true;
    } catch {
      return false;
    }
  }

  async ensureState(state) {
    if (this._useServer) {
      const ok = await this._syncFromServer();
      if (ok) return;
      this._useServer = false;
    }
    if (!this.Q[state]) {
      this.Q[state] = {};
      this.Go[state] = {};
      this.NoGo[state] = {};
      this.N[state] = {};
      this.betaG[state] = {};
      this.betaN[state] = {};
    }
    const caps = this.capabilities.length ? this.capabilities : ["general"];
    for (const c of caps) {
      if (this.Q[state][c] === undefined) {
        this.Q[state][c] = 0.5;
        this.Go[state][c] = 0.5;
        this.NoGo[state][c] = 0.5;
        this.N[state][c] = 0;
        this.betaG[state][c] = 1.0;
        this.betaN[state][c] = 1.0;
      }
    }
  }

  async selectAction(state) {
    await this.ensureState(state);
    const caps = Object.keys(this.Q[state] || {});
    if (caps.length === 0) return null;
    if (Math.random() < this.epsilon) {
      return caps[Math.floor(Math.random() * caps.length)];
    }
    let best = caps[0];
    let bestVal = this.getQ(state, best);
    for (const c of caps) {
      const v = this.getQ(state, c);
      if (v > bestVal) {
        bestVal = v;
        best = c;
      }
    }
    return best;
  }

  async shouldBid(task) {
    const state = this.stateKey(task);
    const action = await this.selectAction(state);
    if (!action) return false;
    this._pendingActions[task.id] = { state, action };
    return (this.getQ(state, action) || 0.5) >= (task.threshold || 0.3);
  }

  async update(task, reward) {
    const sent = await this._sendComplete(task, reward);
    if (sent) return;

    const pending = this._pendingActions[task.id];
    const state = pending ? pending.state : this.stateKey(task);
    const action = pending ? pending.action : this.capabilities[0] || "general";
    delete this._pendingActions[task.id];

    await this.ensureState(state);
    const oldQ = this.getQ(state, action);

    // Hierarchical predictive coding
    const goalPred = task.goal_prediction ?? task.expected_reward ?? 0.5;
    const pcResult = this.pcHierarchy.observe(reward, goalPred);
    const sensoryErr = pcResult.sensory.error;
    const bgErr = pcResult.bg.error;
    const bgPrecision = pcResult.bg.precision;
    const pfcPrecision = pcResult.pfc.precision;

    this.N[state][action] = (this.N[state][action] || 0) + 1;
    const lr = this.alpha / (1 + 0.02 * this.N[state][action]);

    // Precision-weighted DA errors
    const deltaV = sensoryErr * bgPrecision;
    const deltaG = bgErr * pfcPrecision;

    this.Q[state][action] = Math.max(0.01, Math.min(0.99, oldQ + lr * deltaV));
    this.Go[state][action] = Math.max(0.01, Math.min(0.99, (this.Go[state][action] || 0.5) + lr * deltaV * (this.Go[state][action] || 0.5)));
    this.NoGo[state][action] = Math.max(0.01, Math.min(0.99, (this.NoGo[state][action] || 0.5) + lr * (-deltaV) * (this.NoGo[state][action] || 0.5)));
    this.epsilon = Math.max(0.05, this.epsilon * 0.999);

    // Meta-state learner: record and run periodic meta-evaluation
    if (this.metaLearner) {
      this.metaLearner.record(state, action, this.getQ(state, action));
      const metaChanges = this.metaLearner.metaStep(this);
      if (metaChanges.length > 0) {
        console.log(`[MetaStateLearner] ${metaChanges.join("; ")}`);
      }
    }
  }

  toJSON() {
    const data = {
      Go: this.Go, NoGo: this.NoGo, Q: this.Q, N: this.N,
      epsilon: this.epsilon, betaG: this.betaG, betaN: this.betaN,
    };
    if (this.metaLearner) {
      data.metaLearner = this.metaLearner.toDict();
    }
    return data;
  }

  fromJSON(data) {
    if (!data) return;
    this.Go = data.Go || {};
    this.NoGo = data.NoGo || {};
    this.Q = data.Q || {};
    if (!Object.keys(this.Q).length && Object.keys(this.Go).length) {
      for (const [s, vals] of Object.entries(this.Go)) {
        this.Q[s] = {};
        const nv = this.NoGo[s] || {};
        for (const [a, g] of Object.entries(vals)) {
          this.Q[s][a] = g - (nv[a] || 0.5);
        }
      }
    }
    this.N = data.N || {};
    this.epsilon = data.epsilon ?? 0.3;
    this.betaG = data.betaG || {};
    this.betaN = data.betaN || {};

    if (this.metaLearner && data.metaLearner) {
      this.metaLearner.fromDict(data.metaLearner);
    }

    // Convert old _cN state keys to _lN format
    if (this.metaLearner) {
      for (const d of ["Go", "NoGo", "N", "betaG", "betaN"]) {
        for (const k of Object.keys(this[d] || {})) {
          if (k.includes("_c")) {
            const newK = k.replace("_c", "_l");
            this[d][newK] = this[d][k];
            delete this[d][k];
          }
        }
      }
    }
  }
}
