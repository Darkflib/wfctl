"""Renderer tests, including golden-file comparison (PRD §10–11, §15)."""

from __future__ import annotations

from pathlib import Path

from wfctl.loader import load_workflow_file
from wfctl.models import ExecConfig
from wfctl.render import (
    MANAGED_BY_MARKER,
    quote_exec_arg,
    render_exec_start,
    render_service,
    render_timer,
)


def _expand_golden(golden_path: Path, source_path: Path, sha: str) -> str:
    text = golden_path.read_text()
    return text.replace("__SOURCE_PATH__", str(source_path)).replace("__SHA256__", sha)


def test_service_matches_golden(daily_news_path: Path, golden_dir: Path):
    wf = load_workflow_file(daily_news_path)
    expected = _expand_golden(
        golden_dir / "wfctl-daily-news.service", wf.source_path, wf.source_sha256
    )
    assert render_service(wf) == expected


def test_timer_matches_golden(daily_news_path: Path, golden_dir: Path):
    wf = load_workflow_file(daily_news_path)
    expected = _expand_golden(
        golden_dir / "wfctl-daily-news.timer", wf.source_path, wf.source_sha256
    )
    assert render_timer(wf) == expected


def test_managed_header_present(daily_news_path: Path):
    wf = load_workflow_file(daily_news_path)
    svc = render_service(wf)
    assert MANAGED_BY_MARKER in svc
    assert "Workflow-Id: daily-news" in svc
    assert f"Source-SHA256: {wf.source_sha256}" in svc


def test_manual_workflow_has_no_timer(fixtures_dir: Path):
    wf = load_workflow_file(fixtures_dir / "manual-job.yaml")
    assert wf.definition.has_timer is False
    svc = render_service(wf)
    assert "SyslogIdentifier=wfctl-manual-job" in svc
    # no schedule -> profile none -> no hardening directives
    assert "NoNewPrivileges" not in svc


# --- exec rendering ---------------------------------------------------------
def test_uv_run_default_frozen():
    cfg = ExecConfig.model_validate({"mode": "uv-run", "command": ["python", "-m", "m"]})
    assert render_exec_start(cfg) == "uv run --frozen -- python -m m"


def test_uv_run_all_flags():
    cfg = ExecConfig.model_validate(
        {
            "mode": "uv-run",
            "uv_binary": "/opt/uv",
            "python": "3.12",
            "frozen": True,
            "no_sync": True,
            "command": ["python", "app.py"],
        }
    )
    assert render_exec_start(cfg) == "/opt/uv run --python 3.12 --frozen --no-sync -- python app.py"


def test_uv_run_no_frozen():
    cfg = ExecConfig.model_validate({"mode": "uv-run", "frozen": False, "command": ["python"]})
    assert render_exec_start(cfg) == "uv run -- python"


def test_uv_script_render():
    cfg = ExecConfig.model_validate({"mode": "uv-script", "script": "/a/b.py"})
    assert render_exec_start(cfg) == "uv run --script /a/b.py"


def test_command_mode_no_shell_wrapper():
    cfg = ExecConfig.model_validate(
        {"mode": "command", "command": ["/usr/bin/bash", "-lc", "/x/y.sh"]}
    )
    out = render_exec_start(cfg)
    assert out == "/usr/bin/bash -lc /x/y.sh"
    assert "/bin/sh -c" not in out


def test_arg_quoting():
    assert quote_exec_arg("plain") == "plain"
    assert quote_exec_arg("has space") == '"has space"'
    assert quote_exec_arg('a"b') == '"a\\"b"'
    assert quote_exec_arg("") == '""'


def test_security_profiles_are_supersets(fixtures_dir: Path):
    # Build minimal workflows for each profile and check directive nesting.
    from wfctl.loader import LoadedWorkflow
    from wfctl.models import WorkflowDefinition

    def render_profile(profile: str) -> str:
        d = WorkflowDefinition.model_validate(
            {
                "id": "x",
                "description": "d",
                "exec": {"mode": "command", "command": ["/bin/true"]},
                "security": {"profile": profile},
            }
        )
        wf = LoadedWorkflow(definition=d, source_path=Path("/x.yaml"), source_sha256="0")
        return render_service(wf)

    basic = render_profile("basic")
    readonly = render_profile("readonly-home")
    strict = render_profile("strict")
    networkless = render_profile("networkless")

    assert "NoNewPrivileges=true" in basic and "ProtectHome" not in basic
    assert "ProtectHome=read-only" in readonly and "ProtectSystem" not in readonly
    assert "ProtectSystem=strict" in strict and "PrivateNetwork" not in strict
    assert "PrivateNetwork=true" in networkless


def test_env_value_with_space_quoted(fixtures_dir: Path):
    from wfctl.loader import LoadedWorkflow
    from wfctl.models import WorkflowDefinition

    d = WorkflowDefinition.model_validate(
        {
            "id": "x",
            "description": "d",
            "exec": {"mode": "command", "command": ["/bin/true"]},
            "environment": {"variables": {"MSG": "hello world"}},
        }
    )
    wf = LoadedWorkflow(definition=d, source_path=Path("/x.yaml"), source_sha256="0")
    assert 'Environment="MSG=hello world"' in render_service(wf)
