import { PFCBG_Gating } from "./gating.mjs";
import { BiddingProtocol } from "./bidding.mjs";
import { EpisodicMemoryBridge } from "./memory.mjs";

const MEMORIA_URL = "http://localhost:19998";
const POLL_INTERVAL_MS = 30000;
const STATE_TOPIC = "cortex_state";

let gating, bidding, memory;
let pollTimer = null;
let lastPoll = 0;

function getProjectName(project, directory) {
  if (project && project.name) return project.name;
  if (directory) {
    const parts = directory.replace(/\/+$/, "").split("/");
    return parts[parts.length - 1] || "unknown";
  }
  return "unknown";
}

function getDefaultCapabilities(project) {
  if (project && project.language) {
    const langs = Array.isArray(project.language) ? project.language : [project.language];
    return langs.map(l => l.toLowerCase());
  }
  return ["general"];
}

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

async function persistState(projectName) {
  try {
    const payload = {
      gating: gating ? gating.toJSON() : {},
      bidding: bidding ? bidding.toJSON() : {},
      memory: memory ? memory.toJSON() : {},
    };
    await fetchJSON(`/topics/${STATE_TOPIC}`, {
      method: "POST",
      body: JSON.stringify({ project: projectName, text: JSON.stringify(payload) }),
    });
  } catch (_) {}
}

async function loadState(projectName) {
  try {
    const data = await fetchJSON(`/topics/${STATE_TOPIC}?project=${encodeURIComponent(projectName)}`);
    if (Array.isArray(data) && data.length > 0) {
      const last = data[data.length - 1];
      const parsed = typeof last.text === "string" ? JSON.parse(last.text) : last.text;
      if (gating && parsed.gating) gating.fromJSON(parsed.gating);
      if (bidding && parsed.bidding) bidding.fromJSON(parsed.bidding);
      if (memory && parsed.memory) memory.fromJSON(parsed.memory);
    }
  } catch (_) {}
}

async function pollAndBid() {
  if (!bidding || !gating) return;
  const now = Date.now();
  if (now - lastPoll < POLL_INTERVAL_MS) return;
  lastPoll = now;
  try {
    const tasks = await bidding.fetchPendingTasks();
    for (const task of tasks) {
      if (gating.shouldBid(task)) {
        const bid = bidding.computeBid(task);
        if (bid.score >= 0.3) {
          await bidding.submitBid(bid);
          memory.store(task.id, gating.agentId, task.title, "bid_won", 0.5, task.payload);
        }
      }
    }
  } catch (_) {}
}

