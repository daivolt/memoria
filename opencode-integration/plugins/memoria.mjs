const MEMORIA_URL = "http://localhost:19998";
const TIMEOUT_MS = 3000;

function estimateTokens(text) {
  if (!text) return 0;
  return Math.ceil(text.length / 4);
}

export default {
  server: async ({ project, client, $, directory, worktree }) => {
    const registry = new Map();
    const briefingCache = new Map();
    const costTracker = new Map();

    const getProjectName = () => {
      if (project && project.name) return project.name;
      if (directory) {
        const parts = directory.replace(/\/+$/, "").split("/");
        return parts[parts.length - 1] || "unknown";
      }
      return "unknown";
    };

    const postJSON = async (path, body) => {
      const resp = await fetch(`${MEMORIA_URL}${path}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        signal: AbortSignal.timeout(TIMEOUT_MS),
        body: JSON.stringify(body),
      });
      if (!resp.ok) {
        const text = await resp.text().catch(() => "");
        throw new Error(`HTTP ${resp.status}: ${text.slice(0, 200)}`);
      }
      return resp.json();
    };

    const patchJSON = async (path, body) => {
      const resp = await fetch(`${MEMORIA_URL}${path}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        signal: AbortSignal.timeout(TIMEOUT_MS),
        body: JSON.stringify(body),
      });
      if (!resp.ok) {
        const text = await resp.text().catch(() => "");
        throw new Error(`HTTP ${resp.status}: ${text.slice(0, 200)}`);
      }
      return resp.json();
    };

    const httpDelete = async (path) => {
      const resp = await fetch(`${MEMORIA_URL}${path}`, {
        method: "DELETE",
        signal: AbortSignal.timeout(TIMEOUT_MS),
      });
      if (!resp.ok) {
        const text = await resp.text().catch(() => "");
        throw new Error(`HTTP ${resp.status}: ${text.slice(0, 200)}`);
      }
      return resp.json();
    };

    const getJSON = async (path) => {
      const resp = await fetch(`${MEMORIA_URL}${path}`, {
        method: "GET",
        signal: AbortSignal.timeout(TIMEOUT_MS),
      });
      if (!resp.ok) {
        const text = await resp.text().catch(() => "");
        throw new Error(`HTTP ${resp.status}: ${text.slice(0, 200)}`);
      }
      return resp.json();
    };

    return {
      "shell.env": async (input, output) => {
        try {
          if (!output.env.MEMORIA_SERVER) {
            output.env.MEMORIA_SERVER = MEMORIA_URL;
          }
          if (!output.env.MEMORIA_PROJECT) {
            output.env.MEMORIA_PROJECT = getProjectName();
          }
        } catch (_) {}
      },

      "experimental.chat.system.transform": async (ctx) => {
        if (!ctx || !ctx.messages) return ctx;
        const projectName = getProjectName();
        let injected = "";
        const TYPE_TAGS = {
          concept: "CONCEPT_CTX",
          temporal: "TEMPORAL_CTX",
          relation: "RELATION_CTX",
        };
        const costBreakdown = { full: 0, briefing: 0, red_ink: 0, anchor: 0, procedural: 0 };
        try {
          const ctxData = await getJSON(`/ctx/${encodeURIComponent(projectName)}`);
          if (ctxData && ctxData.markdown) {
            injected += ctxData.markdown + "\n\n";
            costBreakdown.full += estimateTokens(ctxData.markdown);
          }
          if (ctxData && ctxData.red_ink_entries && ctxData.red_ink_entries.length > 0) {
            const redInkBlock = "<RED_INK_CTX>\n[CRITICAL — these facts MUST be preserved verbatim in any compaction or summarization]\n"
              + ctxData.red_ink_entries.map(e => `- ${e}`).join("\n")
              + "\n</RED_INK_CTX>\n\n";
            injected += redInkBlock;
            costBreakdown.red_ink += estimateTokens(redInkBlock);
          }
          if (ctxData && ctxData.typed_sections) {
            for (const [mtype, entries] of Object.entries(ctxData.typed_sections)) {
              if (entries.length === 0) continue;
              const tag = TYPE_TAGS[mtype];
              if (!tag) continue;
              const section = `<${tag}>\n${entries.map(e => `- ${e}`).join("\n")}\n</${tag}>\n\n`;
              injected += section;
              costBreakdown.full += estimateTokens(section);
            }
          }
        } catch (_) {}
        try {
          const anchors = await getJSON(`/anchors/${encodeURIComponent(projectName)}`);
          if (anchors) {
            const parts = [];
            if (anchors.completed_tasks && anchors.completed_tasks.length > 0) {
              parts.push("Recently completed:");
              for (const t of anchors.completed_tasks) {
                parts.push(`  - ${t.title || '?'}${t.result ? ': ' + t.result.slice(0, 80) : ''}`);
              }
            }
            if (anchors.active_tasks && anchors.active_tasks.length > 0) {
              parts.push("Active tasks:");
              for (const t of anchors.active_tasks) {
                parts.push(`  - [${t.status || '?'}] ${t.title || '?'} (${t.assigned_to || 'unassigned'})`);
              }
            }
            if (anchors.commit_log && anchors.commit_log.length > 0) {
              parts.push("Recent commits:");
              for (const c of anchors.commit_log) {
                parts.push(`  - ${c.slice(0, 100)}`);
              }
            }
            if (anchors.most_edited_files && anchors.most_edited_files.length > 0) {
              parts.push("Most edited files (24h):");
              for (const f of anchors.most_edited_files) {
                parts.push(`  - ${f.path} (${f.edits} edits)`);
              }
            }
            if (anchors.red_ink_reminders && anchors.red_ink_reminders.length > 0) {
              parts.push("CRITICAL reminders:");
              for (const r of anchors.red_ink_reminders) {
                parts.push(`  - ${r.entry}`);
              }
            }
            if (parts.length > 0) {
              const anchorText = "<ANCHOR_CTX>\n" + parts.join("\n") + "\n</ANCHOR_CTX>\n\n";
              injected += anchorText;
              costBreakdown.anchor += estimateTokens(anchorText);
            }
          }
        } catch (_) {}
        const sessionId = ctx.sessionId || ctx.properties?.sessionId || ctx.properties?.sessionID;
        const briefing = briefingCache.get(sessionId);
        if (briefing) {
          injected += "<BRIEFING_CTX>\n" + briefing + "\n</BRIEFING_CTX>\n\n";
          costBreakdown.briefing += estimateTokens(briefing);
          briefingCache.delete(sessionId);
        }
        try {
          const procSearch = briefing || "";
          if (procSearch) {
            const procResult = await postJSON(`/procedural/${encodeURIComponent(projectName)}/search`, {
              task_description: procSearch.slice(0, 200),
            });
            if (procResult && procResult.procedures && procResult.procedures.length > 0) {
              const procLines = procResult.procedures
                .filter(p => !p.retired)
                .slice(0, 5)
                .map(p => {
                  const steps = Array.isArray(p.steps) ? p.steps.join(" → ") : (p.steps || "");
                  return `- [${p.task_type || 'procedure'}] ${p.task_pattern} (score: ${p.reinforcement_score?.toFixed(2) || '?'}): ${steps}`;
                });
              const procBlock = "<PROCEDURAL_CTX>\n[Proven procedures — follow these steps for tasks matching the pattern]\n"
                + procLines.join("\n")
                + "\n</PROCEDURAL_CTX>\n\n";
              injected += procBlock;
              costBreakdown.procedural += estimateTokens(procBlock);
            }
          }
        } catch (_) {}
        if (injected) {
          const lastMsg = ctx.messages[ctx.messages.length - 1];
          if (lastMsg && lastMsg.content) {
            if (typeof lastMsg.content === "string") {
              lastMsg.content = injected + lastMsg.content;
            } else if (Array.isArray(lastMsg.content)) {
              const textParts = lastMsg.content.filter(p => p.type === "text");
              const otherParts = lastMsg.content.filter(p => p.type !== "text");
              lastMsg.content = [
                { type: "text", text: injected },
                ...textParts,
                ...otherParts,
              ];
            }
          }
        }
        const totalTokens = Object.values(costBreakdown).reduce((a, b) => a + b, 0);
        if (totalTokens > 0 && sessionId) {
          costTracker.set(sessionId, { project: projectName, tokens: totalTokens, breakdown: costBreakdown });
          if (!costTracker.has(sessionId + "_recorded")) {
            const savedInjection = Math.max(0, costBreakdown.full - costBreakdown.briefing);
            try {
              await postJSON("/costs/record", {
                project: projectName,
                session_id: sessionId,
                tokens_injected: totalTokens,
                tokens_saved_injection: savedInjection,
                tokens_saved_forgetting: 0,
                context_type: "full",
                task_outcome: "",
                breakdown: costBreakdown,
              });
            } catch (_) {}
            costTracker.set(sessionId + "_recorded", true);
          }
        }
        return ctx;
      },

      event: async ({ event: ev }) => {
        if (!ev || !ev.type) return;

        if (ev.type === "session.created") {
          const sessionId = ev.properties?.sessionID || ev.properties?.sessionId || ev.properties?.id;
          if (!sessionId) return;

          try {
            const projectName = getProjectName();
            const result = await postJSON("/agents", {
              project: projectName,
              task: "",
              files: [],
              chitchat_name: projectName,
            });

            if (result && result.agent_id) {
              registry.set(sessionId, result.agent_id);
            }
          } catch (_) {}

          // Briefing is independent of agent registration
          try {
            const projectName = getProjectName();
            const title = ev.properties?.title || ev.properties?.task || "";
            if (title) {
              const briefingResult = await postJSON("/briefing", {
                task_description: title,
                project: projectName,
                max_tokens: 2000,
              });
              if (briefingResult && briefingResult.briefing) {
                const sid = ev.properties?.sessionID || ev.properties?.sessionId || ev.properties?.id;
                briefingCache.set(sid, briefingResult.briefing);
              }
            }
          } catch (_) {}
          return;
        }

        if (ev.type === "session.updated") {
          const sessionId = ev.properties?.sessionID || ev.properties?.sessionId || ev.properties?.id;
          if (!sessionId) return;

          const agentId = registry.get(sessionId);
          if (!agentId) return;

          try {
            await patchJSON(`/agents/${agentId}`, { status: "active" });
          } catch (_) {}
          return;
        }

        if (ev.type === "session.deleted") {
          const sessionId = ev.properties?.sessionID || ev.properties?.sessionId || ev.properties?.id;
          if (!sessionId) return;

          const agentId = registry.get(sessionId);
          if (agentId) {
            try {
              await httpDelete(`/agents/${agentId}`);
            } catch (_) {}
          }

          const trackedCost = costTracker.get(sessionId);
          if (trackedCost) {
            try {
              await postJSON("/costs/record", {
                project: trackedCost.project,
                session_id: sessionId,
                tokens_injected: 0,
                tokens_saved_injection: 0,
                tokens_saved_forgetting: 0,
                context_type: "outcome",
                task_outcome: "completed",
              });
            } catch (_) {}
            costTracker.delete(sessionId);
            costTracker.delete(sessionId + "_recorded");
          }

          registry.delete(sessionId);
          briefingCache.delete(sessionId);
          return;
        }
      },
    };
  },
};
