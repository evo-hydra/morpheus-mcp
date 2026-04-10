"""Tests for the gate engine."""

from __future__ import annotations

import json

import pytest

from morpheus_mcp.core.engine import (
    advance,
    advance_batch,
    check_oil_change_advisory,
    close_plan,
    init_plan,
    recommend_gates,
    validate_evidence,
)
from morpheus_mcp.models.enums import Phase, PhaseStatus, PlanStatus, TaskSize, TaskStatus
from morpheus_mcp.models.plan import PlanRecord, TaskRecord


def _code_evidence(sibling: str = "sibling.py") -> dict:
    """Build valid CODE phase evidence (sibling_read)."""
    return {"sibling_read": sibling}


def _grade_evidence(tests: str = "12 passed", fdmc: str = "Consistent — re-read engine.py, matched pattern") -> dict:
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

    def test_small_test_skips_build_verified(self):
        """SMALL tasks skip build_verified requirement."""
        r = validate_evidence(Phase.TEST, {}, task_size=TaskSize.SMALL)
        assert r.passed is True

    def test_small_grade_still_requires_tests_passed(self):
        """SMALL tasks still require tests_passed (only fdmc_review is skipped)."""
        r = validate_evidence(Phase.GRADE, {}, task_size=TaskSize.SMALL)
        assert r.passed is False
        assert "tests_passed" in r.message

    def test_small_grade_accepts_tests_passed_only(self):
        """SMALL tasks pass GRADE with just tests_passed."""
        r = validate_evidence(Phase.GRADE, {"tests_passed": "3 passed"}, task_size=TaskSize.SMALL)
        assert r.passed is True

    def test_micro_code_skips_all(self):
        """MICRO tasks skip all CODE gate requirements."""
        r = validate_evidence(Phase.CODE, {}, task_size=TaskSize.MICRO)
        assert r.passed is True

    def test_micro_grade_skips_all(self):
        """MICRO tasks skip all GRADE gate requirements."""
        r = validate_evidence(Phase.GRADE, {}, task_size=TaskSize.MICRO)
        assert r.passed is True

    def test_micro_commit_skips_all(self):
        """MICRO tasks skip all COMMIT gate requirements."""
        r = validate_evidence(Phase.COMMIT, {}, task_size=TaskSize.MICRO)
        assert r.passed is True

    def test_micro_advance_skips_all(self):
        """MICRO tasks skip all ADVANCE gate requirements."""
        r = validate_evidence(Phase.ADVANCE, {}, task_size=TaskSize.MICRO)
        assert r.passed is True

    def test_micro_test_skips_all(self):
        """MICRO tasks skip all TEST gate requirements."""
        r = validate_evidence(Phase.TEST, {}, task_size=TaskSize.MICRO)
        assert r.passed is True

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
        advance(store, task.id, Phase.CODE, {})  # no sibling_read needed
        advance(store, task.id, Phase.TEST, {})  # no build_verified needed
        advance(store, task.id, Phase.GRADE, {"tests_passed": "ok"})  # only tests_passed
        advance(store, task.id, Phase.COMMIT, {})  # no seraph needed
        result, _ = advance(store, task.id, Phase.ADVANCE, {})  # no knowledge_gate needed
        assert result.passed is True

        retrieved = store.get_task(task.id)
        assert retrieved.status == TaskStatus.DONE


