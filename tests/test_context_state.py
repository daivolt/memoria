from __future__ import annotations

import context as context_mod


def test_project_path_creates_directory(temp_memoria_base):
    path = context_mod.project_path("test-project")
    assert path.exists()
    assert path.name == "context"


def test_current_state_returns_none_when_missing(temp_memoria_base):
    assert context_mod.current_state("missing-project") is None


def test_write_and_read_state(temp_memoria_base):
    context_mod.write_state("test-project", "do thing", files=["a.py"])
    state = context_mod.current_state("test-project")
    assert state is not None
    assert state["task"] == "do thing"
    assert state["files"] == ["a.py"]
