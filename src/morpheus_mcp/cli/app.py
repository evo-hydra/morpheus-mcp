"""Typer CLI for Morpheus."""

from __future__ import annotations

import json
import sqlite3
from typing import Annotated

import typer
from rich.console import Console

from morpheus_mcp.config import MorpheusConfig

app = typer.Typer(
    name="morpheus",
    help="Morpheus — plan state management and phase gate enforcement for AI dev loops.",
    no_args_is_help=True,
)
console = Console(stderr=True)


def _config() -> MorpheusConfig:
    """Load config for this invocation."""
    return MorpheusConfig.load()


@app.command()
def init(
    plan_file: Annotated[str, typer.Argument(help="Path to plan markdown file")],
) -> None:
    """Parse and load a plan file into Morpheus."""
    from morpheus_mcp.core.engine import init_plan
    from morpheus_mcp.core.parser import parse_plan_file
    from morpheus_mcp.core.store import MorpheusStore
    from morpheus_mcp.mcp.formatters import format_plan_summary

    config = _config()
    try:
        plan, tasks = parse_plan_file(plan_file)
        with MorpheusStore(config.db_path) as store:
            init_plan(store, plan, tasks)
        console.print(format_plan_summary(plan, tasks))
    except (FileNotFoundError, ValueError, sqlite3.Error, OSError) as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1) from None


@app.command()
def status(
    plan_id: Annotated[str | None, typer.Argument(help="Plan ID (defaults to most recent)")] = None,
) -> None:
    """Show plan progress and task states."""
    from morpheus_mcp.core.store import MorpheusStore
    from morpheus_mcp.mcp.formatters import format_status

    config = _config()
    try:
        with MorpheusStore(config.db_path) as store:
            if plan_id:
                plan = store.get_plan(plan_id)
            else:
                plans = store.list_plans()
                plan = plans[0] if plans else None

            if plan is None:
                console.print("[dim]No plans found. Use [bold]morpheus init[/bold] to load a plan.[/dim]")
                return

            tasks = store.get_tasks(plan.id)
            phases_by_task = {t.id: store.get_phases(t.id) for t in tasks}
            console.print(format_status(plan, tasks, phases_by_task))
    except (sqlite3.Error, OSError) as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1) from None


@app.command(name="advance")
def advance_cmd(
    task_id: Annotated[str, typer.Argument(help="Task ID to advance")],
    phase: Annotated[str, typer.Argument(help="Phase: CHECK, CODE, TEST, GRADE, COMMIT, ADVANCE")],
    evidence: Annotated[str, typer.Argument(help="JSON evidence string")] = "{}",
) -> None:
    """Advance a task through a phase gate with evidence."""
    from morpheus_mcp.core.engine import advance
    from morpheus_mcp.core.store import MorpheusStore
    from morpheus_mcp.mcp.formatters import format_advance_rejection, format_advance_success
    from morpheus_mcp.models.enums import Phase

    config = _config()
    try:
        phase_enum = Phase(phase.upper())
    except ValueError:
        valid = ", ".join(p.value for p in Phase)
        console.print(f"[red]Error:[/red] Invalid phase '{phase}'. Valid: {valid}")
        raise typer.Exit(1) from None

    try:
        evidence_dict = json.loads(evidence)
    except json.JSONDecodeError as exc:
        console.print(f"[red]Error:[/red] Invalid JSON: {exc}")
        raise typer.Exit(1) from None

    try:
        with MorpheusStore(config.db_path) as store:
            result, phase_record = advance(store, task_id, phase_enum, evidence_dict)
            if not result.passed:
                console.print(format_advance_rejection(phase_enum, result.message))
                raise typer.Exit(1) from None

            task = store.get_task(task_id)
            if task:
                console.print(format_advance_success(phase_enum, task))
    except (sqlite3.Error, OSError) as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1) from None


@app.command()
def close(
    plan_id: Annotated[str, typer.Argument(help="Plan ID to close")],
) -> None:
    """Mark a plan as completed."""
    from morpheus_mcp.core.engine import close_plan
    from morpheus_mcp.core.store import MorpheusStore
    from morpheus_mcp.mcp.formatters import format_close_summary

    config = _config()
    try:
        with MorpheusStore(config.db_path) as store:
            plan = close_plan(store, plan_id)
            if plan is None:
                console.print(f"[red]Error:[/red] Plan '{plan_id}' not found")
                raise typer.Exit(1) from None

            tasks = store.get_tasks(plan.id)
            console.print(format_close_summary(plan, tasks))
    except (sqlite3.Error, OSError) as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1) from None


@app.command(name="list")
def list_cmd() -> None:
    """List all plans."""
    from morpheus_mcp.core.store import MorpheusStore

    config = _config()
    try:
        with MorpheusStore(config.db_path) as store:
            plans = store.list_plans()
            if not plans:
                console.print("[dim]No plans found.[/dim]")
                return

            for p in plans:
                console.print(
                    f"  `{p.id[:12]}` {p.name} [{p.status.value}] "
                    f"({p.created_at.strftime('%Y-%m-%d')})"
                )
    except (sqlite3.Error, OSError) as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1) from None


def main() -> None:
    """Entry point for morpheus CLI."""
    app()


if __name__ == "__main__":
    main()