class TestNoTestCommand:
    """Tests for test_command: none — skip test-related gates honestly."""

    def test_none_skips_build_verified(self):
        """test_command=none skips build_verified in TEST phase."""
        r = validate_evidence(Phase.TEST, {}, test_command="none")
        assert r.passed is True

    def test_none_skips_tests_passed(self):
        """test_command=none skips tests_passed in GRADE phase."""
        r = validate_evidence(Phase.GRADE, {"fdmc_review": "Consistent — read config.py, no issues"}, test_command="none")
        assert r.passed is True

    def test_none_case_insensitive(self):
        """test_command=None (capitalized) also works."""
        r = validate_evidence(Phase.TEST, {}, test_command="None")
        assert r.passed is True

    def test_none_grade_still_requires_fdmc_for_medium(self):
        """test_command=none skips tests_passed but MEDIUM still needs fdmc_review."""
        r = validate_evidence(Phase.GRADE, {}, test_command="none")
        assert r.passed is False
        assert "fdmc_review" in r.message

    def test_none_grade_small_skips_everything(self):
        """SMALL + test_command=none: GRADE has zero required evidence."""
        r = validate_evidence(Phase.GRADE, {}, task_size=TaskSize.SMALL, test_command="none")
        assert r.passed is True

    def test_empty_string_does_not_skip(self):
        """Empty test_command does NOT skip gates (must be explicit 'none')."""
        r = validate_evidence(Phase.TEST, {}, test_command="")
        assert r.passed is False

    def test_real_command_does_not_skip(self):
        """Real test commands still enforce gates."""
        r = validate_evidence(Phase.TEST, {}, test_command="pytest")
        assert r.passed is False

    def test_none_full_lifecycle(self, store):
        """Plan with test_command=none completes lifecycle without test evidence."""
        plan = PlanRecord(name="NoTests", project="/tmp", test_command="none")
        store.save_plan(plan)
        task = TaskRecord(plan_id=plan.id, seq=1, title="T1")
        store.save_task(task)
        store.update_plan_status(plan.id, PlanStatus.ACTIVE)

        advance(store, task.id, Phase.CHECK, {})
        advance(store, task.id, Phase.CODE, {"sibling_read": "config.yaml"})
        r_test, _ = advance(store, task.id, Phase.TEST, {})  # no build_verified
        assert r_test.passed is True
        r_grade, _ = advance(store, task.id, Phase.GRADE, {"fdmc_review": "Consistent — read config.yaml, no issues"})  # no tests_passed
        assert r_grade.passed is True
        advance(store, task.id, Phase.COMMIT, {"seraph_id": "abc"})
        advance(store, task.id, Phase.ADVANCE, {"knowledge_gate": "true"})

        assert store.get_task(task.id).status == TaskStatus.DONE


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

    def test_advance_check_sets_in_progress(self, store, sample_plan_record):
        """Advancing CHECK sets task status to IN_PROGRESS."""
        store.save_plan(sample_plan_record)
        task = TaskRecord(plan_id=sample_plan_record.id, seq=1, title="T1")
        store.save_task(task)
        advance(store, task.id, Phase.CHECK, {})
        retrieved = store.get_task(task.id)
        assert retrieved.status == TaskStatus.IN_PROGRESS

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
        advance(store, task.id, Phase.ADVANCE, {"knowledge_gate": "nothing_surprised", "knowledge_reason": "followed established pattern from prior task — no novel patterns discovered"})

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


