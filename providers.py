import json
import os
import re
import urllib.request
import urllib.error
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "memoria"
PROVIDERS_FILE = CONFIG_DIR / "providers.json"
LOADBALANCER_ENV = Path.home() / "conf" / "env" / "loadbalancer.env"
_lmstudio_loaded_model_cache = None
_lmstudio_loaded_model_ts = 0

_PROVIDER_NAMES = {
    "DEEPSEEK": "DeepSeek",
    "NIM": "NVIDIA NIM",
    "SILICONFLOW": "SiliconFlow",
    "GROQ": "Groq",
    "OLLAMA": "Ollama (Local)",
    "LMSTUDIO": "LM Studio",
    "OPENAI": "OpenAI",
    "OPENCODE-ZEN": "OpenCode Zen",
}


def _parse_loadbalancer_env() -> list[dict]:
    providers = []
    try:
        text = LOADBALANCER_ENV.read_text()
    except Exception:
        return providers
    lines = text.strip().split("\n")
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^(\w+)_API_KEY=(.*)", line)
        if m:
            prefix = m.group(1)
            api_key = m.group(2)
            base_url = None
            enabled = True
            for l in lines:
                l = l.strip()
                if l.startswith(prefix + "_BASE_URL="):
                    base_url = l.split("=", 1)[1]
                if l.startswith(prefix + "_ENABLED="):
                    enabled = l.split("=", 1)[1].lower() == "true"
            if base_url:
                providers.append(
                    {
                        "id": prefix.lower(),
                        "name": _PROVIDER_NAMES.get(prefix, prefix),
                        "base_url": base_url.rstrip("/"),
                        "api_key": api_key,
                        "enabled": enabled,
                    }
                )
    return providers


def _default_providers() -> list[dict]:
    env_providers = _parse_loadbalancer_env()
    if env_providers:
        return env_providers
    return [
        {
            "id": "deepseek",
            "name": "DeepSeek",
            "base_url": "https://api.deepseek.com/v1",
            "api_key": "",
            "enabled": True,
        },
        {
            "id": "openai",
            "name": "OpenAI",
            "base_url": "https://api.openai.com/v1",
            "api_key": "",
            "enabled": False,
        },
        {
            "id": "ollama",
            "name": "Ollama (Local)",
            "base_url": "http://localhost:11434",
            "api_key": "",
            "enabled": True,
        },
    ]


def _default_data() -> dict:
    return {
        "providers": _default_providers(),
        "current": {
            "provider_id": "deepseek",
            "model": "deepseek-v4-flash",
        },
        "llm_locked": True,
        "loadbalancer_url": "http://100.121.245.69:8000/v1",
        "routes": {
            "default": "deepseek/deepseek-v4-flash",
            "enrichment": "deepseek/deepseek-v4-flash",
        },
    }


def load_data() -> dict:
    if not PROVIDERS_FILE.exists():
        data = _default_data()
        save_data(data)
        return data
    try:
        data = json.loads(PROVIDERS_FILE.read_text())
        if "providers" not in data or "current" not in data:
            raise ValueError("missing keys")
        return data
    except Exception:
        data = _default_data()
        save_data(data)
        return data


def save_data(data: dict):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    PROVIDERS_FILE.write_text(json.dumps(data, indent=2))


def get_providers() -> list[dict]:
    return load_data().get("providers", [])


def get_current() -> dict:
    return load_data().get("current", {"provider_id": "", "model": ""})


def set_current(provider_id: str, model: str):
    data = load_data()
    data["current"] = {"provider_id": provider_id, "model": model}
    save_data(data)


def get_lmstudio_loaded_model() -> str | None:
    """Query LM Studio for the currently loaded model key.
    Returns the model key if a model is loaded, None otherwise.
    This is the ONLY safe way to get the model name for LM Studio requests.
    Results are cached for 60 seconds to avoid hammering LM Studio.
    """
    global _lmstudio_loaded_model_cache, _lmstudio_loaded_model_ts
    now = __import__("time").time()
    if _lmstudio_loaded_model_ts and now - _lmstudio_loaded_model_ts < 60:
        return _lmstudio_loaded_model_cache
    try:
        data = load_data()
        prov = next(
            (p for p in data.get("providers", []) if p["id"] == "lmstudio"), None
        )
        if not prov:
            return None
        base = prov["base_url"].rstrip("/")
        if base.endswith("/api/v1/chat"):
            base = base.replace("/api/v1/chat", "")
        url = base + "/api/v1/models"
        resp = urllib.request.urlopen(url, timeout=5)
        models = json.loads(resp.read()).get("models", [])
        for m in models:
            if m.get("loaded_instances"):
                result = m.get("key", m.get("id"))
                _lmstudio_loaded_model_cache = result
                _lmstudio_loaded_model_ts = now
                return result
        _lmstudio_loaded_model_cache = None
        _lmstudio_loaded_model_ts = now
        return None
    except Exception:
        return _lmstudio_loaded_model_cache


def add_provider(provider: dict) -> dict:
    data = load_data()
    providers = data["providers"]
    if any(p["id"] == provider["id"] for p in providers):
        raise ValueError(f"provider '{provider['id']}' already exists")
    entry = {
        "id": provider["id"],
        "name": provider.get("name", provider["id"]),
        "base_url": provider["base_url"].rstrip("/"),
        "api_key": provider.get("api_key", ""),
        "enabled": provider.get("enabled", True),
    }
    providers.append(entry)
    save_data(data)
    return entry


