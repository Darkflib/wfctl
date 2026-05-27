"""Pydantic v2 models for workflow definitions (PRD §8–9).

These models enforce the *structural* schema and self-contained field rules
(formats, ranges, no newline injection). Cross-cutting and environment-aware
checks — path existence, calendar validity, duplicate ids — live in
``validate.py`` / ``loader.py`` where they have access to the filesystem and
the full set of workflows.
"""

from __future__ import annotations

import re
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")

# Env var names: POSIX-ish — start with a letter or underscore, then
# alphanumerics/underscores. Forbidding everything else also forbids newlines.
ENV_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Substrings that suggest a value is a secret and should live in an
# EnvironmentFile rather than inline YAML (PRD §16.4). Used for warnings only.
SECRET_NAME_HINTS = (
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "API_KEY",
    "PRIVATE_KEY",
    "CREDENTIAL",
)


def _no_newline(value: str, field: str) -> str:
    if "\n" in value or "\r" in value:
        raise ValueError(f"{field} must not contain newline characters")
    return value


def _is_absolute(path: str) -> bool:
    # Target is Linux; judge POSIX-absoluteness regardless of the host OS so
    # that validation behaves identically when developing on macOS/Windows.
    return PurePosixPath(path).is_absolute()


class ExecMode(StrEnum):
    UV_RUN = "uv-run"
    UV_SCRIPT = "uv-script"
    COMMAND = "command"


class RestartPolicy(StrEnum):
    NO = "no"
    ON_FAILURE = "on-failure"
    ALWAYS = "always"


class SecurityProfile(StrEnum):
    NONE = "none"
    BASIC = "basic"
    READONLY_HOME = "readonly-home"
    STRICT = "strict"
    NETWORKLESS = "networkless"


class _Strict(BaseModel):
    """Base config: reject unknown keys so typos fail loudly."""

    model_config = ConfigDict(extra="forbid")


