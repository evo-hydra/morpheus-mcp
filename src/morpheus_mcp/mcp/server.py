"""FastMCP server factory with 4 MCP tools."""

from __future__ import annotations

import json
import sqlite3


def create_server(config=None):
    """Create and return a configured FastMCP server instance."""
    from mcp.server.fastmcp import FastMCP

    from morpheus_mcp.config import MorpheusConfig

    mcp = FastMCP(
        "morpheus",
        instructions=(
            "Morpheus — plan state management and phase gate enforcement "
            "for AI dev loops. All tools are deterministic and fast (<100ms)."
        ),
    )
    _config = config or MorpheusConfig.load()

    @mcp.tool()
    def morpheus_init(plan_file: str) -> str:
        """Load a plan file into Morpheus and begin tracking state.

        Parses the markdown plan file, saves plan and tasks to the store,
        and returns a summary with task list and IDs.

        Args:
            plan_file: Filesystem path to the plan markdown file
        """
        from morpheus_mcp.core.engine import init_plan
        from morpheus_mcp.core.parser import parse_plan_file
        from morpheus_mcp.core.store import MorpheusStore
        from morpheus_mcp.mcp.formatters import format_plan_summary

        try:
            plan, tasks = parse_plan_file(plan_file)
            with MorpheusStore(_config.db_path) as store:
                init_plan(store, plan, tasks)
                return format_plan_summary(plan, tasks)
        except (FileNotFoundError, ValueError, sqlite3.Error, OSError) as exc:
            return f"Error: {exc}"

    @mcp.tool()
    def morpheus_status(plan_id: str | None = None) -> str:
        """Get current plan progress, task states, and active phase.

        If plan_id is omitted, returns the most recently created plan.

        Args:
            plan_id: Plan ID (optional — defaults to most recent plan)
        """
        from morpheus_mcp.core.store import MorpheusStore
        from morpheus_mcp.mcp.formatters import format_status

        try:
            with MorpheusStore(_config.db_path) as store:
                if plan_id:
                    plan = store.get_plan(plan_id)
                else:
                    plans = store.list_plans()
                    plan = plans[0] if plans else None

                if plan is None:
                    return "No plans found. Use `morpheus_init` to load a plan."

                tasks = store.get_tasks(plan.id)
                phases_by_task = {
                    t.id: store.get_phases(t.id) for t in tasks
                }
                return format_status(plan, tasks, phases_by_task)
        except (sqlite3.Error, OSError) as exc:
            return f"Error: {exc}"

    @mcp.tool()
    def morpheus_advance(task_id: str, phase: str, evidence: str = "{}") -> str:
        """Advance a task through a phase gate with evidence.

        Validates that the evidence satisfies the gate requirements.
        Returns success with next phase instructions, or rejection
        with details on what's missing.

        Args:
            task_id: The task ID to advance
            phase: Phase name: CHECK, CODE, TEST, GRADE, COMMIT, or ADVANCE
            evidence: JSON string with evidence key-value pairs
        """
        from morpheus_mcp.core.engine import advance
        from morpheus_mcp.core.store import MorpheusStore
        from morpheus_mcp.mcp.formatters import (
            format_advance_rejection,
            format_advance_success,
        )
        from morpheus_mcp.models.enums import Phase

        try:
            phase_enum = Phase(phase.upper())
        except ValueError:
            valid = ", ".join(p.value for p in Phase)
            return f"Error: Invalid phase '{phase}'. Valid phases: {valid}"

        try:
            evidence_dict = json.loads(evidence) if isinstance(evidence, str) else evidence
        except json.JSONDecodeError as exc:
            return f"Error: Invalid evidence JSON: {exc}"

        try:
            with MorpheusStore(_config.db_path) as store:
                result, phase_record = advance(store, task_id, phase_enum, evidence_dict)

                if not result.passed:
                    return format_advance_rejection(phase_enum, result.message)

                task = store.get_task(task_id)
                if task is None:
                    return f"Error: Task '{task_id}' not found after advance"

                return format_advance_success(phase_enum, task)
        except (sqlite3.Error, OSError) as exc:
            return f"Error: {exc}"

    @mcp.tool()
    def morpheus_close(plan_id: str) -> str:
        """Mark a plan as completed and return final summary.

        Args:
            plan_id: The plan ID to close
        """
        from morpheus_mcp.core.engine import close_plan
        from morpheus_mcp.core.store import MorpheusStore
        from morpheus_mcp.mcp.formatters import format_close_summary

        try:
            with MorpheusStore(_config.db_path) as store:
                plan = close_plan(store, plan_id)
                if plan is None:
                    return f"Error: Plan '{plan_id}' not found"

                tasks = store.get_tasks(plan.id)
                return format_close_summary(plan, tasks)
        except (sqlite3.Error, OSError) as exc:
            return f"Error: {exc}"

    return mcp


def main() -> None:
    """Entry point for morpheus-mcp."""
    server = create_server()
    server.run()


if __name__ == "__main__":
    main()
