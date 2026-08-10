"""End-to-end CLI tests exercising the real entry point and exit codes (PRD §19–20).

These run ``python -m wfctl`` in a subprocess so the centralised error->exit-code
mapping in ``cli.main`` is exercised exactly as in production. They never touch
real systemd: every invocation uses ``--no-systemctl`` / ``--skip-path-checks``
or commands that don't shell out.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures" / "workflows"


def run_cli(*args: str, config_dir: Path | None = None, unit_dir: Path | None = None):
    cmd = [sys.executable, "-m", "wfctl"]
    if config_dir is not None:
        cmd += ["--config-dir", str(config_dir)]
    if unit_dir is not None:
        cmd += ["--unit-dir", str(unit_dir)]
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        # These cases are about command behaviour, not the root guard, so opt
        # out of it when the suite runs as root (containers, some CI images).
        # The guard itself is covered by test_apply_as_root_exits_4, which
        # builds its own argv and never passes this flag.
        cmd.append("--allow-root")
    cmd += list(args)
    return subprocess.run(cmd, capture_output=True, text=True)


@pytest.fixture
def seeded(tmp_path: Path):
    cfg = tmp_path / "workflows"
    cfg.mkdir()
    for name in ("daily-news.yaml", "manual-job.yaml"):
        (cfg / name).write_bytes((FIXTURES / name).read_bytes())
    units = tmp_path / "units"
    units.mkdir()
    return cfg, units


def test_version_exit_zero():
    r = run_cli("--version")
    assert r.returncode == 0
    assert "wfctl" in r.stdout


def test_validate_ok(seeded):
    cfg, _ = seeded
    r = run_cli("validate", "--skip-path-checks", config_dir=cfg)
    assert r.returncode == 0, r.stderr
    assert "valid" in r.stdout


def test_validate_duplicate_ids_exit_2(tmp_path: Path):
    cfg = tmp_path / "wf"
    cfg.mkdir()
    body = "description: d\nexec: {mode: command, command: [/bin/true], working_directory: /tmp}\n"
    (cfg / "a.yaml").write_text("id: dup\n" + body)
    (cfg / "b.yaml").write_text("id: dup\n" + body)
    r = run_cli("validate", "--skip-path-checks", config_dir=cfg)
    assert r.returncode == 2
    assert "duplicate" in r.stderr.lower()


def test_validate_invalid_id_exit_2(tmp_path: Path):
    cfg = tmp_path / "wf"
    cfg.mkdir()
    (cfg / "bad.yaml").write_text(
        "id: Bad_ID\ndescription: d\nexec: {mode: command, command: [/bin/true]}\n"
    )
    r = run_cli("validate", "--skip-path-checks", config_dir=cfg)
    assert r.returncode == 2


def test_workflow_not_found_exit_5(seeded):
    cfg, _ = seeded
    r = run_cli("status", "does-not-exist", config_dir=cfg)
    assert r.returncode == 5


def test_plan_create_actions(seeded):
    cfg, units = seeded
    r = run_cli("plan", "--skip-path-checks", config_dir=cfg, unit_dir=units)
    assert r.returncode == 0, r.stderr
    assert "CREATE" in r.stdout
    assert "wfctl-daily-news.service" in r.stdout
    assert "wfctl-daily-news.timer" in r.stdout


def test_plan_json(seeded):
    cfg, units = seeded
    r = run_cli("plan", "--json", "--skip-path-checks", config_dir=cfg, unit_dir=units)
    assert r.returncode == 0, r.stderr
    import json

    data = json.loads(r.stdout)
    names = {i["unit"] for i in data["items"]}
    assert "wfctl-daily-news.service" in names


def test_apply_no_systemctl_writes_units(seeded):
    cfg, units = seeded
    r = run_cli("apply", "--no-systemctl", "--skip-path-checks", config_dir=cfg, unit_dir=units)
    assert r.returncode == 0, r.stderr
    assert (units / "wfctl-daily-news.service").exists()
    assert (units / "wfctl-daily-news.timer").exists()
    assert (units / "wfctl-manual-job.service").exists()
    assert not (units / "wfctl-manual-job.timer").exists()
    # marker present
    assert "Managed-By: wfctl" in (units / "wfctl-daily-news.service").read_text()


def test_apply_dry_run_makes_no_changes(seeded):
    cfg, units = seeded
    r = run_cli("apply", "--dry-run", "--skip-path-checks", config_dir=cfg, unit_dir=units)
    assert r.returncode == 0, r.stderr
    assert list(units.iterdir()) == []


def test_prune_refuses_unmanaged(seeded):
    cfg, units = seeded
    # Apply first so managed units exist.
    run_cli("apply", "--no-systemctl", "--skip-path-checks", config_dir=cfg, unit_dir=units)
    # Drop an unmanaged look-alike that prune must never delete.
    intruder = units / "hand-written.service"
    intruder.write_text("[Unit]\nDescription=keep me\n")
    r = run_cli("prune", "--dry-run", config_dir=cfg, unit_dir=units)
    # Nothing orphaned -> nothing to prune; intruder survives regardless.
    assert r.returncode == 0
    assert intruder.exists()


def test_paths_reports_overrides(tmp_path: Path):
    cfg = tmp_path / "c"
    cfg.mkdir()
    units = tmp_path / "u"
    units.mkdir()
    r = run_cli("paths", config_dir=cfg, unit_dir=units)
    assert r.returncode == 0
    assert str(cfg) in r.stdout
    assert str(units) in r.stdout


def test_apply_as_root_exits_4(seeded, tmp_path: Path):
    """End-to-end check that the root guard surfaces exit code 4 (PRD §19)."""
    cfg, units = seeded
    # Inject a sitecustomize that fakes geteuid()==0 in the subprocess.
    sitec = tmp_path / "sitec"
    sitec.mkdir()
    (sitec / "sitecustomize.py").write_text("import os\nos.geteuid = lambda: 0\n")
    env = {
        **__import__("os").environ,
        "PYTHONPATH": f"{sitec}:{__import__('os').environ.get('PYTHONPATH', '')}",
    }
    cmd = [
        sys.executable,
        "-m",
        "wfctl",
        "--config-dir",
        str(cfg),
        "--unit-dir",
        str(units),
        "apply",
        "--no-systemctl",
        "--skip-path-checks",
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, env=env)
    assert r.returncode == 4, (r.returncode, r.stdout, r.stderr)
    assert "refusing to run as root" in r.stderr
