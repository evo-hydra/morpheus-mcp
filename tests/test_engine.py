"""Tests for the gate engine."""

from __future__ import annotations

import json

import pytest

from morpheus_mcp.core.engine import (
    advance,
    advance_batch,
    close_plan,
    init_plan,
    validate_evidence,
)
from morpheus_mcp.models.enums import Phase, PhaseStatus, PlanStatus, TaskSize, TaskStatus
from morpheus_mcp.models.plan import PlanRecord, TaskRecord


def _code_evidence(sibling: str = "sibling.py") -> dict:
    """Build valid CODE phase evidence (sibling_read)."""
    return {"sibling_read": sibling}


def _grade_evidence(tests: str = "12 passed", fdmc: str = "Consistent — matched pattern") -> dict:
    """Build valid GRADE phase evidence."""
    return {"tests_passed": tests, "fdmc_review": fdmc}


class TestValidateEvidence:
    def test_check_no_gate(self):
        """CHECK phase has no gate requirements."""
        r = validate_evidence(Phase.CHECK, {})
        assert r.passed is True

    def test_code_reject_empty(self):
        """CODE rejects empty evidence."""
        r = validate_evidence(Phase.CODE, {})
        assert r.passed is False
        assert "sibling_read" in r.message

    def test_code_reject_empty_sibling(self):
        """CODE rejects empty sibling_read string."""
        r = validate_evidence(Phase.CODE, {"sibling_read": ""})
        assert r.passed is False

    def test_code_accept_valid(self):
        """CODE accepts valid sibling_read evidence."""
        r = validate_evidence(Phase.CODE, _code_evidence())
        assert r.passed is True

    def test_code_backward_compat_fdmc_preflight(self):
        """CODE accepts old fdmc_preflight format by extracting sibling_read."""
        r = validate_evidence(Phase.CODE, {
            "fdmc_preflight": json.dumps({
                "consistent": {"sibling_read": "sibling.py"},
                "future_proof": "ok",
                "dynamic": "ok",
                "modular": "ok",
            })
        })
        assert r.passed is True

    def test_test_reject_empty(self):
        """TEST rejects without build_verified."""
        r = validate_evidence(Phase.TEST, {})
        assert r.passed is False
        assert "build_verified" in r.message

    def test_test_accept(self):
        """TEST accepts with build_verified."""
        r = validate_evidence(Phase.TEST, {"build_verified": "cmake build ok"})
        assert r.passed is True

    def test_grade_reject_empty(self):
        """GRADE rejects without tests_passed."""
        r = validate_evidence(Phase.GRADE, {})
        assert r.passed is False

    def test_grade_accept(self):
        """GRADE accepts with tests_passed and fdmc_review."""
        r = validate_evidence(Phase.GRADE, _grade_evidence())
        assert r.passed is True

    def test_grade_reject_missing_fdmc_review(self):
        """GRADE rejects without fdmc_review."""
        r = validate_evidence(Phase.GRADE, {"tests_passed": "12 passed"})
        assert r.passed is False
        assert "fdmc_review" in r.message

    def test_commit_reject_no_seraph(self):
        """COMMIT rejects without seraph_id when grade enabled."""
        r = validate_evidence(Phase.COMMIT, {}, grade_enabled=True)
        assert r.passed is False

    def test_commit_accept_seraph(self):
        """COMMIT accepts with seraph_id."""
        r = validate_evidence(Phase.COMMIT, {"seraph_id": "abc123"})
        assert r.passed is True

    def test_commit_skip_seraph_grade_disabled(self):
        """COMMIT skips seraph requirement when grade disabled."""
        r = validate_evidence(Phase.COMMIT, {}, grade_enabled=False)
        assert r.passed is True

    def test_advance_reject_empty(self):
        """ADVANCE rejects without knowledge_gate."""
        r = validate_evidence(Phase.ADVANCE, {})
        assert r.passed is False

    def test_advance_accept(self):
        """ADVANCE accepts with knowledge_gate."""
        r = validate_evidence(Phase.ADVANCE, {"knowledge_gate": "nothing_surprised", "knowledge_reason": "followed established pattern"})
        assert r.passed is True

    # --- Rejection message format tests ---

    def test_code_rejection_includes_example(self):
        """CODE rejection message includes expected format example."""
        r = validate_evidence(Phase.CODE, {})
        assert "Expected format:" in r.message
        assert '"sibling_read"' in r.message

    def test_test_rejection_includes_example(self):
        """TEST rejection message includes expected format example."""
        r = validate_evidence(Phase.TEST, {})
        assert "Expected format:" in r.message
        assert '"build_verified"' in r.message

    def test_grade_rejection_includes_example(self):
        """GRADE rejection message includes expected format example."""
        r = validate_evidence(Phase.GRADE, {})
        assert "Expected format:" in r.message
        assert '"tests_passed"' in r.message

    def test_commit_rejection_includes_example(self):
        """COMMIT rejection message includes expected format example."""
        r = validate_evidence(Phase.COMMIT, {})
        assert "Expected format:" in r.message
        assert '"seraph_id"' in r.message

    def test_advance_rejection_includes_example(self):
        """ADVANCE rejection message includes expected format example."""
        r = validate_evidence(Phase.ADVANCE, {})
        assert "Expected format:" in r.message
        assert '"knowledge_gate"' in r.message

    # --- Provided-vs-expected key tests ---

    def test_rejection_shows_provided_keys(self):
        """Rejection message includes which keys the caller actually provided."""
        r = validate_evidence(Phase.GRADE, {"tests_passed": "12 passed"})
        assert r.passed is False
        assert "You provided: [tests_passed]" in r.message
        assert "expected keys: [tests_passed, fdmc_review]" in r.message
        assert "fdmc_review" in r.message

    def test_rejection_shows_none_when_empty(self):
        """Rejection message shows '(none)' when no keys provided."""
        r = validate_evidence(Phase.CODE, {})
        assert "You provided: [(none)]" in r.message

    def test_rejection_shows_all_provided_keys(self):
        """Rejection message includes all provided keys even irrelevant ones."""
        r = validate_evidence(Phase.GRADE, {
            "tests_passed": "ok",
            "extra_key": "bonus",
        })
        assert r.passed is False
        assert "extra_key" in r.message
        assert "tests_passed" in r.message


