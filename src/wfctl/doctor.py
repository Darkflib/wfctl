"""Preflight health checks for the wfctl environment.

``wfctl doctor`` is purely diagnostic: it never writes files or talks to
systemd in a mutating way. Each check returns a :class:`CheckResult` with a
status and a short remediation hint, so users on a fresh box can spot missing
prerequisites (no systemd, no user manager, lingering disabled, etc.) before
they ever run ``apply``.
"""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from enum import StrEnum

from .paths import Paths
from .systemd import SystemdRunner

MIN_PYTHON = (3, 12)


class CheckStatus(StrEnum):
    OK = "ok"
    WARN = "warn"
    FAIL = "fail"


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: CheckStatus
    detail: str
    hint: str | None = None


# --------------------------------------------------------------------------
# Individual checks. Each is small, side-effect-free, and easily testable.
# --------------------------------------------------------------------------
def check_python_version() -> CheckResult:
    v = sys.version_info
    current = f"{v.major}.{v.minor}.{v.micro}"
    if (v.major, v.minor) >= MIN_PYTHON:
        return CheckResult("python", CheckStatus.OK, current)
    return CheckResult(
        "python",
        CheckStatus.FAIL,
        f"{current} (need >= {MIN_PYTHON[0]}.{MIN_PYTHON[1]})",
        hint=f"install Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ (e.g. `uv python install`)",
    )


def check_systemctl(runner: SystemdRunner) -> CheckResult:
    if runner.has_binary("systemctl"):
        return CheckResult("systemctl", CheckStatus.OK, "present")
    return CheckResult(
        "systemctl",
        CheckStatus.FAIL,
        "not found on PATH",
        hint="install systemd (this host appears not to be a systemd Linux box)",
    )


def check_journalctl(runner: SystemdRunner) -> CheckResult:
    if runner.has_binary("journalctl"):
        return CheckResult("journalctl", CheckStatus.OK, "present")
    return CheckResult(
        "journalctl",
        CheckStatus.WARN,
        "not found on PATH",
        hint="`wfctl logs` will not work without journalctl",
    )


def check_systemd_analyze(runner: SystemdRunner) -> CheckResult:
    if runner.has_binary("systemd-analyze"):
        return CheckResult("systemd-analyze", CheckStatus.OK, "present")
    return CheckResult(
        "systemd-analyze",
        CheckStatus.WARN,
        "not found on PATH",
        hint="calendar expressions cannot be fully validated without it",
    )


def check_uv(runner: SystemdRunner) -> CheckResult:
    path = shutil.which("uv")
    if not path:
        return CheckResult(
            "uv",
            CheckStatus.WARN,
            "not found on PATH",
            hint="needed for `mode: uv-run` and `mode: uv-script` workflows",
        )
    # Capture `uv --version` so we report the actual binary, not just its path.
    result = runner.run(["uv", "--version"], check=False)
    version = result.stdout.strip() if result.ok else "(version unknown)"
    return CheckResult("uv", CheckStatus.OK, f"{path}  ({version})")


def check_user_manager(runner: SystemdRunner) -> CheckResult:
    """Is the per-user systemd manager actually running?"""
    if not runner.has_binary("systemctl"):
        return CheckResult(
            "user manager",
            CheckStatus.FAIL,
            "skipped (systemctl missing)",
        )
    result = runner.systemctl("is-system-running", check=False)
    state = (result.stdout or result.stderr).strip() or "unknown"
    # `is-system-running` returns non-zero for degraded but that is still
    # operational — only a missing or stopped manager is a real failure.
    if state in {"running", "degraded", "starting"}:
        status = CheckStatus.OK if state == "running" else CheckStatus.WARN
        return CheckResult("user manager", status, state)
    return CheckResult(
        "user manager",
        CheckStatus.FAIL,
        state,
        hint="check `systemctl --user status` and that the user manager is enabled",
    )


def check_linger(runner: SystemdRunner) -> CheckResult:
    """Is lingering enabled for the current user?

    Without lingering, user units only run while a session is open. For
    headless or always-on hosts this is almost always wrong.
    """
    if not runner.has_binary("loginctl"):
        return CheckResult(
            "linger",
            CheckStatus.WARN,
            "loginctl missing — cannot determine state",
        )
    user = os.environ.get("USER") or os.environ.get("LOGNAME") or ""
    if not user:
        return CheckResult("linger", CheckStatus.WARN, "USER not set; cannot check")
    result = runner.run(
        ["loginctl", "show-user", user, "--property=Linger", "--value"],
        check=False,
    )
    value = result.stdout.strip().lower() if result.ok else ""
    if value == "yes":
        return CheckResult("linger", CheckStatus.OK, f"enabled for {user}")
    if value == "no":
        return CheckResult(
            "linger",
            CheckStatus.WARN,
            f"disabled for {user} — user timers won't fire while logged out",
            hint=f'loginctl enable-linger "{user}"',
        )
    return CheckResult(
        "linger",
        CheckStatus.WARN,
        f"could not parse loginctl output: {value!r}",
    )


def check_root() -> CheckResult:
    """wfctl manages user units only; running as root is almost always a mistake."""
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        return CheckResult(
            "running as root",
            CheckStatus.FAIL,
            "yes",
            hint="re-run as a regular user (use --allow-root only if you really mean it)",
        )
    return CheckResult("running as root", CheckStatus.OK, "no")


def check_config_dir(paths: Paths) -> CheckResult:
    p = paths.config_dir
    if p.is_dir():
        try:
            count = sum(
                1 for f in p.iterdir() if f.is_file() and f.suffix.lower() in (".yaml", ".yml")
            )
        except OSError as exc:
            return CheckResult("config-dir", CheckStatus.WARN, f"{p} ({exc})")
        return CheckResult("config-dir", CheckStatus.OK, f"{p}  ({count} workflow file(s))")
    return CheckResult(
        "config-dir",
        CheckStatus.WARN,
        f"{p} (does not exist)",
        hint=f"create it: mkdir -p {p}",
    )


def check_unit_dir(paths: Paths) -> CheckResult:
    p = paths.unit_dir
    if p.is_dir():
        if os.access(p, os.W_OK):
            return CheckResult("unit-dir", CheckStatus.OK, str(p))
        return CheckResult(
            "unit-dir",
            CheckStatus.FAIL,
            f"{p} (not writable)",
            hint="check directory permissions",
        )
    return CheckResult(
        "unit-dir",
        CheckStatus.WARN,
        f"{p} (does not exist — will be created on apply)",
    )


# --------------------------------------------------------------------------
# Orchestrator
# --------------------------------------------------------------------------
def run_checks(paths: Paths, runner: SystemdRunner) -> list[CheckResult]:
    """Run every preflight check, in a stable, user-friendly order."""
    return [
        check_python_version(),
        check_root(),
        check_systemctl(runner),
        check_user_manager(runner),
        check_linger(runner),
        check_journalctl(runner),
        check_systemd_analyze(runner),
        check_uv(runner),
        check_config_dir(paths),
        check_unit_dir(paths),
    ]


def overall_status(results: list[CheckResult]) -> CheckStatus:
    """Worst-of: FAIL beats WARN beats OK."""
    if any(r.status is CheckStatus.FAIL for r in results):
        return CheckStatus.FAIL
    if any(r.status is CheckStatus.WARN for r in results):
        return CheckStatus.WARN
    return CheckStatus.OK
