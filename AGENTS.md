# Agents

## Deploy

```bash
./deploy.sh  # ci_check → restart → verify
```

**NEVER use `--force`**. It skips ci_check and can leave services in a broken state. If deploy fails, fix the issue first, then deploy cleanly.

Full service restart: stops chitchat-server, memoria-server, and all agents, then starts in order. CI check gates the deploy. If ci_check hangs because services are down, start services manually first, then run deploy.sh.

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
- No specifying a model name in LLM requests that differs from the currently loaded model in LM Studio (this triggers JIT reload which is the root cause of model thrashing)

**ONLY the user** can load/unload/configure models, via the Settings dashboard in the web UI. The `/lmstudio/load` and `/lmstudio/unload` endpoints exist solely for manual user interaction from the dashboard.

### Why this matters
LM Studio has Just-In-Time (JIT) model loading: if a request specifies a model name that doesn't match the currently loaded model, LM Studio will **unload the current model and load the requested one**. With 7 agents and an enrichment worker all making LLM calls every 5-30 seconds, a model name mismatch causes continuous unload/load cycles that consume all GPU memory and crash the machine.

### How agents handle model names
Agents and enrichment must dynamically query LM Studio's `/api/v1/models` endpoint to find the currently loaded model, then use that model key in all LLM requests. The static `model` field in `providers.json` is only used as a fallback.
