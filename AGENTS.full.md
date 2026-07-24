# Memoria — AgentOS Memory Server

Monolithic memory server (port 19998). All memory processes live here: CRUD, recall, consolidation, decay, red-ink, briefing, procedures, anchors, costs, cortex, agents, tasks, services, federation, chitchat consolidation, providers, enrichment, papers, config.

## Project Scope

**IN scope** (this repo):
- Memory CRUD, recall, review, context, topics, proposals
- Consolidation, decay, red-ink, briefing, archive
- Procedural memory, anchoring, cost analytics
- Cortex engine (task board, bidding, gating, replay)
- Agent registration, services, task management
- Chitchat polling and consolidation (memory process)
- Social learning, teach/cultural memory
- Safety snapshots, federation sync
- Providers, enrichment, papers, config
- LM Studio integration, LLM lock

**OUT of scope** (moved to `memoria-agents/`):
- TUI (Go terminal UI)
- Dashboard HTML + static assets
- Agent scripts (orchestrator, researcher, pilosopher, sage, builder, mini, gnotes)
- Agent systemd services (bridge, chitchat-server, sidepane, opencode-serve, warden, xvfb)
- `start_all.sh`

## Deploy

```bash
./deploy.sh  # ci_check → restart → verify
```

**NEVER use `--force`**. It skips ci_check and can leave services in a broken state.

## CI

```bash
./ci_check.sh  # syntax, health, E2E
```

JS check extracts `<script>` from Python `"""` HTML string. If you add escaped quotes in JS inside `"""`, use `"'"` concatenation instead of `\'` to avoid ci_check false positives.

## LLM Config

`providers.json` at `~/.config/memoria/providers.json` stores API keys. Server syncs enrichment on startup. Settings tab in dashboard has provider management UI.

## LM Studio — Model Loading Policy

**PROHIBITED: The system, agents, enrichment pipeline, and all automated processes must NEVER load, unload, or reload models in LM Studio.** This includes:

- No calls to `/lmstudio/load` or `/lmstudio/unload` from any automated process
- No calls to `lmstudio` SDK `load_new_instance()` or `unload()` from agents or server code
- No subprocess calls that invoke `python3 -c "import lmstudio; ..."` for model loading/unloading
- No specifying a model name in LLM requests that differs from the currently loaded model in LM Studio

**ONLY the user** can load/unload/configure models, via the Settings dashboard in the web UI.