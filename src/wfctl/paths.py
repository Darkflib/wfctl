"""Resolution of wfctl's filesystem locations.

Precedence for every path is: explicit CLI override > environment variable >
XDG-based default (PRD §7).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ENV_CONFIG_DIR = "WFCTL_CONFIG_DIR"
ENV_UNIT_DIR = "WFCTL_UNIT_DIR"
ENV_STATE_DIR = "WFCTL_STATE_DIR"


def _xdg(env_var: str, default_suffix: str) -> Path:
    base = os.environ.get(env_var)
    if base:
        return Path(base).expanduser()
    return Path.home() / default_suffix


def default_config_dir() -> Path:
    """``$XDG_CONFIG_HOME/wfctl/workflows`` (default ``~/.config/wfctl/workflows``)."""
    return _xdg("XDG_CONFIG_HOME", ".config") / "wfctl" / "workflows"


def default_unit_dir() -> Path:
    """``$XDG_CONFIG_HOME/systemd/user`` (default ``~/.config/systemd/user``)."""
    return _xdg("XDG_CONFIG_HOME", ".config") / "systemd" / "user"


def default_state_dir() -> Path:
    """``$XDG_STATE_HOME/wfctl`` (default ``~/.local/state/wfctl``)."""
    return _xdg("XDG_STATE_HOME", ".local/state") / "wfctl"


def default_share_dir() -> Path:
    """``$XDG_DATA_HOME/wfctl/generated`` (default ``~/.local/share/wfctl/generated``)."""
    return _xdg("XDG_DATA_HOME", ".local/share") / "wfctl" / "generated"


@dataclass(frozen=True)
class Paths:
    """The set of directories wfctl reads from and writes to."""

    config_dir: Path
    unit_dir: Path
    state_dir: Path
    share_dir: Path

    @classmethod
    def resolve(
        cls,
        *,
        config_dir: Path | str | None = None,
        unit_dir: Path | str | None = None,
        state_dir: Path | str | None = None,
        share_dir: Path | str | None = None,
    ) -> Paths:
        """Build a :class:`Paths` honouring CLI > env > default precedence.

        ``None`` arguments fall back to the environment variable, then the
        XDG-based default.
        """

        def pick(cli: Path | str | None, env_var: str, default: Path) -> Path:
            if cli is not None:
                return Path(cli).expanduser()
            env = os.environ.get(env_var)
            if env:
                return Path(env).expanduser()
            return default

        return cls(
            config_dir=pick(config_dir, ENV_CONFIG_DIR, default_config_dir()),
            unit_dir=pick(unit_dir, ENV_UNIT_DIR, default_unit_dir()),
            state_dir=pick(state_dir, ENV_STATE_DIR, default_state_dir()),
            # share_dir has no documented env override; derive from default only.
            share_dir=Path(share_dir).expanduser() if share_dir else default_share_dir(),
        )
