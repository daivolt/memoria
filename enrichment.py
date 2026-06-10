"""
enrichment — SIRA-style LLM search vocabulary enrichment for memoria.

Pattern: LLM proposes vocabulary → DF/corpus statistics filter → BM25/similarity scores.
Adapted from the Superintelligent Retrieval Agent (SIRA) paper.

Two surfaces:
  CORPUS-SIDE (write-time): LLM generates missing search terms per record,
    stored in search_enrichments TEXT[] column. Runs async via enrichment_queue.
  QUERY-SIDE (recall-time): LLM expands user query → DF filter validates terms
    against the corpus → weighted retrieval.

LLM backend: Ollama Cloud via local proxy (deepseek-v4-flash:cloud).
"""

import asyncio
import json
import logging
import os
import time
from typing import Any

import aiohttp

logger = logging.getLogger("enrichment")

LLM_URL = os.environ.get(
    "MEMORIA_LLM_URL", "http://localhost:11434/v1/chat/completions"
)
LLM_MODEL = os.environ.get("MEMORIA_LLM_MODEL", "deepseek-v4-flash:cloud")
ENRICH_ENABLED = os.environ.get("MEMORIA_ENRICH_ENABLED", "true").lower() == "true"
EXPANSION_WEIGHT = float(os.environ.get("MEMORIA_ENRICH_WEIGHT", "0.5"))
MAX_DF_RATIO = float(os.environ.get("MEMORIA_ENRICH_DF_RATIO", "0.10"))
ENRICH_MAX_TOKENS = int(os.environ.get("MEMORIA_ENRICH_MAX_TOKENS", "512"))
ENRICH_MAX_TOKENS_PAPERS = int(
    os.environ.get("MEMORIA_ENRICH_MAX_TOKENS_PAPERS", "2048")
)
ENRICH_TEMPERATURE = float(os.environ.get("MEMORIA_ENRICH_TEMPERATURE", "0.0"))
ENRICH_MAX_RETRIES = 3
ENRICH_CONCURRENCY = int(os.environ.get("MEMORIA_ENRICH_CONCURRENCY", "4"))
STALE_PROCESSING_SEC = int(os.environ.get("MEMORIA_ENRICH_STALE_SEC", "600"))

# Sentinel for records that can't be enriched (prevents infinite re-enqueue)
_NOKW_SENTINEL = "__NOKW__"

# ── Prompt Templates ─────────────────────────────────────────

CORPUS_PROMPT = """Generate search keywords NOT in this text. Synonyms, abbreviations, alternate names. 3-5 phrases. Output only JSON:

Text: {text}

{{"keywords": ['keyword1', 'keyword2']}}"""

QUERY_PROMPT = """Generate search terms NOT in this query that a correct result would contain. 5-10 phrases. Output only JSON:

Query: {query}

{{"keywords": ['term1', 'term2']}}"""

# ── LLM Client ───────────────────────────────────────────────


async def _post_chat(
    session: aiohttp.ClientSession, payload: dict, retries: int = ENRICH_MAX_RETRIES
) -> dict[str, Any]:
    for attempt in range(retries):
        try:
            async with session.post(
                LLM_URL, json=payload, timeout=aiohttp.ClientTimeout(total=60)
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
                text = await resp.text()
                if attempt < retries - 1:
                    await asyncio.sleep(2**attempt)
                    continue
                raise RuntimeError(f"LLM HTTP {resp.status}: {text[:200]}")
        except (asyncio.TimeoutError, aiohttp.ClientError) as e:
            if attempt < retries - 1:
                await asyncio.sleep(2**attempt)
                continue
            raise RuntimeError(f"LLM connection failed: {e}") from e


def _extract_content(data: dict) -> str:
    choice = data.get("choices", [{}])[0]
    msg = choice.get("message", {})
    content = msg.get("content", "") or ""
    reasoning = msg.get("reasoning", "") or msg.get("reasoning_content", "") or ""
    if content:
        return content
    if reasoning:
        return reasoning
    return ""


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
) -> list[str]:
    if not ENRICH_ENABLED:
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
    payload = {
        "model": LLM_MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": ENRICH_TEMPERATURE,
    }
    own_session = session is None
    if own_session:
        session = aiohttp.ClientSession()
    try:
        data = await _post_chat(session, payload)
        raw = _extract_content(data)
        keywords = _parse_keywords(raw)
        if not keywords:
            keywords = _extract_keywords_from_reasoning(data)
        return keywords[:max_keywords]
    except Exception as e:
        logger.warning("enrich_internal failed: %s", e)
        return []
    finally:
        if own_session:
            await session.close()


async def enrich_text(
    text: str,
    session: aiohttp.ClientSession = None,
    is_paper: bool = False,
) -> list[str]:
    return await _enrich_internal(text, CORPUS_PROMPT, 5, session, is_paper=is_paper)


async def expand_query(
    query: str,
    session: aiohttp.ClientSession = None,
) -> list[str]:
    return await _enrich_internal(query, QUERY_PROMPT, 10, session)


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
        is_paper = surface == "papers"
        keywords = await enrich_text(text, session, is_paper=is_paper)
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
