"""Markdown formatters for MCP tool responses."""

from __future__ import annotations

from morpheus_mcp.models.enums import Phase, TaskStatus
from morpheus_mcp.models.plan import PhaseRecord, PlanRecord, TaskRecord


_STATUS_ICONS = {
    TaskStatus.PENDING: "pending",
    TaskStatus.IN_PROGRESS: "active",
    TaskStatus.DONE: "done",
    TaskStatus.FAILED: "FAILED",
    TaskStatus.SKIPPED: "skipped",
}


def format_plan_summary(plan: PlanRecord, tasks: list[TaskRecord]) -> str:
    """Format a plan summary with task status list."""
    total = len(tasks)
    done = sum(1 for t in tasks if t.status == TaskStatus.DONE)
    failed = sum(1 for t in tasks if t.status == TaskStatus.FAILED)
    skipped = sum(1 for t in tasks if t.status == TaskStatus.SKIPPED)
    pct = int(done / total * 100) if total else 0

    lines = [
        f"## Plan: {plan.name}",
        f"**ID:** `{plan.id[:12]}`",
        f"**Project:** {plan.project}",
        f"**Progress:** {done}/{total} tasks ({pct}%)",
        "",
    ]

    # Find the next pending task
    next_task_id = None
    for t in tasks:
        if t.status == TaskStatus.PENDING:
            next_task_id = t.id
            break

    for t in tasks:
        icon = _STATUS_ICONS.get(t.status, "?")
        marker = "  <-- next" if t.id == next_task_id else ""
        lines.append(f"  [{icon}] {t.seq}. {t.title}{marker}")

    if failed or skipped:
        lines.append("")
        lines.append(f"**Summary:** {done} done, {failed} failed, {skipped} skipped")

    return "\n".join(lines)


def format_status(
    plan: PlanRecord,
    tasks: list[TaskRecord],
    phases_by_task: dict[str, list[PhaseRecord]],
) -> str:
    """Format detailed status with phase information."""
    lines = [format_plan_summary(plan, tasks), ""]

    # Show phase detail for the active task
    active = [t for t in tasks if t.status == TaskStatus.IN_PROGRESS]
    if active:
        t = active[0]
        phases = phases_by_task.get(t.id, [])
        lines.append(f"### Active: {t.seq}. {t.title}")
        if phases:
            for p in phases:
                lines.append(f"  - {p.phase.value}: {p.status.value}")
        else:
            lines.append("  No phases recorded yet")

    return "\n".join(lines)


def format_advance_success(phase: Phase, task: TaskRecord) -> str:
    """Format a successful phase advance."""
    phase_idx = list(Phase).index(phase)
    phases = list(Phase)
    if phase_idx + 1 < len(phases):
        next_phase = phases[phase_idx + 1]
        return (
            f"**{phase.value}** gate passed for task {task.seq}. {task.title}\n\n"
            f"Next phase: **{next_phase.value}**"
        )
    return (
        f"**{phase.value}** gate passed for task {task.seq}. {task.title}\n\n"
        f"Task complete."
    )


def format_advance_rejection(phase: Phase, message: str) -> str:
    """Format a gate rejection."""
    return f"**REJECTED** — {phase.value} gate\n\n{message}"


def format_close_summary(
    plan: PlanRecord, tasks: list[TaskRecord]
) -> str:
    """Format plan closure summary."""
    total = len(tasks)
    done = sum(1 for t in tasks if t.status == TaskStatus.DONE)
    failed = sum(1 for t in tasks if t.status == TaskStatus.FAILED)
    skipped = sum(1 for t in tasks if t.status == TaskStatus.SKIPPED)

    return (
        f"## Plan Complete: {plan.name}\n\n"
        f"**Result:** {done}/{total} tasks done, "
        f"{failed} failed, {skipped} skipped\n"
        f"**Closed at:** {plan.closed_at.isoformat() if plan.closed_at else 'N/A'}"
    )
