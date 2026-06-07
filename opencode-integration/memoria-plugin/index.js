const MEMORIA_URL = "http://localhost:19998";
const TIMEOUT_MS = 3000;

module.exports = {
  MemoriaPlugin: async ({ project, client, $, directory, worktree }) => {
    const registry = new Map();

    const getProjectName = () => {
      if (project && project.name) return project.name;
      if (process.env.MEMORIA_PROJECT) return process.env.MEMORIA_PROJECT;
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
        } catch (_) {}
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
        } catch (_) {}
      },

      "session.updated": async (input, output) => {
        const sessionId = input && (input.id || input.session?.id);
        if (!sessionId) return;

        const agentId = registry.get(sessionId);
        if (!agentId) return;

        try {
          await patchJSON(`/agents/${agentId}`, { status: "active" });
        } catch (_) {}
      },

      "session.deleted": async (input, output) => {
        const sessionId = input && (input.id || input.session?.id);
        if (!sessionId) return;

        const agentId = registry.get(sessionId);
        if (!agentId) return;

        try {
          await httpDelete(`/agents/${agentId}`);
        } catch (_) {}

        registry.delete(sessionId);
      },
    };
  }
};