class TestSizeAwareGates:
    """Tests for task size-based gate relaxation/enforcement."""

    def test_small_code_skips_fdmc(self):
        """SMALL tasks skip fdmc_preflight requirement."""
        r = validate_evidence(Phase.CODE, {}, task_size=TaskSize.SMALL)
        assert r.passed is True

    def test_small_commit_skips_seraph(self):
        """SMALL tasks skip seraph_id requirement."""
        r = validate_evidence(Phase.COMMIT, {}, task_size=TaskSize.SMALL)
        assert r.passed is True

    def test_small_advance_skips_knowledge(self):
        """SMALL tasks skip knowledge_gate requirement."""
        r = validate_evidence(Phase.ADVANCE, {}, task_size=TaskSize.SMALL)
        assert r.passed is True

    def test_small_test_still_required(self):
        """SMALL tasks still require build_verified."""
        r = validate_evidence(Phase.TEST, {}, task_size=TaskSize.SMALL)
        assert r.passed is False

    def test_small_grade_still_required(self):
        """SMALL tasks still require tests_passed."""
        r = validate_evidence(Phase.GRADE, {}, task_size=TaskSize.SMALL)
        assert r.passed is False

    def test_medium_unchanged(self):
        """MEDIUM tasks behave identically to default (CODE requires sibling_read)."""
        r = validate_evidence(Phase.CODE, {}, task_size=TaskSize.MEDIUM)
        assert r.passed is False
        assert "sibling_read" in r.message

    def test_large_requires_seraph_even_grade_disabled(self):
        """LARGE tasks require seraph_id even when grade is disabled."""
        r = validate_evidence(
            Phase.COMMIT, {}, grade_enabled=False, task_size=TaskSize.LARGE,
        )
        assert r.passed is False
        assert "seraph_id" in r.message

    def test_large_accepts_seraph(self):
        """LARGE tasks accept seraph_id normally."""
        r = validate_evidence(
            Phase.COMMIT, {"seraph_id": "abc123"}, task_size=TaskSize.LARGE,
        )
        assert r.passed is True

    def test_medium_accepts_seraph_unavailable(self):
        """MEDIUM tasks accept 'seraph_unavailable' as valid seraph_id."""
        r = validate_evidence(
            Phase.COMMIT, {"seraph_id": "seraph_unavailable"}, task_size=TaskSize.MEDIUM,
        )
        assert r.passed is True

    def test_large_rejects_seraph_unavailable(self):
        """LARGE tasks reject 'seraph_unavailable' — they always need a real ID."""
        r = validate_evidence(
            Phase.COMMIT, {"seraph_id": "seraph_unavailable"}, task_size=TaskSize.LARGE,
        )
        assert r.passed is False
        assert "LARGE" in r.message

    def test_small_full_lifecycle(self, store, sample_plan_record):
        """SMALL tasks can complete the full lifecycle with minimal evidence."""
        store.save_plan(sample_plan_record)
        task = TaskRecord(
            plan_id=sample_plan_record.id, seq=1, title="Small",
            size=TaskSize.SMALL,
        )
        store.save_task(task)

        # Walk through all phases with minimal evidence
        advance(store, task.id, Phase.CHECK, {})
        advance(store, task.id, Phase.CODE, {})  # no fdmc needed
        advance(store, task.id, Phase.TEST, {"build_verified": "ok"})
        advance(store, task.id, Phase.GRADE, {"tests_passed": "ok"})
        advance(store, task.id, Phase.COMMIT, {})  # no seraph needed
        result, _ = advance(store, task.id, Phase.ADVANCE, {})  # no knowledge_gate needed
        assert result.passed is True

        retrieved = store.get_task(task.id)
        assert retrieved.status == TaskStatus.DONE


