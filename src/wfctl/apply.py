"""Execute a plan: atomic writes, safe deletes, and systemd reconciliation.

Order of operations (PRD §12.2):
  1. Write/update/delete unit files (atomically, managed-only for deletes).
  2. ``systemctl --user daemon-reload``.
  3. Enable+start timers for enabled scheduled workflows.
  4. Disable+stop timers for disabled scheduled workflows.
Services are never auto-started (use ``wfctl run``).
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .errors import UnsafeOperationError
from .loader import LoadedWorkflow
from .logging import get_logger
from .plan import Action, Plan, is_managed_unit_file
from .systemd import SystemdRunner

logger = get_logger()

UNIT_FILE_MODE = 0o644


def atomic_write(path: Path, content: str, *, mode: int = UNIT_FILE_MODE) -> None:
    """Write *content* to *path* atomically: temp file in the same dir + rename.

    Same-directory temp guarantees the rename is atomic (same filesystem). The
    file is fsync'd before rename so a crash can't leave a half-written unit.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.tmp-",
        dir=str(path.parent),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp_path, mode)
        os.replace(tmp_path, path)
    except BaseException:
        # Best effort cleanup; never leak the temp file.
        tmp_path.unlink(missing_ok=True)
        raise


def safe_delete(path: Path) -> None:
    """Delete *path* only if it is a wfctl-managed unit file.

    Refusing otherwise is the core safety guarantee (PRD §16.2, §20.4).
    """
    if not is_managed_unit_file(path):
        raise UnsafeOperationError(
            f"refusing to delete non-managed file: {path} "
            "(missing 'Managed-By: wfctl' marker or wrong name)"
        )
    path.unlink(missing_ok=True)


@dataclass
class ApplyReport:
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    enabled: list[str] = field(default_factory=list)
    disabled: list[str] = field(default_factory=list)
    daemon_reloaded: bool = False
    systemctl_skipped: bool = False


def apply_plan(
    plan: Plan,
    workflows: list[LoadedWorkflow],
    *,
    runner: SystemdRunner,
    use_systemctl: bool = True,
) -> ApplyReport:
    """Write the plan to disk and reconcile timers via systemd.

    *workflows* is the validated desired set, used to decide which timers to
    enable/disable. When *use_systemctl* is False, files are written but no
    systemd commands run (``--no-systemctl``; PRD §12.2).
    """
    report = ApplyReport(systemctl_skipped=not use_systemctl)

    # --- 1. file writes (and prunes) -------------------------------------
    file_changed = False
    for item in plan.items:
        if item.action is Action.CREATE or item.action is Action.UPDATE:
            if item.content is None:
                # Programming error: planner must always supply content for
                # CREATE/UPDATE. Raise instead of assert so the check survives
                # `python -O` (bandit B101).
                raise RuntimeError(
                    f"plan item {item.unit_name!r} has no content for {item.action.value}"
                )
            atomic_write(item.path, item.content)
            (report.created if item.action is Action.CREATE else report.updated).append(
                item.unit_name
            )
            file_changed = True
        elif item.action is Action.DELETE:
            # For an orphaned *timer*, ask systemd to disable+stop it *before*
            # we delete the unit file. Otherwise systemd's enablement symlinks
            # and any running schedule state stay behind and a daemon-reload
            # later may complain. Services are oneshot and inactive between
            # runs, so a plain file delete is enough.
            if use_systemctl and item.unit_name.endswith(".timer"):
                runner.disable_now(item.unit_name)
                report.disabled.append(item.unit_name)
            safe_delete(item.path)
            report.deleted.append(item.unit_name)
            file_changed = True
        # UNCHANGED: nothing to do.

    if not use_systemctl:
        for line in _summary_lines(report):
            logger.info(line)
        return report

    # --- 2. daemon-reload -------------------------------------------------
    # Reload if anything on disk changed (covers deletes of enabled timers too).
    if file_changed:
        runner.daemon_reload()
        report.daemon_reloaded = True

    # --- 3/4. enable or disable timers -----------------------------------
    for wf in workflows:
        if not wf.definition.has_timer:
            continue  # manual workflow: never touch timers
        timer = wf.definition.timer_unit_name
        if wf.definition.enabled:
            runner.enable_now(timer)
            report.enabled.append(timer)
        else:
            runner.disable_now(timer)
            report.disabled.append(timer)

    for line in _summary_lines(report):
        logger.info(line)
    return report


def _summary_lines(report: ApplyReport) -> list[str]:
    lines = []
    if report.created:
        lines.append(f"created: {', '.join(report.created)}")
    if report.updated:
        lines.append(f"updated: {', '.join(report.updated)}")
    if report.deleted:
        lines.append(f"deleted: {', '.join(report.deleted)}")
    if report.enabled:
        lines.append(f"enabled timers: {', '.join(report.enabled)}")
    if report.disabled:
        lines.append(f"disabled timers: {', '.join(report.disabled)}")
    if not lines:
        lines.append("no changes")
    return lines
