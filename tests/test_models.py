"""Schema validation tests for the Pydantic models (PRD §8–9, §14)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError as PydanticValidationError

from wfctl.models import (
    DEFAULT_TIMEOUT_SEC,
    EnvironmentConfig,
    ExecConfig,
    RestartPolicy,
    SecurityProfile,
    WorkflowDefinition,
)


def _minimal(**overrides) -> dict:
    base = {
        "id": "ok-id",
        "description": "desc",
        "exec": {"mode": "command", "command": ["/bin/true"]},
    }
    base.update(overrides)
    return base


def test_minimal_workflow_valid():
    wf = WorkflowDefinition.model_validate(_minimal())
    assert wf.id == "ok-id"
    assert wf.enabled is True
    assert wf.timeout_sec == DEFAULT_TIMEOUT_SEC
    assert wf.has_timer is False
    assert wf.service_unit_name == "wfctl-ok-id.service"
    assert wf.timer_unit_name == "wfctl-ok-id.timer"


@pytest.mark.parametrize(
    "bad_id",
    ["-leading-hyphen", "Upper", "has_underscore", "white space", "", "a" * 81],
)
def test_invalid_ids_rejected(bad_id):
    with pytest.raises(PydanticValidationError):
        WorkflowDefinition.model_validate(_minimal(id=bad_id))


@pytest.mark.parametrize("good_id", ["a", "0", "daily-news", "x" * 80, "job-123"])
def test_valid_ids_accepted(good_id):
    assert WorkflowDefinition.model_validate(_minimal(id=good_id)).id == good_id


def test_explicit_null_timeout_omits(monkeypatch):
    wf = WorkflowDefinition.model_validate(_minimal(timeout_sec=None))
    assert wf.timeout_sec is None


def test_unknown_field_rejected():
    with pytest.raises(PydanticValidationError):
        WorkflowDefinition.model_validate(_minimal(bogus=True))


def test_command_mode_requires_command():
    with pytest.raises(PydanticValidationError):
        ExecConfig.model_validate({"mode": "command"})


def test_uv_run_requires_command():
    with pytest.raises(PydanticValidationError):
        ExecConfig.model_validate({"mode": "uv-run", "working_directory": "/x"})


def test_uv_script_requires_script_and_forbids_command():
    ExecConfig.model_validate({"mode": "uv-script", "script": "/a/b.py"})
    with pytest.raises(PydanticValidationError):
        ExecConfig.model_validate({"mode": "uv-script"})
    with pytest.raises(PydanticValidationError):
        ExecConfig.model_validate({"mode": "uv-script", "script": "/a.py", "command": ["x"]})


def test_relative_working_directory_rejected():
    with pytest.raises(PydanticValidationError):
        ExecConfig.model_validate(
            {"mode": "command", "command": ["x"], "working_directory": "relative/dir"}
        )


def test_frozen_defaults_true():
    cfg = ExecConfig.model_validate({"mode": "uv-run", "command": ["python"]})
    assert cfg.frozen is True
    assert cfg.no_sync is False


def test_env_name_validation():
    with pytest.raises(PydanticValidationError):
        EnvironmentConfig.model_validate({"variables": {"bad name": "1"}})
    with pytest.raises(PydanticValidationError):
        EnvironmentConfig.model_validate({"variables": {"OK": "line\nbreak"}})


def test_env_file_must_be_absolute():
    with pytest.raises(PydanticValidationError):
        EnvironmentConfig.model_validate({"files": ["relative.env"]})


def test_secret_like_names_detected():
    cfg = EnvironmentConfig.model_validate(
        {"variables": {"MY_API_KEY": "x", "PLAIN": "y", "DB_PASSWORD": "z"}}
    )
    hits = set(cfg.secret_like_names())
    assert hits == {"MY_API_KEY", "DB_PASSWORD"}


def test_newline_in_description_rejected():
    with pytest.raises(PydanticValidationError):
        WorkflowDefinition.model_validate(_minimal(description="two\nlines"))


@pytest.mark.parametrize("mem", ["1G", "512M", "1024", "2.5G", "100KB"])
def test_memory_max_plausible(mem):
    from wfctl.models import ResourcesConfig

    ResourcesConfig.model_validate({"memory_max": mem})


@pytest.mark.parametrize("mem", ["lots", "1Gigabyte", "G"])
def test_memory_max_implausible(mem):
    from wfctl.models import ResourcesConfig

    with pytest.raises(PydanticValidationError):
        ResourcesConfig.model_validate({"memory_max": mem})


def test_cpu_quota_requires_percent():
    from wfctl.models import ResourcesConfig

    ResourcesConfig.model_validate({"cpu_quota": "50%"})
    with pytest.raises(PydanticValidationError):
        ResourcesConfig.model_validate({"cpu_quota": "50"})


def test_restart_policy_default_no():
    from wfctl.models import RestartConfig

    assert RestartConfig().policy is RestartPolicy.NO


def test_security_profile_default_none():
    from wfctl.models import SecurityConfig

    assert SecurityConfig().profile is SecurityProfile.NONE
