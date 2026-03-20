"""Tests for the gate engine."""

from __future__ import annotations

import json

import pytest

from morpheus_mcp.core.engine import (
    advance,
    close_plan,
    init_plan,
    validate_evidence,
)
from morpheus_mcp.models.enums import Phase, PhaseStatus, PlanStatus, TaskSize, TaskStatus
from morpheus_mcp.models.plan import PlanRecord, TaskRecord


def _fdmc_evidence(sibling: str = "sibling.py") -> dict:
    """Build valid FDMC preflight evidence."""
    return {
        "fdmc_preflight": json.dumps({
            "consistent": {"sibling_read": sibling, "note": "matched"},
            "future_proof": "no assumptions",
            "dynamic": "configurable",
            "modular": "single responsibility",
        })
    }


class TestValidateEvidence:
    def test_check_no_gate(self):
        """CHECK phase has no gate requirements."""
        r = validate_evidence(Phase.CHECK, {})
        assert r.passed is True

    def test_code_reject_empty(self):
        """CODE rejects empty evidence."""
        r = validate_evidence(Phase.CODE, {})
        assert r.passed is False
        assert "fdmc_preflight" in r.message

    def test_code_reject_missing_lenses(self):
        """CODE rejects FDMC with missing lenses."""
        r = validate_evidence(Phase.CODE, {
            "fdmc_preflight": json.dumps({"consistent": {"sibling_read": "x"}})
        })
        assert r.passed is False
        assert "missing lenses" in r.message

    def test_code_reject_no_sibling_read(self):
        """CODE rejects consistent lens without sibling_read."""
        r = validate_evidence(Phase.CODE, {
            "fdmc_preflight": json.dumps({
                "consistent": {"note": "looked good"},
                "future_proof": "ok",
                "dynamic": "ok",
                "modular": "ok",
            })
        })
        assert r.passed is False
        assert "sibling_read" in r.message

    def test_code_accept_valid(self):
        """CODE accepts valid FDMC evidence."""
        r = validate_evidence(Phase.CODE, _fdmc_evidence())
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
        """GRADE accepts with tests_passed."""
        r = validate_evidence(Phase.GRADE, {"tests_passed": "12 passed"})
        assert r.passed is True

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
        r = validate_evidence(Phase.ADVANCE, {"knowledge_gate": "nothing_surprised"})
        assert r.passed is True


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
        """MEDIUM tasks behave identically to default (CODE requires fdmc)."""
        r = validate_evidence(Phase.CODE, {}, task_size=TaskSize.MEDIUM)
        assert r.passed is False
        assert "fdmc_preflight" in r.message

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
        advance(store, task.id, Phase.CODE, _fdmc_evidence())
        advance(store, task.id, Phase.TEST, {"build_verified": "ok"})
        advance(store, task.id, Phase.GRADE, {"tests_passed": "ok"})
        advance(store, task.id, Phase.COMMIT, {"seraph_id": "abc123"})
        advance(store, task.id, Phase.ADVANCE, {"knowledge_gate": "nothing_surprised"})

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
        result, phase = advance(store, task.id, Phase.CODE, _fdmc_evidence())
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
