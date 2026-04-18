"""Markdown formatters for MCP tool responses."""

from __future__ import annotations

from morpheus_mcp.models.enums import Phase, TaskSize, TaskStatus
from morpheus_mcp.models.plan import PhaseRecord, PlanRecord, TaskRecord


_STATUS_ICONS = {
    TaskStatus.PENDING: "pending",
    TaskStatus.IN_PROGRESS: "active",
    TaskStatus.DONE: "done",
    TaskStatus.FAILED: "FAILED",
    TaskStatus.SKIPPED: "skipped",
}


def _count_tasks_by_status(tasks: list[TaskRecord]) -> dict[TaskStatus, int]:
    """Count tasks grouped by status."""
    counts: dict[TaskStatus, int] = {}
    for t in tasks:
        counts[t.status] = counts.get(t.status, 0) + 1
    return counts


def format_plan_summary(plan: PlanRecord, tasks: list[TaskRecord]) -> str:
    """Format a plan summary with task status list."""
    total = len(tasks)
    counts = _count_tasks_by_status(tasks)
    done = counts.get(TaskStatus.DONE, 0)
    failed = counts.get(TaskStatus.FAILED, 0)
    skipped = counts.get(TaskStatus.SKIPPED, 0)
    pct = int(done / total * 100) if total else 0

    mode_tag = f" [{plan.mode}]" if plan.mode != "standard" else ""
    # Surface project in the heading so cross-project bleed is visible at a glance.
    # When `plumbline_status` is called from one project and returns another's plan,
    # the heading itself makes the mismatch obvious without line-by-line scanning.
    project_tag = f" · {plan.project}" if plan.project else ""
    lines = [
        f"## Plan: {plan.name}{mode_tag}{project_tag}",
        f"**ID:** `{plan.id}`",
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

    # Show size column only if any task has non-default size
    has_sizes = any(t.size != TaskSize.MEDIUM for t in tasks)

    lines.append("### Tasks")
    lines.append("")
    if has_sizes:
        lines.append("| # | Task | ID | Size | Status |")
        lines.append("|---|------|----|------|--------|")
    else:
        lines.append("| # | Task | ID | Status |")
        lines.append("|---|------|----|--------|")
    for t in tasks:
        icon = _STATUS_ICONS.get(t.status, "?")
        marker = " **<-- next**" if t.id == next_task_id else ""
        if has_sizes:
            lines.append(f"| {t.seq} | {t.title}{marker} | `{t.id[:12]}` | {t.size.value} | {icon} |")
        else:
            lines.append(f"| {t.seq} | {t.title}{marker} | `{t.id[:12]}` | {icon} |")
    lines.append("")
    lines.append("*Use the `ID` column values for `morpheus_advance(task_id, ...)`*")

    if failed or skipped:
        lines.append("")
        lines.append(f"**Summary:** {done} done, {failed} failed, {skipped} skipped")

    return "\n".join(lines)


def format_status(
    plan: PlanRecord,
    tasks: list[TaskRecord],
    phases_by_task: dict[str, list[PhaseRecord]],
    progress_by_task: dict[str, list[tuple[str, str, str]]] | None = None,
) -> str:
    """Format detailed status with phase and progress information."""
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

        # Show recent progress entries
        if progress_by_task:
            entries = progress_by_task.get(t.id, [])
            if entries:
                lines.append("")
                lines.append("**Recent progress:**")
                for _id, message, created_at in entries:
                    ts = created_at.split("T")[1][:8] if "T" in created_at else created_at
                    lines.append(f"  - [{ts}] {message}")

    return "\n".join(lines)


def format_advance_success(
    phase: Phase,
    task: TaskRecord,
    phase_order: list[Phase] | None = None,
    extra_message: str = "",
) -> str:
    """Format a successful phase advance.

    Args:
        phase: The phase that just passed.
        task: The task being advanced.
        phase_order: Active phase order (standard or verify). Defaults to standard.
        extra_message: Optional additional text (e.g., gate skip recommendations).
    """
    size_note = f" [{task.size.value}]" if task.size != TaskSize.MEDIUM else ""
    phases = phase_order if phase_order else list(Phase)

    if phase in phases:
        phase_idx = phases.index(phase)
    else:
        phase_idx = len(phases) - 1

    suffix = ""
    if extra_message:
        suffix = f"\n\n{extra_message}"

    if phase_idx + 1 < len(phases):
        next_phase = phases[phase_idx + 1]
        return (
            f"**{phase.value}** gate passed for task {task.seq}.{size_note} {task.title}\n\n"
            f"Next phase: **{next_phase.value}**{suffix}"
        )
    return (
        f"**{phase.value}** gate passed for task {task.seq}.{size_note} {task.title}\n\n"
        f"Task complete.{suffix}"
    )


def format_advance_rejection(phase: Phase, message: str) -> str:
    """Format a gate rejection."""
    return f"**REJECTED** — {phase.value} gate\n\n{message}"


def format_close_summary(
    plan: PlanRecord, tasks: list[TaskRecord]
) -> str:
    """Format plan closure summary."""
    total = len(tasks)
    counts = _count_tasks_by_status(tasks)
    done = counts.get(TaskStatus.DONE, 0)
    failed = counts.get(TaskStatus.FAILED, 0)
    skipped = counts.get(TaskStatus.SKIPPED, 0)

    # Size distribution
    sizes = {s: sum(1 for t in tasks if t.size == s) for s in TaskSize}
    size_parts = [f"{s.value}: {n}" for s, n in sizes.items() if n > 0]
    size_line = f"**Task sizes:** {', '.join(size_parts)}\n" if len(size_parts) > 1 else ""

    return (
        f"## Plan Complete: {plan.name}\n\n"
        f"**Result:** {done}/{total} tasks done, "
        f"{failed} failed, {skipped} skipped\n"
        f"{size_line}"
        f"**Closed at:** {plan.closed_at.isoformat() if plan.closed_at else 'N/A'}"
    )