class TestAdaptiveKnowledgeGate:
    """Tests for plan-size-based knowledge gate relaxation."""

    def test_small_plan_skips_knowledge_gate(self):
        """Plans with fewer tasks than threshold skip knowledge_gate."""
        r = validate_evidence(
            Phase.ADVANCE, {}, task_count=3, knowledge_gate_task_threshold=5,
        )
        assert r.passed is True

    def test_large_plan_enforces_knowledge_gate(self):
        """Plans at or above threshold enforce knowledge_gate."""
        r = validate_evidence(
            Phase.ADVANCE, {}, task_count=5, knowledge_gate_task_threshold=5,
        )
        assert r.passed is False
        assert "knowledge_gate" in r.message

    def test_unknown_task_count_enforces_gate(self):
        """task_count=0 (unknown) still enforces knowledge_gate."""
        r = validate_evidence(
            Phase.ADVANCE, {}, task_count=0, knowledge_gate_task_threshold=5,
        )
        assert r.passed is False

    def test_custom_threshold(self):
        """Custom threshold is respected."""
        # 3 tasks, threshold 3 — should enforce (not below)
        r = validate_evidence(
            Phase.ADVANCE, {}, task_count=3, knowledge_gate_task_threshold=3,
        )
        assert r.passed is False

        # 2 tasks, threshold 3 — should skip
        r = validate_evidence(
            Phase.ADVANCE, {}, task_count=2, knowledge_gate_task_threshold=3,
        )
        assert r.passed is True

    def test_small_plan_still_accepts_knowledge_gate(self):
        """Small plans still accept knowledge_gate if provided."""
        r = validate_evidence(
            Phase.ADVANCE,
            {"knowledge_gate": "nothing_surprised", "knowledge_reason": "simple config change with no interactions or novel patterns"},
            task_count=3, knowledge_gate_task_threshold=5,
        )
        assert r.passed is True

    def test_small_task_overrides_plan_size(self):
        """SMALL task size still skips regardless of plan size."""
        r = validate_evidence(
            Phase.ADVANCE, {}, task_size=TaskSize.SMALL,
            task_count=10, knowledge_gate_task_threshold=5,
        )
        assert r.passed is True

    def test_adaptive_gate_full_lifecycle(self, store):
        """Small plan (3 tasks) completes ADVANCE without knowledge_gate."""
        plan = PlanRecord(name="SmallPlan", project="/tmp")
        store.save_plan(plan)
        for i in range(1, 4):
            store.save_task(TaskRecord(plan_id=plan.id, seq=i, title=f"T{i}"))
        store.update_plan_status(plan.id, PlanStatus.ACTIVE)

        task = store.get_tasks(plan.id)[0]

        # Walk through all phases
        advance(store, task.id, Phase.CHECK, {})
        advance(store, task.id, Phase.CODE, {"sibling_read": "test.py"})
        advance(store, task.id, Phase.TEST, {"build_verified": "cmake build ok"})
        advance(store, task.id, Phase.GRADE, _grade_evidence())
        advance(store, task.id, Phase.COMMIT, {"seraph_id": "abc123"})
        # ADVANCE without knowledge_gate — plan has 3 tasks < threshold 5
        result, _ = advance(store, task.id, Phase.ADVANCE, {})
        assert result.passed is True


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
        advance(store, task.id, Phase.TEST, {"build_verified": "cmake build ok"})
        advance(store, task.id, Phase.GRADE, _grade_evidence())

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

    def test_batch_continues_past_failure(self, store, sample_plan_record):
        """Batch continues processing after a failed advance."""
        store.save_plan(sample_plan_record)
        t1 = TaskRecord(plan_id=sample_plan_record.id, seq=1, title="T1")
        t2 = TaskRecord(plan_id=sample_plan_record.id, seq=2, title="T2")
        store.save_task(t1)
        store.save_task(t2)

        # t1 CODE fails (no CHECK done), but t2 CHECK should still succeed
        batch = advance_batch(store, [
            {"task_id": t1.id, "phase": "CODE", "evidence": {}},  # fails
            {"task_id": t2.id, "phase": "CHECK", "evidence": {}},  # succeeds
        ])
        assert len(batch.results) == 2
        assert batch.results[0][2].passed is False
        assert batch.results[1][2].passed is True

    def test_batch_invalid_phase_continues(self, store, sample_plan_record):
        """Batch continues past invalid phase name."""
        store.save_plan(sample_plan_record)
        t1 = TaskRecord(plan_id=sample_plan_record.id, seq=1, title="T1")
        t2 = TaskRecord(plan_id=sample_plan_record.id, seq=2, title="T2")
        store.save_task(t1)
        store.save_task(t2)

        batch = advance_batch(store, [
            {"task_id": t1.id, "phase": "INVALID", "evidence": {}},
            {"task_id": t2.id, "phase": "CHECK", "evidence": {}},
        ])
        assert len(batch.results) == 2
        assert batch.results[0][2].passed is False
        assert "Invalid phase" in batch.results[0][2].message
        assert batch.results[1][2].passed is True

    def test_batch_exception_captured(self, store, sample_plan_record):
        """Exceptions during advance are captured, not propagated."""
        store.save_plan(sample_plan_record)
        t1 = TaskRecord(plan_id=sample_plan_record.id, seq=1, title="T1")
        store.save_task(t1)

        # Nonexistent task produces error but doesn't crash the batch
        batch = advance_batch(store, [
            {"task_id": "nonexistent_task_id_xxxxx", "phase": "CHECK", "evidence": {}},
            {"task_id": t1.id, "phase": "CHECK", "evidence": {}},
        ])
        assert len(batch.results) == 2
        assert batch.results[0][2].passed is False
        assert batch.results[1][2].passed is True

    def test_batch_small_check_and_code(self, store, sample_plan_record):
        """Batch advance SMALL task through CHECK+CODE with empty evidence — no rejections."""
        store.save_plan(sample_plan_record)
        t1 = TaskRecord(
            plan_id=sample_plan_record.id, seq=1, title="Small1",
            size=TaskSize.SMALL,
        )
        store.save_task(t1)

        batch = advance_batch(store, [
            {"task_id": t1.id, "phase": "CHECK", "evidence": {}},
            {"task_id": t1.id, "phase": "CODE", "evidence": {}},
        ])
        assert len(batch.results) == 2
        assert batch.results[0][2].passed is True, f"CHECK failed: {batch.results[0][2].message}"
        assert batch.results[1][2].passed is True, f"CODE failed: {batch.results[1][2].message}"

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


