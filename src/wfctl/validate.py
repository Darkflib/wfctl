"""Semantic validation beyond the structural Pydantic schema (PRD §14).

Pydantic already enforces required fields, id format, absolute-path fields,
non-empty commands, recognised enums, and resource plausibility. This module
adds environment-aware checks: filesystem existence, calendar validity via
``systemd-analyze``, and advisory warnings (secret-like names, restart/oneshot
mismatch).

Hard failures raise :class:`ValidationError` (exit code 2). Advisory issues are
returned as warning strings; the caller decides how to surface them.
"""

from __future__ import annotations

from pathlib import Path

from .errors import ValidationError
from .loader import LoadedWorkflow
from .models import RestartPolicy
from .systemd import SystemdRunner


def validate_workflows(
    workflows: list[LoadedWorkflow],
    *,
    runner: SystemdRunner | None = None,
    skip_path_checks: bool = False,
) -> list[str]:
    """Run semantic checks across all *workflows*.

    Returns a list of human-readable warnings. Raises on the first hard error
    so we *fail closed* and never apply an invalid set.
    """
    runner = runner or SystemdRunner()
    warnings: list[str] = []
    calendar_available = runner.has_binary("systemd-analyze")

    for wf in workflows:
        warnings.extend(_validate_one(wf, runner, calendar_available, skip_path_checks))

    if not calendar_available:
        warnings.append(
            "systemd-analyze not found: calendar expressions were not validated "
            "(only syntactic checks applied). Validate on the target host."
        )
    return warnings


def _validate_one(
    wf: LoadedWorkflow,
    runner: SystemdRunner,
    calendar_available: bool,
    skip_path_checks: bool,
) -> list[str]:
    d = wf.definition
    where = f"{wf.id} ({wf.source_path})"
    warnings: list[str] = []

    # --- working directory existence -------------------------------------
    if not skip_path_checks and d.exec.working_directory:
        wd = Path(d.exec.working_directory)
        if not wd.is_dir():
            raise ValidationError(
                f"{where}: exec.working_directory does not exist: {wd} "
                "(use --skip-path-checks to bypass on a build host)"
            )

    # --- uv-script existence ---------------------------------------------
    if not skip_path_checks and d.exec.script:
        script = Path(d.exec.script)
        if not script.is_file():
            raise ValidationError(
                f"{where}: exec.script does not exist: {script} "
                "(use --skip-path-checks to bypass on a build host)"
            )

    # --- environment files existence -------------------------------------
    if not skip_path_checks and d.environment:
        for env_file in d.environment.files:
            if not Path(env_file).is_file():
                raise ValidationError(
                    f"{where}: environment file does not exist: {env_file} "
                    "(use --skip-path-checks to bypass on a build host)"
                )

    # --- secret-like inline env names (advisory) -------------------------
    if d.environment:
        for name in d.environment.secret_like_names():
            warnings.append(
                f"{wf.id}: environment variable {name!r} looks secret-like; "
                "prefer an EnvironmentFile over an inline value (PRD §16.4)."
            )

    # --- calendar validation ---------------------------------------------
    if d.schedule is not None:
        for expr in d.schedule.calendar_expressions:
            if calendar_available:
                result = runner.analyze_calendar(expr)
                if not result.ok:
                    raise ValidationError(
                        f"{where}: invalid schedule.on_calendar {expr!r}:\n"
                        f"{(result.stderr or result.stdout).strip()}"
                    )
            else:
                _syntactic_calendar_check(expr, where)

    # --- restart/oneshot mismatch (advisory) -----------------------------
    if d.restart and d.restart.policy is RestartPolicy.ALWAYS:
        warnings.append(
            f"{wf.id}: restart.policy 'always' is rejected by systemd for "
            "Type=oneshot services; systemd may refuse to load this unit. "
            "Use 'on-failure' instead."
        )

    return warnings


def _syntactic_calendar_check(expr: str, where: str) -> None:
    """Minimal fallback when systemd-analyze is unavailable.

    We can't fully parse OnCalendar without systemd; reject only obviously
    broken input (empty/whitespace) and otherwise accept, having already warned
    globally that calendar validation was skipped.
    """
    if not expr.strip():
        raise ValidationError(f"{where}: schedule.on_calendar is empty")
