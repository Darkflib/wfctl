"""Regression tests for the security findings raised in Codex review.

Covers:
* P1 — newline injection via ``exec.python`` (and any other rendered field).
* P2 — central systemd directive escaping / forbidden characters in paths.
* P2 — ExecStart prefix sigils (``@-:+!``) in the leading argv token.
* P2 — ``prune`` disables a timer before its unit file is removed.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError as PydanticValidationError

from wfctl.apply import apply_plan
from wfctl.loader import LoadedWorkflow, load_workflows
from wfctl.models import EnvironmentConfig, ExecConfig, SecurityConfig, WorkflowDefinition
from wfctl.plan import Action, PlanItem, build_plan
from wfctl.plan import Plan as PlanT
from wfctl.render import quote_unit_value, render_service
from wfctl.systemd import SystemdRunner


# --------------------------------------------------------------------------
# P1: exec.python injection
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "value",
    [
        "3.12\nEnvironment=PWNED=1",  # newline -> directive injection
        "3.12\rEnvironment=BAD=1",  # carriage return
        "3.12 extra",  # whitespace breaks argv parsing
        '3.12"',  # stray quote
        "3.12\\bad",  # backslash
    ],
)
def test_exec_python_rejects_injection_payloads(value):
    with pytest.raises(PydanticValidationError):
        ExecConfig.model_validate({"mode": "uv-run", "command": ["python"], "python": value})


def test_exec_python_normal_value_accepted():
    cfg = ExecConfig.model_validate({"mode": "uv-run", "command": ["python"], "python": "3.12"})
    assert cfg.python == "3.12"


# --------------------------------------------------------------------------
# P2: central path validation — forbid quotes/backslashes/control chars
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "path",
    ['/etc/"weird"', "/etc/back\\slash", "/etc/with\x00null", "/etc/with\tcontrol"],
)
def test_working_directory_rejects_unsafe_chars(path):
    with pytest.raises(PydanticValidationError):
        ExecConfig.model_validate(
            {"mode": "command", "command": ["/bin/true"], "working_directory": path}
        )


def test_environment_file_rejects_backslash():
    with pytest.raises(PydanticValidationError):
        EnvironmentConfig.model_validate({"files": ["/etc/back\\slash.env"]})


def test_read_write_path_rejects_quote():
    with pytest.raises(PydanticValidationError):
        SecurityConfig.model_validate({"profile": "basic", "read_write_paths": ['/etc/"x"']})


# --------------------------------------------------------------------------
# P2: render-time quoting of legitimate paths with spaces
# --------------------------------------------------------------------------
def test_quote_unit_value_passes_through_simple_paths():
    assert quote_unit_value("/usr/bin/uv") == "/usr/bin/uv"


def test_quote_unit_value_quotes_whitespace():
    assert quote_unit_value("/home/me/has space/x") == '"/home/me/has space/x"'


def test_render_service_quotes_working_directory_with_space():
    d = WorkflowDefinition.model_validate(
        {
            "id": "x",
            "description": "d",
            "exec": {
                "mode": "command",
                "command": ["/bin/true"],
                "working_directory": "/home/me/has space",
            },
        }
    )
    wf = LoadedWorkflow(definition=d, source_path=Path("/x.yaml"), source_sha256="0")
    rendered = render_service(wf)
    assert 'WorkingDirectory="/home/me/has space"' in rendered


def test_render_service_quotes_read_write_paths_with_space():
    d = WorkflowDefinition.model_validate(
        {
            "id": "x",
            "description": "d",
            "exec": {"mode": "command", "command": ["/bin/true"]},
            "security": {"profile": "basic", "read_write_paths": ["/var/with space"]},
        }
    )
    wf = LoadedWorkflow(definition=d, source_path=Path("/x.yaml"), source_sha256="0")
    assert 'ReadWritePaths="/var/with space"' in render_service(wf)


# --------------------------------------------------------------------------
# P2: ExecStart prefix sigils in the leading argv token
# --------------------------------------------------------------------------
@pytest.mark.parametrize("prefix", ["@", "-", ":", "+", "!"])
def test_command_mode_rejects_exec_prefix_sigil(prefix):
    with pytest.raises(PydanticValidationError):
        ExecConfig.model_validate({"mode": "command", "command": [f"{prefix}/bin/true"]})


def test_uv_binary_with_prefix_sigil_rejected():
    with pytest.raises(PydanticValidationError):
        ExecConfig.model_validate(
            {"mode": "uv-run", "command": ["python"], "uv_binary": "+/usr/bin/uv"}
        )


def test_normal_command_accepted():
    ExecConfig.model_validate({"mode": "command", "command": ["/bin/true"]})


# --------------------------------------------------------------------------
# P2: prune disables an orphan timer before deletion
# --------------------------------------------------------------------------
class _RecordingRunner(SystemdRunner):
    """Records the sequence of calls (in order) for assertion."""

    def __init__(self) -> None:
        super().__init__(dry_run=False)
        self.calls: list[tuple[str, str]] = []

    def daemon_reload(self) -> None:
        self.calls.append(("daemon-reload", ""))

    def enable_now(self, unit: str) -> None:
        self.calls.append(("enable", unit))

    def disable_now(self, unit: str) -> None:
        self.calls.append(("disable", unit))


def test_prune_disables_orphan_timer_before_deleting(tmp_path: Path):
    unit_dir = tmp_path / "units"
    unit_dir.mkdir()
    orphan = unit_dir / "wfctl-stale.timer"
    orphan.write_text("# Managed-By: wfctl\n[Unit]\n[Timer]\nOnCalendar=hourly\n")
    runner = _RecordingRunner()
    delete_only = PlanT(
        items=[
            PlanItem(
                action=Action.DELETE,
                unit_name="wfctl-stale.timer",
                content=None,
                path=orphan,
                workflow_id=None,
            )
        ]
    )

    apply_plan(delete_only, workflows=[], runner=runner, use_systemctl=True)

    # disable-now must come *before* the file disappears; since deletion is
    # synchronous before daemon-reload, just check the disable call is recorded
    # and the file is gone.
    assert ("disable", "wfctl-stale.timer") in runner.calls
    assert not orphan.exists()
    # daemon-reload happens after file removal
    disable_idx = runner.calls.index(("disable", "wfctl-stale.timer"))
    reload_idx = runner.calls.index(("daemon-reload", ""))
    assert disable_idx < reload_idx


def test_prune_no_systemctl_skips_disable(tmp_path: Path):
    unit_dir = tmp_path / "units"
    unit_dir.mkdir()
    orphan = unit_dir / "wfctl-stale.timer"
    orphan.write_text("# Managed-By: wfctl\n[Unit]\n[Timer]\n")
    runner = _RecordingRunner()
    delete_only = PlanT(
        items=[
            PlanItem(
                action=Action.DELETE,
                unit_name="wfctl-stale.timer",
                content=None,
                path=orphan,
                workflow_id=None,
            )
        ]
    )

    apply_plan(delete_only, workflows=[], runner=runner, use_systemctl=False)

    assert runner.calls == []
    assert not orphan.exists()


def test_prune_service_orphan_does_not_disable(tmp_path: Path):
    """Services don't need disable-now; only timers do."""
    unit_dir = tmp_path / "units"
    unit_dir.mkdir()
    orphan = unit_dir / "wfctl-stale.service"
    orphan.write_text("# Managed-By: wfctl\n[Unit]\n")
    runner = _RecordingRunner()
    delete_only = PlanT(
        items=[
            PlanItem(
                action=Action.DELETE,
                unit_name="wfctl-stale.service",
                content=None,
                path=orphan,
                workflow_id=None,
            )
        ]
    )

    apply_plan(delete_only, workflows=[], runner=runner, use_systemctl=True)

    assert not any(call[0] == "disable" for call in runner.calls)
    assert ("daemon-reload", "") in runner.calls


# --------------------------------------------------------------------------
# Bandit B101: render_timer raises (not asserts) when called without schedule
# --------------------------------------------------------------------------
def test_render_timer_raises_without_schedule():
    from wfctl.render import render_timer

    d = WorkflowDefinition.model_validate(
        {
            "id": "x",
            "description": "d",
            "exec": {"mode": "command", "command": ["/bin/true"]},
        }
    )
    wf = LoadedWorkflow(definition=d, source_path=Path("/x.yaml"), source_sha256="0")
    with pytest.raises(ValueError, match="no schedule"):
        render_timer(wf)


# --------------------------------------------------------------------------
# Helper used by load-and-plan smoke check
# --------------------------------------------------------------------------
def test_plan_still_works_with_quoted_paths(tmp_path: Path):
    cfg = tmp_path / "wf"
    cfg.mkdir()
    (cfg / "ok.yaml").write_text(
        "id: ok\ndescription: d\n"
        "exec: {mode: command, command: [/bin/true], working_directory: '/has space'}\n"
    )
    units = tmp_path / "u"
    units.mkdir()
    workflows = load_workflows(cfg)
    plan = build_plan(workflows, units)
    assert any(i.action is Action.CREATE for i in plan.items)
