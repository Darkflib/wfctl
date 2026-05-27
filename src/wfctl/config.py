"""Shared application context assembled from global CLI options.

A single :class:`AppContext` is built once in the CLI callback and stashed on
Typer's context object, so every command sees the same resolved paths and
flags.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from .errors import UnsafeOperationError
from .loader import LoadedWorkflow, load_workflows
from .logging import get_logger
from .paths import Paths
from .systemd import SystemdRunner

logger = get_logger()


@dataclass
class AppContext:
    paths: Paths
    allow_root: bool = False
    verbose: bool = False

    def __post_init__(self) -> None:
        self._guard_root()

    def _guard_root(self) -> None:
        """Refuse to run as root unless explicitly allowed (PRD §16.1)."""
        if hasattr(os, "geteuid") and os.geteuid() == 0 and not self.allow_root:
            raise UnsafeOperationError(
                "refusing to run as root: wfctl manages user units only. "
                "Re-run as a normal user, or pass --allow-root to override "
                "(even then, system units are never managed)."
            )

    def load(self) -> list[LoadedWorkflow]:
        """Load and validate (structurally) all workflows from the config dir."""
        return load_workflows(self.paths.config_dir)

    def runner(self, *, dry_run: bool = False) -> SystemdRunner:
        return SystemdRunner(dry_run=dry_run)
