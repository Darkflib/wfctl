"""Apply tests: atomic writes, safe delete, and timer reconciliation (PRD §12)."""

from __future__ import annotations

from pathlib import Path

import pytest

from wfctl.apply import apply_plan, atomic_write, safe_delete
from wfctl.errors import UnsafeOperationError
from wfctl.loader import load_workflows
from wfctl.plan import build_plan
from wfctl.systemd import SystemdRunner


class RecordingRunner(SystemdRunner):
    """A SystemdRunner that records calls instead of shelling out."""

    def __init__(self) -> None:
        super().__init__(dry_run=False)
        self.calls: list[tuple[str, str]] = []

    def daemon_reload(self) -> None:
        self.calls.append(("daemon-reload", ""))

    def enable_now(self, unit: str) -> None:
        self.calls.append(("enable", unit))

    def disable_now(self, unit: str) -> None:
        self.calls.append(("disable", unit))


def test_atomic_write_creates_file(tmp_path: Path):
    target = tmp_path / "sub" / "unit.service"
    atomic_write(target, "hello\n")
    assert target.read_text() == "hello\n"
    assert (target.stat().st_mode & 0o777) == 0o644
    # no leftover temp files
    assert list((tmp_path / "sub").glob(".*tmp*")) == []


def test_atomic_write_overwrites(tmp_path: Path):
    target = tmp_path / "u.service"
    atomic_write(target, "v1\n")
    atomic_write(target, "v2\n")
    assert target.read_text() == "v2\n"


def test_safe_delete_refuses_unmanaged(tmp_path: Path):
    f = tmp_path / "hand-written.service"
    f.write_text("[Unit]\nDescription=mine\n")
    with pytest.raises(UnsafeOperationError):
        safe_delete(f)
    assert f.exists()


def test_safe_delete_removes_managed(tmp_path: Path):
    f = tmp_path / "wfctl-x.service"
    f.write_text("# Managed-By: wfctl\n[Unit]\n")
    safe_delete(f)
    assert not f.exists()


def test_apply_no_systemctl_writes_files(workflows_dir: Path, tmp_path: Path):
    unit_dir = tmp_path / "units"
    unit_dir.mkdir()
    workflows = load_workflows(workflows_dir)
    plan = build_plan(workflows, unit_dir)
    runner = RecordingRunner()

    report = apply_plan(plan, workflows, runner=runner, use_systemctl=False)

    assert (unit_dir / "wfctl-daily-news.service").exists()
    assert (unit_dir / "wfctl-daily-news.timer").exists()
    assert (unit_dir / "wfctl-manual-job.service").exists()
    assert not (unit_dir / "wfctl-manual-job.timer").exists()
    # no systemd calls in --no-systemctl mode
    assert runner.calls == []
    assert report.systemctl_skipped is True


def test_apply_enables_scheduled_disables_when_off(workflows_dir: Path, tmp_path: Path):
    # Flip manual-job to scheduled+disabled and daily-news stays enabled.
    (workflows_dir / "manual-job.yaml").write_text(
        "id: manual-job\ndescription: d\nenabled: false\n"
        "exec: {mode: command, command: [/bin/true], working_directory: /tmp}\n"
        "schedule: {on_calendar: hourly}\n"
    )
    unit_dir = tmp_path / "units"
    unit_dir.mkdir()
    workflows = load_workflows(workflows_dir)
    plan = build_plan(workflows, unit_dir)
    runner = RecordingRunner()

    apply_plan(plan, workflows, runner=runner, use_systemctl=True)

    assert ("daemon-reload", "") in runner.calls
    assert ("enable", "wfctl-daily-news.timer") in runner.calls
    assert ("disable", "wfctl-manual-job.timer") in runner.calls


def test_apply_does_not_touch_manual_timers(workflows_dir: Path, tmp_path: Path):
    unit_dir = tmp_path / "units"
    unit_dir.mkdir()
    workflows = load_workflows(workflows_dir)
    plan = build_plan(workflows, unit_dir)
    runner = RecordingRunner()

    apply_plan(plan, workflows, runner=runner, use_systemctl=True)

    # manual-job has no schedule -> no enable/disable for it
    units_touched = {u for _, u in runner.calls}
    assert "wfctl-manual-job.timer" not in units_touched
    assert ("enable", "wfctl-daily-news.timer") in runner.calls


def test_apply_prune_deletes_orphan(workflows_dir: Path, tmp_path: Path):
    unit_dir = tmp_path / "units"
    unit_dir.mkdir()
    orphan = unit_dir / "wfctl-orphan.service"
    orphan.write_text("# Managed-By: wfctl\n[Unit]\n")
    workflows = load_workflows(workflows_dir)
    plan = build_plan(workflows, unit_dir, prune=True)
    runner = RecordingRunner()

    report = apply_plan(plan, workflows, runner=runner, use_systemctl=True)

    assert not orphan.exists()
    assert "wfctl-orphan.service" in report.deleted
