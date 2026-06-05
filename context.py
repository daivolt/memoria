"""
Active task context for memoria — real-time state per project.

/var/tmp/memoria/<project>/context/state.json
"""

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

BASE = Path("/var/tmp/memoria")
STALE_SEC = 300


def project_path(project: str) -> Path:
    p = BASE / project / "context"
    p.mkdir(parents=True, exist_ok=True)
    return p


def current_state(project: str) -> Optional[dict[str, Any]]:
    path = project_path(project) / "state.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    since = data.get("since", 0)
    pid = data.get("pid")
    now = time.time()
    if pid and now - since > STALE_SEC:
        try:
            os.kill(pid, 0)
        except OSError:
            path.unlink(missing_ok=True)
            return None
    return data


def write_state(
    project: str,
    task: str,
    files: Optional[list[str]] = None,
    claims: Optional[dict[str, int]] = None,
) -> dict[str, Any]:
    state = {
        "pid": os.getpid(),
        "since": time.time(),
        "task": task[:500],
        "files": files or [],
        "claims": claims or {},
    }
    path = project_path(project) / "state.json"
    fd, tmp = tempfile.mkstemp(dir=str(path.parent))
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(state, f, ensure_ascii=False)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return state


def release_state(project: str) -> bool:
    path = project_path(project) / "state.json"
    if path.exists():
        path.unlink(missing_ok=True)
        return True
    return False


def claim_file(project: str, filename: str) -> tuple[bool, Optional[int]]:
    state = current_state(project)
    if state is None:
        write_state(project, "", claims={filename: os.getpid()})
        return True, None
    claims = state.get("claims", {})
    existing = claims.get(filename)
    if existing is not None and existing != os.getpid():
        try:
            os.kill(existing, 0)
            return False, existing
        except OSError:
            # stale claim — take over
            pass
    claims[filename] = os.getpid()
    state["claims"] = claims
    path = project_path(project) / "state.json"
    fd, tmp = tempfile.mkstemp(dir=str(path.parent))
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(state, f, ensure_ascii=False)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return True, existing if existing and existing != os.getpid() else None
