"""
enrichment — SIRA-style LLM search vocabulary enrichment for memoria.

Pattern: LLM proposes vocabulary → DF/corpus statistics filter → BM25/similarity scores.
Adapted from the Superintelligent Retrieval Agent (SIRA) paper.

Two surfaces:
  CORPUS-SIDE (write-time): LLM generates missing search terms per record,
    stored in search_enrichments TEXT[] column. Runs async via enrichment_queue.
  QUERY-SIDE (recall-time): LLM expands user query → DF filter validates terms
    against the corpus → weighted retrieval.

LLM backend: DeepSeek API (deepseek-v4-flash).
"""

import asyncio
import json
import logging
import os
import time
import urllib.request
import urllib.error
from collections import deque
from typing import Any

import aiohttp

logger = logging.getLogger("enrichment")

LLM_URL = os.environ.get(
    "MEMORIA_LLM_URL", "https://zen-proxy.daivolt.workers.dev/v1/chat/completions"
)
LLM_MODEL = os.environ.get("MEMORIA_LLM_MODEL", "deepseek-v4-flash-free")
LLM_API_KEY = os.environ.get("MEMORIA_LLM_API_KEY", "")
ENRICH_ENABLED = os.environ.get("MEMORIA_ENRICH_ENABLED", "true").lower() == "true"
EXPANSION_WEIGHT = float(os.environ.get("MEMORIA_ENRICH_WEIGHT", "0.5"))
MAX_DF_RATIO = float(os.environ.get("MEMORIA_ENRICH_DF_RATIO", "0.10"))
ENRICH_MAX_TOKENS = int(os.environ.get("MEMORIA_ENRICH_MAX_TOKENS", "512"))
ENRICH_MAX_TOKENS_PAPERS = int(
    os.environ.get("MEMORIA_ENRICH_MAX_TOKENS_PAPERS", "2048")
)
ENRICH_TEMPERATURE = float(os.environ.get("MEMORIA_ENRICH_TEMPERATURE", "0.0"))
ENRICH_MAX_RETRIES = 1
ENRICH_CONCURRENCY = int(os.environ.get("MEMORIA_ENRICH_CONCURRENCY", "1"))
STALE_PROCESSING_SEC = int(os.environ.get("MEMORIA_ENRICH_STALE_SEC", "600"))
IDLE_TIMEOUT_MINUTES = int(os.environ.get("MEMORIA_ENRICH_IDLE_MIN", "5"))

# Sentinel for records that can't be enriched (prevents infinite re-enqueue)
_NOKW_SENTINEL = "__NOKW__"


def _is_global_locked() -> bool:
    """Check if the global LLM lock is enabled (reads providers.json directly)."""
    try:
        import providers

        return providers.get_llm_locked()
    except Exception:
        return True


_lmstudio_cache = {"model": None, "ts": 0.0}
_LMSTUDIO_CACHE_TTL = 60


class _TokenBucket:
    __slots__ = ("rate", "burst", "tokens", "last")

    def __init__(self, rate: float, burst: int):
        self.rate = rate
        self.burst = burst
        self.tokens = float(burst)
        self.last = time.time()

    def consume(self) -> bool:
        now = time.time()
        self.tokens = min(self.burst, self.tokens + (now - self.last) * self.rate)
        self.last = now
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False


_ZEN_RATE_LIMIT = 10 / 60.0
_ZEN_BURST = 4
_zen_rate_bucket = _TokenBucket(_ZEN_RATE_LIMIT, _ZEN_BURST)
_ZEN_BACKOFF_UNTIL = 0.0


def _get_lmstudio_loaded_model() -> str | None:
    """Query LM Studio for the currently loaded model. Returns model key or None.
    Cached for 60 seconds to avoid hammering LM Studio."""
    now = time.time()
    if _lmstudio_cache["ts"] and now - _lmstudio_cache["ts"] < _LMSTUDIO_CACHE_TTL:
        return _lmstudio_cache["model"]
    try:
        import providers

        data = providers.load_data()
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
                _lmstudio_cache["model"] = result
                _lmstudio_cache["ts"] = now
                return result
        _lmstudio_cache["model"] = None
        _lmstudio_cache["ts"] = now
    except Exception:
        pass
    return _lmstudio_cache["model"]