class TestOilChangeAdvisory:
    def test_no_history_returns_none(self, store):
        """No oil change history returns None (can't advise)."""
        assert check_oil_change_advisory(store, "/tmp/project") is None

    def test_within_interval_returns_none(self, store):
        """Commit count below interval returns None."""
        plan = PlanRecord(name="Test", project="/tmp/project")
        store.save_plan(plan)
        store.save_oil_change(plan.id, "hc-1", 10)
        assert check_oil_change_advisory(store, "/tmp/project", oil_change_interval=40) is None

    def test_exceeds_interval_returns_advisory(self, store):
        """Commit count above interval returns advisory message."""
        plan = PlanRecord(name="Test", project="/tmp/project")
        store.save_plan(plan)
        store.save_oil_change(plan.id, "hc-1", 50)
        msg = check_oil_change_advisory(store, "/tmp/project", oil_change_interval=40)
        assert msg is not None
        assert "50 commits" in msg
        assert "threshold: 40" in msg

    def test_exact_threshold_returns_advisory(self, store):
        """Commit count exactly at interval triggers advisory."""
        plan = PlanRecord(name="Test", project="/tmp/project")
        store.save_plan(plan)
        store.save_oil_change(plan.id, "hc-1", 40)
        msg = check_oil_change_advisory(store, "/tmp/project", oil_change_interval=40)
        assert msg is not None


class TestOilChangeEnforcement:
    def test_rejects_check_when_oil_change_due(self, store):
        """First task's CHECK is rejected when oil_change_due is set."""
        plan = PlanRecord(name="Test", project="/tmp", oil_change_due=True)
        store.save_plan(plan)
        task = TaskRecord(plan_id=plan.id, seq=1, title="Task 1")
        store.save_task(task)

        result, _ = advance(store, task.id, Phase.CHECK, {})
        assert result.passed is False
        assert "Oil change required" in result.message

    def test_allows_check_after_oil_change_cleared(self, store):
        """First task's CHECK proceeds after oil_change_due is cleared."""
        plan = PlanRecord(name="Test", project="/tmp", oil_change_due=True)
        store.save_plan(plan)
        store.update_plan_status(plan.id, PlanStatus.ACTIVE)
        task = TaskRecord(plan_id=plan.id, seq=1, title="Task 1")
        store.save_task(task)

        # Clear the flag
        store.set_oil_change_due(plan.id, False)

        result, phase_rec = advance(store, task.id, Phase.CHECK, {})
        assert result.passed is True

    def test_non_first_task_not_gated(self, store):
        """Second task's CHECK is not gated even when oil_change_due is set."""
        plan = PlanRecord(name="Test", project="/tmp", oil_change_due=True)
        store.save_plan(plan)
        store.update_plan_status(plan.id, PlanStatus.ACTIVE)
        task1 = TaskRecord(plan_id=plan.id, seq=1, title="Task 1")
        task2 = TaskRecord(plan_id=plan.id, seq=2, title="Task 2")
        store.save_task(task1)
        store.save_task(task2)

        result, _ = advance(store, task2.id, Phase.CHECK, {})
        assert result.passed is True


