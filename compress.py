"""
Phase 1 + 2 compression engine. Pure stdlib, deterministic, zero LLM cost.

Phase 1 — Tool output → 1-line summaries (regex).
Phase 2 — Structured turn merging: acks dropped, reasoning
  → placeholder, sequential tools grouped, errors preserved.
"""

import re
from typing import Optional

_TOOL_LINE = re.compile(r"^\[(\w+)\]\s+(.+)")
_EXIT_LINE = re.compile(r"exit (\d+)")
_MATCH_LINE = re.compile(r"Found (\d+) matches", re.IGNORECASE)
_LINE_CNT = re.compile(r"(\d+) lines?", re.IGNORECASE)
_ACK_PATTERNS = re.compile(
    r"^(sure|ok|got it|i('ll| will)|let me|absolutely|certainly)",
    re.IGNORECASE,
)
_IS_TOOL = re.compile(r"^\[(\w+)\]\s+(ran `[^`]+`|executed|returned)")
_IS_TOOL_ANY = re.compile(r"^\[(\w+)\]")

# ── Phase 1 — tool output → 1-liner ──────────────────────────


def _compress_tool_block(lines: list[str]) -> list[str]:
    if not lines:
        return []
    first = lines[0].strip()
    m = _TOOL_LINE.match(first)
    if not m:
        return lines
    tool = m.group(1)
    action = m.group(2)
    code = "?"
    count = len(lines) - 1
    err = any("Error:" in l for l in lines)
    for l in lines:
        x = _EXIT_LINE.search(l)
        if x:
            code = x.group(1)
    status = f"exit {code}{' (error)' if err else ''}"
    for l in lines[1:6]:
        f = _MATCH_LINE.search(l)
        if f:
            return [f"[{tool}] {action} → {status}, {f.group(1)} matches\n"]
        c = _LINE_CNT.search(l)
        if c:
            return [f"[{tool}] {action} → {status}, {c.group(1)}\n"]
    return [
        f"[{tool}] {action} → {status}, {count} lines{' (errors)' if err else ''}\n"
    ]


def phase1(text: str, max_block: int = 30) -> str:
    lines = text.splitlines(keepends=True)
    result = []
    buf = []
    in_tool = False
    for line in lines:
        m = _IS_TOOL.match(line)
        if m:
            if buf:
                result.extend(_compress_tool_block(buf))
            buf = [line]
            in_tool = True
            continue
        if in_tool:
            if _IS_TOOL_ANY.match(line):
                result.extend(_compress_tool_block(buf))
                result.append(line)
                buf = []
                in_tool = False
            elif line.strip() == "" and len(buf) > max_block:
                result.extend(_compress_tool_block(buf))
                result.append(line)
                buf = []
                in_tool = False
            else:
                buf.append(line)
            continue
        result.append(line)
    if buf:
        result.extend(_compress_tool_block(buf))
    return "".join(result)


# ── Phase 2 — structured turn merging ────────────────────────


def _is_ack(text: str) -> bool:
    first = text.strip().lower()[:60]
    if len(first) < 5:
        return True
    if _ACK_PATTERNS.match(first):
        return len(first) < 80
    return False


def _merge_tool_group(group: list[str]) -> str:
    tools = {}
    for g in group:
        m = _TOOL_LINE.match(g.strip())
        if m:
            name = m.group(1)
            tools[name] = tools.get(name, 0) + 1
    parts = [f"{n}×{c}" if c > 1 else n for n, c in sorted(tools.items())]
    total = sum(1 for g in group)
    return f"  [{', '.join(parts)}] ({total} calls merged)\n"


def _classify_text(text: str) -> str:
    tl = text.strip()
    if not tl or len(tl) < 5:
        return "ack"
    if _is_ack(tl):
        return "ack"
    if tl.startswith("[reasoning]") or tl.startswith("reasoning"):
        return "reasoning"
    return "keep"


def phase2(text: str, reasoning_placeholder: str = "[reasoning]") -> str:
    lines = text.splitlines()
    result = []
    tool_group: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped or stripped == reasoning_placeholder:
            i += 1
            continue
        # Tool line (post-Phase-1)
        if _IS_TOOL_ANY.match(line):
            tool_group.append(line)
            i += 1
            continue
        # End of tool group
        if tool_group:
            result.append(_merge_tool_group(tool_group))
            tool_group = []
            continue
        # Classify text
        cls = _classify_text(line)
        if cls == "ack":
            i += 1
            continue
        elif cls == "reasoning":
            result.append(reasoning_placeholder)
            i += 1
            continue
        elif cls == "keep":
            result.append(stripped)
            i += 1
            continue
        i += 1
    if tool_group:
        result.append(_merge_tool_group(tool_group))
    return "\n".join(result)


def compress(text: str, phase: int = 2) -> str:
    if not text:
        return ""
    if phase >= 1:
        text = phase1(text)
    if phase >= 2:
        text = phase2(text)
    return text