# In-memory token counters (reset on restart)
_token_counters: dict[str, int] = {
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "total_tokens": 0,
    "calls": 0,
}


def token_stats() -> dict:
    return dict(_token_counters)


# In-memory idle tracking
_last_activity_time: float = time.time()


def record_activity():
    global _last_activity_time
    _last_activity_time = time.time()


def idle_status() -> dict:
    if IDLE_TIMEOUT_MINUTES <= 0:
        return {
            "idle": False,
            "remaining_sec": -1,
            "timeout_min": 0,
            "last_activity": _last_activity_time,
        }
    elapsed = time.time() - _last_activity_time
    timeout_sec = IDLE_TIMEOUT_MINUTES * 60
    remaining = max(0, timeout_sec - elapsed)
    return {
        "idle": elapsed >= timeout_sec,
        "remaining_sec": remaining,
        "timeout_min": IDLE_TIMEOUT_MINUTES,
        "last_activity": _last_activity_time,
    }


# Pipeline log — ring buffer of recent enrichment operations
_pipeline_log: deque = deque(maxlen=500)


def _log_pipeline(
    surface: str = "?",
    record_id: str = "?",
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: int = 0,
    keywords: int = 0,
    status: str = "ok",
    error: str = "",
    duration_ms: int = 0,
    queue_id: int = 0,
):
    _pipeline_log.append(
        {
            "ts": time.time(),
            "surface": surface,
            "record_id": record_id,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "keywords": keywords,
            "status": status,
            "error": error,
            "duration_ms": duration_ms,
            "queue_id": queue_id,
        }
    )


def pipeline_log(limit: int = 50) -> list[dict]:
    return list(_pipeline_log)[-limit:]


# ── Prompt Templates ─────────────────────────────────────────

CORPUS_PROMPT = """Generate search keywords NOT in this text. Synonyms, abbreviations, alternate names. 3-5 phrases. Output only JSON:

Text: {text}

{{"keywords": ['keyword1', 'keyword2']}}"""

QUERY_PROMPT = """Generate search terms NOT in this query that a correct result would contain. 5-10 phrases. Output only JSON:

Query: {query}

{{"keywords": ['term1', 'term2']}}"""

CLASSIFY_PROMPT = """Classify this memory entry into exactly one type:
- red: critical, must never be forgotten, pinned or high-priority
- concept: architectural decision, key insight, factual knowledge
- procedural: step-by-step how-to, instructions, imperative actions
- temporal: time-stamped event, date reference, occurrence
- relation: dependency, connection, "depends on", "uses", "requires"

Entry: {text}

Output only the type name (one word):"""

CONSOLIDATE_PROMPT = """Given these session summaries from the past week, extract 3-5 key insights that
span multiple sessions. Focus on patterns, decisions, and outcomes that repeat or build on each other.
For each insight, classify it as one of: concept (architectural decision or key insight),
procedural (how-to or step-by-step), or relation (dependency or connection).

Sessions:
{text}

Output a JSON object with a single key "insights" containing an array of objects,
each with "content" (the insight text) and "type" (concept/procedural/relation).
Example: {{"insights": [{{"content": "Always use parameterized queries", "type": "concept"}}]}}"""

PROCEDURE_EXTRACT_PROMPT = """Given this completed task, extract the key steps that led to its successful resolution.
Focus on the repeatable procedure — what someone should do to accomplish the same task again.
Remove task-specific details, keep the generalizable steps.

Task: {task_title}
Type: {task_type}
Result: {task_result}

Output a JSON object with:
- "task_pattern": a short generalized description of the task type (e.g., "deploy to production", "fix failing test")
- "steps": an array of step descriptions in order, each step as a string
- "task_type": the category (e.g., "deployment", "bugfix", "refactor", "feature")

Example: {{"task_pattern": "deploy to production", "steps": ["run test suite", "update version number", "build docker image", "push to registry", "update kubernetes manifest"], "task_type": "deployment"}}"""