class TestInlineReflect:
    """Test that reflect_* fields in evidence auto-record gate outcomes."""

    def test_inline_reflect_records_gate_outcome(self, store, sample_plan_record):
        """reflect_* fields in evidence are extracted and stored as gate outcomes."""
        store.save_plan(sample_plan_record)
        task = TaskRecord(plan_id=sample_plan_record.id, seq=1, title="T1")
        store.save_task(task)

        advance(store, task.id, Phase.CHECK, {})

        # Advance CODE with inline reflect data
        evidence = {
            "sibling_read": "src/parser.py",
            "reflect_caught_issue": True,
            "reflect_changed_code": True,
            "reflect_detail": "matched singleton pattern from sibling",
        }
        result, _ = advance(store, task.id, Phase.CODE, evidence)
        assert result.passed is True

        # Verify gate outcome was recorded
        summary = store.get_gate_summary(plan_id=sample_plan_record.id)
        assert len(summary) == 1
        assert summary[0]["gate"] == "sibling_read"
        assert summary[0]["caught"] == 1
        assert summary[0]["changed"] == 1

    def test_inline_reflect_not_stored_in_evidence(self, store, sample_plan_record):
        """reflect_* fields are popped from evidence — not persisted in phase record."""
        store.save_plan(sample_plan_record)
        task = TaskRecord(plan_id=sample_plan_record.id, seq=1, title="T1")
        store.save_task(task)

        advance(store, task.id, Phase.CHECK, {})

        evidence = {
            "sibling_read": "src/parser.py",
            "reflect_caught_issue": False,
            "reflect_detail": "no issues found",
        }
        advance(store, task.id, Phase.CODE, evidence)

        phases = store.get_phases(task.id)
        code_phase = [p for p in phases if p.phase == Phase.CODE][0]
        import json
        stored = json.loads(code_phase.evidence_json)
        assert "reflect_caught_issue" not in stored
        assert "reflect_detail" not in stored
        assert "sibling_read" in stored

    def test_no_reflect_fields_no_gate_outcome(self, store, sample_plan_record):
        """Without reflect_* fields, no gate outcome is recorded."""
        store.save_plan(sample_plan_record)
        task = TaskRecord(plan_id=sample_plan_record.id, seq=1, title="T1")
        store.save_task(task)

        advance(store, task.id, Phase.CHECK, {})
        advance(store, task.id, Phase.CODE, {"sibling_read": "x.py"})

        summary = store.get_gate_summary(plan_id=sample_plan_record.id)
        assert len(summary) == 0


