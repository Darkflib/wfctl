"""Diff desired (rendered) units against actual managed unit files (PRD §12.1).

The planner is pure: it reads the unit directory and the rendered desired
state, and emits a list of actions. It never writes anything.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from .loader import LoadedWorkflow
from .render import MANAGED_BY_MARKER, render_service, render_timer

# Only files matching this prefix/suffix are ever considered wfctl-managed.
MANAGED_NAME_RE = re.compile(r"^wfctl-[a-z0-9][a-z0-9-]*\.(service|timer)$")


class Action(StrEnum):
    CREATE = "CREATE"
    UPDATE = "UPDATE"
    DELETE = "DELETE"
    UNCHANGED = "UNCHANGED"


@dataclass(frozen=True)
class PlanItem:
    action: Action
    unit_name: str
    # Desired rendered content (None for DELETE).
    content: str | None
    # Absolute target path in the unit directory.
    path: Path
    # The workflow id this unit belongs to (None for orphan DELETE).
    workflow_id: str | None


@dataclass(frozen=True)
class Plan:
    items: list[PlanItem]

    def changes(self) -> list[PlanItem]:
        return [i for i in self.items if i.action is not Action.UNCHANGED]

    @property
    def has_changes(self) -> bool:
        return bool(self.changes())


def is_managed_unit_file(path: Path) -> bool:
    """True iff *path* both matches the wfctl naming convention and carries the
    ``Managed-By: wfctl`` marker in its header.

    Both conditions are required (PRD §10, §16.2) so wfctl can never touch a
    hand-written unit that merely happens to share the name.
    """
    if not MANAGED_NAME_RE.match(path.name):
        return False
    try:
        # Header is in the first handful of lines; read a bounded prefix.
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            head = fh.read(4096)
    except OSError:
        return False
    return MANAGED_BY_MARKER in head


def discover_managed_units(unit_dir: Path) -> dict[str, Path]:
    """Map unit filename -> path for every managed unit in *unit_dir*."""
    if not unit_dir.is_dir():
        return {}
    found: dict[str, Path] = {}
    for path in unit_dir.iterdir():
        if path.is_file() and is_managed_unit_file(path):
            found[path.name] = path
    return found


def _desired_units(workflows: list[LoadedWorkflow]) -> dict[str, tuple[str, str]]:
    """Return desired unit name -> (rendered content, workflow id)."""
    desired: dict[str, tuple[str, str]] = {}
    for wf in workflows:
        desired[wf.definition.service_unit_name] = (render_service(wf), wf.id)
        if wf.definition.has_timer:
            desired[wf.definition.timer_unit_name] = (render_timer(wf), wf.id)
    return desired


def build_plan(
    workflows: list[LoadedWorkflow],
    unit_dir: Path,
    *,
    prune: bool = False,
) -> Plan:
    """Compare desired units to managed units on disk.

    Deletes for orphaned managed units are only included when *prune* is True
    (PRD §12.1).
    """
    desired = _desired_units(workflows)
    actual = discover_managed_units(unit_dir)
    items: list[PlanItem] = []

    for name in sorted(desired):
        content, wf_id = desired[name]
        target = unit_dir / name
        if name not in actual:
            items.append(PlanItem(Action.CREATE, name, content, target, wf_id))
            continue
        current = actual[name].read_text(encoding="utf-8", errors="replace")
        if current == content:
            items.append(PlanItem(Action.UNCHANGED, name, content, target, wf_id))
        else:
            items.append(PlanItem(Action.UPDATE, name, content, target, wf_id))

    if prune:
        for name in sorted(actual):
            if name not in desired:
                items.append(PlanItem(Action.DELETE, name, None, actual[name], None))

    return Plan(items=items)