# ── LLM Client ───────────────────────────────────────────────


async def _post_chat(
    session: aiohttp.ClientSession, payload: dict, retries: int = ENRICH_MAX_RETRIES
) -> dict[str, Any]:
    global _ZEN_BACKOFF_UNTIL, _zen_rate_bucket
    is_zen = "opencode.ai/zen" in LLM_URL or "zen-proxy" in LLM_URL
    if is_zen:
        now = time.time()
        if now < _ZEN_BACKOFF_UNTIL:
            raise RuntimeError(f"zen backoff {_ZEN_BACKOFF_UNTIL - now:.0f}s remaining")
        if not _zen_rate_bucket.consume():
            raise RuntimeError("zen rate limited, skipping")
    headers = {"User-Agent": "Mozilla/5.0"}
    if LLM_API_KEY:
        headers["Authorization"] = f"Bearer {LLM_API_KEY}"
    for attempt in range(retries):
        try:
            async with session.post(
                LLM_URL,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
                if resp.status == 429 and is_zen:
                    _zen_rate_bucket.rate = max(_zen_rate_bucket.rate / 2, 0.01)
                    _ZEN_BACKOFF_UNTIL = time.time() + 60
                    raise RuntimeError("zen 429 rate limit, halving rate")
                text = await resp.text()
                if attempt < retries - 1:
                    await asyncio.sleep(2**attempt)
                    continue
                raise RuntimeError(f"LLM HTTP {resp.status}: {text[:200]}")
        except (asyncio.TimeoutError, aiohttp.ClientError) as e:
            if attempt < retries - 1:
                await asyncio.sleep(2**attempt)
                continue
            raise RuntimeError(f"LLM connection failed ({LLM_URL}): {e}") from e


def _extract_content(data: dict) -> str:
    output = data.get("output")
    if isinstance(output, list):
        for item in output:
            if item.get("type") == "message":
                return item.get("content") or ""
    choice = data.get("choices", [{}])[0]
    msg = choice.get("message", {})
    content = msg.get("content", "") or ""
    reasoning = msg.get("reasoning", "") or msg.get("reasoning_content", "") or ""
    if content:
        return content
    if reasoning:
        return reasoning
    return ""


def _parse_keywords(raw: str) -> list[str]:
    """Parse JSON keywords from LLM output, handling DeepSeek reasoning
    that may precede the JSON content."""
    if not raw:
        return []
    try:
        data = json.loads(raw)
        return data.get("keywords", [])
    except json.JSONDecodeError:
        pass
    idx = raw.rfind('{"keywords"')
    if idx != -1:
        end = raw.find("}", idx)
        if end != -1:
            try:
                data = json.loads(raw[idx : end + 1])
                return data.get("keywords", [])
            except json.JSONDecodeError:
                pass
    return []


def _extract_keywords_from_reasoning(data: dict) -> list[str]:
    """Fallback: extract keyword-like phrases from reasoning text when DeepSeek
    eats all tokens on thinking and never produces JSON content field."""
    msg = data.get("choices", [{}])[0].get("message", {})
    reasoning = msg.get("reasoning", "") or msg.get("reasoning_content", "") or ""
    if not reasoning:
        return []

    lines = [l.strip() for l in reasoning.split("\n") if l.strip()]
    candidates = []
    for line in lines:
        line = line.lstrip("- *•#").strip()
        if "keyword" in line.lower() or "term" in line.lower():
            for phrase in line.replace('"', "").replace("'", "").split(","):
                phrase = phrase.strip()
                words = [w for w in phrase.split() if len(w) > 2]
                if words:
                    candidates.append(" ".join(words[:3]))

    if not candidates and len(reasoning) > 100:
        import re

        phrases = re.findall(r'"[^"]{3,30}"', reasoning)
        for phrase in phrases:
            clean = phrase.strip('"').strip()
            if len(clean.split()) <= 3 and len(clean) >= 3:
                candidates.append(clean)

    seen: set[str] = set()
    result = []
    for c in candidates:
        key = c.lower()
        if key not in seen:
            seen.add(key)
            result.append(c)
    return result


async def _enrich_internal(
    text: str,
    prompt_template: str,
    max_keywords: int,
    session: aiohttp.ClientSession = None,
    is_paper: bool = False,
    pipeline_ctx: dict | None = None,
) -> list[str]:
    if not ENRICH_ENABLED:
        return []
    if _is_global_locked():
        return []
    if not text or not text.strip():
        return []
    max_tokens = ENRICH_MAX_TOKENS_PAPERS if is_paper else ENRICH_MAX_TOKENS
    prompt = prompt_template.format(text=text[:3000], query=text[:3000])
    messages = []
    if is_paper:
        messages.append(
            {
                "role": "system",
                "content": "You are a search index enricher. Output ONLY a JSON object. No explanations, no markdown, no reasoning. Just the JSON.",
            }
        )
    messages.append({"role": "user", "content": prompt})
    model = LLM_MODEL
    if "/api/v1/chat" in LLM_URL:
        loaded = _get_lmstudio_loaded_model()
        if loaded:
            model = loaded
        payload = {
            "model": model,
            "input": prompt,
            "max_output_tokens": max_tokens,
            "temperature": ENRICH_TEMPERATURE,
        }
    else:
        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": ENRICH_TEMPERATURE,
        }
    own_session = session is None
    if own_session:
        session = aiohttp.ClientSession()
    t0 = time.time()
    try:
        data = await _post_chat(session, payload)
        stats = data.get("stats") if "/api/v1/chat" in LLM_URL else None
        if stats:
            pt = stats.get("input_tokens", 0) or 0
            ct = stats.get("total_output_tokens", 0) or 0
            tt = pt + ct
        else:
            usage = data.get("usage", {}) or {}
            pt = usage.get("prompt_tokens", 0) or 0
            ct = usage.get("completion_tokens", 0) or 0
            tt = usage.get("total_tokens", 0) or 0
        _token_counters["prompt_tokens"] += pt
        _token_counters["completion_tokens"] += ct
        _token_counters["total_tokens"] += tt
        _token_counters["calls"] += 1
        raw = _extract_content(data)
        keywords = _parse_keywords(raw)
        if not keywords:
            keywords = _extract_keywords_from_reasoning(data)
        nkw = len(keywords[:max_keywords])
        _log_pipeline(
            **(pipeline_ctx or {}),
            prompt_tokens=pt,
            completion_tokens=ct,
            total_tokens=tt,
            keywords=nkw,
            status="ok",
            duration_ms=int((time.time() - t0) * 1000),
        )
        return keywords[:max_keywords]
    except Exception as e:
        _log_pipeline(
            **(pipeline_ctx or {}),
            status="error",
            error=str(e)[:200],
            duration_ms=int((time.time() - t0) * 1000),
        )
        if "rate limited" in str(e) or "backoff" in str(e):
            logger.debug("enrich skipped: %s", e)
        else:
            logger.warning("enrich_internal failed: %s", e)
        return []
    finally:
        if own_session:
            await session.close()


