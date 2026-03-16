"""Gate engine — validates phase evidence and manages plan lifecycle."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone

from morpheus_mcp.core.store import MorpheusStore
from morpheus_mcp.models.enums import Phase, PhaseStatus, PlanStatus, TaskStatus
from morpheus_mcp.models.plan import PhaseRecord, PlanRecord, TaskRecord

# Gate definitions: what evidence each phase requires before advancing.
# CHECK has no gate (it's the entry point).
# Each value is a dict of {evidence_key: description} that must be present.
GATES: dict[Phase, dict[str, str]] = {
    Phase.CHECK: {},
    Phase.CODE: {
        "fdmc_preflight": (
            "Dict with keys: consistent (must include sibling_read), "
            "future_proof, dynamic, modular"
        ),
    },
    Phase.TEST: {
        "build_verified": "Build command output or confirmation",
    },
    Phase.GRADE: {
        "tests_passed": "Test output summary or skip reason",
    },
    Phase.COMMIT: {
        "seraph_id": "Seraph assessment ID (or 'grade_disabled' if plan has grade=false)",
    },
    Phase.ADVANCE: {
        "knowledge_gate": (
            "sentinel_solution_id, sentinel_verify_id, or 'nothing_surprised'"
        ),
    },
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
) -> GateResult:
    """Validate that evidence satisfies the gate requirements for a phase.

    Args:
        phase: The phase being advanced to completion.
        evidence: Dict of evidence key-value pairs.
        grade_enabled: Whether the plan has grading enabled.

    Returns:
        GateResult with passed=True if gate is satisfied, or
        passed=False with a message explaining what's missing.
    """
    required = GATES.get(phase, {})
    if not required:
        return GateResult(passed=True, message="No gate for this phase")

    missing: list[str] = []
    for key, description in required.items():
        # Special case: COMMIT gate — seraph_id not required if grade disabled
        if phase == Phase.COMMIT and key == "seraph_id" and not grade_enabled:
            continue

        if key not in evidence or not evidence[key]:
            missing.append(f"'{key}': {description}")

    if missing:
        missing_str = "\n  - ".join(missing)
        return GateResult(
            passed=False,
            message=f"Gate '{phase.value}' requires:\n  - {missing_str}",
        )

    # Validate FDMC preflight structure (CODE gate)
    if phase == Phase.CODE and "fdmc_preflight" in evidence:
        fdmc = evidence["fdmc_preflight"]
        if isinstance(fdmc, str):
            try:
                fdmc = json.loads(fdmc)
            except (json.JSONDecodeError, TypeError):
                return GateResult(
                    passed=False,
                    message="Gate 'CODE': fdmc_preflight must be a JSON dict",
                )

        if isinstance(fdmc, dict):
            required_lenses = {"consistent", "future_proof", "dynamic", "modular"}
            present = set(fdmc.keys())
            lens_missing = required_lenses - present
            if lens_missing:
                return GateResult(
                    passed=False,
                    message=f"Gate 'CODE': fdmc_preflight missing lenses: {lens_missing}",
                )

            # Consistent lens must include sibling_read
            consistent = fdmc.get("consistent", {})
            if isinstance(consistent, str):
                try:
                    consistent = json.loads(consistent)
                except (json.JSONDecodeError, TypeError):
                    consistent = {"note": consistent}

            if isinstance(consistent, dict) and "sibling_read" not in consistent:
                return GateResult(
                    passed=False,
                    message=(
                        "Gate 'CODE': fdmc_preflight.consistent must include "
                        "'sibling_read' (the file path you read)"
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
) -> tuple[GateResult, PhaseRecord | None]:
    """Validate gate and record phase completion.

    Args:
        store: Open store instance.
        task_id: The task being advanced.
        phase: The phase being completed.
        evidence: Evidence dict for gate validation.

    Returns:
        Tuple of (GateResult, PhaseRecord if gate passed else None).
    """
    # Look up the task to check plan-level settings
    task = store.get_task(task_id)
    if task is None:
        return GateResult(passed=False, message=f"Task '{task_id}' not found"), None

    plan = store.get_plan(task.plan_id)
    grade_enabled = plan.grade_enabled if plan else True

    # Validate the gate
    result = validate_evidence(phase, evidence, grade_enabled=grade_enabled)
    if not result.passed:
        # Record the rejection
        rejected = PhaseRecord(
            task_id=task_id,
            phase=phase,
            status=PhaseStatus.REJECTED,
            evidence_json=json.dumps(evidence, default=str),
        )
        store.save_phase(rejected)
        return result, None

    # Gate passed — record completion
    completed = PhaseRecord(
        task_id=task_id,
        phase=phase,
        status=PhaseStatus.COMPLETED,
        evidence_json=json.dumps(evidence, default=str),
        completed_at=datetime.now(timezone.utc),
    )
    store.save_phase(completed)

    # If this is the ADVANCE phase, mark the task as done
    if phase == Phase.ADVANCE:
        store.update_task_status(task_id, TaskStatus.DONE)

    return result, completed


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
