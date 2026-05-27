"""Domain exceptions, each carrying a stable process exit code.

Exit codes (see PRD §19)::

    0  success
    1  generic error
    2  validation error
    3  systemd command failed
    4  unsafe operation refused
    5  workflow not found
"""

from __future__ import annotations


class WfctlError(Exception):
    """Base class for all wfctl errors. Maps to a process exit code."""

    exit_code: int = 1


class ConfigError(WfctlError):
    """A configuration / environment problem unrelated to workflow content."""

    exit_code = 1


class ValidationError(WfctlError):
    """A workflow definition failed schema or semantic validation."""

    exit_code = 2


class SystemdError(WfctlError):
    """An underlying systemctl/journalctl/systemd-analyze call failed."""

    exit_code = 3


class UnsafeOperationError(WfctlError):
    """A requested operation was refused because it was unsafe.

    Examples: deleting a file that is not wfctl-managed, or running as root
    without an explicit override.
    """

    exit_code = 4


class WorkflowNotFoundError(WfctlError):
    """The named workflow does not exist in the configured directory."""

    exit_code = 5