async def enrich_text(
    text: str,
    session: aiohttp.ClientSession = None,
    is_paper: bool = False,
    pipeline_ctx: dict | None = None,
) -> list[str]:
    return await _enrich_internal(
        text, CORPUS_PROMPT, 5, session, is_paper=is_paper, pipeline_ctx=pipeline_ctx
    )


async def expand_query(
    query: str,
    session: aiohttp.ClientSession = None,
    pipeline_ctx: dict | None = None,
) -> list[str]:
    return await _enrich_internal(
        query, QUERY_PROMPT, 10, session, pipeline_ctx=pipeline_ctx
    )


async def classify_memory_entry(
    text: str, session: aiohttp.ClientSession = None
) -> str | None:
    valid_types = ("red", "concept", "procedural", "temporal", "relation")
    own_session = session is None
    if own_session:
        session = aiohttp.ClientSession()
    try:
        prompt = CLASSIFY_PROMPT.format(text=text[:3000])
        messages = [{"role": "user", "content": prompt}]
        payload = {
            "model": LLM_MODEL,
            "messages": messages,
            "max_tokens": ENRICH_MAX_TOKENS,
            "temperature": ENRICH_TEMPERATURE,
        }
        data = await _post_chat(session, payload)
        raw = _extract_content(data).strip().lower()
        if raw in valid_types:
            return raw
        for word in raw.split():
            word = word.strip(".,;:!?\"'()")
            if word in valid_types:
                return word
        return None
    except Exception:
        logger.warning("classify_memory_entry failed", exc_info=True)
        return None
    finally:
        if own_session:
            await session.close()