export default {
  id: "cortex",
  server: async ({ project, client, $, directory, worktree }) => {
    const projectName = getProjectName(project, directory);
    const capabilities = getDefaultCapabilities(project);
    const agentId = `cortex-${projectName}-${Date.now()}`;

    gating = new PFCBG_Gating(projectName, agentId, capabilities);
    bidding = new BiddingProtocol(projectName, agentId, capabilities);
    memory = new EpisodicMemoryBridge(projectName);

    return {
      "shell.env": async (input, output) => {
        output.env.CORTEX_AGENT_ID = agentId;
        output.env.CORTEX_PROJECT = projectName;
        output.env.CORTEX_CAPABILITIES = capabilities.join(",");

        if (!output.env.PFC_EPSILON) output.env.PFC_EPSILON = String(gating.epsilon);
        if (!output.env.BID_REPUTATION) output.env.BID_REPUTATION = String(bidding.reputation);
      },

      event: async ({ event: ev }) => {
        if (!ev || !ev.type) return;

        if (ev.type === "session.created") {
          try {
            await fetchJSON("/agents", {
              method: "POST",
              body: JSON.stringify({
                project: projectName,
                task: "cortex autonomous agent",
                capabilities: capabilities,
                chitchat_name: projectName,
              }),
            });
          } catch (_) {}
          await loadState(projectName);
          pollTimer = setInterval(pollAndBid, 15000);
          return;
        }

        if (ev.type === "session.updated") {
          await pollAndBid().catch(() => {});
          return;
        }

        if (ev.type === "session.deleted") {
          if (pollTimer) clearInterval(pollTimer);
          await persistState(projectName);
          pollTimer = null;
          gating = bidding = memory = null;
          return;
        }

        if (ev.type === "tool.execute.after") {
          const toolName = ev.properties?.tool || "";
          const args = ev.properties?.args || {};
          if (toolName === "memoria_done" || toolName === "cortex_complete") {
            const taskId = args.taskId || args.task_id;
            const result = args.result || "completed";
            if (taskId && bidding) {
              const reward = args.reward !== undefined ? parseFloat(args.reward) : 0.8;
              await bidding.completeTask(taskId, result, reward);
              gating.update({ type: args.type || "generic", complexity: 5 }, reward);
              memory.store(taskId, agentId, taskId, "completed", reward, { tool: toolName });
              await persistState(projectName);
            }
          }
          return;
        }
      },

      tool: {
        cortex_bid: {
          description: "Trigger CORTEX to scan and bid on pending tasks matching agent capabilities",
          args: {},
          execute: async () => {
            try {
              const tasks = await bidding.fetchPendingTasks();
              const results = [];
              for (const task of tasks) {
                const bid = bidding.computeBid(task);
                const should = gating.shouldBid(task);
                results.push({
                  taskId: task.id,
                  title: task.title,
                  bidScore: bid.score,
                  shouldBid: should,
                  action: should ? "bid_submitted" : "skipped",
                });
                if (should && bid.score >= 0.3) {
                  await bidding.submitBid(bid);
                }
              }
              return JSON.stringify(results, null, 2);
            } catch (err) {
              return `Error: ${err.message}`;
            }
          },
        },

        cortex_status: {
          description: "Show CORTEX internal state: Q-values, reputation, episodic memory stats",
          args: {},
          execute: async () => {
            try {
              const qSummary = gating ? Object.entries(gating.Q).map(([s, vals]) => {
                const best = Object.entries(vals).sort((a, b) => b[1] - a[1])[0];
                return `${s}: best=${best ? best[0] : "none"} Q=${best ? best[1].toFixed(3) : "N/A"} ε=${gating.epsilon.toFixed(3)}`;
              }) : [];
              const epCount = memory ? memory.episodes.length : 0;
              const avgReward = memory ? memory.avgRewardForAgent(agentId).toFixed(3) : "N/A";
              return JSON.stringify({
                agentId,
                capabilities: capabilities.join(", "),
                epsilon: gating?.epsilon.toFixed(3),
                reputation: bidding?.reputation.toFixed(3),
                qTableSize: Object.keys(gating?.Q || {}).length,
                qEntries: qSummary.slice(0, 20),
                episodes: epCount,
                avgReward,
                cacheHitRate: memory ? (memory.cacheHit / (memory.cacheHit + memory.cacheMiss + 1)).toFixed(3) : "N/A",
              }, null, 2);
            } catch (err) {
              return `Error: ${err.message}`;
            }
          },
        },

        cortex_learnings: {
          description: "Show CORTEX accumulated learnings from past task outcomes",
          args: {
            topK: {
              type: "number",
              description: "Number of similar past episodes to show (default 5)",
              default: 5,
            },
          },
          execute: async (args) => {
            try {
              const topK = args?.topK || 5;
              const recent = (memory ? memory.episodes.slice(-topK) : []).reverse();
              const summary = recent.map(e => ({
                task: e.taskTitle,
                agent: e.agentId,
                outcome: e.outcome,
                reward: e.reward,
                ago: `${Math.round((Date.now() - e.timestamp) / 1000)}s`,
              }));
              return JSON.stringify(summary, null, 2);
            } catch (err) {
              return `Error: ${err.message}`;
            }
          },
        },

        cortex_assign: {
          description: "Create a task and let CORTEX auto-assign it to the best agent",
          args: {
            title: { type: "string", description: "Task title" },
            description: { type: "string", description: "Task description" },
            type: { type: "string", description: "Task type (e.g. coding, research, review)", default: "generic" },
            complexity: { type: "number", description: "Task complexity 1-10", default: 5 },
          },
          execute: async (args) => {
            try {
              const task = await bidding.createTask(
                args.title, args.description, args.type, args.complexity
              );
              const bid = bidding.computeBid(task);
              const shouldBid = gating.shouldBid(task);
              let assigned = false;
              if (shouldBid && bid.score >= 0.3) {
                await bidding.submitBid(bid);
                assigned = true;
              }
              return JSON.stringify({
                taskId: task.id,
                title: task.title,
                assigned,
                bidScore: bid.score,
                byAgent: assigned ? agentId : "unassigned",
              }, null, 2);
            } catch (err) {
              return `Error: ${err.message}`;
            }
          },
        },

        cortex_complete: {
          description: "Mark a CORTEX-assigned task as complete with reward feedback",
          args: {
            taskId: { type: "string", description: "Task ID to complete" },
            result: { type: "string", description: "Result summary", default: "completed" },
            reward: { type: "number", description: "Reward signal 0.0-1.0 (default 0.8)", default: 0.8 },
            type: { type: "string", description: "Task type for Q-learning update", default: "generic" },
          },
          execute: async (args) => {
            try {
              const reward = args.reward !== undefined ? parseFloat(args.reward) : 0.8;
              await bidding.completeTask(args.taskId, args.result, reward);
              gating.update({ type: args.type || "generic", complexity: 5 }, reward);
              memory.store(args.taskId, agentId, args.taskId, "completed", reward, { type: args.type });
              await persistState(projectName);
              return JSON.stringify({
                taskId: args.taskId,
                status: "completed",
                reward,
                newReputation: bidding.reputation.toFixed(3),
              }, null, 2);
            } catch (err) {
              return `Error: ${err.message}`;
            }
          },
        },
      },
    };
  },
};
