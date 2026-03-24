"""FastMCP server factory with 7 MCP tools."""

from __future__ import annotations

import json
import logging
import sqlite3

logger = logging.getLogger(__name__)


def _self_test(db_path: str) -> bool:
    """Quick round-trip to verify the store is healthy.

    Creates a minimal plan, reads it back, deletes it.
    Returns True if healthy, False if degraded.
    """
    from morpheus_mcp.core.store import MorpheusStore
    from morpheus_mcp.models.enums import PlanStatus
    from morpheus_mcp.models.plan import PlanRecord

    test_id = "__selftest__"
    try:
        with MorpheusStore(db_path) as store:
            plan = PlanRecord(
                id=test_id, name="self-test", project="__test__",
                test_command="true", status=PlanStatus.PENDING,
            )
            store.save_plan(plan)
            retrieved = store.get_plan(test_id)
            store.conn.execute("DELETE FROM plans WHERE id = ?", (test_id,))
            store.conn.commit()
            return retrieved is not None
    except Exception:
        logger.warning("Morpheus self-test failed — running in degraded mode", exc_info=True)
        return False


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
    _degraded = not _self_test(_config.db_path)

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
                summary = format_plan_summary(plan, tasks)
                if _degraded:
                    summary = (
                        "**WARNING:** Morpheus is running in degraded mode "
                        "— self-test failed on startup. Phase evidence may "
                        "not persist correctly.\n\n" + summary
                    )
                return summary
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
                # Include progress for active tasks
                active = [t for t in tasks if t.status.value == "in_progress"]
                progress_by_task = {
                    t.id: store.get_progress(t.id) for t in active
                }
                return format_status(plan, tasks, phases_by_task, progress_by_task)
        except (sqlite3.Error, OSError) as exc:
            return f"Error: {exc}"

    @mcp.tool()
    def morpheus_advance(task_id: str, phase: str, evidence: str = "{}", skip_reason: str = "") -> str:
        """Advance a task through a phase gate with evidence.

        Validates that the evidence satisfies the gate requirements.
        Returns success with next phase instructions, or rejection
        with details on what's missing.

        Args:
            task_id: The task ID to advance
            phase: Phase name: CHECK, CODE, TEST, GRADE, COMMIT, or ADVANCE
            evidence: JSON string with evidence key-value pairs
            skip_reason: When provided, fills missing evidence keys to bypass
                the gate. Use for intentional skips (e.g., "greenfield — no
                diff for Seraph"). Recorded in evidence for auditability.
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
                result, phase_record = advance(
                    store, task_id, phase_enum, evidence_dict,
                    skip_reason=skip_reason,
                    knowledge_gate_task_threshold=_config.gates.knowledge_gate_task_threshold,
                )

                if not result.passed:
                    return format_advance_rejection(phase_enum, result.message)

                task = store.get_task(task_id)
                if task is None:
                    return f"Error: Task '{task_id}' not found after advance"

                return format_advance_success(phase_enum, task)
        except (sqlite3.Error, OSError) as exc:
            return f"Error: {exc}"

    @mcp.tool()
    def morpheus_advance_batch(advances: str) -> str:
        """Advance multiple tasks through phase gates in a single call.

        Processes each advance sequentially. Stops at the first failure.
        Accepts a JSON array of objects with keys: task_id, phase, evidence.

        Args:
            advances: JSON array of {task_id, phase, evidence} objects
        """
        from morpheus_mcp.core.engine import advance_batch
        from morpheus_mcp.core.store import MorpheusStore

        try:
            items = json.loads(advances) if isinstance(advances, str) else advances
        except json.JSONDecodeError as exc:
            return f"Error: Invalid JSON: {exc}"

        if not isinstance(items, list) or not items:
            return "Error: advances must be a non-empty JSON array"

        try:
            with MorpheusStore(_config.db_path) as store:
                batch = advance_batch(store, items)

                lines = []
                for task_id, phase, result in batch.results:
                    status = "PASSED" if result.passed else "REJECTED"
                    lines.append(f"- `{task_id[:12]}` {phase}: **{status}**")
                    if not result.passed:
                        lines.append(f"  {result.message}")

                header = f"## Batch Advance: {len(batch.results)} processed"
                return f"{header}\n\n" + "\n".join(lines)
        except (sqlite3.Error, OSError) as exc:
            return f"Error: {exc}"

    @mcp.tool()
    def morpheus_progress(task_id: str, message: str) -> str:
        """Log progress for a task without advancing phases.

        Purely observational — records a timestamped message for the task.
        Visible in morpheus_status output for the active task.

        Args:
            task_id: The task ID to log progress for
            message: Progress message to record
        """
        from morpheus_mcp.core.store import MorpheusStore

        try:
            with MorpheusStore(_config.db_path) as store:
                task = store.get_task(task_id)
                if task is None:
                    return f"Error: Task '{task_id}' not found"
                entry_id = store.save_progress(task.id, message)
                return f"Progress logged: `{entry_id[:12]}` — {message}"
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

    @mcp.tool()
    def morpheus_version() -> str:
        """Return Morpheus server version, plan schema version, and Python version.

        No arguments required. Use for introspection and compatibility checks.
        """
        import sys

        from morpheus_mcp import __version__
        from morpheus_mcp.core.store import SCHEMA_VERSION

        return json.dumps({
            "server_version": __version__,
            "schema_version": SCHEMA_VERSION,
            "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        })

    return mcp


def main() -> None:
    """Entry point for morpheus-mcp."""
    server = create_server()
    server.run()


if __name__ == "__main__":
    main()
