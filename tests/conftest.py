from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

import context as context_mod


@pytest.fixture
def temp_memoria_base(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setattr(context_mod, "BASE", Path(tmp))
        yield Path(tmp)
