const MEMORIA_URL = "http://localhost:19998";

export class EpisodicMemoryBridge {
  constructor(projectName) {
    this.projectName = projectName;
    this.episodes = [];
    this.cacheHit = 0;
    this.cacheMiss = 0;
  }

  async recall(query, topK = 3) {
    try {
      const resp = await fetch(`${MEMORIA_URL}/recall?q=${encodeURIComponent(query)}&n=${topK}`, {
        signal: AbortSignal.timeout(3000),
      });
      if (resp.ok) {
        const results = await resp.json();
        this.cacheHit += results.length || 0;
        return results || [];
      }
    } catch (_) {}
    const local = this.episodes
      .filter(e => e.text.toLowerCase().includes(query.toLowerCase()))
      .slice(-topK);
    this.cacheMiss += local.length || 1;
    return local;
  }

  async store(taskId, agentId, taskTitle, outcome, reward, meta) {
    const text = `[CORTEX] task=${taskTitle} agent=${agentId} outcome=${outcome} reward=${reward} ${meta ? JSON.stringify(meta) : ""}`;
    const entry = { taskId, agentId, taskTitle, outcome, reward, meta, text, timestamp: Date.now() };
    this.episodes.push(entry);
    try {
      await fetch(`${MEMORIA_URL}/tasks/${taskId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        signal: AbortSignal.timeout(3000),
        body: JSON.stringify({ payload: { cortex_memory: entry } }),
      });
    } catch (_) {}
    return entry;
  }

  findSimilar(type, complexity, topK = 3) {
    const scored = this.episodes.map(e => {
      const m = e.meta || {};
      let score = 0;
      if (m.type === type) score += 0.5;
      if (m.complexity && complexity) {
        score += 0.3 * (1 - Math.abs(m.complexity - complexity) / 10);
      }
      score += 0.2 * e.reward;
      return { episode: e, score };
    });
    return scored.sort((a, b) => b.score - a.score).slice(0, topK);
  }

  avgRewardForAgent(agentId) {
    const ag = this.episodes.filter(e => e.agentId === agentId && e.reward !== undefined);
    if (!ag.length) return 0.5;
    return ag.reduce((s, e) => s + e.reward, 0) / ag.length;
  }

  toJSON() {
    return { episodes: this.episodes.slice(-500), cacheHit: this.cacheHit, cacheMiss: this.cacheMiss };
  }

  fromJSON(data) {
    if (data) {
      this.episodes = data.episodes || this.episodes;
      this.cacheHit = data.cacheHit || 0;
      this.cacheMiss = data.cacheMiss || 0;
    }
  }
}