class TestVerifyMode:
    """Tests for VERIFY mode — streamlined CHECK → TEST → ADVANCE for pre-implemented tasks."""

    def _make_task(self, store, plan_id, **kwargs):
        task = TaskRecord(plan_id=plan_id, seq=1, title="Pre-impl task", **kwargs)
        store.save_task(task)
        return task

    def test_verify_mode_full_lifecycle(self, store, sample_plan_record):
        """Pre-implemented task completes via CHECK → TEST → ADVANCE."""
        store.save_plan(sample_plan_record)
        task = self._make_task(store, sample_plan_record.id)

        # CHECK with pre_implemented evidence
        result, _ = advance(store, task.id, Phase.CHECK, {
            "status": "pre_implemented",
            "files_confirmed": ["src/foo.py"],
        })
        assert result.passed is True

        # TEST directly (skipping CODE)
        result, _ = advance(store, task.id, Phase.TEST, {
            "build_verified": "python -m py_compile src/foo.py — OK",
        })
        assert result.passed is True

        # ADVANCE directly (skipping GRADE and COMMIT)
        result, _ = advance(store, task.id, Phase.ADVANCE, {
            "knowledge_gate": "true",
        })
        assert result.passed is True

        retrieved = store.get_task(task.id)
        assert retrieved.status == TaskStatus.DONE

    def test_verify_mode_rejects_code_phase(self, store, sample_plan_record):
        """CODE phase is rejected in verify mode."""
        store.save_plan(sample_plan_record)
        task = self._make_task(store, sample_plan_record.id)

        advance(store, task.id, Phase.CHECK, {
            "status": "pre_implemented",
            "files_confirmed": ["src/foo.py"],
        })

        result, phase = advance(store, task.id, Phase.CODE, {"sibling_read": "x.py"})
        assert result.passed is False
        assert "not valid in verify mode" in result.message
        assert phase is None

    def test_verify_mode_rejects_grade_phase(self, store, sample_plan_record):
        """GRADE phase is rejected in verify mode."""
        store.save_plan(sample_plan_record)
        task = self._make_task(store, sample_plan_record.id)

        advance(store, task.id, Phase.CHECK, {
            "status": "pre_implemented",
            "files_confirmed": ["src/foo.py"],
        })
        advance(store, task.id, Phase.TEST, {"build_verified": "ok"})

        result, _ = advance(store, task.id, Phase.GRADE, _grade_evidence())
        assert result.passed is False
        assert "not valid in verify mode" in result.message

    def test_verify_mode_rejects_commit_phase(self, store, sample_plan_record):
        """COMMIT phase is rejected in verify mode."""
        store.save_plan(sample_plan_record)
        task = self._make_task(store, sample_plan_record.id)

        advance(store, task.id, Phase.CHECK, {
            "status": "pre_implemented",
            "files_confirmed": ["src/foo.py"],
        })
        advance(store, task.id, Phase.TEST, {"build_verified": "ok"})

        result, _ = advance(store, task.id, Phase.COMMIT, {"seraph_id": "abc"})
        assert result.passed is False
        assert "not valid in verify mode" in result.message

    def test_verify_mode_still_requires_test_evidence(self, store, sample_plan_record):
        """TEST gate still enforces build_verified in verify mode."""
        store.save_plan(sample_plan_record)
        task = self._make_task(store, sample_plan_record.id)

        advance(store, task.id, Phase.CHECK, {
            "status": "pre_implemented",
            "files_confirmed": ["src/foo.py"],
        })

        result, _ = advance(store, task.id, Phase.TEST, {})
        assert result.passed is False
        assert "build_verified" in result.message

    def test_normal_task_unaffected(self, store, sample_plan_record):
        """Tasks without pre_implemented evidence still use standard path."""
        store.save_plan(sample_plan_record)
        task = self._make_task(store, sample_plan_record.id)

        # CHECK without pre_implemented
        advance(store, task.id, Phase.CHECK, {})

        # CODE is required (standard path)
        result, _ = advance(store, task.id, Phase.TEST, {"build_verified": "ok"})
        assert result.passed is False
        assert "CODE not completed" in result.message

    def test_verify_mode_mixed_plan(self, store, sample_plan_record):
        """Plan with one pre-implemented task and one normal task."""
        store.save_plan(sample_plan_record)

        # Task 1: pre-implemented (verify path)
        t1 = TaskRecord(plan_id=sample_plan_record.id, seq=1, title="Pre-impl")
        store.save_task(t1)

        # Task 2: normal (standard path)
        t2 = TaskRecord(plan_id=sample_plan_record.id, seq=2, title="Normal")
        store.save_task(t2)

        # Task 1: verify path (CHECK → TEST → ADVANCE)
        advance(store, t1.id, Phase.CHECK, {
            "status": "pre_implemented",
            "files_confirmed": ["src/foo.py"],
        })
        advance(store, t1.id, Phase.TEST, {"build_verified": "ok"})
        r1, _ = advance(store, t1.id, Phase.ADVANCE, {"knowledge_gate": "true"})
        assert r1.passed is True

        # Task 2: standard path (CHECK → CODE → TEST → ...)
        advance(store, t2.id, Phase.CHECK, {})
        r2, _ = advance(store, t2.id, Phase.CODE, _code_evidence())
        assert r2.passed is True


