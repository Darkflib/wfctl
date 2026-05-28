"""Centralised, thin wrappers around systemctl / journalctl / systemd-analyze.

All subprocess execution funnels through :class:`SystemdRunner` so it can be
mocked in tests (PRD §21.3) and so command metadata is logged in one place.
wfctl targets the *user* manager exclusively (``systemctl --user``).
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass

from .errors import SystemdError
from .logging import get_logger

logger = get_logger()


@dataclass(frozen=True)
class CommandResult:
    args: list[str]
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


class SystemdRunner:
    """Runs systemd-family commands. Stateless apart from the dry-run flag.

    When *dry_run* is set, mutating commands are logged and skipped (read-only
    commands like ``show`` still run so status/list output stays meaningful).
    """

    def __init__(self, dry_run: bool = False) -> None:
        self.dry_run = dry_run

    # -- low-level -------------------------------------------------------
    def _run(
        self,
        args: list[str],
        *,
        check: bool,
        capture: bool = True,
    ) -> CommandResult:
        logger.debug("exec: %s", " ".join(args))
        try:
            proc = subprocess.run(
                args,
                capture_output=capture,
                text=True,
                check=False,
            )
        except FileNotFoundError as exc:
            raise SystemdError(f"command not found: {args[0]!r} ({exc})") from exc

        result = CommandResult(
            args=args,
            returncode=proc.returncode,
            stdout=proc.stdout or "" if capture else "",
            stderr=proc.stderr or "" if capture else "",
        )
        if check and not result.ok:
            raise SystemdError(
                f"command failed ({result.returncode}): {' '.join(args)}\n{result.stderr.strip()}"
            )
        return result

    # -- capability checks ----------------------------------------------
    @staticmethod
    def has_binary(name: str) -> bool:
        return shutil.which(name) is not None

    # -- general-purpose command runner ---------------------------------
    def run(self, args: list[str], *, check: bool = False, capture: bool = True) -> CommandResult:
        """Run an arbitrary command through the same path as the systemd wrappers.

        Exposed so callers (e.g. :mod:`wfctl.doctor`) and tests can inject canned
        behaviour by subclassing :class:`SystemdRunner`.
        """
        return self._run(args, check=check, capture=capture)

    # -- systemctl --user ------------------------------------------------
    def systemctl(self, *args: str, check: bool = True, capture: bool = True) -> CommandResult:
        return self._run(["systemctl", "--user", *args], check=check, capture=capture)

    def daemon_reload(self) -> None:
        if self.dry_run:
            logger.info("[dry-run] systemctl --user daemon-reload")
            return
        self.systemctl("daemon-reload")

    def enable_now(self, unit: str) -> None:
        if self.dry_run:
            logger.info("[dry-run] systemctl --user enable --now %s", unit)
            return
        self.systemctl("enable", "--now", unit)

    def disable_now(self, unit: str) -> None:
        if self.dry_run:
            logger.info("[dry-run] systemctl --user disable --now %s", unit)
            return
        # A disabled/non-existent unit should not be a hard failure.
        self.systemctl("disable", "--now", unit, check=False)

    def start(self, unit: str) -> None:
        if self.dry_run:
            logger.info("[dry-run] systemctl --user start %s", unit)
            return
        self.systemctl("start", unit)

    def show_property(self, unit: str, prop: str) -> str:
        """Return a single ``systemctl show`` property value, or '' on failure."""
        result = self.systemctl("show", unit, "--property", prop, "--value", check=False)
        return result.stdout.strip() if result.ok else ""

    def status(self, unit: str) -> CommandResult:
        # status returns non-zero for inactive units; surface it without raising.
        return self.systemctl("status", unit, check=False)

    # -- journalctl ------------------------------------------------------
    def journal(self, args: list[str], *, capture: bool = True) -> CommandResult:
        return self._run(["journalctl", *args], check=False, capture=capture)

    # -- systemd-analyze -------------------------------------------------
    def analyze_calendar(self, expr: str) -> CommandResult:
        return self._run(
            ["systemd-analyze", "calendar", expr],
            check=False,
        )
