"""CLI exit-code propagation tests (Codex P3 findings).

* ``status`` propagates the worst non-zero ``systemctl status`` exit.
* ``logs`` propagates ``journalctl``'s exit.
* Diagnostic commands (``doctor``) are *not* pre-empted by the root guard;
  only mutating commands (``apply``/``prune``/``run``) refuse to run as root.
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from wfctl import cli as cli_mod
from wfctl.systemd import CommandResult, SystemdRunner

runner = CliRunner()

FIXTURES = Path(__file__).parent / "fixtures" / "workflows"


def _seed(tmp_path: Path) -> Path:
    cfg = tmp_path / "wf"
    cfg.mkdir()
    (cfg / "manual-job.yaml").write_bytes((FIXTURES / "manual-job.yaml").read_bytes())
    return cfg


class _FakeRunner(SystemdRunner):
    """Stub runner: canned status / journal responses."""

    def __init__(
        self,
        *,
        status_rc: int = 0,
        journal_rc: int = 0,
        binaries: set[str] | None = None,
    ) -> None:
        super().__init__(dry_run=False)
        self._status_rc = status_rc
        self._journal_rc = journal_rc
        self._binaries = binaries or set()

    def has_binary(self, name: str) -> bool:  # type: ignore[override]
        return name in self._binaries

    def status(self, unit: str) -> CommandResult:
        return CommandResult(args=[unit], returncode=self._status_rc, stdout="(status)", stderr="")

    def journal(self, args, *, capture=True) -> CommandResult:
        return CommandResult(args=list(args), returncode=self._journal_rc, stdout="", stderr="")


def test_status_zero_when_active(tmp_path: Path, monkeypatch):
    cfg = _seed(tmp_path)
    monkeypatch.setattr(
        cli_mod.AppContext, "runner", lambda self, dry_run=False: _FakeRunner(status_rc=0)
    )
    r = runner.invoke(cli_mod.app, ["--config-dir", str(cfg), "status", "manual-job"])
    assert r.exit_code == 0


def test_status_propagates_inactive_exit(tmp_path: Path, monkeypatch):
    """systemctl status returns 3 for inactive units — surface that to the shell."""
    cfg = _seed(tmp_path)
    monkeypatch.setattr(
        cli_mod.AppContext, "runner", lambda self, dry_run=False: _FakeRunner(status_rc=3)
    )
    r = runner.invoke(cli_mod.app, ["--config-dir", str(cfg), "status", "manual-job"])
    assert r.exit_code == 3


def test_logs_propagates_journalctl_exit(tmp_path: Path, monkeypatch):
    cfg = _seed(tmp_path)
    monkeypatch.setattr(
        cli_mod.AppContext, "runner", lambda self, dry_run=False: _FakeRunner(journal_rc=4)
    )
    r = runner.invoke(cli_mod.app, ["--config-dir", str(cfg), "logs", "manual-job"])
    assert r.exit_code == 4


def test_logs_zero_on_success(tmp_path: Path, monkeypatch):
    cfg = _seed(tmp_path)
    monkeypatch.setattr(
        cli_mod.AppContext, "runner", lambda self, dry_run=False: _FakeRunner(journal_rc=0)
    )
    r = runner.invoke(cli_mod.app, ["--config-dir", str(cfg), "logs", "manual-job"])
    assert r.exit_code == 0


# --------------------------------------------------------------------------
# Doctor as root: not pre-empted by global root guard
# --------------------------------------------------------------------------
def test_doctor_runs_as_root_without_allow_root(tmp_path: Path, monkeypatch):
    """Diagnostic commands should *not* be blocked by the root guard; the
    doctor's own check is what reports running-as-root."""
    cfg = _seed(tmp_path)
    monkeypatch.setattr("os.geteuid", lambda: 0)
    r = runner.invoke(
        cli_mod.app, ["--config-dir", str(cfg), "--unit-dir", str(tmp_path), "doctor"]
    )
    # Doctor should run and report; even if overall is FAIL (exit 1) it must
    # *not* be the UnsafeOperationError 'refusing to run as root' (exit 4).
    assert r.exit_code in (0, 1), r.output
    assert "refusing to run as root" not in r.output


def test_apply_refuses_root_without_allow_root(tmp_path: Path, monkeypatch):
    """Mutating commands *do* still enforce the root guard (regression check).

    Verifies via the surfaced exception type, because CliRunner doesn't run
    through ``main()``'s WfctlError→exit_code mapping. The end-to-end exit
    code is covered by the subprocess-based test in ``tests/test_cli.py``.
    """
    from wfctl.errors import UnsafeOperationError

    cfg = _seed(tmp_path)
    units = tmp_path / "u"
    units.mkdir()
    monkeypatch.setattr("os.geteuid", lambda: 0)
    r = runner.invoke(
        cli_mod.app,
        [
            "--config-dir",
            str(cfg),
            "--unit-dir",
            str(units),
            "apply",
            "--no-systemctl",
            "--skip-path-checks",
        ],
    )
    assert isinstance(r.exception, UnsafeOperationError), r.exception
    assert r.exception.exit_code == 4
    assert "refusing to run as root" in str(r.exception)


def test_apply_dry_run_allowed_as_root(tmp_path: Path, monkeypatch):
    """Dry-run apply is purely diagnostic and shouldn't trip the root guard."""
    cfg = _seed(tmp_path)
    units = tmp_path / "u"
    units.mkdir()
    monkeypatch.setattr("os.geteuid", lambda: 0)
    r = runner.invoke(
        cli_mod.app,
        [
            "--config-dir",
            str(cfg),
            "--unit-dir",
            str(units),
            "apply",
            "--dry-run",
            "--skip-path-checks",
        ],
    )
    assert r.exit_code == 0, r.output