class TestEvidenceHardening:
    """Tests for evidence content quality validation (anti-fabrication)."""

    # --- tests_passed ---

    def test_rejects_bare_yes(self):
        """tests_passed='yes' is rejected as a bare assertion."""
        r = validate_evidence(Phase.GRADE, {"tests_passed": "yes", "fdmc_review": "Consistent — read foo.py"})
        assert r.passed is False
        assert "bare assertion" in r.message

    def test_rejects_bare_true(self):
        """tests_passed='true' is rejected."""
        r = validate_evidence(Phase.GRADE, {"tests_passed": "true", "fdmc_review": "Consistent — read foo.py"})
        assert r.passed is False
        assert "bare assertion" in r.message

    def test_rejects_bare_ok(self):
        """tests_passed='ok' is rejected."""
        r = validate_evidence(Phase.GRADE, {"tests_passed": "ok", "fdmc_review": "Consistent — read foo.py"})
        assert r.passed is False
        assert "bare assertion" in r.message

    def test_rejects_no_test_pattern(self):
        """tests_passed without recognizable test output is rejected."""
        r = validate_evidence(Phase.GRADE, {
            "tests_passed": "everything looks good",
            "fdmc_review": "Consistent — read foo.py",
        })
        assert r.passed is False
        assert "recognizable test output" in r.message

    def test_accepts_numeric_output(self):
        """tests_passed with numeric counts is accepted."""
        r = validate_evidence(Phase.GRADE, {
            "tests_passed": "38/38 passed",
            "fdmc_review": "Consistent — read foo.py, no issues",
        })
        assert r.passed is True

    def test_accepts_pytest_output(self):
        """tests_passed with pytest runner name is accepted."""
        r = validate_evidence(Phase.GRADE, {
            "tests_passed": "223 passed (pytest)",
            "fdmc_review": "Consistent — read engine.py, matched pattern",
        })
        assert r.passed is True

    def test_accepts_vitest_output(self):
        """tests_passed with vitest runner name is accepted."""
        r = validate_evidence(Phase.GRADE, {
            "tests_passed": "12 tests passed (vitest)",
            "fdmc_review": "Consistent — read foo.ts, ok",
        })
        assert r.passed is True

    def test_skipped_evidence_bypasses_check(self):
        """Skipped tests_passed bypasses content check."""
        r = validate_evidence(Phase.GRADE, {
            "tests_passed": "skipped: no test command",
            "fdmc_review": "skipped: small task",
        })
        assert r.passed is True

    def test_small_task_skips_content_check(self):
        """SMALL tasks skip content quality checks."""
        r = validate_evidence(Phase.GRADE, {
            "tests_passed": "ok",
        }, task_size=TaskSize.SMALL)
        assert r.passed is True

    # --- fdmc_review ---

    def test_fdmc_rejects_bare_ok(self):
        """fdmc_review='ok' is rejected."""
        r = validate_evidence(Phase.GRADE, {
            "tests_passed": "12 passed (pytest)",
            "fdmc_review": "ok",
        })
        assert r.passed is False
        assert "bare assertion" in r.message

    def test_fdmc_rejects_no_lens(self):
        """fdmc_review without a lens name is rejected."""
        r = validate_evidence(Phase.GRADE, {
            "tests_passed": "12 passed (pytest)",
            "fdmc_review": "reviewed the code and it looks good",
        })
        assert r.passed is False
        assert "FDMC lens" in r.message

    def test_fdmc_rejects_no_file(self):
        """fdmc_review with lens but no file reference is rejected."""
        r = validate_evidence(Phase.GRADE, {
            "tests_passed": "12 passed (pytest)",
            "fdmc_review": "Consistent — matched existing pattern",
        })
        assert r.passed is False
        assert "file" in r.message.lower()

    def test_fdmc_accepts_valid(self):
        """fdmc_review with lens + file is accepted."""
        r = validate_evidence(Phase.GRADE, {
            "tests_passed": "12 passed (pytest)",
            "fdmc_review": "Consistent — re-read auth.ts, matched UserService pattern",
        })
        assert r.passed is True

    def test_fdmc_accepts_path_with_slash(self):
        """fdmc_review with path containing / is accepted."""
        r = validate_evidence(Phase.GRADE, {
            "tests_passed": "12 passed (pytest)",
            "fdmc_review": "Future-Proof — re-read src/core/engine, made config optional",
        })
        assert r.passed is True

    # --- knowledge_reason length ---

    def test_knowledge_reason_too_short(self):
        """knowledge_reason under 20 chars is rejected."""
        r = validate_evidence(Phase.ADVANCE, {
            "knowledge_gate": "nothing_surprised",
            "knowledge_reason": "simple task",
        })
        assert r.passed is False
        assert "too short" in r.message

    def test_knowledge_reason_adequate(self):
        """knowledge_reason at 20+ chars is accepted."""
        r = validate_evidence(Phase.ADVANCE, {
            "knowledge_gate": "nothing_surprised",
            "knowledge_reason": "task was a config addition with no interactions",
        })
        assert r.passed is True

    # --- sibling_read content (via advance) ---

    def test_sibling_read_rejects_target_file(self, store, sample_plan_record):
        """sibling_read pointing to a task target file is rejected."""
        store.save_plan(sample_plan_record)
        task = TaskRecord(
            plan_id=sample_plan_record.id, seq=1, title="T1",
            files_json='["src/engine.py"]',
        )
        store.save_task(task)

        advance(store, task.id, Phase.CHECK, {})
        result, phase = advance(store, task.id, Phase.CODE, {
            "sibling_read": "src/engine.py",
        })
        assert result.passed is False
        assert "target file" in result.message.lower()
        assert phase is None

    def test_sibling_read_accepts_different_file(self, store, sample_plan_record):
        """sibling_read pointing to a non-target file is accepted."""
        store.save_plan(sample_plan_record)
        task = TaskRecord(
            plan_id=sample_plan_record.id, seq=1, title="T1",
            files_json='["src/engine.py"]',
        )
        store.save_task(task)

        advance(store, task.id, Phase.CHECK, {})
        result, _ = advance(store, task.id, Phase.CODE, {
            "sibling_read": "src/parser.py",
        })
        assert result.passed is True

    def test_sibling_read_rejects_bare_assertion(self, store, sample_plan_record):
        """sibling_read='yes' is rejected via advance."""
        store.save_plan(sample_plan_record)
        task = TaskRecord(
            plan_id=sample_plan_record.id, seq=1, title="T1",
            files_json='["src/foo.py"]',
        )
        store.save_task(task)

        advance(store, task.id, Phase.CHECK, {})
        result, _ = advance(store, task.id, Phase.CODE, {"sibling_read": "yes"})
        assert result.passed is False
        assert "bare assertion" in result.message


