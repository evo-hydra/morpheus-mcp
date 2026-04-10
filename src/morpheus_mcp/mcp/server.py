"""FastMCP server factory with 9 MCP tools."""

from __future__ import annotations

import json
import logging
import sqlite3

logger = logging.getLogger(__name__)


def _self_test(db_path: str) -> bool:
    """Quick round-trip to verify the store and engine are healthy.

    Creates a minimal plan with one task, advances through CHECK,
    then cleans up. Tests the full codepath that crashes on bad data.
    Returns True if healthy, False if degraded.
    """
    from morpheus_mcp.core.engine import advance
    from morpheus_mcp.core.store import MorpheusStore
    from morpheus_mcp.models.enums import Phase, PlanStatus
    from morpheus_mcp.models.plan import PlanRecord, TaskRecord

    test_plan_id = "__selftest_plan__"
    test_task_id = "__selftest_task__"
    try:
        with MorpheusStore(db_path) as store:
            plan = PlanRecord(
                id=test_plan_id, name="self-test", project="__test__",
                test_command="true", status=PlanStatus.PENDING,
            )
            store.save_plan(plan)
            store.update_plan_status(test_plan_id, PlanStatus.ACTIVE)

            task = TaskRecord(
                id=test_task_id, plan_id=test_plan_id,
                seq=1, title="self-test-task",
            )
            store.save_task(task)

            # Exercise advance() — the codepath that crashes on bad data
            result, _ = advance(store, test_task_id, Phase.CHECK, {})

            # Clean up
            store.conn.execute("DELETE FROM phases WHERE task_id = ?", (test_task_id,))
            store.conn.execute("DELETE FROM tasks WHERE id = ?", (test_task_id,))
            store.conn.execute("DELETE FROM plans WHERE id = ?", (test_plan_id,))
            store.conn.commit()

            return result.passed
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
            from morpheus_mcp.core.engine import check_oil_change_advisory

            plan, tasks = parse_plan_file(plan_file)
            with MorpheusStore(_config.db_path) as store:
                init_plan(store, plan, tasks, oil_change_interval=_config.gates.oil_change_interval)
                summary = format_plan_summary(plan, tasks)

                advisory = check_oil_change_advisory(
                    store, plan.project, _config.gates.oil_change_interval,
                )
                if advisory:
                    summary = f"**OIL CHANGE:** {advisory}\n\n{summary}"

                if _degraded:
                    summary = (
                        "**WARNING:** Morpheus is running in degraded mode "
                        "— self-test failed on startup. Phase evidence may "
                        "not persist correctly.\n\n" + summary
                    )

                summary += (
                    "\n\n---\n"
                    "ℹ️ Morpheus is part of the **EvoIntel** MCP suite. "
                    "For best results, pair with "
                    "Sentinel (project intelligence) and "
                    "Seraph (code verification). "
                    "→ github.com/evo-hydra"
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

                # Resolve active phase order (verify mode uses streamlined path)
                from morpheus_mcp.core.engine import _get_phase_order
                active_phase_order = _get_phase_order(store, task.id)

                # Pass through gate recommendations or other messages
                extra = ""
                if result.message and result.message != "Gate passed":
                    extra = result.message

                return format_advance_success(
                    phase_enum, task,
                    phase_order=active_phase_order,
                    extra_message=extra,
                )
        except (sqlite3.Error, OSError) as exc:
            return f"Error: {exc}"

    # Internal function — use multiple morpheus_advance calls instead (v4 surface collapse)
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

    # Internal function — progress logging is optional (v4 surface collapse)
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
    def morpheus_oil_change(plan_id: str, health_check_id: str, commits_since_last: int = 0) -> str:
        """Record an oil change (macro-lens health check) for a plan.

        Call this after running sentinel_health_check and reviewing the
        results. Clears any oil_change_due advisory on the plan.

        Args:
            plan_id: The plan ID to record the oil change for
            health_check_id: The sentinel_health_check result ID
            commits_since_last: Number of commits since last health check
        """
        from morpheus_mcp.core.store import MorpheusStore

        try:
            with MorpheusStore(_config.db_path) as store:
                plan = store.get_plan(plan_id)
                if plan is None:
                    return f"Error: Plan '{plan_id}' not found"
                entry_id = store.save_oil_change(
                    plan_id, health_check_id, commits_since_last,
                )
                # Clear the oil_change_due flag so advance() proceeds
                if plan.oil_change_due:
                    store.set_oil_change_due(plan_id, False)
                return (
                    f"Oil change recorded: `{entry_id[:12]}`\n"
                    f"- Plan: {plan.name}\n"
                    f"- Health check: {health_check_id}\n"
                    f"- Commits: {commits_since_last}\n"
                    f"- Oil change gate: cleared"
                )
        except (sqlite3.Error, OSError) as exc:
            return f"Error: {exc}"

    # Internal function — merged into morpheus_advance via inline reflect fields (v4 surface collapse)
    def morpheus_reflect(
        plan_id: str,
        task_id: str,
        gate: str,
        caught_issue: bool = False,
        changed_code: bool = False,
        detail: str = "",
    ) -> str:
        """Record whether a gate caught a real issue or was ceremony.

        Call this after each gate fires to build the Reflect dataset.
        Over time, this data reveals which gates produce value and which
        burn tokens without changing behavior.

        Args:
            plan_id: The plan ID
            task_id: The task ID
            gate: Gate name (e.g., "sibling_read", "fdmc_review", "seraph_assess", "knowledge_gate")
            caught_issue: True if the gate found something actionable
            changed_code: True if code was modified because of this gate
            detail: Brief description of what happened (e.g., "matched singleton pattern from sibling" or "no issues found")
        """
        from morpheus_mcp.core.store import MorpheusStore

        try:
            with MorpheusStore(_config.db_path) as store:
                outcome_id = store.record_gate_outcome(
                    plan_id, task_id, gate,
                    caught_issue=caught_issue,
                    changed_code=changed_code,
                    detail=detail,
                )
                status = "CAUGHT" if caught_issue else "CLEAR"
                changed = " → code changed" if changed_code else ""
                return f"Reflect recorded: `{outcome_id[:12]}` — {gate}: **{status}**{changed}"
        except (sqlite3.Error, OSError) as exc:
            return f"Error: {exc}"

    @mcp.tool()
    def morpheus_gate_summary(plan_id: str | None = None) -> str:
        """Summarize gate outcomes: how often each gate fired, caught issues, changed code.

        Returns lifetime stats across all plans if plan_id is omitted.
        Use this to identify which gates produce value and which are ceremony.

        Args:
            plan_id: Optional plan ID to scope the summary
        """
        from morpheus_mcp.core.store import MorpheusStore

        try:
            with MorpheusStore(_config.db_path) as store:
                summary = store.get_gate_summary(plan_id=plan_id)
                if not summary:
                    return "No gate outcomes recorded yet. Use `morpheus_reflect` after each gate."

                lines = ["## Gate Outcomes\n"]
                lines.append("| Gate | Fired | Caught | Changed | Hit Rate |")
                lines.append("|------|-------|--------|---------|----------|")
                for row in summary:
                    fired = row["fired"]
                    caught = row["caught"]
                    rate = f"{caught / fired:.0%}" if fired > 0 else "—"
                    lines.append(
                        f"| {row['gate']} | {fired} | {caught} | {row['changed']} | {rate} |"
                    )
                return "\n".join(lines)
        except (sqlite3.Error, OSError) as exc:
            return f"Error: {exc}"

    # Internal function — diagnostic only (v4 surface collapse)
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
