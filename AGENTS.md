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
