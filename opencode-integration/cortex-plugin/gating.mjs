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

export class PFCBG_Gating {
  constructor(projectName, agentId, capabilities) {
    this.projectName = projectName;
    this.agentId = agentId;
    this.capabilities = capabilities || [];
    this.Q = {};
    this.Go = {};
    this.NoGo = {};
    this.N = {};
    this.alpha = 0.15;
    this.gamma = 0.9;
    this.epsilon = 0.3;
    this.betaG = {};
    this.betaN = {};
    this._useServer = true;
    this._pendingActions = {};
  }

  stateKey(task) {
    const t = (task.type || "generic").toLowerCase();
    const c = Math.min(Math.floor((task.complexity || 5) / 2), 4);
    return `${t}_c${c}`;
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
    let bestVal = this.Q[state][best] || 0;
    for (const c of caps) {
      const v = this.Q[state][c] || 0;
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
    return (this.Q[state][action] || 0.5) >= (task.threshold || 0.3);
  }

  async update(task, reward) {
    const sent = await this._sendComplete(task, reward);
    if (sent) return;

    const pending = this._pendingActions[task.id];
    const state = pending ? pending.state : this.stateKey(task);
    const action = pending ? pending.action : this.capabilities[0] || "general";
    delete this._pendingActions[task.id];

    await this.ensureState(state);
    const oldQ = this.Q[state][action] || 0.5;
    this.N[state][action] = (this.N[state][action] || 0) + 1;
    const lr = this.alpha / (1 + 0.02 * this.N[state][action]);
    const rpe = reward - oldQ;
    this.Q[state][action] = Math.max(0.01, Math.min(0.99, oldQ + lr * rpe));
    this.Go[state][action] = Math.max(0.01, Math.min(0.99, (this.Go[state][action] || 0.5) + lr * rpe * (this.Go[state][action] || 0.5)));
    this.NoGo[state][action] = Math.max(0.01, Math.min(0.99, (this.NoGo[state][action] || 0.5) + lr * (-rpe) * (this.NoGo[state][action] || 0.5)));
    this.epsilon = Math.max(0.05, this.epsilon * 0.999);
  }

  toJSON() {
    return { Go: this.Go, NoGo: this.NoGo, Q: this.Q, N: this.N, epsilon: this.epsilon, betaG: this.betaG, betaN: this.betaN };
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
  }
}