async def consolidate_sessions(
    session_summaries: list[str], session: aiohttp.ClientSession | None = None
) -> list[dict]:
    own_session = session is None
    if own_session:
        session = aiohttp.ClientSession()
    try:
        combined = "\n---\n".join(session_summaries[:10])
        prompt = CONSOLIDATE_PROMPT.format(text=combined[:4000])
        messages = [{"role": "user", "content": prompt}]
        payload = {
            "model": LLM_MODEL,
            "messages": messages,
            "max_tokens": ENRICH_MAX_TOKENS,
            "temperature": ENRICH_TEMPERATURE,
        }
        data = await _post_chat(session, payload)
        raw = _extract_content(data)
        if not raw:
            return []
        try:
            parsed = json.loads(raw)
            return parsed.get("insights", [])
        except json.JSONDecodeError:
            idx = raw.rfind('{"insights"')
            if idx != -1:
                end = raw.find("]}", idx)
                if end != -1:
                    try:
                        parsed = json.loads(raw[idx : end + 2])
                        return parsed.get("insights", [])
                    except json.JSONDecodeError:
                        pass
        return []
    except Exception:
        logger.warning("consolidate_sessions failed", exc_info=True)
        return []
    finally:
        if own_session:
            await session.close()


async def extract_procedure(
    task_title: str,
    task_type: str = "task",
    task_result: str = "",
    session: aiohttp.ClientSession | None = None,
) -> dict | None:
    own_session = session is None
    if own_session:
        session = aiohttp.ClientSession()
    try:
        prompt = PROCEDURE_EXTRACT_PROMPT.format(
            task_title=task_title[:200],
            task_type=task_type,
            task_result=(task_result or "N/A")[:1000],
        )
        messages = [{"role": "user", "content": prompt}]
        payload = {
            "model": LLM_MODEL,
            "messages": messages,
            "max_tokens": ENRICH_MAX_TOKENS,
            "temperature": ENRICH_TEMPERATURE,
        }
        data = await _post_chat(session, payload)
        raw = _extract_content(data)
        if not raw:
            return None
        try:
            parsed = json.loads(raw)
            if "task_pattern" in parsed and "steps" in parsed:
                return parsed
        except json.JSONDecodeError:
            idx = raw.find("{")
            if idx != -1:
                try:
                    parsed = json.loads(raw[idx:])
                    if "task_pattern" in parsed and "steps" in parsed:
                        return parsed
                except json.JSONDecodeError:
                    pass
        return None
    except Exception:
        logger.warning("extract_procedure failed", exc_info=True)
        return None
    finally:
        if own_session:
            await session.close()


# ── DF (Document Frequency) Filter ───────────────────────────


