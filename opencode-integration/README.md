# opencode + memoria integration

## What it does

- **Plugin** (`memoria-plugin/`) registers a memoria agent whenever opencode creates a session (e.g., when the chitchat participant sends a message, or when a user opens the TUI via SSH).
- **Skill** (`skills/memoria/`) provides agents with `!memoria` commands (list agents, search topics, recall, etc.).
- **Chitchat participant** (`conf/chitchat/chitchat-participant.py`) queries memoria for context (topics + recall) before sending prompts to the opencode LLM.

## Architecture

```
chitchat message
  → POST /session (opencode serve API, port 4096)
    → fires "session.created" event
      → memoria plugin (generic `event` hook)
        → POST /agents (memoria REST API, port 19998)
          → agent registered

opencode TUI (via SSH on port 23234)
  → user opens session
    → fires "session.created" event
      → memoria plugin
        → agent registered
```

## Plugin file

`memoria-plugin/index.mjs` — a path-based npm module installed via `opencode plugin --global`.

Key format (discovered through binary analysis, not documented in opencode docs):

```js
export default {
  id: "memoria",
  server: async ({ project, client, $, directory, worktree }) => {
    return {
      "shell.env": async (input, output) => { /* set MEMORIA_SERVER, MEMORIA_PROJECT */ },
      event: async ({ event }) => {
        if (event.type === "session.created")  { /* POST /agents */ }
        if (event.type === "session.updated")  { /* PATCH /agents/:id */ }
        if (event.type === "session.deleted")  { /* DELETE /agents/:id */ }
      },
    };
  },
};
```

### Requirements (binary v1.16.2)
1. `export default` — NOT named export (`export const`)
2. Must have `id` field — validated by `"id" in H` check
3. Must use `server` key (for serve mode) or `tui` key (for TUI mode) — NOT a raw function
4. Server-side events (`session.created`, etc.) use the generic `event` hook, NOT named `"session.created"` keys
5. `package.json` must have `"exports": {"./server": "./index.mjs"}` for resolve

## Files

| Path | Purpose |
|------|---------|
| `opencode-integration/memoria-plugin/index.mjs` | Plugin source (ESM, default export with `id` + `server`) |
| `opencode-integration/memoria-plugin/package.json` | npm module metadata |
| `opencode-integration/plugins/memoria.mjs` | Reference copy (same as index.mjs) |
| `opencode-integration/skills/memoria/SKILL.md` | Agent skill with `!memoria` commands |
| `~/.config/opencode/opencode.jsonc` | Global config — `"plugin"` array points to repo path |
| `~/.config/opencode/skills/memoria/SKILL.md` | Installed skill |
| `/etc/systemd/system/opencode-serve.service` | systemd unit for opencode serve (port 4096) |
