# Memoria — AgentOS Memory Server

> Canonical knowledge for this project lives in `pneural-context` under project `memoria`.
> Query it via the pneural-context dashboard or recall API.
> Full backup: `AGENTS.full.md`

## Run / Build

```bash
(see AGENTS.full.md for build/run commands)
```

## Project-Specific Red Ink

- No calls to `/lmstudio/load` or `/lmstudio/unload` from any automated process
- No calls to `lmstudio` SDK `load_new_instance()` or `unload()` from agents or server code
- No subprocess calls that invoke `python3 -c "import lmstudio; ..."` for model loading/unloading
- No specifying a model name in LLM requests that differs from the currently loaded model in LM Studio

## See Also

- Engineering standards: `~/.config/opencode/.standards/`
- Infrastructure reference: `pneural-context` project `code-root`