def _make_table_map() -> dict[str, tuple[str, str]]:
    return {
        "sessions": (
            "sessions",
            "COALESCE(title,'') || ' ' || COALESCE(summary,'') || ' ' || "
            "COALESCE(task,'') || ' ' || COALESCE(array_to_string(search_enrichments,' '), '')",
        ),
        "chitchat": (
            "chitchat_messages",
            "COALESCE(text,'') || ' ' || COALESCE(array_to_string(search_enrichments,' '), '')",
        ),
        "topics": (
            "topics",
            "name || ' ' || COALESCE(array_to_string(facts,' '), '') || ' ' || "
            "COALESCE(array_to_string(search_enrichments,' '), '')",
        ),
        "memory": (
            "project_memory",
            "COALESCE(entry,'') || ' ' || COALESCE(array_to_string(search_enrichments,' '), '')",
        ),
        "papers": (
            "papers",
            "COALESCE(text,'') || ' ' || COALESCE(enriched_text,'')",
        ),
    }


async def df_filter(
    terms: list[str],
    pool,
    surface: str,
    max_df_ratio: float = MAX_DF_RATIO,
) -> list[str]:
    if not terms:
        return []
    table_map = _make_table_map()
    if surface not in table_map:
        return terms
    table, text_col = table_map[surface]
    async with pool.acquire() as conn:
        total = await conn.fetchval(f"SELECT COUNT(*) FROM {table}")
        if total == 0:
            return terms
        max_df = max(3, int(total * max_df_ratio))
        kept = []
        for term in terms:
            clean = term.strip().lower()
            if len(clean) < 3:
                continue
            count = await conn.fetchval(
                f"SELECT COUNT(*) FROM {table} WHERE {text_col} ILIKE $1",
                f"%{clean}%",
            )
            if 0 < count <= max_df:
                kept.append(term)
        return kept


async def df_filter_combined(terms: list[str], pool) -> list[str]:
    if not terms:
        return []
    surfaces = ["sessions", "chitchat", "topics", "memory", "papers"]
    results = await asyncio.gather(*[df_filter(terms, pool, s) for s in surfaces])
    seen: set[str] = set()
    merged: list[str] = []
    for r in results:
        for term in r:
            if term.lower() not in seen:
                seen.add(term.lower())
                merged.append(term)
    return merged


# ── Queue Processing ─────────────────────────────────────────


async def enqueue_enrichment(
    pool,
    surface: str,
    record_id: str,
    text: str,
) -> None:
    if not ENRICH_ENABLED:
        return
    if not text or not text.strip():
        return
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO enrichment_queue (surface, record_id, text) "
                "VALUES ($1, $2, $3) "
                "ON CONFLICT (surface, record_id) WHERE status IN ('pending', 'processing') DO NOTHING",
                surface,
                record_id,
                text[:2000],
            )
    except Exception as e:
        logger.warning("enqueue_enrichment failed: %s", e)


