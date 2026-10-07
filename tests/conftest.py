from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from jobinja_mcp.config import Settings

FIXTURES = Path(__file__).parent / "fixtures"


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf8")


def fixture_json(name: str) -> Any:
    return json.loads(fixture_text(name))


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path, cache_enabled=False, min_request_interval=0, max_retries=0)