class TestGreenfieldMode:
    """Tests for greenfield plan mode relaxations."""

    def test_greenfield_skips_sibling_read(self):
        """Greenfield mode skips sibling_read in CODE FDMC check."""
        evidence = {
            "fdmc_preflight": json.dumps({
                "consistent": {"note": "greenfield, no siblings"},
                "future_proof": "ok",
                "dynamic": "ok",
                "modular": "ok",
            })
        }
        r = validate_evidence(Phase.CODE, evidence, plan_mode="greenfield")
        assert r.passed is True

    def test_standard_still_requires_sibling_read(self):
        """Standard mode still requires sibling_read."""
        evidence = {
            "fdmc_preflight": json.dumps({
                "consistent": {"note": "looked good"},
                "future_proof": "ok",
                "dynamic": "ok",
                "modular": "ok",
            })
        }
        r = validate_evidence(Phase.CODE, evidence, plan_mode="standard")
        assert r.passed is False
        assert "sibling_read" in r.message

    def test_greenfield_small_combo(self):
        """Greenfield + SMALL is the most relaxed path."""
        # CODE: no fdmc needed (small), no sibling_read needed (greenfield)
        r = validate_evidence(Phase.CODE, {}, task_size=TaskSize.SMALL, plan_mode="greenfield")
        assert r.passed is True

    def test_greenfield_full_lifecycle(self, store):
        """Greenfield plan completes full lifecycle without sibling_read."""
        plan = PlanRecord(name="GF", project="/tmp", mode="greenfield")
        store.save_plan(plan)
        task = TaskRecord(plan_id=plan.id, seq=1, title="T1")
        store.save_task(task)
        store.update_plan_status(plan.id, PlanStatus.ACTIVE)

        evidence_code = {
            "fdmc_preflight": json.dumps({
                "consistent": {"note": "greenfield"},
                "future_proof": "ok",
                "dynamic": "ok",
                "modular": "ok",
            })
        }
        advance(store, task.id, Phase.CHECK, {})
        result, _ = advance(store, task.id, Phase.CODE, evidence_code)
        assert result.passed is True


