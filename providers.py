import json
import os
import re
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "memoria"
PROVIDERS_FILE = CONFIG_DIR / "providers.json"
LOADBALANCER_ENV = Path.home() / "conf" / "env" / "loadbalancer.env"

_PROVIDER_NAMES = {
    "DEEPSEEK": "DeepSeek",
    "NIM": "NVIDIA NIM",
    "SILICONFLOW": "SiliconFlow",
    "GROQ": "Groq",
    "OLLAMA": "Ollama (Local)",
    "LMSTUDIO": "LM Studio",
    "OPENAI": "OpenAI",
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


def _ensure_chat_url(url: str) -> str:
    if not url or "/chat/completions" in url:
        return url
    url = url.rstrip("/")
    if "/v1/" in url or url.endswith("/v1"):
        return url + "/chat/completions"
    return url + "/v1/chat/completions"


def get_llm_locked() -> bool:
    return load_data().get("llm_locked", True)


def set_llm_locked(locked: bool):
    data = load_data()
    data["llm_locked"] = locked
    save_data(data)
    sync_enrichment()


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
            return _ensure_chat_url(prov["base_url"]), model, prov.get("api_key", "")
        return "", model, ""
    url = lb_url.rstrip("/")
    if "/chat/completions" not in url:
        if "/v1/" in url or url.endswith("/v1"):
            url += "/chat/completions"
        else:
            url += "/v1/chat/completions"
    return url, route_str, ""


def resolve_route(name: str) -> tuple[str, str, str]:
    data = load_data()
    all_providers = {p["id"]: p for p in data.get("providers", [])}
    lb_url = data.get("loadbalancer_url", "http://100.121.245.69:8000/v1")
    routes = data.get("routes", {})
    route_str = routes.get(name) or routes.get("default")
    if route_str:
        return _resolve_route_str(route_str, all_providers, lb_url)
    cur = data.get("current", {})
    pid = cur.get("provider_id", "")
    model = cur.get("model", "")
    if pid and pid in all_providers:
        prov = all_providers[pid]
        return _ensure_chat_url(prov["base_url"]), model, prov.get("api_key", "")
    return "", "", ""


def sync_enrichment():
    """Sync enrichment.LLM_URL, LLM_MODEL, LLM_API_KEY, LLM_LOCKED from providers.json."""
    import enrichment

    data = load_data()
    all_providers = {p["id"]: p for p in data.get("providers", [])}
    lb_url = data.get("loadbalancer_url", "http://100.121.245.69:8000/v1")
    routes = data.get("routes", {})
    route_str = routes.get("enrichment") or routes.get("default")
    enrichment.LLM_LOCKED = data.get("llm_locked", True)
    if route_str:
        url, model, key = _resolve_route_str(route_str, all_providers, lb_url)
        enrichment.LLM_URL = url
        enrichment.LLM_MODEL = model
        enrichment.LLM_API_KEY = key
    else:
        cur = data.get("current", {})
        pid = cur.get("provider_id", "")
        model = cur.get("model", "")
        if pid and pid in all_providers:
            prov = all_providers[pid]
            enrichment.LLM_URL = _ensure_chat_url(prov["base_url"])
            enrichment.LLM_MODEL = model
            enrichment.LLM_API_KEY = prov.get("api_key", "")
