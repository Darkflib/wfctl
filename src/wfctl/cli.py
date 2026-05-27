"""wfctl command-line interface (PRD §6, §13).

Conventions:
  * stdout carries command output (plans, JSON, tables); logging goes to stderr.
  * Every command maps domain errors to the stable exit codes in errors.py.
  * Runtime commands (status/logs/run) are thin wrappers over systemctl/journalctl.
"""

from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from . import __version__
from .apply import apply_plan
from .config import AppContext
from .errors import WfctlError, WorkflowNotFoundError
from .loader import LoadedWorkflow
from .logging import configure_logging, get_logger
from .paths import Paths
from .plan import Action, Plan, build_plan
from .validate import validate_workflows

app = typer.Typer(
    name="wfctl",
    help="Declarative workflow controller for systemd --user units.",
    add_completion=False,
)

console = Console()  # stdout
err_console = Console(stderr=True)
logger = get_logger()


# --------------------------------------------------------------------------
# Global options / context
# --------------------------------------------------------------------------
@app.callback(invoke_without_command=True)
def main_callback(
    ctx: typer.Context,
    config_dir: Path | None = typer.Option(
        None, "--config-dir", help="Workflow definition directory.", envvar=None
    ),
    unit_dir: Path | None = typer.Option(
        None, "--unit-dir", help="Target systemd --user unit directory."
    ),
    state_dir: Path | None = typer.Option(
        None, "--state-dir", help="State/cache directory."
    ),
    allow_root: bool = typer.Option(
        False, "--allow-root", help="Permit running as root (user units only)."
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose logging."),
    version: bool = typer.Option(
        False, "--version", help="Show version and exit.", is_eager=True
    ),
) -> None:
    """Resolve global options into an AppContext shared by all commands."""
    if version:
        console.print(f"wfctl {__version__}")
        raise typer.Exit(0)

    configure_logging(verbose=verbose)
    paths = Paths.resolve(config_dir=config_dir, unit_dir=unit_dir, state_dir=state_dir)
    ctx.obj = AppContext(paths=paths, allow_root=allow_root, verbose=verbose)

    # With invoke_without_command=True the callback runs even for a bare
    # `wfctl`; show help and exit in that case (mirrors no_args_is_help).
    if ctx.invoked_subcommand is None:
        console.print(ctx.get_help())
        raise typer.Exit(0)


def _ctx(ctx: typer.Context) -> AppContext:
    assert isinstance(ctx.obj, AppContext)
    return ctx.obj


def _find_workflow(workflows: list[LoadedWorkflow], workflow_id: str) -> LoadedWorkflow:
    for wf in workflows:
        if wf.id == workflow_id:
            return wf
    raise WorkflowNotFoundError(f"workflow not found: {workflow_id!r}")


# --------------------------------------------------------------------------
# validate
# --------------------------------------------------------------------------
@app.command()
def validate(
    ctx: typer.Context,
    skip_path_checks: bool = typer.Option(
        False, "--skip-path-checks", help="Skip working-dir / env-file / script existence checks."
    ),
) -> None:
    """Validate all workflow definitions. Fails closed on any error."""
    app_ctx = _ctx(ctx)
    workflows = app_ctx.load()
    warnings = validate_workflows(
        workflows, runner=app_ctx.runner(), skip_path_checks=skip_path_checks
    )
    for w in warnings:
        err_console.print(f"[yellow]warning:[/] {w}")
    console.print(f"[green]ok[/] — {len(workflows)} workflow(s) valid")


# --------------------------------------------------------------------------
# plan
# --------------------------------------------------------------------------
_ACTION_STYLE = {
    Action.CREATE: "green",
    Action.UPDATE: "yellow",
    Action.DELETE: "red",
    Action.UNCHANGED: "dim",
}


@app.command()
def plan(
    ctx: typer.Context,
    prune: bool = typer.Option(
        False, "--prune", help="Include deletes for orphaned managed units."
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit the plan as JSON."),
    skip_path_checks: bool = typer.Option(
        False, "--skip-path-checks", help="Skip path existence checks."
    ),
) -> None:
    """Show the actions a subsequent apply would take."""
    app_ctx = _ctx(ctx)
    workflows = app_ctx.load()
    validate_workflows(workflows, runner=app_ctx.runner(), skip_path_checks=skip_path_checks)
    the_plan = build_plan(workflows, app_ctx.paths.unit_dir, prune=prune)

    if json_out:
        console.print_json(json.dumps(_plan_to_dict(the_plan)))
        return
    _print_plan(the_plan)


def _plan_to_dict(the_plan: Plan) -> dict:
    return {
        "items": [
            {
                "action": item.action.value,
                "unit": item.unit_name,
                "workflow_id": item.workflow_id,
                "path": str(item.path),
            }
            for item in the_plan.items
        ]
    }


def _print_plan(the_plan: Plan) -> None:
    if not the_plan.items:
        console.print("[dim]no units to manage[/]")
        return
    for item in the_plan.items:
        style = _ACTION_STYLE[item.action]
        console.print(f"[{style}]{item.action.value:<9}[/] {item.unit_name}")
    n = len(the_plan.changes())
    console.print(f"\n{n} change(s).")


# --------------------------------------------------------------------------
# apply
# --------------------------------------------------------------------------
@app.command()
def apply(
    ctx: typer.Context,
    dry_run: bool = typer.Option(False, "--dry-run", help="Show the plan; make no changes."),
    prune: bool = typer.Option(False, "--prune", help="Delete orphaned managed units."),
    no_systemctl: bool = typer.Option(
        False, "--no-systemctl", help="Write files only; skip daemon-reload and enable/disable."
    ),
    skip_path_checks: bool = typer.Option(
        False, "--skip-path-checks", help="Skip path existence checks."
    ),
) -> None:
    """Validate, render, and reconcile units into the systemd --user directory."""
    app_ctx = _ctx(ctx)
    # 1. Validate everything first — never partially apply (PRD §12.2).
    workflows = app_ctx.load()
    warnings = validate_workflows(
        workflows, runner=app_ctx.runner(), skip_path_checks=skip_path_checks
    )
    for w in warnings:
        err_console.print(f"[yellow]warning:[/] {w}")

    the_plan = build_plan(workflows, app_ctx.paths.unit_dir, prune=prune)

    if dry_run:
        _print_plan(the_plan)
        console.print("\n[dim](dry-run: no changes made)[/]")
        return

    runner = app_ctx.runner()
    apply_plan(the_plan, workflows, runner=runner, use_systemctl=not no_systemctl)
    if no_systemctl:
        console.print("[green]applied[/] (files only; systemctl skipped)")
    else:
        console.print("[green]applied[/]")


# --------------------------------------------------------------------------
# list
# --------------------------------------------------------------------------
@app.command("list")
def list_workflows(ctx: typer.Context) -> None:
    """List configured workflows and their current unit/timer state."""
    app_ctx = _ctx(ctx)
    workflows = app_ctx.load()
    runner = app_ctx.runner()
    systemctl_available = runner.has_binary("systemctl")

    table = Table(show_header=True, header_style="bold")
    table.add_column("ID")
    table.add_column("ENABLED")
    table.add_column("SCHEDULE")
    table.add_column("UNIT STATE")
    table.add_column("TIMER STATE")

    for wf in workflows:
        d = wf.definition
        schedule = ", ".join(d.schedule.calendar_expressions) if d.schedule else "manual"
        unit_state = "-"
        timer_state = "-"
        if systemctl_available:
            unit_state = runner.show_property(d.service_unit_name, "ActiveState") or "unknown"
            if d.has_timer:
                timer_state = runner.show_property(d.timer_unit_name, "ActiveState") or "unknown"
        elif d.has_timer:
            timer_state = "?"
            unit_state = "?"
        table.add_row(
            d.id,
            "yes" if d.enabled else "no",
            schedule,
            unit_state,
            timer_state if d.has_timer else "-",
        )

    console.print(table)
    if not systemctl_available:
        err_console.print(
            "[yellow]warning:[/] systemctl not found; unit/timer state unavailable on this host."
        )


# --------------------------------------------------------------------------
# status
# --------------------------------------------------------------------------
@app.command()
def status(ctx: typer.Context, workflow_id: str = typer.Argument(...)) -> None:
    """Show systemctl status for a workflow's service (and timer if present)."""
    app_ctx = _ctx(ctx)
    workflows = app_ctx.load()
    wf = _find_workflow(workflows, workflow_id)
    runner = app_ctx.runner()

    svc = runner.status(wf.definition.service_unit_name)
    console.print(svc.stdout or svc.stderr)
    if wf.definition.has_timer:
        tmr = runner.status(wf.definition.timer_unit_name)
        console.print(tmr.stdout or tmr.stderr)


# --------------------------------------------------------------------------
# logs
# --------------------------------------------------------------------------
@app.command()
def logs(
    ctx: typer.Context,
    workflow_id: str = typer.Argument(...),
    tail: int | None = typer.Option(
        None, "--tail", help="Show the last N lines (journalctl -n)."
    ),
    follow: bool = typer.Option(False, "--follow", help="Follow new log output (journalctl -f)."),
    since: str | None = typer.Option(
        None, "--since", help="Show entries since TIME (journalctl --since)."
    ),
) -> None:
    """Show journald logs for a workflow's service unit."""
    app_ctx = _ctx(ctx)
    workflows = app_ctx.load()
    wf = _find_workflow(workflows, workflow_id)

    args = ["--user-unit", wf.definition.service_unit_name]
    if tail is not None:
        args += ["-n", str(tail)]
    if since is not None:
        args += ["--since", since]
    if follow:
        args.append("-f")

    runner = app_ctx.runner()
    # Stream directly to the terminal (no capture) so --follow works.
    runner.journal(args, capture=False)


# --------------------------------------------------------------------------
# run
# --------------------------------------------------------------------------
@app.command()
def run(
    ctx: typer.Context,
    workflow_id: str = typer.Argument(...),
    wait: bool = typer.Option(False, "--wait", help="Run synchronously and wait for completion."),
) -> None:
    """Start a workflow's service immediately (systemctl --user start)."""
    app_ctx = _ctx(ctx)
    workflows = app_ctx.load()
    wf = _find_workflow(workflows, workflow_id)
    runner = app_ctx.runner()
    unit = wf.definition.service_unit_name

    if wait:
        # --wait blocks until the (oneshot) job finishes.
        runner.systemctl("start", "--wait", unit)
    else:
        runner.start(unit)
    console.print(f"[green]started[/] {unit}")


# --------------------------------------------------------------------------
# prune
# --------------------------------------------------------------------------
@app.command()
def prune(
    ctx: typer.Context,
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be deleted."),
    no_systemctl: bool = typer.Option(
        False, "--no-systemctl", help="Delete files only; skip daemon-reload."
    ),
) -> None:
    """Delete managed units that no longer have a backing workflow definition."""
    app_ctx = _ctx(ctx)
    workflows = app_ctx.load()
    the_plan = build_plan(workflows, app_ctx.paths.unit_dir, prune=True)
    deletes = [i for i in the_plan.items if i.action is Action.DELETE]

    if not deletes:
        console.print("[dim]nothing to prune[/]")
        return

    for item in deletes:
        console.print(f"[red]DELETE   [/] {item.unit_name}")

    if dry_run:
        console.print("\n[dim](dry-run: nothing deleted)[/]")
        return

    runner = app_ctx.runner()
    # Reuse apply with a delete-only plan so safe-delete + reload are consistent.
    apply_plan(Plan(items=deletes), workflows, runner=runner, use_systemctl=not no_systemctl)
    console.print(f"[green]pruned[/] {len(deletes)} unit(s)")


# --------------------------------------------------------------------------
# paths
# --------------------------------------------------------------------------
@app.command()
def paths(ctx: typer.Context) -> None:
    """Print the resolved configuration, unit, state, and share directories.

    Output is plain ``key: path`` lines (no table) so full paths are never
    truncated and the command is easy to grep/parse.
    """
    p = _ctx(ctx).paths
    rows = [
        ("config-dir", p.config_dir),
        ("unit-dir", p.unit_dir),
        ("state-dir", p.state_dir),
        ("share-dir", p.share_dir),
    ]
    width = max(len(k) for k, _ in rows)
    for key, value in rows:
        # markup=False so paths containing '[' are never treated as Rich markup.
        console.print(
            f"{key.ljust(width)}  {value}", markup=False, highlight=False, soft_wrap=True
        )


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------
def main() -> None:
    """Console-script entry point. Maps domain errors to stable exit codes.

    Runs with ``standalone_mode=False`` so the click runtime hands control back
    here for centralised error mapping instead of exiting itself.
    """
    # Typer >= 0.26 vendors click as ``typer._click``; older typer (and the
    # Linux target, potentially) ships real ``click``. Support both.
    try:
        from click import exceptions as ce
    except ModuleNotFoundError:  # pragma: no cover - depends on typer packaging
        from typer._click import exceptions as ce

    try:
        app(standalone_mode=False)
    except WfctlError as exc:
        err_console.print(f"[red]error:[/] {exc}")
        raise SystemExit(exc.exit_code) from exc
    except ce.Abort as exc:
        err_console.print("aborted")
        raise SystemExit(130) from exc
    except ce.ClickException as exc:
        # Usage/parameter errors: let click format them, then use its exit code.
        exc.show()
        raise SystemExit(exc.exit_code) from exc
    except ce.Exit as exc:
        # Raised by `raise typer.Exit(code)` (e.g. --version).
        raise SystemExit(exc.exit_code) from exc


if __name__ == "__main__":
    main()