class TestInitPlan:
    def test_init_saves_and_activates(self, store):
        """init_plan saves plan and tasks, sets status to active."""
        plan = PlanRecord(name="Test", project="/tmp")
        tasks = [TaskRecord(plan_id=plan.id, seq=i, title=f"T{i}") for i in range(1, 4)]
        pid = init_plan(store, plan, tasks)
        assert pid == plan.id

        retrieved = store.get_plan(pid)
        assert retrieved.status == PlanStatus.ACTIVE
        assert len(store.get_tasks(pid)) == 3


class TestAdvance:
    def test_advance_check(self, store, sample_plan_record):
        """Advancing CHECK succeeds with empty evidence."""
        store.save_plan(sample_plan_record)
        task = TaskRecord(plan_id=sample_plan_record.id, seq=1, title="T1")
        store.save_task(task)
        result, phase = advance(store, task.id, Phase.CHECK, {})
        assert result.passed is True
        assert phase is not None
        assert phase.status == PhaseStatus.COMPLETED

    def test_advance_code_rejected(self, store, sample_plan_record):
        """Advancing CODE with missing evidence is rejected."""
        store.save_plan(sample_plan_record)
        task = TaskRecord(plan_id=sample_plan_record.id, seq=1, title="T1")
        store.save_task(task)

        # Must complete CHECK first (sequential enforcement)
        advance(store, task.id, Phase.CHECK, {})

        result, phase = advance(store, task.id, Phase.CODE, {})
        assert result.passed is False
        assert phase is None

        # Rejection is recorded (CHECK completed + CODE rejected = 2 phases)
        phases = store.get_phases(task.id)
        rejected = [p for p in phases if p.status == PhaseStatus.REJECTED]
        assert len(rejected) == 1
        assert rejected[0].status == PhaseStatus.REJECTED

    def test_advance_marks_task_done(self, store, sample_plan_record):
        """ADVANCE phase marks the task as done (after completing all prior phases)."""
        store.save_plan(sample_plan_record)
        task = TaskRecord(plan_id=sample_plan_record.id, seq=1, title="T1")
        store.save_task(task)

        # Walk through all phases sequentially
        advance(store, task.id, Phase.CHECK, {})
        advance(store, task.id, Phase.CODE, _code_evidence())
        advance(store, task.id, Phase.TEST, {"build_verified": "ok"})
        advance(store, task.id, Phase.GRADE, _grade_evidence())
        advance(store, task.id, Phase.COMMIT, {"seraph_id": "abc123"})
        advance(store, task.id, Phase.ADVANCE, {"knowledge_gate": "nothing_surprised", "knowledge_reason": "followed established pattern"})

        retrieved = store.get_task(task.id)
        assert retrieved.status == TaskStatus.DONE

    def test_advance_unknown_task(self, store):
        """Advancing with unknown task_id fails."""
        result, phase = advance(store, "nonexistent", Phase.CHECK, {})
        assert result.passed is False
        assert "not found" in result.message

    def test_advance_rejects_skipped_phase(self, store, sample_plan_record):
        """Cannot skip phases — must complete prior phase first."""
        store.save_plan(sample_plan_record)
        task = TaskRecord(plan_id=sample_plan_record.id, seq=1, title="T1")
        store.save_task(task)

        # Try to jump to CODE without completing CHECK
        result, phase = advance(store, task.id, Phase.CODE, _code_evidence())
        assert result.passed is False
        assert "CHECK not completed" in result.message

        # Try to jump to ADVANCE without any prior phases
        result, phase = advance(store, task.id, Phase.ADVANCE, {
            "knowledge_gate": "nothing_surprised",
        })
        assert result.passed is False
        assert "COMMIT not completed" in result.message

    def test_advance_prefix_matching(self, store, sample_plan_record):
        """Task IDs can be matched by prefix (min 8 chars)."""
        store.save_plan(sample_plan_record)
        task = TaskRecord(plan_id=sample_plan_record.id, seq=1, title="T1")
        store.save_task(task)

        prefix = task.id[:12]
        result, phase = advance(store, prefix, Phase.CHECK, {})
        assert result.passed is True


