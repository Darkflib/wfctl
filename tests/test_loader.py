"""Loader / discovery tests (PRD §18 loader, §14)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from wfctl.errors import ValidationError
from wfctl.loader import discover_workflow_files, load_workflow_file, load_workflows


def test_discover_only_yaml(tmp_path: Path):
    (tmp_path / "a.yaml").write_text("id: a\ndescription: d\nexec: {mode: command, command: [x]}\n")
    (tmp_path / "b.yml").write_text("id: b\ndescription: d\nexec: {mode: command, command: [x]}\n")
    (tmp_path / "notes.txt").write_text("ignore me")
    (tmp_path / "README.md").write_text("nope")
    found = discover_workflow_files(tmp_path)
    assert [p.name for p in found] == ["a.yaml", "b.yml"]


def test_missing_dir_raises(tmp_path: Path):
    with pytest.raises(ValidationError):
        discover_workflow_files(tmp_path / "nope")


def test_sha256_matches_source(daily_news_path: Path):
    wf = load_workflow_file(daily_news_path)
    expected = hashlib.sha256(daily_news_path.read_bytes()).hexdigest()
    assert wf.source_sha256 == expected
    assert wf.source_path == daily_news_path


def test_empty_file_rejected(tmp_path: Path):
    p = tmp_path / "empty.yaml"
    p.write_text("")
    with pytest.raises(ValidationError):
        load_workflow_file(p)


def test_non_mapping_rejected(tmp_path: Path):
    p = tmp_path / "list.yaml"
    p.write_text("- a\n- b\n")
    with pytest.raises(ValidationError):
        load_workflow_file(p)


def test_bad_yaml_rejected(tmp_path: Path):
    p = tmp_path / "bad.yaml"
    p.write_text("id: a\n  : : broken")
    with pytest.raises(ValidationError):
        load_workflow_file(p)


def test_duplicate_ids_rejected(tmp_path: Path):
    body = "description: d\nexec: {mode: command, command: [x]}\n"
    (tmp_path / "one.yaml").write_text("id: dup\n" + body)
    (tmp_path / "two.yaml").write_text("id: dup\n" + body)
    with pytest.raises(ValidationError, match="duplicate"):
        load_workflows(tmp_path)


def test_load_workflows_sorted(workflows_dir: Path):
    loaded = load_workflows(workflows_dir)
    ids = [w.id for w in loaded]
    assert ids == sorted(ids)
    assert set(ids) == {"daily-news", "manual-job"}
