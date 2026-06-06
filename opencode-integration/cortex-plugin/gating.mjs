const MEMORIA_URL = "http://localhost:19998";

export class PFCBG_Gating {
  constructor(projectName, agentId, capabilities) {
    this.projectName = projectName;
    this.agentId = agentId;
    this.capabilities = capabilities || [];
    this.Q = {};        
    this.N = {};        
    this.alpha = 0.1;   
    this.gamma = 0.9;   
    this.epsilon = 0.2; 
    this.lastAction = null;
  }

  stateKey(task) {
    const t = (task.type || "generic").toLowerCase();
    const c = Math.min(Math.floor((task.complexity || 5) / 2), 4);
    return `${t}_c${c}`;
  }

  getQ(state) {
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
    const q = this.getQ(state);
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
    const q = this.getQ(state);
    return q[action] >= task.threshold || 0.3;
  }

  update(task, reward) {
    if (!this.lastAction) return;
    const { state, action } = this.lastAction;
    const q = this.getQ(state);
    const oldQ = q[action];
    this.N[state][action] = (this.N[state][action] || 0) + 1;
    const lr = this.alpha / (1 + 0.01 * this.N[state][action]);
    q[action] = oldQ + lr * (reward + this.gamma * 0 - oldQ);
    this.epsilon = Math.max(0.05, this.epsilon * 0.9995);
    this.lastAction = null;
  }

  toJSON() {
    return { Q: this.Q, N: this.N, epsilon: this.epsilon };
  }

  fromJSON(data) {
    if (data) {
      this.Q = data.Q || {};
      this.N = data.N || {};
      this.epsilon = data.epsilon ?? 0.2;
    }
  }
}