class TestSimplifiedKnowledgeGate:
    """Tests for boolean-style knowledge_gate in ADVANCE phase."""

    def test_accept_true_string(self):
        """ADVANCE accepts 'true' as knowledge_gate."""
        r = validate_evidence(Phase.ADVANCE, {"knowledge_gate": "true"})
        assert r.passed is True

    def test_accept_false_string_with_reason(self):
        """ADVANCE accepts 'false' as knowledge_gate when reason provided."""
        r = validate_evidence(Phase.ADVANCE, {
            "knowledge_gate": "false",
            "knowledge_reason": "pure config change, no new patterns",
        })
        assert r.passed is True

    def test_reject_false_string_without_reason(self):
        """ADVANCE rejects 'false' without knowledge_reason."""
        r = validate_evidence(Phase.ADVANCE, {"knowledge_gate": "false"})
        assert r.passed is False
        assert "knowledge_reason" in r.message

    def test_reject_nothing_surprised_without_reason(self):
        """ADVANCE rejects 'nothing_surprised' without knowledge_reason."""
        r = validate_evidence(Phase.ADVANCE, {"knowledge_gate": "nothing_surprised"})
        assert r.passed is False
        assert "knowledge_reason" in r.message

    def test_accept_nothing_surprised_with_reason(self):
        """ADVANCE accepts 'nothing_surprised' with knowledge_reason."""
        r = validate_evidence(Phase.ADVANCE, {
            "knowledge_gate": "nothing_surprised",
            "knowledge_reason": "followed Task 1 pattern exactly",
        })
        assert r.passed is True

    def test_accept_sentinel_id_without_reason(self):
        """ADVANCE accepts sentinel IDs without requiring knowledge_reason."""
        r = validate_evidence(Phase.ADVANCE, {"knowledge_gate": "sol_abc123"})
        assert r.passed is True

    def test_reject_empty(self):
        """ADVANCE rejects empty knowledge_gate."""
        r = validate_evidence(Phase.ADVANCE, {"knowledge_gate": ""})
        assert r.passed is False

    def test_small_task_skips(self):
        """SMALL tasks skip knowledge_gate entirely."""
        r = validate_evidence(Phase.ADVANCE, {}, task_size=TaskSize.SMALL)
        assert r.passed is True


