"""Shared pytest fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures" / "workflows"
GOLDEN = Path(__file__).parent / "golden"


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES


@pytest.fixture
def golden_dir() -> Path:
    return GOLDEN


@pytest.fixture
def daily_news_path() -> Path:
    return FIXTURES / "daily-news.yaml"


@pytest.fixture
def workflows_dir(tmp_path: Path) -> Path:
    """A temp config dir seeded with the daily-news and manual-job fixtures."""
    dest = tmp_path / "workflows"
    dest.mkdir()
    for name in ("daily-news.yaml", "manual-job.yaml"):
        (dest / name).write_bytes((FIXTURES / name).read_bytes())
    return dest
