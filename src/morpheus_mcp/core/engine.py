"""Gate engine — validates phase evidence and manages plan lifecycle."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

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
        "seraph_id": "Seraph assessment ID, 'grade_disabled', or 'seraph_unavailable'",
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
    Phase.COMMIT: '{"seraph_id": "a1b2c3d4"} or {"seraph_id": "seraph_unavailable"}',
    Phase.ADVANCE: '{"knowledge_gate": "nothing_surprised", "knowledge_reason": "followed established pattern from Task 1"}',
}

# Phase ordering for sequence validation
_PHASE_ORDER = list(Phase)

# Verify mode: streamlined path for pre-implemented tasks (CHECK → TEST → ADVANCE)
_VERIFY_PHASE_ORDER = [Phase.CHECK, Phase.TEST, Phase.ADVANCE]

# Map phases to gate names for inline reflect recording
_PHASE_TO_GATE: dict[Phase, str] = {
    Phase.CODE: "sibling_read",
    Phase.TEST: "build_verified",
    Phase.GRADE: "fdmc_review",
    Phase.COMMIT: "seraph_assess",
    Phase.ADVANCE: "knowledge_gate",
}


def _is_verify_mode(store: MorpheusStore, task_id: str) -> bool:
    """Check if a task entered verify mode during CHECK.

    A task is in verify mode when its CHECK phase was completed with
    evidence containing ``"status": "pre_implemented"``. This switches
    the task to a streamlined path: CHECK → TEST → ADVANCE, skipping
    CODE, GRADE, and COMMIT gates entirely.
    """
    phases = store.get_phases(task_id)
    for phase_rec in phases:
        if phase_rec.phase == Phase.CHECK and phase_rec.status == PhaseStatus.COMPLETED:
            try:
                evidence = json.loads(phase_rec.evidence_json)
            except (json.JSONDecodeError, TypeError):
                evidence = {}
            if evidence.get("status") == "pre_implemented":
                return True
    return False


def _get_phase_order(store: MorpheusStore, task_id: str) -> list[Phase]:
    """Return the phase ordering for a task — standard or verify."""
    if _is_verify_mode(store, task_id):
        return _VERIFY_PHASE_ORDER
    return _PHASE_ORDER


@dataclass(frozen=True, slots=True)
class GateResult:
    """Result of a gate validation check."""

    passed: bool
    message: str


# Named return type for advance() — provides type safety for callers.
AdvanceResult = tuple[GateResult, PhaseRecord | None]


def validate_evidence(
    phase: Phase,
    evidence: dict[str, Any],
    grade_enabled: bool = True,
    task_size: TaskSize = TaskSize.MEDIUM,
    plan_mode: str = "standard",
    task_count: int = 0,
    knowledge_gate_task_threshold: int = 5,
    test_command: str = "",
) -> GateResult:
    """Validate that evidence satisfies the gate requirements for a phase.

    Args:
        phase: The phase being advanced to completion.
        evidence: Dict of evidence key-value pairs.
        grade_enabled: Whether the plan has grading enabled.
        task_size: Task size tier — affects gate strictness.
        plan_mode: Plan mode — 'greenfield' relaxes sibling_read.
        task_count: Total tasks in the plan — small plans relax knowledge gate.
        knowledge_gate_task_threshold: Plans below this count skip knowledge gate.
        test_command: Plan test command — 'none' skips test-related gates.

    Returns:
        GateResult with passed=True if gate is satisfied, or
        passed=False with a message explaining what's missing.
    """
    required = GATES.get(phase, {})
    if not required:
        return GateResult(passed=True, message="No gate for this phase")

    # MICRO tasks: all gates accept empty evidence — zero ceremony
    if task_size == TaskSize.MICRO:
        return GateResult(passed=True, message="MICRO task — gate skipped")

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

    missing: list[str] = []
    for key, description in required.items():
        # SMALL tasks: skip sibling_read, build_verified, fdmc_review, seraph_id, knowledge_gate
        if task_size == TaskSize.SMALL:
            if phase == Phase.CODE and key == "sibling_read":
                continue
            if phase == Phase.TEST and key == "build_verified":
                continue
            if phase == Phase.GRADE and key == "fdmc_review":
                continue
            if phase == Phase.COMMIT and key == "seraph_id":
                continue
            if phase == Phase.ADVANCE and key == "knowledge_gate":
                continue

        # test_command: none — skip test-related evidence when there's nothing to test
        if test_command.lower() == "none":
            if phase == Phase.TEST and key == "build_verified":
                continue
            if phase == Phase.GRADE and key == "tests_passed":
                continue

        # Small plans: skip knowledge_gate when plan has fewer tasks than threshold
        if (
            phase == Phase.ADVANCE
            and key == "knowledge_gate"
            and 0 < task_count < knowledge_gate_task_threshold
        ):
            continue

        # Greenfield mode: sibling_read not required
        if phase == Phase.CODE and key == "sibling_read" and plan_mode == "greenfield":
            continue

        # MEDIUM tasks: COMMIT gate — seraph_id not required if grade disabled
        if phase == Phase.COMMIT and key == "seraph_id" and not grade_enabled:
            # LARGE tasks override: always require seraph_id
            if task_size != TaskSize.LARGE:
                continue

        # COMMIT gate: accept "seraph_unavailable" for non-LARGE tasks
        if (
            phase == Phase.COMMIT
            and key == "seraph_id"
            and evidence.get("seraph_id") == "seraph_unavailable"
            and task_size != TaskSize.LARGE
        ):
            continue

        if key not in evidence or not evidence[key]:
            missing.append(f"'{key}': {description}")

    if missing:
        missing_str = "\n  - ".join(missing)
        provided_keys = sorted(k for k in evidence if evidence[k])
        provided_line = ", ".join(provided_keys) if provided_keys else "(none)"
        expected_keys = ", ".join(required.keys())
        example = GATE_EXAMPLES.get(phase, "")
        example_line = f"\n\nExpected format: {example}" if example else ""
        return GateResult(
            passed=False,
            message=(
                f"Gate '{phase.value}' expected keys: [{expected_keys}]. "
                f"You provided: [{provided_line}]. "
                f"Missing:\n  - {missing_str}{example_line}"
            ),
        )

    # COMMIT phase: LARGE tasks reject "seraph_unavailable" — they need a real ID
    if (
        phase == Phase.COMMIT
        and task_size == TaskSize.LARGE
        and evidence.get("seraph_id") == "seraph_unavailable"
    ):
        return GateResult(
            passed=False,
            message=(
                "LARGE tasks require a real Seraph assessment ID — "
                "'seraph_unavailable' is only accepted for SMALL/MEDIUM tasks."
            ),
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
    oil_change_interval: int = 40,
) -> str:
    """Save a plan and its tasks to the store.

    If commits since last oil change exceed the interval, sets
    oil_change_due on the plan so advance() can enforce it.

    Args:
        store: Open store instance.
        plan: The parsed plan record.
        tasks: The parsed task records.
        oil_change_interval: Commit threshold for oil change advisory.

    Returns:
        The plan ID.
    """
    store.save_plan(plan)
    for task in tasks:
        store.save_task(task)

    # Check if oil change is due and set the flag
    advisory = check_oil_change_advisory(store, plan.project, oil_change_interval)
    if advisory:
        store.set_oil_change_due(plan.id, True)

    store.update_plan_status(plan.id, PlanStatus.ACTIVE)
    return plan.id


def check_oil_change_advisory(
    store: MorpheusStore,
    project: str,
    oil_change_interval: int = 40,
) -> str | None:
    """Check if an oil change is recommended for this project.

    Returns an advisory message if commits since last oil change exceed
    the interval, or None if the project is within the interval.
    """
    last = store.get_last_oil_change(project)
    if last is None:
        return None  # No oil change history — can't advise yet
    commit_count = last["commit_count"]
    if commit_count >= oil_change_interval:
        return (
            f"Oil change recommended: {commit_count} commits since last "
            f"health check (threshold: {oil_change_interval}). Run "
            f"`sentinel_health_check`, review results, then call "
            f"`morpheus_oil_change`."
        )
    return None


def advance(
    store: MorpheusStore,
    task_id: str,
    phase: Phase,
    evidence: dict[str, Any],
    skip_reason: str = "",
    knowledge_gate_task_threshold: int = 5,
) -> AdvanceResult:
    """Validate gate and record phase completion.

    Args:
        store: Open store instance.
        task_id: The task being advanced.
        phase: The phase being completed.
        evidence: Evidence dict for gate validation.
        skip_reason: When provided, fills missing evidence to bypass the gate.
        knowledge_gate_task_threshold: Plans below this task count skip knowledge gate.

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

    # Oil change enforcement: reject CHECK on first task when oil_change_due
    if phase == Phase.CHECK and plan and plan.oil_change_due:
        tasks = store.get_tasks(plan.id)
        first_task = tasks[0] if tasks else None
        if first_task and first_task.id == full_id:
            return GateResult(
                passed=False,
                message=(
                    "Oil change required before starting plan. Run "
                    "`sentinel_health_check`, review results, then call "
                    "`morpheus_oil_change` to clear the gate."
                ),
            ), None

    # Enforce sequential phase ordering (verify mode uses streamlined path)
    if phase != Phase.CHECK:
        phase_order = _get_phase_order(store, full_id)

        # Reject phases not in the active path (e.g., CODE in verify mode)
        if phase not in phase_order:
            return GateResult(
                passed=False,
                message=(
                    f"Phase {phase.value} is not valid in verify mode. "
                    f"Pre-implemented tasks follow: CHECK → TEST → ADVANCE"
                ),
            ), None

        prev_phase = phase_order[phase_order.index(phase) - 1]
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

    # skip_reason: fill missing evidence keys before validation so they're
    # captured in the serialized phase record for auditability
    if skip_reason:
        required = GATES.get(phase, {})
        skip_value = f"skipped: {skip_reason}"
        for key in required:
            if key not in evidence or not evidence[key]:
                evidence[key] = skip_value

    # Validate the gate evidence
    plan_mode = plan.mode if plan else "standard"
    plan_test_command = plan.test_command if plan else ""
    task_count = len(store.get_tasks(task.plan_id)) if plan else 0
    result = validate_evidence(
        phase, evidence, grade_enabled=grade_enabled, task_size=task.size,
        plan_mode=plan_mode, task_count=task_count,
        knowledge_gate_task_threshold=knowledge_gate_task_threshold,
        test_command=plan_test_command,
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

    # Extract inline reflect data before saving evidence.
    # Agents can embed reflect_caught_issue, reflect_changed_code, and
    # reflect_detail directly in the evidence dict instead of making a
    # separate morpheus_reflect call. This is the primary path — the
    # standalone reflect tool is kept as a fallback.
    reflect_caught = evidence.pop("reflect_caught_issue", None)
    reflect_changed = evidence.pop("reflect_changed_code", None)
    reflect_detail = evidence.pop("reflect_detail", None)

    # Gate passed — record completion
    completed = PhaseRecord(
        task_id=full_id,
        phase=phase,
        status=PhaseStatus.COMPLETED,
        evidence_json=json.dumps(evidence, default=str),
        completed_at=datetime.now(timezone.utc),
    )
    store.save_phase(completed)

    # Auto-record gate outcome if inline reflect data was provided.
    if reflect_caught is not None and plan is not None:
        gate_name = _PHASE_TO_GATE.get(phase, phase.value)
        store.record_gate_outcome(
            plan_id=plan.id,
            task_id=full_id,
            gate=gate_name,
            caught_issue=bool(reflect_caught),
            changed_code=bool(reflect_changed) if reflect_changed is not None else False,
            detail=str(reflect_detail or ""),
        )

    # Update task status based on phase progression
    if phase == Phase.CHECK:
        store.update_task_status(full_id, TaskStatus.IN_PROGRESS)
    elif phase == Phase.ADVANCE:
        store.update_task_status(full_id, TaskStatus.DONE)

    return result, completed


@dataclass(frozen=True, slots=True)
class BatchResult:
    """Result of a batch advance operation."""

    results: list[tuple[str, str, GateResult]]  # (task_id, phase, result)


def advance_batch(
    store: MorpheusStore,
    advances: list[dict[str, Any]],
) -> BatchResult:
    """Process multiple phase advances, collecting results per task.

    Continues processing remaining advances even if one fails. Each advance
    is independent — a failure (gate rejection, invalid phase, corrupt data)
    is recorded for that task but does not block subsequent advances.

    Args:
        store: Open store instance.
        advances: List of dicts with keys: task_id, phase, evidence.

    Returns:
        BatchResult with per-advance results including any errors.
    """
    results: list[tuple[str, str, GateResult]] = []
    for item in advances:
        task_id = item.get("task_id", "")
        phase_str = item.get("phase", "")
        evidence = item.get("evidence", {})
        if isinstance(evidence, str):
            try:
                evidence = json.loads(evidence)
            except (json.JSONDecodeError, TypeError):
                evidence = {}

        try:
            phase = Phase(phase_str.upper())
        except ValueError:
            gate = GateResult(passed=False, message=f"Invalid phase '{phase_str}'")
            results.append((task_id, phase_str, gate))
            continue

        try:
            result, _ = advance(store, task_id, phase, evidence)
            results.append((task_id, phase.value, result))
        except Exception as exc:
            gate = GateResult(
                passed=False,
                message=f"Error advancing task '{task_id}' through {phase.value}: {exc}",
            )
            results.append((task_id, phase.value, gate))

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
