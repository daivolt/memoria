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
    this.N = {};
    this.Go = {};
    this.NoGo = {};
    this.alpha = 0.15;
    this.gamma = 0.9;
    this.epsilon = 0.3;
    this.beta_g = 1.0;
    this.beta_n = 1.0;
    this.lastAction = null;
    this._useServer = true;
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

  async getQ(state) {
    if (this._useServer) {
      try {
        const data = await this._serverStatus();
        if (data && data.cortex && data.cortex.q_summary && data.cortex.q_summary[state]) {
          this.epsilon = data.cortex.epsilon;
          return data.cortex.q_summary[state];
        }
      } catch {}
      this._useServer = false;
    }
    if (!this.Q[state]) {
      this.Q[state] = {};
      this.N[state] = {};
    }
    const caps = this.capabilities.length ? this.capabilities : ["general"];
    for (const c of caps) {
      if (this.Q[state][c] === undefined) {
        this.Q[state][c] = 0.5;
        this.N[state][c] = 0;
      }
    }
    return this.Q[state];
  }

  selectAction(state) {
    const q = this.Q[state] || {};
    const caps = Object.keys(q);
    if (caps.length === 0) return null;
    if (Math.random() < this.epsilon) {
      return caps[Math.floor(Math.random() * caps.length)];
    }
    let best = caps[0];
    let bestVal = q[best];
    for (const c of caps) {
      if (q[c] > bestVal) {
        bestVal = q[c];
        best = c;
      }
    }
    return best;
  }

  shouldBid(task) {
    const state = this.stateKey(task);
    const action = this.selectAction(state);
    if (!action) return false;
    this.lastAction = { state, action };
    const q = this.Q[state] || {};
    return (q[action] || 0.5) >= (task.threshold || 0.3);
  }

  async update(task, reward) {
    if (this._useServer) {
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
        return;
      } catch {}
      this._useServer = false;
    }
    if (!this.lastAction) return;
    const { state, action } = this.lastAction;
    if (!this.Q[state]) this.Q[state] = {};
    if (!this.N[state]) this.N[state] = {};
    const oldQ = this.Q[state][action] || 0.5;
    this.N[state][action] = (this.N[state][action] || 0) + 1;
    const lr = this.alpha / (1 + 0.02 * this.N[state][action]);
    const rpe = reward - oldQ;
    this.Q[state][action] = oldQ + lr * rpe;
    this.epsilon = Math.max(0.05, this.epsilon * 0.999);
    this.lastAction = null;
  }

  toJSON() {
    return { Go: this.Go, NoGo: this.NoGo, Q: this.Q, N: this.N, epsilon: this.epsilon };
  }

  fromJSON(data) {
    if (data) {
      this.Q = data.Q || data.Go || {};
      this.Go = data.Go || {};
      this.NoGo = data.NoGo || {};
      this.N = data.N || {};
      this.epsilon = data.epsilon ?? 0.3;
    }
  }
}
