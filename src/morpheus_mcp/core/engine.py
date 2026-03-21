"""Gate engine — validates phase evidence and manages plan lifecycle."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone

from morpheus_mcp.core.store import MorpheusStore
from morpheus_mcp.models.enums import Phase, PhaseStatus, PlanStatus, TaskSize, TaskStatus
from morpheus_mcp.models.plan import PhaseRecord, PlanRecord, TaskRecord

# Gate definitions: what evidence each phase requires before advancing.
# CHECK has no gate (it's the entry point).
# Each value is a dict of {evidence_key: description} that must be present.
GATES: dict[Phase, dict[str, str]] = {
    Phase.CHECK: {},
    Phase.CODE: {
        "sibling_read": "Path to sibling file read, or 'N/A' for greenfield",
    },
    Phase.TEST: {
        "build_verified": "Build command output or confirmation",
    },
    Phase.GRADE: {
        "tests_passed": "Test output summary or skip reason",
        "fdmc_review": "FDMC lens one-liner: what you checked or fixed post-code",
    },
    Phase.COMMIT: {
        "seraph_id": "Seraph assessment ID (or 'grade_disabled' if plan has grade=false)",
    },
    Phase.ADVANCE: {
        "knowledge_gate": (
            "sentinel_solution_id, sentinel_verify_id, 'nothing_surprised', "
            "or 'true'/'false' (boolean-style fast-pass)"
        ),
    },
}

# Example evidence for each gated phase — shown in rejection messages so agents
# know the exact format expected, rather than guessing from key names alone.
GATE_EXAMPLES: dict[Phase, str] = {
    Phase.CODE: '{"sibling_read": "src/core/parser.py"}',
    Phase.TEST: '{"build_verified": "python -m py_compile src/main.py — OK"}',
    Phase.GRADE: '{"tests_passed": "12 passed, 0 failed", "fdmc_review": "Consistent — matched existing pattern"}',
    Phase.COMMIT: '{"seraph_id": "a1b2c3d4"}',
    Phase.ADVANCE: '{"knowledge_gate": "nothing_surprised", "knowledge_reason": "followed established pattern from Task 1"}',
}

# Phase ordering for sequence validation
_PHASE_ORDER = list(Phase)


@dataclass(frozen=True, slots=True)
class GateResult:
    """Result of a gate validation check."""

    passed: bool
    message: str


def validate_evidence(
    phase: Phase,
    evidence: dict,
    grade_enabled: bool = True,
    task_size: TaskSize = TaskSize.MEDIUM,
    plan_mode: str = "standard",
    skip_reason: str = "",
) -> GateResult:
    """Validate that evidence satisfies the gate requirements for a phase.

    Args:
        phase: The phase being advanced to completion.
        evidence: Dict of evidence key-value pairs.
        grade_enabled: Whether the plan has grading enabled.
        task_size: Task size tier — affects gate strictness.
        plan_mode: Plan mode — 'greenfield' relaxes sibling_read.
        skip_reason: When provided, fills missing evidence keys with
            "skipped: {reason}" so the gate passes. Recorded in evidence
            for auditability.

    Returns:
        GateResult with passed=True if gate is satisfied, or
        passed=False with a message explaining what's missing.
    """
    required = GATES.get(phase, {})
    if not required:
        return GateResult(passed=True, message="No gate for this phase")

    # Backward compat: if old fdmc_preflight is provided, extract sibling_read
    if phase == Phase.CODE and "fdmc_preflight" in evidence and "sibling_read" not in evidence:
        fdmc = evidence["fdmc_preflight"]
        if isinstance(fdmc, str):
            try:
                fdmc = json.loads(fdmc)
            except (json.JSONDecodeError, TypeError):
                fdmc = {}
        if isinstance(fdmc, dict):
            consistent = fdmc.get("consistent", {})
            if isinstance(consistent, str):
                try:
                    consistent = json.loads(consistent)
                except (json.JSONDecodeError, TypeError):
                    consistent = {}
            if isinstance(consistent, dict) and "sibling_read" in consistent:
                evidence = {**evidence, "sibling_read": consistent["sibling_read"]}

    # skip_reason: fill missing evidence keys so the gate passes, with an auditable value
    if skip_reason:
        skip_value = f"skipped: {skip_reason}"
        for key in required:
            if key not in evidence or not evidence[key]:
                evidence = {**evidence, key: skip_value}

    missing: list[str] = []
    for key, description in required.items():
        # SMALL tasks: skip sibling_read, fdmc_review, seraph_id, knowledge_gate
        if task_size == TaskSize.SMALL:
            if phase == Phase.CODE and key == "sibling_read":
                continue
            if phase == Phase.GRADE and key == "fdmc_review":
                continue
            if phase == Phase.COMMIT and key == "seraph_id":
                continue
            if phase == Phase.ADVANCE and key == "knowledge_gate":
                continue

        # Greenfield mode: sibling_read not required
        if phase == Phase.CODE and key == "sibling_read" and plan_mode == "greenfield":
            continue

        # MEDIUM tasks: COMMIT gate — seraph_id not required if grade disabled
        if phase == Phase.COMMIT and key == "seraph_id" and not grade_enabled:
            # LARGE tasks override: always require seraph_id
            if task_size != TaskSize.LARGE:
                continue

        if key not in evidence or not evidence[key]:
            missing.append(f"'{key}': {description}")

    if missing:
        missing_str = "\n  - ".join(missing)
        example = GATE_EXAMPLES.get(phase, "")
        example_line = f"\n\nExpected format: {example}" if example else ""
        return GateResult(
            passed=False,
            message=f"Gate '{phase.value}' requires:\n  - {missing_str}{example_line}",
        )

    # ADVANCE phase: "nothing_surprised" and "false" require a knowledge_reason
    _REASON_REQUIRED_VALUES = {"nothing_surprised", "false"}
    if (
        phase == Phase.ADVANCE
        and task_size != TaskSize.SMALL
        and evidence.get("knowledge_gate", "") in _REASON_REQUIRED_VALUES
    ):
        reason = evidence.get("knowledge_reason", "")
        if not reason or not reason.strip():
            example = GATE_EXAMPLES.get(Phase.ADVANCE, "")
            return GateResult(
                passed=False,
                message=(
                    f"knowledge_gate='{evidence['knowledge_gate']}' requires a "
                    f"'knowledge_reason' explaining why nothing was learned.\n\n"
                    f"Expected format: {example}"
                ),
            )

    return GateResult(passed=True, message="Gate passed")


def init_plan(
    store: MorpheusStore,
    plan: PlanRecord,
    tasks: list[TaskRecord],
) -> str:
    """Save a plan and its tasks to the store.

    Args:
        store: Open store instance.
        plan: The parsed plan record.
        tasks: The parsed task records.

    Returns:
        The plan ID.
    """
    store.save_plan(plan)
    for task in tasks:
        store.save_task(task)
    store.update_plan_status(plan.id, PlanStatus.ACTIVE)
    return plan.id


def advance(
    store: MorpheusStore,
    task_id: str,
    phase: Phase,
    evidence: dict,
    skip_reason: str = "",
) -> tuple[GateResult, PhaseRecord | None]:
    """Validate gate and record phase completion.

    Args:
        store: Open store instance.
        task_id: The task being advanced.
        phase: The phase being completed.
        evidence: Evidence dict for gate validation.
        skip_reason: When provided, fills missing evidence to bypass the gate.

    Returns:
        Tuple of (GateResult, PhaseRecord if gate passed else None).
    """
    # Look up the task to check plan-level settings
    task = store.get_task(task_id)
    if task is None:
        return GateResult(passed=False, message=f"Task '{task_id}' not found"), None

    # Use the resolved full ID from here on (supports prefix matching)
    full_id = task.id

    plan = store.get_plan(task.plan_id)
    grade_enabled = plan.grade_enabled if plan else True

    # Enforce sequential phase ordering
    if phase != Phase.CHECK:
        prev_phase = _PHASE_ORDER[_PHASE_ORDER.index(phase) - 1]
        completed_phases = store.get_phases(full_id)
        prev_completed = any(
            p.phase == prev_phase and p.status == PhaseStatus.COMPLETED
            for p in completed_phases
        )
        if not prev_completed:
            return GateResult(
                passed=False,
                message=(
                    f"Cannot advance to {phase.value}: "
                    f"previous phase {prev_phase.value} not completed"
                ),
            ), None

    # Validate the gate evidence
    plan_mode = plan.mode if plan else "standard"
    result = validate_evidence(
        phase, evidence, grade_enabled=grade_enabled, task_size=task.size,
        plan_mode=plan_mode, skip_reason=skip_reason,
    )
    if not result.passed:
        # Record the rejection
        rejected = PhaseRecord(
            task_id=full_id,
            phase=phase,
            status=PhaseStatus.REJECTED,
            evidence_json=json.dumps(evidence, default=str),
        )
        store.save_phase(rejected)
        return result, None

    # Gate passed — record completion
    completed = PhaseRecord(
        task_id=full_id,
        phase=phase,
        status=PhaseStatus.COMPLETED,
        evidence_json=json.dumps(evidence, default=str),
        completed_at=datetime.now(timezone.utc),
    )
    store.save_phase(completed)

    # If this is the ADVANCE phase, mark the task as done
    if phase == Phase.ADVANCE:
        store.update_task_status(full_id, TaskStatus.DONE)

    return result, completed


@dataclass(frozen=True, slots=True)
class BatchResult:
    """Result of a batch advance operation."""

    results: list[tuple[str, str, GateResult]]  # (task_id, phase, result)


def advance_batch(
    store: MorpheusStore,
    advances: list[dict],
) -> BatchResult:
    """Process multiple phase advances atomically.

    If any advance fails, all preceding advances in this batch are still
    committed (no rollback) but the batch stops at the first failure.

    Args:
        store: Open store instance.
        advances: List of dicts with keys: task_id, phase, evidence.

    Returns:
        BatchResult with per-advance results.
    """
    results: list[tuple[str, str, GateResult]] = []
    for item in advances:
        task_id = item.get("task_id", "")
        phase_str = item.get("phase", "")
        evidence = item.get("evidence", {})

        try:
            phase = Phase(phase_str.upper())
        except ValueError:
            gate = GateResult(passed=False, message=f"Invalid phase '{phase_str}'")
            results.append((task_id, phase_str, gate))
            break

        result, _ = advance(store, task_id, phase, evidence)
        results.append((task_id, phase.value, result))
        if not result.passed:
            break

    return BatchResult(results=results)


def close_plan(store: MorpheusStore, plan_id: str) -> PlanRecord | None:
    """Mark a plan as completed.

    Args:
        store: Open store instance.
        plan_id: The plan to close.

    Returns:
        The updated PlanRecord, or None if not found.
    """
    plan = store.get_plan(plan_id)
    if plan is None:
        return None

    store.update_plan_status(plan_id, PlanStatus.COMPLETED)
    return store.get_plan(plan_id)