def update_provider(provider_id: str, updates: dict) -> dict | None:
    data = load_data()
    for p in data["providers"]:
        if p["id"] == provider_id:
            if "name" in updates:
                p["name"] = updates["name"]
            if "base_url" in updates:
                p["base_url"] = updates["base_url"].rstrip("/")
            if "api_key" in updates:
                p["api_key"] = updates["api_key"]
            if "enabled" in updates:
                p["enabled"] = updates["enabled"]
            save_data(data)
            return p
    return None


def delete_provider(provider_id: str) -> bool:
    data = load_data()
    before = len(data["providers"])
    data["providers"] = [p for p in data["providers"] if p["id"] != provider_id]
    if len(data["providers"]) < before:
        if data["current"].get("provider_id") == provider_id:
            data["current"] = {"provider_id": "", "model": ""}
        save_data(data)
        return True
    return False


def get_llm_locked() -> bool:
    return load_data().get("llm_locked", True)


def set_llm_locked(locked: bool):
    data = load_data()
    data["llm_locked"] = locked
    save_data(data)


def get_loadbalancer_url() -> str:
    data = load_data()
    return data.get("loadbalancer_url", "http://100.121.245.69:8000/v1").rstrip("/")


def get_route(name: str) -> str | None:
    data = load_data()
    routes = data.get("routes", {})
    if name in routes:
        return routes[name]
    return routes.get("default")


def set_route(name: str, route_str: str):
    data = load_data()
    if "routes" not in data:
        data["routes"] = {}
    data["routes"][name] = route_str
    save_data(data)


def get_routes() -> dict:
    return load_data().get("routes", {})


def _resolve_route_str(
    route_str: str, all_providers: dict, lb_url: str
) -> tuple[str, str, str]:
    if "/" in route_str:
        pid, model = route_str.split("/", 1)
        prov = all_providers.get(pid)
        if prov:
            return prov["base_url"], model, prov.get("api_key", "")
        return "", model, ""
    return lb_url.rstrip("/"), route_str, ""


def resolve_route(name: str) -> tuple[str, str, str]:
    data = load_data()
    all_providers = {p["id"]: p for p in data.get("providers", [])}
    lb_url = data.get("loadbalancer_url", "http://100.121.245.69:8000/v1")
    routes = data.get("routes", {})
    route_str = routes.get(name) or routes.get("default")
    if route_str:
        url, model, key = _resolve_route_str(route_str, all_providers, lb_url)
        if model and "/api/v1/chat" in url:
            loaded = get_lmstudio_loaded_model()
            if loaded and loaded != model:
                model = loaded
        return url, model, key
    cur = data.get("current", {})
    pid = cur.get("provider_id", "")
    model = cur.get("model", "")
    if pid and pid in all_providers:
        prov = all_providers[pid]
        url = prov["base_url"]
        if "/api/v1/chat" in url:
            loaded = get_lmstudio_loaded_model()
            if loaded and loaded != model:
                model = loaded
        return url, model, prov.get("api_key", "")
    return "", "", ""


def get_model_config(model_key: str) -> dict:
    data = load_data()
    configs = data.get("lmstudio_model_configs", {})
    return configs.get(model_key, {})


def set_model_config(model_key: str, config: dict):
    data = load_data()
    if "lmstudio_model_configs" not in data:
        data["lmstudio_model_configs"] = {}
    data["lmstudio_model_configs"][model_key] = config
    save_data(data)


def _normalize_llm_url(url: str) -> str:
    if not url:
        return url
    if url.endswith(
        (
            "/chat",
            "/chat/",
            "/completions",
            "/completions/",
            "/chat/completions",
            "/chat/completions/",
        )
    ):
        return url
    if "/chat/completions" in url:
        return url
    if "/api/v1/chat" in url:
        return url
    if "/v1/" in url or url.endswith("/v1"):
        return url.rstrip("/") + "/chat/completions"
    return url.rstrip("/") + "/v1/chat/completions"


def sync_enrichment():
    """Sync enrichment.LLM_URL, LLM_MODEL, LLM_API_KEY from providers.json."""
    import enrichment

    data = load_data()
    all_providers = {p["id"]: p for p in data.get("providers", [])}
    lb_url = data.get("loadbalancer_url", "http://100.121.245.69:8000/v1")
    routes = data.get("routes", {})
    route_str = routes.get("enrichment") or routes.get("default")
    if route_str:
        url, model, key = _resolve_route_str(route_str, all_providers, lb_url)
        enrichment.LLM_URL = _normalize_llm_url(url)
        enrichment.LLM_MODEL = model
        enrichment.LLM_API_KEY = key
    else:
        cur = data.get("current", {})
        pid = cur.get("provider_id", "")
        model = cur.get("model", "")
        if pid and pid in all_providers:
            prov = all_providers[pid]
            url = _normalize_llm_url(prov.get("base_url", "").rstrip("/"))
            enrichment.LLM_URL = url
            enrichment.LLM_MODEL = model
            enrichment.LLM_API_KEY = prov.get("api_key", "")