class TestSkipReason:
    """Tests for skip_reason gate bypass."""

    def test_skip_reason_bypasses_missing_evidence(self, store, sample_plan_record):
        """skip_reason fills missing keys so the gate passes."""
        store.save_plan(sample_plan_record)
        task = TaskRecord(plan_id=sample_plan_record.id, seq=1, title="T1")
        store.save_task(task)

        advance(store, task.id, Phase.CHECK, {})
        result, _ = advance(store, task.id, Phase.CODE, {}, skip_reason="greenfield — no diff")
        assert result.passed is True

    def test_without_skip_reason_enforces_gates(self):
        """Without skip_reason, gates are enforced normally."""
        r = validate_evidence(Phase.COMMIT, {})
        assert r.passed is False

    def test_skip_reason_on_check_is_noop(self, store, sample_plan_record):
        """CHECK has no gate, so skip_reason changes nothing."""
        store.save_plan(sample_plan_record)
        task = TaskRecord(plan_id=sample_plan_record.id, seq=1, title="T1")
        store.save_task(task)

        result, _ = advance(store, task.id, Phase.CHECK, {}, skip_reason="test")
        assert result.passed is True

    def test_skip_reason_with_advance_function(self, store, sample_plan_record):
        """skip_reason flows through advance() to validate_evidence()."""
        store.save_plan(sample_plan_record)
        task = TaskRecord(plan_id=sample_plan_record.id, seq=1, title="T1")
        store.save_task(task)

        # Walk to COMMIT phase
        advance(store, task.id, Phase.CHECK, {})
        advance(store, task.id, Phase.CODE, {"sibling_read": "test.py"})
        advance(store, task.id, Phase.TEST, {"build_verified": "ok"})
        advance(store, task.id, Phase.GRADE, {"tests_passed": "ok", "fdmc_review": "ok"})

        # COMMIT with skip_reason instead of seraph_id
        result, phase_record = advance(
            store, task.id, Phase.COMMIT, {}, skip_reason="greenfield — no diff for Seraph",
        )
        assert result.passed is True
        assert phase_record is not None
        assert "skipped:" in phase_record.evidence_json


class TestBatchAdvance:
    """Tests for advance_batch."""

    def test_batch_multiple_check_phases(self, store, sample_plan_record):
        """Batch advance multiple tasks through CHECK."""
        store.save_plan(sample_plan_record)
        t1 = TaskRecord(plan_id=sample_plan_record.id, seq=1, title="T1")
        t2 = TaskRecord(plan_id=sample_plan_record.id, seq=2, title="T2")
        store.save_task(t1)
        store.save_task(t2)

        batch = advance_batch(store, [
            {"task_id": t1.id, "phase": "CHECK", "evidence": {}},
            {"task_id": t2.id, "phase": "CHECK", "evidence": {}},
        ])
        assert len(batch.results) == 2
        assert all(r[2].passed for r in batch.results)

    def test_batch_stops_on_failure(self, store, sample_plan_record):
        """Batch stops at first failure."""
        store.save_plan(sample_plan_record)
        t1 = TaskRecord(plan_id=sample_plan_record.id, seq=1, title="T1")
        store.save_task(t1)

        # CHECK succeeds, CODE fails (no evidence, skipped CHECK)
        batch = advance_batch(store, [
            {"task_id": t1.id, "phase": "CHECK", "evidence": {}},
            {"task_id": t1.id, "phase": "CODE", "evidence": {}},  # will fail
        ])
        assert len(batch.results) == 2
        assert batch.results[0][2].passed is True
        assert batch.results[1][2].passed is False

    def test_batch_invalid_phase(self, store, sample_plan_record):
        """Batch rejects invalid phase name."""
        store.save_plan(sample_plan_record)
        t1 = TaskRecord(plan_id=sample_plan_record.id, seq=1, title="T1")
        store.save_task(t1)

        batch = advance_batch(store, [
            {"task_id": t1.id, "phase": "INVALID", "evidence": {}},
        ])
        assert len(batch.results) == 1
        assert batch.results[0][2].passed is False
        assert "Invalid phase" in batch.results[0][2].message

    def test_batch_empty_list(self, store):
        """Empty batch returns empty results."""
        batch = advance_batch(store, [])
        assert len(batch.results) == 0


class TestClosePlan:
    def test_close(self, store):
        """close_plan marks plan as completed."""
        plan = PlanRecord(name="Test", project="/tmp")
        store.save_plan(plan)
        closed = close_plan(store, plan.id)
        assert closed.status == PlanStatus.COMPLETED
        assert closed.closed_at is not None

    def test_close_nonexistent(self, store):
        """Closing nonexistent plan returns None."""
        assert close_plan(store, "nonexistent") is None
