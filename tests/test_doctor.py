"""Tests for the doctor preflight checks.

Every check that touches the environment goes through a stub
:class:`SystemdRunner` so tests are hermetic and OS-independent.
"""

from __future__ import annotations

from pathlib import Path

from wfctl.doctor import (
    CheckStatus,
    check_config_dir,
    check_journalctl,
    check_linger,
    check_python_version,
    check_root,
    check_systemctl,
    check_systemd_analyze,
    check_unit_dir,
    check_user_manager,
    check_uv,
    overall_status,
    run_checks,
)
from wfctl.paths import Paths
from wfctl.systemd import CommandResult, SystemdRunner


class StubRunner(SystemdRunner):
    """Runner that fakes ``has_binary`` and arbitrary command outputs."""

    def __init__(
        self,
        *,
        binaries: set[str] | None = None,
        responses: dict[tuple[str, ...], CommandResult] | None = None,
    ) -> None:
        super().__init__(dry_run=False)
        self._binaries = binaries or set()
        self._responses = responses or {}

    # NB: SystemdRunner.has_binary is a @staticmethod; override as a regular
    # method so each instance can answer for its own canned binary set.
    def has_binary(self, name: str) -> bool:  # type: ignore[override]
        return name in self._binaries

    def run(self, args: list[str], *, check: bool = False, capture: bool = True) -> CommandResult:
        key = tuple(args)
        if key in self._responses:
            return self._responses[key]
        return CommandResult(args=list(args), returncode=0, stdout="", stderr="")

    def systemctl(self, *args: str, check: bool = True, capture: bool = True) -> CommandResult:
        key = ("systemctl", "--user", *args)
        if key in self._responses:
            return self._responses[key]
        return CommandResult(args=list(key), returncode=0, stdout="running\n", stderr="")


def _result(rc: int, out: str = "", err: str = "") -> CommandResult:
    return CommandResult(args=[], returncode=rc, stdout=out, stderr=err)


# --- python / root ----------------------------------------------------------
def test_check_python_version_ok():
    # We run under py3.12+ by project policy; just confirm OK and shape.
    r = check_python_version()
    assert r.status is CheckStatus.OK
    assert r.name == "python"


def test_check_root_ok_for_non_root_user(monkeypatch):
    # Pin euid rather than trusting the ambient user: the suite has to give the
    # same answer on a CI runner and inside a root container.
    monkeypatch.setattr("os.geteuid", lambda: 1000)
    assert check_root().status is CheckStatus.OK


def test_check_root_fails_for_root_user(monkeypatch):
    monkeypatch.setattr("os.geteuid", lambda: 0)
    r = check_root()
    assert r.status is CheckStatus.FAIL
    assert r.hint  # has remediation


# --- systemctl --------------------------------------------------------------
def test_check_systemctl_present():
    r = check_systemctl(StubRunner(binaries={"systemctl"}))
    assert r.status is CheckStatus.OK


def test_check_systemctl_missing():
    r = check_systemctl(StubRunner())
    assert r.status is CheckStatus.FAIL
    assert r.hint  # has remediation


# --- journalctl / systemd-analyze ------------------------------------------
def test_check_journalctl_missing_is_warn_not_fail():
    r = check_journalctl(StubRunner())
    assert r.status is CheckStatus.WARN


def test_check_systemd_analyze_present():
    r = check_systemd_analyze(StubRunner(binaries={"systemd-analyze"}))
    assert r.status is CheckStatus.OK


# --- uv ---------------------------------------------------------------------
def test_check_uv_uses_runner(monkeypatch):
    monkeypatch.setattr("wfctl.doctor.shutil.which", lambda name: "/fake/uv")
    runner = StubRunner(
        responses={("uv", "--version"): _result(0, "uv 0.4.0")},
    )
    r = check_uv(runner)
    assert r.status is CheckStatus.OK
    assert "/fake/uv" in r.detail and "uv 0.4.0" in r.detail


def test_check_uv_missing(monkeypatch):
    monkeypatch.setattr("wfctl.doctor.shutil.which", lambda name: None)
    r = check_uv(StubRunner())
    assert r.status is CheckStatus.WARN


# --- user manager -----------------------------------------------------------
def test_user_manager_running():
    runner = StubRunner(
        binaries={"systemctl"},
        responses={("systemctl", "--user", "is-system-running"): _result(0, "running\n")},
    )
    r = check_user_manager(runner)
    assert r.status is CheckStatus.OK
    assert r.detail == "running"


def test_user_manager_degraded_is_warn():
    runner = StubRunner(
        binaries={"systemctl"},
        responses={("systemctl", "--user", "is-system-running"): _result(1, "degraded\n")},
    )
    r = check_user_manager(runner)
    assert r.status is CheckStatus.WARN


def test_user_manager_missing_systemctl_fails():
    r = check_user_manager(StubRunner())
    assert r.status is CheckStatus.FAIL


# --- linger ----------------------------------------------------------------
def test_linger_enabled(monkeypatch):
    monkeypatch.setenv("USER", "alice")
    runner = StubRunner(
        binaries={"loginctl"},
        responses={
            ("loginctl", "show-user", "alice", "--property=Linger", "--value"): _result(0, "yes\n"),
        },
    )
    r = check_linger(runner)
    assert r.status is CheckStatus.OK
    assert "alice" in r.detail