class ExecConfig(_Strict):
    mode: ExecMode

    # Shared / uv-run / command
    working_directory: str | None = None
    command: list[str] | None = None

    # uv-run / uv-script options
    uv_binary: str | None = None
    python: str | None = None
    frozen: bool = True
    no_sync: bool = False

    # uv-script
    script: str | None = None

    @field_validator("working_directory", "uv_binary", "script")
    @classmethod
    def _abs_no_newline(cls, v: str | None, info) -> str | None:
        if v is None:
            return v
        _no_newline(v, info.field_name)
        if not _is_absolute(v):
            raise ValueError(f"{info.field_name} must be an absolute path: {v!r}")
        return v

    @field_validator("command")
    @classmethod
    def _command_clean(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        for arg in v:
            _no_newline(arg, "exec.command entry")
        return v

    @model_validator(mode="after")
    def _check_mode_requirements(self) -> ExecConfig:
        if self.mode in (ExecMode.UV_RUN, ExecMode.COMMAND):
            if not self.command:
                raise ValueError(f"exec.command must be non-empty for mode {self.mode.value!r}")
        if self.mode is ExecMode.UV_SCRIPT:
            if not self.script:
                raise ValueError("exec.script is required for mode 'uv-script'")
            if self.command:
                raise ValueError("exec.command is not allowed for mode 'uv-script'")
        return self


class ScheduleConfig(_Strict):
    on_calendar: str | list[str]
    persistent: bool | None = None
    randomized_delay_sec: int | None = Field(default=None, ge=0)

    @property
    def calendar_expressions(self) -> list[str]:
        if isinstance(self.on_calendar, str):
            return [self.on_calendar]
        return list(self.on_calendar)

    @field_validator("on_calendar")
    @classmethod
    def _calendar_clean(cls, v: str | list[str]) -> str | list[str]:
        items = [v] if isinstance(v, str) else v
        if not items:
            raise ValueError("schedule.on_calendar must not be empty")
        for expr in items:
            _no_newline(expr, "schedule.on_calendar")
            if not expr.strip():
                raise ValueError("schedule.on_calendar entries must not be blank")
        return v


class EnvironmentConfig(_Strict):
    variables: dict[str, str] = Field(default_factory=dict)
    files: list[str] = Field(default_factory=list)

    @field_validator("variables")
    @classmethod
    def _validate_variables(cls, v: dict[str, str]) -> dict[str, str]:
        for name, value in v.items():
            if not ENV_NAME_PATTERN.match(name):
                raise ValueError(f"invalid environment variable name: {name!r}")
            # Values are coerced to str by the annotation; guard newlines.
            _no_newline(str(value), f"environment value for {name}")
        return v

    @field_validator("files")
    @classmethod
    def _validate_files(cls, v: list[str]) -> list[str]:
        for path in v:
            _no_newline(path, "environment.files entry")
            if not _is_absolute(path):
                raise ValueError(f"environment file must be an absolute path: {path!r}")
        return v

    def secret_like_names(self) -> list[str]:
        """Inline variable names that look like secrets (for warnings)."""
        hits = []
        for name in self.variables:
            upper = name.upper()
            if any(hint in upper for hint in SECRET_NAME_HINTS):
                hits.append(name)
        return hits


class RestartConfig(_Strict):
    policy: RestartPolicy = RestartPolicy.NO
    restart_sec: int | None = Field(default=None, ge=0)
    start_limit_burst: int | None = Field(default=None, ge=0)
    start_limit_interval_sec: int | None = Field(default=None, ge=0)


class SecurityConfig(_Strict):
    profile: SecurityProfile = SecurityProfile.NONE
    read_write_paths: list[str] = Field(default_factory=list)

    @field_validator("read_write_paths")
    @classmethod
    def _validate_paths(cls, v: list[str]) -> list[str]:
        for path in v:
            _no_newline(path, "security.read_write_paths entry")
            if not _is_absolute(path):
                raise ValueError(f"read_write_paths entry must be absolute: {path!r}")
        return v


class ResourcesConfig(_Strict):
    memory_max: str | int | None = None
    cpu_quota: str | None = None
    tasks_max: int | None = Field(default=None, gt=0)

    @field_validator("memory_max")
    @classmethod
    def _validate_memory(cls, v: str | int | None) -> str | int | None:
        if v is None or isinstance(v, int):
            return v
        # systemd-ish size: digits with optional unit suffix (K/M/G/T, opt 'B').
        if not re.match(r"^\d+(\.\d+)?[KMGTP]?B?$", v):
            raise ValueError(f"memory_max is not a plausible size: {v!r}")
        return v

    @field_validator("cpu_quota")
    @classmethod
    def _validate_cpu(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if not v.endswith("%"):
            raise ValueError(f"cpu_quota must end with '%': {v!r}")
        if not v[:-1].strip().isdigit():
            raise ValueError(f"cpu_quota must be a percentage like '50%': {v!r}")
        return v


# Default applied when timeout_sec is omitted entirely (PRD §9.6). Pydantic
# distinguishes "key absent" (uses this default) from an explicit ``null``
# (becomes None -> directive omitted), so no special handling is needed.
DEFAULT_TIMEOUT_SEC = 300


class WorkflowDefinition(_Strict):
    id: Annotated[str, Field(min_length=1, max_length=80)]
    description: str
    exec: ExecConfig

    enabled: bool = True
    schedule: ScheduleConfig | None = None
    environment: EnvironmentConfig | None = None
    timeout_sec: int | None = DEFAULT_TIMEOUT_SEC
    restart: RestartConfig | None = None
    security: SecurityConfig | None = None
    resources: ResourcesConfig | None = None
    metadata: dict[str, object] | None = None

    @field_validator("id")
    @classmethod
    def _validate_id(cls, v: str) -> str:
        if not ID_PATTERN.match(v):
            raise ValueError(
                f"invalid id {v!r}: must match {ID_PATTERN.pattern} "
                "(lowercase slug, a-z 0-9 hyphen, starting alphanumeric, <=80 chars)"
            )
        return v

    @field_validator("description")
    @classmethod
    def _validate_description(cls, v: str) -> str:
        return _no_newline(v, "description")

    @field_validator("timeout_sec")
    @classmethod
    def _validate_timeout(cls, v: int | None) -> int | None:
        if v is not None and v < 0:
            raise ValueError("timeout_sec must be >= 0 or null")
        return v

    @property
    def service_unit_name(self) -> str:
        return f"wfctl-{self.id}.service"

    @property
    def timer_unit_name(self) -> str:
        return f"wfctl-{self.id}.timer"

    @property
    def has_timer(self) -> bool:
        return self.schedule is not None
