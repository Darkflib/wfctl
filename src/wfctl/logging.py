"""Logging setup for wfctl, routed through Rich for readable CLI output.

Note: wfctl never logs environment variable values or secrets (PRD §16.4).
Callers must pass already-redacted strings to the logger.
"""

from __future__ import annotations

import logging

from rich.console import Console
from rich.logging import RichHandler

# A single stderr console shared by logging; stdout is reserved for command
# output (plans, JSON, tables) so wfctl composes cleanly in pipelines.
_err_console = Console(stderr=True)

LOGGER_NAME = "wfctl"


def configure_logging(verbose: bool = False) -> logging.Logger:
    """Configure and return the wfctl logger.

    Idempotent: repeated calls only adjust the level and do not stack handlers.
    """
    logger = logging.getLogger(LOGGER_NAME)
    level = logging.DEBUG if verbose else logging.INFO
    logger.setLevel(level)

    if not logger.handlers:
        handler = RichHandler(
            console=_err_console,
            show_time=False,
            show_path=False,
            rich_tracebacks=True,
            markup=False,
        )
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
        logger.propagate = False

    return logger


def get_logger() -> logging.Logger:
    """Return the shared wfctl logger (configuring it with defaults if needed)."""
    logger = logging.getLogger(LOGGER_NAME)
    if not logger.handlers:
        return configure_logging()
    return logger
