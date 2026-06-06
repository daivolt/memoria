const MEMORIA_URL = "http://localhost:19998";

export class BiddingProtocol {
  constructor(projectName, agentId, capabilities) {
    this.projectName = projectName;
    this.agentId = agentId;
    this.capabilities = capabilities || [];
    this.reputation = 0.5;
    this.bidHistory = [];
  }

  async createTask(title, description, type, complexity, payload) {
    const resp = await fetch(`${MEMORIA_URL}/tasks`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      signal: AbortSignal.timeout(3000),
      body: JSON.stringify({
        project: this.projectName,
        title,
        description,
        status: "pending",
        payload: { type, complexity: complexity || 5, threshold: 0.3, ...payload },
      }),
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    return resp.json();
  }

  async fetchPendingTasks() {
    const resp = await fetch(`${MEMORIA_URL}/tasks?project=${encodeURIComponent(this.projectName)}`, {
      signal: AbortSignal.timeout(3000),
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const tasks = await resp.json();
    return (tasks || []).filter(t => t.status === "pending");
  }

  computeBid(task) {
    const meta = task.payload || {};
    const taskType = (meta.type || "generic").toLowerCase();
    let capabilityMatch = 0.3;
    for (const cap of this.capabilities) {
      if (taskType.includes(cap.toLowerCase()) || cap.toLowerCase().includes(taskType)) {
        capabilityMatch = 0.9;
        break;
      }
    }
    const complexity = meta.complexity || 5;
    const confidence = capabilityMatch * (1 - Math.abs(complexity - 5) / 10);
    const score = 0.6 * confidence + 0.3 * this.reputation + 0.1 * (Math.random() * 0.2);
    return {
      taskId: task.id,
      agentId: this.agentId,
      score: Math.round(score * 100) / 100,
      capabilityMatch: Math.round(capabilityMatch * 100) / 100,
      reputation: Math.round(this.reputation * 100) / 100,
      timestamp: Date.now(),
    };
  }

  async submitBid(bid) {
    const resp = await fetch(`${MEMORIA_URL}/tasks/${bid.taskId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      signal: AbortSignal.timeout(3000),
      body: JSON.stringify({
        status: "assigned",
        assigned_to: this.agentId,
        payload: { bid, assigned_at: Date.now() },
      }),
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    this.bidHistory.push({ ...bid, won: true, time: Date.now() });
    return resp.json();
  }

  async claimTask(taskId) {
    const resp = await fetch(`${MEMORIA_URL}/tasks/${taskId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      signal: AbortSignal.timeout(3000),
      body: JSON.stringify({ status: "in_progress", assigned_to: this.agentId }),
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    return resp.json();
  }

  async completeTask(taskId, result, reward) {
    const resp = await fetch(`${MEMORIA_URL}/tasks/${taskId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      signal: AbortSignal.timeout(3000),
      body: JSON.stringify({
        status: "completed",
        result: result || "done",
        payload: { completed_at: Date.now(), reward },
      }),
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    this.reputation = Math.min(1, this.reputation + 0.05 * (reward - 0.5));
    const bid = this.bidHistory.find(b => b.taskId === taskId);
    if (bid) bid.reward = reward;
    return resp.json();
  }

  toJSON() {
    return { reputation: this.reputation, bidHistory: this.bidHistory.slice(-100) };
  }

  fromJSON(data) {
    if (data) {
      this.reputation = data.reputation ?? 0.5;
      this.bidHistory = data.bidHistory || [];
    }
  }
}
