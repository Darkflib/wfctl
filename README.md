# wfctl

[![CI](https://github.com/Darkflib/wfctl/actions/workflows/ci.yml/badge.svg)](https://github.com/Darkflib/wfctl/actions/workflows/ci.yml)

**Declarative workflow controller for systemd `--user` units.**

`wfctl` syncs a directory of YAML workflow definitions into managed
`systemd --user` `.service` and `.timer` units. Think of it as a tiny,
Kubernetes-style reconciler:

```
desired state:   workflows/*.yaml
actual state:    ~/.config/systemd/user/wfctl-*.service
                 ~/.config/systemd/user/wfctl-*.timer
runtime authority: systemd --user
```

You describe *what* should run and when; systemd does the actual running.

## What wfctl is

- A renderer + reconciler. It reads YAML, validates it, renders units, diffs
  them against what's on disk, writes changes atomically, reloads the user
  manager, and enables/disables timers.
- Safe by default: it only ever touches files named `wfctl-*.service` /
  `wfctl-*.timer` that carry a `Managed-By: wfctl` header.

## What wfctl is *not*

It is **not** a scheduler, daemon, runner, retry engine, worker pool, DAG
orchestrator, web UI, or log store. All of that is delegated to systemd:
scheduling (timers), process execution, journald logging, restart policy, and
sandboxing. wfctl never runs your jobs itself.

## Requirements

- Linux with systemd and a running **user** manager
- Python 3.12+
- [uv](https://docs.astral.sh/uv/)

> wfctl manages **user** units only and refuses to run as root (override with
> `--allow-root`, which still never touches system units).

## Install

```bash
uv tool install wfctl        # as a standalone CLI tool
# or, from a checkout:
uv pip install -e ".[dev]"
```

## Quick start

1. Drop a workflow file in `~/.config/wfctl/workflows/`:

```yaml
# ~/.config/wfctl/workflows/daily-news.yaml
id: daily-news
description: Generate daily personalised news digest
enabled: true
exec:
  mode: uv-run
  working_directory: /home/me/workflows
  frozen: true
  command:
    - python
    - -m
    - workflows.daily_news
schedule:
  on_calendar: "*-*-* 07:00:00"
  persistent: true
  randomized_delay_sec: 120
timeout_sec: 600
security:
  profile: basic
```

2. Preview and apply:

```bash
wfctl validate
wfctl plan
wfctl apply
```

3. Operate:

```bash
wfctl list
wfctl status daily-news
wfctl logs daily-news --tail 100
wfctl run daily-news          # start the service now
wfctl paths                   # show resolved directories
```

## Commands

| Command | Purpose |
| --- | --- |
| `wfctl validate` | Validate all definitions; fails closed. |
| `wfctl plan [--prune] [--json]` | Show CREATE/UPDATE/DELETE/UNCHANGED actions. |
| `wfctl apply [--dry-run] [--prune] [--no-systemctl]` | Render, write, reload, enable/disable. |
| `wfctl list` | Show workflows with unit/timer state. |
| `wfctl status <id>` | `systemctl --user status` for the service (and timer). |
| `wfctl logs <id> [--tail N] [--follow] [--since T]` | `journalctl --user-unit` wrapper. |
| `wfctl run <id> [--wait]` | `systemctl --user start` the service now. |
| `wfctl prune [--dry-run] [--no-systemctl]` | Delete managed units with no backing definition. |
| `wfctl doctor [--strict]` | Check prerequisites: systemd, user manager, lingering, uv, paths. |
| `wfctl paths` | Print resolved config/unit/state/share directories. |

`--no-systemctl` writes/deletes files but skips all systemd calls — useful for
CI and for hosts without systemd (e.g. developing on macOS).

## Configuration locations

| Purpose | Default | Override |
| --- | --- | --- |
| Workflow definitions | `~/.config/wfctl/workflows` | `--config-dir`, `WFCTL_CONFIG_DIR` |
| Generated units | `~/.config/systemd/user` | `--unit-dir`, `WFCTL_UNIT_DIR` |
| State / cache | `~/.local/state/wfctl` | `--state-dir`, `WFCTL_STATE_DIR` |
| Debug output | `~/.local/share/wfctl/generated` | — |

CLI flags take precedence over environment variables, which take precedence
over the defaults.

## Workflow schema

See [`examples/`](examples/) for a worked sample of each `exec` mode (with
[`examples/README.md`](examples/README.md) explaining when to use which).
Top-level fields:

- `id` (required) — lowercase slug, `^[a-z0-9][a-z0-9-]{0,79}$`. Units derive
  from it: `wfctl-<id>.service`, `wfctl-<id>.timer`.
- `description` (required), `exec` (required).
- `enabled`, `schedule`, `environment`, `timeout_sec`, `restart`, `security`,
  `resources`, `metadata` (optional).

### exec modes

- `uv-run` (recommended for project workflows) → `uv run --frozen -- <cmd>`.
  Keeps units stable across venv rebuilds and lockfile-reproducible.
- `command` → raw argv, no shell wrapper.
- `uv-script` → `uv run --script <path>` for standalone scripts with inline
  metadata.

### security profiles

`none` ⊂ `basic` ⊂ `readonly-home` ⊂ `strict` ⊂ `networkless`, each adding more
systemd hardening directives. `read_write_paths` maps to `ReadWritePaths=`.

## Security notes

- ExecStart is rendered as argv — **no `/bin/sh -c` wrapper** unless you supply
  one explicitly via `command` mode.
- Prefer `EnvironmentFile` for secrets. wfctl never logs env values and warns if
  an inline variable name looks secret-like (`TOKEN`, `SECRET`, `PASSWORD`, …).
- wfctl never creates environment files for you.
- Generated units are written `0644`; state files `0600` where appropriate.

## systemd user-unit caveats

User timers only fire while your user manager is running. For always-on,
headless use you may need lingering enabled:

```bash
loginctl enable-linger "$USER"
```

wfctl will **not** run this for you.

## Troubleshooting

- **`systemd-analyze not found`** — calendar expressions can't be validated off
  a systemd host; wfctl only syntactically checks them and warns. Validate on
  the target.
- **Timer enabled but never fires** — check the user manager is running and
  lingering is enabled; inspect with `systemctl --user list-timers`.
- **`wfctl apply` reverted my manual `systemctl enable`** — enablement is
  driven by the `enabled:` field in YAML. Edit the definition, not systemd.
- **Path checks fail on a build host** — pass `--skip-path-checks` to validate
  definitions whose working directories live only on the target machine.

## Development

```bash
uv venv
uv pip install -e ".[dev]"
uv run pytest
uv run ruff check .
```

Tests never require real systemd — subprocess calls are mocked and file output
is asserted in temp directories. This is what makes the suite runnable on
macOS/Windows as well as Linux.

### Continuous integration

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs `ruff` and `pytest`
on `ubuntu-latest` across Python 3.12 and 3.13 for every push to `main` and
every pull request. Because the runner ships systemd, CI also exercises the
real `systemd-analyze calendar` validation path that a typical dev machine
can't.
