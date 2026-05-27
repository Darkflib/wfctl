"""Discovery and parsing of workflow YAML files (PRD §18 loader).

Responsibilities: find ``*.yaml`` / ``*.yml`` files, parse them, build
validated :class:`WorkflowDefinition` models, attach the source path, compute
the source SHA-256, and detect duplicate ids across the directory.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import ValidationError as PydanticValidationError

from .errors import ValidationError
from .models import WorkflowDefinition

YAML_SUFFIXES = (".yaml", ".yml")


@dataclass(frozen=True)
class LoadedWorkflow:
    """A validated workflow plus provenance used in generated unit headers."""

    definition: WorkflowDefinition
    source_path: Path
    source_sha256: str

    @property
    def id(self) -> str:
        return self.definition.id


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def discover_workflow_files(config_dir: Path) -> list[Path]:
    """Return sorted workflow files in *config_dir* (non-recursive)."""
    if not config_dir.exists():
        raise ValidationError(f"workflow directory does not exist: {config_dir}")
    if not config_dir.is_dir():
        raise ValidationError(f"workflow path is not a directory: {config_dir}")
    files = [
        p
        for p in config_dir.iterdir()
        if p.is_file() and p.suffix.lower() in YAML_SUFFIXES
    ]
    return sorted(files)


def load_workflow_file(path: Path) -> LoadedWorkflow:
    """Parse and validate a single workflow file.

    Raises :class:`ValidationError` on YAML parse errors, non-mapping
    documents, or schema violations — with the offending file named.
    """
    raw = path.read_bytes()
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ValidationError(f"{path}: YAML parse error: {exc}") from exc

    if data is None:
        raise ValidationError(f"{path}: file is empty")
    if not isinstance(data, dict):
        raise ValidationError(
            f"{path}: top-level YAML must be a mapping defining exactly one workflow"
        )

    try:
        definition = WorkflowDefinition.model_validate(data)
    except PydanticValidationError as exc:
        raise ValidationError(f"{path}: schema validation failed:\n{exc}") from exc

    return LoadedWorkflow(
        definition=definition,
        source_path=path,
        source_sha256=_sha256_hex(raw),
    )


def load_workflows(config_dir: Path) -> list[LoadedWorkflow]:
    """Load every workflow in *config_dir*, rejecting duplicate ids.

    The result is sorted by id for deterministic plan/list output.
    """
    files = discover_workflow_files(config_dir)
    loaded: list[LoadedWorkflow] = []
    seen: dict[str, Path] = {}

    for path in files:
        wf = load_workflow_file(path)
        if wf.id in seen:
            raise ValidationError(
                f"duplicate workflow id {wf.id!r}: defined in "
                f"{seen[wf.id]} and {path}"
            )
        seen[wf.id] = path
        loaded.append(wf)

    loaded.sort(key=lambda w: w.id)
    return loaded