class TestRecommendGates:
    """Tests for Dynamic Weigh — gate skip recommendations based on historical data."""

    def test_no_data_returns_empty(self, store):
        """No gate outcomes means no recommendations."""
        assert recommend_gates(store) == []

    def test_below_min_samples_returns_empty(self, store, sample_plan_record):
        """Gates with fewer than min_samples aren't recommended for skip."""
        store.save_plan(sample_plan_record)
        # Record 5 outcomes (below default min_samples=20)
        for i in range(5):
            store.record_gate_outcome(
                sample_plan_record.id, f"task_{i}", "sibling_read",
                caught_issue=False, changed_code=False, detail="noop",
            )
        assert recommend_gates(store) == []

    def test_low_hit_rate_recommends_skip(self, store, sample_plan_record):
        """Gates with <10% hit rate over 20+ samples are recommended for skip."""
        store.save_plan(sample_plan_record)
        # Record 25 outcomes, only 1 caught (4% hit rate)
        for i in range(25):
            store.record_gate_outcome(
                sample_plan_record.id, f"task_{i}", "seraph_assess",
                caught_issue=(i == 0), changed_code=False, detail="test",
            )
        recs = recommend_gates(store, min_samples=20, threshold=0.10)
        assert len(recs) == 1
        assert recs[0]["gate"] == "seraph_assess"
        assert "skip" in recs[0]["recommendation"]

    def test_high_hit_rate_not_recommended(self, store, sample_plan_record):
        """Gates with >10% hit rate are not recommended for skip."""
        store.save_plan(sample_plan_record)
        # Record 20 outcomes, 5 caught (25% hit rate)
        for i in range(20):
            store.record_gate_outcome(
                sample_plan_record.id, f"task_{i}", "sibling_read",
                caught_issue=(i < 5), changed_code=(i < 3), detail="test",
            )
        recs = recommend_gates(store, min_samples=20, threshold=0.10)
        assert len(recs) == 0

    def test_custom_threshold(self, store, sample_plan_record):
        """Custom threshold is respected."""
        store.save_plan(sample_plan_record)
        for i in range(30):
            store.record_gate_outcome(
                sample_plan_record.id, f"task_{i}", "fdmc_review",
                caught_issue=(i < 6), changed_code=False, detail="test",
            )
        # 20% hit rate — above 10% threshold, not recommended
        assert recommend_gates(store, min_samples=20, threshold=0.10) == []
        # 20% hit rate — below 25% threshold, recommended
        recs = recommend_gates(store, min_samples=20, threshold=0.25)
        assert len(recs) == 1

    def test_recommendations_attached_to_check(self, store, sample_plan_record):
        """CHECK advance includes recommendations when data exists."""
        store.save_plan(sample_plan_record)
        # Seed enough low-ROI gate data
        for i in range(25):
            store.record_gate_outcome(
                sample_plan_record.id, f"old_task_{i}", "seraph_assess",
                caught_issue=False, changed_code=False, detail="noop",
            )
        task = TaskRecord(plan_id=sample_plan_record.id, seq=1, title="T1")
        store.save_task(task)

        result, _ = advance(store, task.id, Phase.CHECK, {})
        assert result.passed is True
        assert "Recommended skips" in result.message
        assert "seraph_assess" in result.message