def test_linger_disabled_includes_remediation(monkeypatch):
    monkeypatch.setenv("USER", "alice")
    runner = StubRunner(
        binaries={"loginctl"},
        responses={
            ("loginctl", "show-user", "alice", "--property=Linger", "--value"): _result(0, "no\n"),
        },
    )
    r = check_linger(runner)
    assert r.status is CheckStatus.WARN
    assert "enable-linger" in (r.hint or "")


def test_linger_no_loginctl():
    r = check_linger(StubRunner())
    assert r.status is CheckStatus.WARN


# --- config-dir / unit-dir --------------------------------------------------
def test_config_dir_counts_workflows(tmp_path: Path):
    cfg = tmp_path / "wf"
    cfg.mkdir()
    (cfg / "a.yaml").write_text("x")
    (cfg / "b.yml").write_text("x")
    (cfg / "readme.md").write_text("ignored")
    paths = Paths(config_dir=cfg, unit_dir=tmp_path / "u", state_dir=tmp_path, share_dir=tmp_path)
    r = check_config_dir(paths)
    assert r.status is CheckStatus.OK
    assert "2 workflow file(s)" in r.detail


def test_config_dir_missing_is_warn(tmp_path: Path):
    paths = Paths(
        config_dir=tmp_path / "nope",
        unit_dir=tmp_path,
        state_dir=tmp_path,
        share_dir=tmp_path,
    )
    assert check_config_dir(paths).status is CheckStatus.WARN


def test_unit_dir_writable(tmp_path: Path):
    paths = Paths(config_dir=tmp_path, unit_dir=tmp_path, state_dir=tmp_path, share_dir=tmp_path)
    assert check_unit_dir(paths).status is CheckStatus.OK


def test_unit_dir_missing_is_warn(tmp_path: Path):
    paths = Paths(
        config_dir=tmp_path,
        unit_dir=tmp_path / "nope",
        state_dir=tmp_path,
        share_dir=tmp_path,
    )
    assert check_unit_dir(paths).status is CheckStatus.WARN


# --- orchestrator -----------------------------------------------------------
def test_run_checks_returns_full_set(tmp_path: Path):
    paths = Paths(config_dir=tmp_path, unit_dir=tmp_path, state_dir=tmp_path, share_dir=tmp_path)
    results = run_checks(paths, StubRunner())
    names = [r.name for r in results]
    expected = {
        "python",
        "running as root",
        "systemctl",
        "user manager",
        "linger",
        "journalctl",
        "systemd-analyze",
        "uv",
        "config-dir",
        "unit-dir",
    }
    assert set(names) == expected


def test_overall_status_priority():
    from wfctl.doctor import CheckResult

    def res(s: CheckStatus) -> CheckResult:
        return CheckResult("x", s, "")

    assert overall_status([res(CheckStatus.OK), res(CheckStatus.OK)]) is CheckStatus.OK
    assert overall_status([res(CheckStatus.OK), res(CheckStatus.WARN)]) is CheckStatus.WARN
    assert overall_status([res(CheckStatus.WARN), res(CheckStatus.FAIL)]) is CheckStatus.FAIL


# --- CLI exit code (regression for standalone_mode=False return-value bug) --
def test_cli_doctor_exits_nonzero_when_failures(tmp_path: Path):
    """Re-tests the fix for typer.Exit() being swallowed by standalone_mode=False."""
    import subprocess
    import sys

    cfg = tmp_path / "wf"
    cfg.mkdir()
    # Running on macOS in dev means systemctl is missing -> FAIL -> exit 1.
    # On Linux CI systemctl is present; but root-check passes either way and
    # the path missing on a fresh tmp results in WARNs only, not FAILs.
    # To make this assertion stable across hosts, force a FAIL: pass a unit-dir
    # path that exists but is not writable.
    ro = tmp_path / "ro"
    ro.mkdir()
    ro.chmod(0o500)  # read+exec, no write
    try:
        r = subprocess.run(
            [
                sys.executable,
                "-m",
                "wfctl",
                "--config-dir",
                str(cfg),
                "--unit-dir",
                str(ro),
                "doctor",
            ],
            capture_output=True,
            text=True,
        )
        # We expect at least one FAIL (the unit-dir not-writable case).
        assert "FAIL" in r.stdout
        assert r.returncode == 1
    finally:
        ro.chmod(0o700)  # let pytest clean up


def test_overall_status_empty_is_ok():
    assert overall_status([]) is CheckStatus.OK


def test_cli_doctor_strict_promotes_warn_to_failure(tmp_path: Path, monkeypatch):
    """`--strict` turns a WARN-only result into a non-zero exit.

    Host-independent: we monkeypatch ``run_checks`` to return a controlled
    WARN-only set and invoke the typer command directly (no subprocess), so
    the outcome doesn't depend on the actual systemd state of the test host.
    """
    from typer.testing import CliRunner

    from wfctl import cli as cli_mod
    from wfctl.doctor import CheckResult, CheckStatus

    warn_only = [CheckResult("fake", CheckStatus.WARN, "synthetic")]
    monkeypatch.setattr(cli_mod, "run_checks", lambda paths, runner: warn_only)

    runner = CliRunner()
    cfg = tmp_path / "wf"
    cfg.mkdir()
    base = ["--config-dir", str(cfg), "--unit-dir", str(tmp_path), "doctor"]

    # Without --strict, WARN-only -> exit 0.
    r1 = runner.invoke(cli_mod.app, base)
    assert r1.exit_code == 0, r1.output

    # With --strict, WARN-only -> exit 1 (CliRunner converts typer.Exit to exit_code).
    r2 = runner.invoke(cli_mod.app, [*base, "--strict"])
    assert r2.exit_code == 1, r2.output
