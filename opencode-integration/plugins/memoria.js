/**
 * memoria plugin — automatic agent lifecycle for opencode.
 *
 * SAFETY:
 *   - Never touches opencode.db
 *   - Never modifies opencode config
 *   - All HTTP calls have 3s timeout + AbortSignal
 *   - All errors caught silently (memoria down → session continues)
 *   - Agent IDs tracked in-memory only (cleaned on process exit)
 */

const MEMORIA_URL = "http://localhost:19998";
const TIMEOUT_MS = 3000;

export const MemoriaPlugin = async ({ project, client, $, directory, worktree }) => {
  const registry = new Map();

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

  return {
    "shell.env": async (input, output) => {
      try {
        if (!output.env.MEMORIA_SERVER) {
          output.env.MEMORIA_SERVER = MEMORIA_URL;
        }
        if (!output.env.MEMORIA_PROJECT) {
          output.env.MEMORIA_PROJECT = getProjectName();
        }
      } catch (_) {
        /* fail silently */
      }
    },

    "session.created": async (input, output) => {
      const sessionId = input && (input.id || input.session?.id);
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
      } catch (_) {
        /* fail silently — memoria may be unavailable */
      }
    },

    "session.updated": async (input, output) => {
      const sessionId = input && (input.id || input.session?.id);
      if (!sessionId) return;

      const agentId = registry.get(sessionId);
      if (!agentId) return;

      try {
        await patchJSON(`/agents/${agentId}`, {
          status: "active",
        });
      } catch (_) {
        /* fail silently */
      }
    },

    "session.deleted": async (input, output) => {
      const sessionId = input && (input.id || input.session?.id);
      if (!sessionId) return;

      const agentId = registry.get(sessionId);
      if (!agentId) return;

      try {
        await httpDelete(`/agents/${agentId}`);
      } catch (_) {
        /* fail silently */
      }

      registry.delete(sessionId);
    },
  };
};
