# wfctl example workflows

A worked example for each of the three `exec` modes. Each file is heavily
commented so you can read it top-to-bottom and understand both the *what* and
the *why*.

## Which mode do I want?

```
Is your code a Python project (a folder with pyproject.toml / uv.lock)?
└── yes ─► mode: uv-run         (see uv-run-scheduled.yaml)
└── no  ─► Is it a single-file Python script with PEP 723 inline metadata?
          └── yes ─► mode: uv-script    (see uv-script-hourly.yaml)
          └── no  ─► mode: command      (see command-manual.yaml)
```

Quick reference:

| Mode | When to use | Renders to |
| --- | --- | --- |
| `uv-run` | Python project workflows — anything with a `pyproject.toml` / `uv.lock`. Recommended. | `uv run --frozen -- <argv>` |
| `uv-script` | A single Python file that declares its dependencies with [PEP 723](https://peps.python.org/pep-0723/) inline metadata. | `uv run --script <path>` |
| `command` | Anything else: shell scripts, system binaries, non-Python tools. | Your `command` argv, executed directly (no shell wrapper). |

## Files

- **`uv-run-scheduled.yaml`** — daily Python job from a uv-managed project, with hardening and resource limits.
- **`uv-script-hourly.yaml`** — hourly run of a self-contained PEP 723 script.
- **`command-manual.yaml`** — on-demand shell script invocation (no schedule).

## Trying them

```bash
# Inspect what would be created
wfctl --config-dir ./examples plan --skip-path-checks

# Render to a sandbox without touching real systemd
wfctl --config-dir ./examples --unit-dir /tmp/wfctl-demo apply --no-systemctl --skip-path-checks
ls /tmp/wfctl-demo
```

`--skip-path-checks` exists because these examples reference paths under
`/home/mike/...` that don't exist on your machine. Drop it once you've
adapted the paths to your own setup.