async def process_queue(pool, batch_size: int = ENRICH_CONCURRENCY) -> int:
    if not ENRICH_ENABLED:
        return 0
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            WITH batch AS (
                SELECT id, surface, record_id, text
                FROM enrichment_queue
                WHERE status = 'pending'
                ORDER BY id
                LIMIT $1
                FOR UPDATE SKIP LOCKED
            )
            UPDATE enrichment_queue SET status = 'processing'
            FROM batch WHERE enrichment_queue.id = batch.id
            RETURNING enrichment_queue.id, enrichment_queue.surface,
                      enrichment_queue.record_id, enrichment_queue.text
            """,
            batch_size,
        )
        if not rows:
            return 0
    processed = 0
    async with aiohttp.ClientSession() as session:
        tasks = [
            _process_one(
                pool, session, r["id"], r["surface"], r["record_id"], r["text"]
            )
            for r in rows
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for r in results:
            if isinstance(r, Exception):
                logger.warning("process_queue item failed: %s", r)
            elif r:
                processed += 1
    return processed


async def _process_one(
    pool,
    session: aiohttp.ClientSession,
    queue_id: int,
    surface: str,
    record_id: str,
    text: str,
) -> bool:
    try:
        ctx = {"surface": surface, "record_id": record_id, "queue_id": queue_id}
        is_paper = surface == "papers"
        keywords = await enrich_text(text, session, is_paper=is_paper, pipeline_ctx=ctx)
        if not keywords:
            await _mark_queue_error(pool, queue_id, "no_keywords_generated")
            await _mark_unenrichable(pool, surface, record_id)
            return False
        await _store_enrichments(pool, surface, record_id, keywords)
        await _mark_queue_done(pool, queue_id)
        return True
    except Exception as e:
        await _mark_queue_error(pool, queue_id, str(e)[:500])
        return False


async def _mark_unenrichable(pool, surface: str, record_id: str):
    """Mark a record as unenrichable to prevent infinite re-enqueue."""
    table_map = {
        "sessions": ("sessions", "id", False),
        "chitchat": ("chitchat_messages", "id", False),
        "topics": ("topics", "name", False),
        "memory": ("project_memory", "id", True),
        "papers": ("papers", "id", True),
    }
    if surface not in table_map:
        return
    table, pk, is_bigint = table_map[surface]
    rid: int | str = int(record_id) if is_bigint else record_id
    try:
        async with pool.acquire() as conn:
            if surface == "papers":
                await conn.execute(
                    f"UPDATE papers SET enriched_text = $1 WHERE {pk} = $2",
                    _NOKW_SENTINEL,
                    rid,
                )
            else:
                await conn.execute(
                    f"UPDATE {table} SET search_enrichments = $1 WHERE {pk} = $2",
                    [_NOKW_SENTINEL],
                    rid,
                )
    except Exception as e:
        logger.warning("mark_unenrichable failed for %s/%s: %s", surface, record_id, e)


async def _store_enrichments(pool, surface: str, record_id: str, keywords: list[str]):
    table_map = {
        "sessions": ("sessions", "id", False),
        "chitchat": ("chitchat_messages", "id", False),
        "topics": ("topics", "name", False),
        "memory": ("project_memory", "id", True),
        "papers": ("papers", "id", True),
    }
    if surface not in table_map:
        return
    table, pk, is_bigint = table_map[surface]
    rid: int | str = int(record_id) if is_bigint else record_id
    try:
        async with pool.acquire() as conn:
            if surface == "papers":
                await conn.execute(
                    f"UPDATE {table} SET enriched_text = $1 WHERE {pk} = $2",
                    " ".join(keywords),
                    rid,
                )
            else:
                await conn.execute(
                    f"UPDATE {table} SET search_enrichments = $1 WHERE {pk} = $2",
                    keywords,
                    rid,
                )
    except Exception as e:
        logger.warning("_store_enrichments failed for %s/%s: %s", surface, record_id, e)


async def _mark_queue_done(pool, queue_id: int):
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE enrichment_queue SET status = 'done', processed_at = $1 "
            "WHERE id = $2",
            time.time(),
            queue_id,
        )


async def _mark_queue_error(pool, queue_id: int, error: str):
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE enrichment_queue SET status = 'error', processed_at = $1, error = $2 "
            "WHERE id = $3",
            time.time(),
            error[:500],
            queue_id,
        )


async def recover_stale(pool) -> int:
    """Reset stale processing rows back to pending."""
    async with pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE enrichment_queue SET status = 'pending', processed_at = NULL, error = NULL "
            "WHERE status = 'processing' AND created_at < $1",
            time.time() - STALE_PROCESSING_SEC,
        )
        count = int(result.split()[-1])
        if count:
            logger.info("recovered %d stale enrichment items", count)
        return count


# ── Queue Stats ──────────────────────────────────────────────


async def queue_stats(pool) -> dict:
    async with pool.acquire() as conn:
        pending = await conn.fetchval(
            "SELECT COUNT(*) FROM enrichment_queue WHERE status = 'pending'"
        )
        processing = await conn.fetchval(
            "SELECT COUNT(*) FROM enrichment_queue WHERE status = 'processing'"
        )
        row = await conn.fetchrow(
            "SELECT processed_at FROM enrichment_queue "
            "WHERE status = 'done' ORDER BY processed_at DESC LIMIT 1"
        )
        last_enriched = row["processed_at"] if row else None
        error_count = await conn.fetchval(
            "SELECT COUNT(*) FROM enrichment_queue WHERE status = 'error'"
        )
        total = await conn.fetchval("SELECT COUNT(*) FROM enrichment_queue")
        total_done = await conn.fetchval(
            "SELECT COUNT(*) FROM enrichment_queue WHERE status = 'done'"
        )
    return {
        "pending": pending,
        "processing": processing,
        "done": total_done,
        "error": error_count,
        "total": total,
        "last_enriched": last_enriched,
    }


# ── Reindex ──────────────────────────────────────────────────


async def reindex_all(pool) -> dict:
    """Re-enqueue all enrichable records for re-processing."""
    if not ENRICH_ENABLED:
        return {"ok": False, "error": "enrichment disabled"}
    count = 0
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, COALESCE(title,'') || ' ' || COALESCE(summary,'') || ' ' "
            "|| COALESCE(task,'') AS text "
            "FROM sessions WHERE search_enrichments = '{}' OR search_enrichments IS NULL"
        )
        for r in rows:
            await conn.execute(
                "INSERT INTO enrichment_queue (surface, record_id, text) VALUES ($1, $2, $3) "
                "ON CONFLICT (surface, record_id) WHERE status IN ('pending', 'processing') DO NOTHING",
                "sessions",
                r["id"],
                (r["text"] or "")[:2000],
            )
            count += 1
        rows = await conn.fetch(
            "SELECT id, text FROM chitchat_messages "
            "WHERE search_enrichments = '{}' OR search_enrichments IS NULL"
        )
        for r in rows:
            await conn.execute(
                "INSERT INTO enrichment_queue (surface, record_id, text) VALUES ($1, $2, $3) "
                "ON CONFLICT (surface, record_id) WHERE status IN ('pending', 'processing') DO NOTHING",
                "chitchat",
                r["id"],
                (r["text"] or "")[:2000],
            )
            count += 1
        rows = await conn.fetch(
            "SELECT name, name || ' ' || COALESCE(array_to_string(facts, ' '), '') AS text "
            "FROM topics "
            "WHERE search_enrichments = '{}' OR search_enrichments IS NULL"
        )
        for r in rows:
            await conn.execute(
                "INSERT INTO enrichment_queue (surface, record_id, text) VALUES ($1, $2, $3) "
                "ON CONFLICT (surface, record_id) WHERE status IN ('pending', 'processing') DO NOTHING",
                "topics",
                r["name"],
                (r["text"] or "")[:2000],
            )
            count += 1
        rows = await conn.fetch(
            "SELECT id::text, entry AS text FROM project_memory "
            "WHERE search_enrichments = '{}' OR search_enrichments IS NULL"
        )
        for r in rows:
            await conn.execute(
                "INSERT INTO enrichment_queue (surface, record_id, text) VALUES ($1, $2, $3) "
                "ON CONFLICT (surface, record_id) WHERE status IN ('pending', 'processing') DO NOTHING",
                "memory",
                r["id"],
                (r["text"] or "")[:2000],
            )
            count += 1
        rows = await conn.fetch(
            "SELECT id::text, text FROM papers "
            "WHERE enriched_text = '' OR enriched_text IS NULL"
        )
        for r in rows:
            await conn.execute(
                "INSERT INTO enrichment_queue (surface, record_id, text) VALUES ($1, $2, $3) "
                "ON CONFLICT (surface, record_id) WHERE status IN ('pending', 'processing') DO NOTHING",
                "papers",
                r["id"],
                (r["text"] or "")[:2000],
            )
            count += 1
    return {"ok": True, "enqueued": count}
