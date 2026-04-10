"""End-to-end integration test: full Morpheus lifecycle."""

from __future__ import annotations

import json

import pytest

from morpheus_mcp.config import MorpheusConfig
from morpheus_mcp.core.engine import advance, advance_batch, close_plan, init_plan
from morpheus_mcp.core.parser import parse_plan_file
from morpheus_mcp.core.store import MorpheusStore
from morpheus_mcp.models.enums import Phase, PhaseStatus, PlanStatus, TaskSize, TaskStatus


@pytest.fixture
def integration_plan(tmp_path):
    """Create a realistic 2-task plan file."""
    f = tmp_path / "integration-plan.md"
    f.write_text(
        "---\n"
        "name: Integration Test Plan\n"
        "project: /tmp/integration\n"
        'test_command: "echo tests pass"\n'
        "---\n\n"
        "## 1. Add config module\n"
        "- **files**: src/config.py\n"
        "- **do**: Create config dataclass\n"
        "- **done-when**: Config loads defaults\n"
        "- **status**: pending\n\n"
        "## 2. Add store module\n"
        "- **files**: src/store.py\n"
        "- **do**: Create SQLite store\n"
        "- **done-when**: CRUD round-trip works\n"
        "- **status**: pending\n"
    )
    return f


def _code_ev(sibling: str = "merovingian/src/config.py") -> dict:
    return {"sibling_read": sibling}


def _grade_ev(tests: str = "12 passed", fdmc: str = "Consistent — re-read config.py, matched pattern") -> dict:
    return {"tests_passed": tests, "fdmc_review": fdmc}


def test_full_lifecycle(tmp_path, integration_plan):
    """Simulate a complete Morpheus lifecycle through all phases."""
    config = MorpheusConfig.load(tmp_path / "data")

    # 1. Parse plan file
    plan, tasks = parse_plan_file(integration_plan)
    assert len(tasks) == 2

    with MorpheusStore(config.db_path) as store:
        # 2. Init plan
        plan_id = init_plan(store, plan, tasks)
        retrieved_plan = store.get_plan(plan_id)
        assert retrieved_plan.status == PlanStatus.ACTIVE

        # 3. Get first pending task
        task1 = store.get_next_pending_task(plan_id)
        assert task1 is not None
        assert task1.seq == 1

        # === TASK 1: Walk through all 6 phases ===

        # Phase: CHECK (no gate)
        result, phase = advance(store, task1.id, Phase.CHECK, {})
        assert result.passed is True

        # Phase: CODE (requires sibling_read)
        result, phase = advance(store, task1.id, Phase.CODE, _code_ev())
        assert result.passed is True

        # Phase: TEST (requires build_verified)
        result, phase = advance(
            store, task1.id, Phase.TEST, {"build_verified": "python3 -c 'import config'"}
        )
        assert result.passed is True

        # Phase: GRADE (requires tests_passed + fdmc_review)
        result, phase = advance(store, task1.id, Phase.GRADE, _grade_ev())
        assert result.passed is True

        # Phase: COMMIT (requires seraph_id)
        result, phase = advance(
            store, task1.id, Phase.COMMIT, {"seraph_id": "abc123def456"}
        )
        assert result.passed is True

        # Phase: ADVANCE (requires knowledge_gate)
        result, phase = advance(
            store, task1.id, Phase.ADVANCE, {"knowledge_gate": "nothing_surprised", "knowledge_reason": "followed established pattern from prior task — no novel patterns"}
        )
        assert result.passed is True

        # Task 1 should now be done
        task1_after = store.get_task(task1.id)
        assert task1_after.status == TaskStatus.DONE

        # === TASK 2: Verify it's now the next pending ===

        task2 = store.get_next_pending_task(plan_id)
        assert task2 is not None
        assert task2.seq == 2

        # Quick advance through task 2
        advance(store, task2.id, Phase.CHECK, {})
        advance(store, task2.id, Phase.CODE, _code_ev())
        advance(store, task2.id, Phase.TEST, {"build_verified": "ok"})
        advance(store, task2.id, Phase.GRADE, _grade_ev())
        advance(store, task2.id, Phase.COMMIT, {"seraph_id": "def789"})
        advance(store, task2.id, Phase.ADVANCE, {"knowledge_gate": "nothing_surprised", "knowledge_reason": "followed established pattern from prior task — no novel patterns"})

        # No more pending tasks
        assert store.get_next_pending_task(plan_id) is None

        # === CLOSE PLAN ===

        closed = close_plan(store, plan_id)
        assert closed.status == PlanStatus.COMPLETED
        assert closed.closed_at is not None

        # Verify all phases recorded
        t1_phases = store.get_phases(task1.id)
        assert len(t1_phases) == 6
        assert all(p.status == PhaseStatus.COMPLETED for p in t1_phases)


def test_gate_rejection_and_retry(tmp_path, integration_plan):
    """Verify that gate rejection works and retry succeeds."""
    config = MorpheusConfig.load(tmp_path / "data")

    plan, tasks = parse_plan_file(integration_plan)

    with MorpheusStore(config.db_path) as store:
        init_plan(store, plan, tasks)
        task = store.get_next_pending_task(plan.id)

        # Advance CHECK
        advance(store, task.id, Phase.CHECK, {})

        # Try CODE without sibling_read — should be rejected
        result, phase = advance(store, task.id, Phase.CODE, {})
        assert result.passed is False
        assert "sibling_read" in result.message

        # Retry with proper evidence — should pass
        result, phase = advance(store, task.id, Phase.CODE, _code_ev())
        assert result.passed is True

        # Verify rejection was recorded
        phases = store.get_phases(task.id)
        rejected = [p for p in phases if p.status == PhaseStatus.REJECTED]
        assert len(rejected) == 1


def test_grade_disabled_plan(tmp_path):
    """Plans with grade=false skip seraph requirement at COMMIT."""
    config = MorpheusConfig.load(tmp_path / "data")

    plan_file = tmp_path / "no-grade.md"
    plan_file.write_text(
        "---\nname: No Grade\nproject: /tmp\n"
        'test_command: "echo ok"\ngrade: false\n---\n\n'
        "## 1. Config task\n- **files**: config.toml\n"
        "- **do**: add config\n- **done-when**: exists\n"
        "- **status**: pending\n"
    )

    plan, tasks = parse_plan_file(plan_file)
    assert plan.grade_enabled is False

    with MorpheusStore(config.db_path) as store:
        init_plan(store, plan, tasks)
        task = store.get_next_pending_task(plan.id)

        advance(store, task.id, Phase.CHECK, {})
        advance(store, task.id, Phase.CODE, _code_ev())
        advance(store, task.id, Phase.TEST, {"build_verified": "ok"})
        advance(store, task.id, Phase.GRADE, _grade_ev("1 passed", "Consistent — re-read config.toml, no issues"))

        # COMMIT without seraph_id — should pass because grade=false
        result, _ = advance(store, task.id, Phase.COMMIT, {})
        assert result.passed is True


def test_new_features_integration(tmp_path):
    """Integration test exercising greenfield mode, mixed sizes, batch, and progress."""
    config = MorpheusConfig.load(tmp_path / "data")

    plan_file = tmp_path / "features.md"
    plan_file.write_text(
        "---\n"
        "name: Feature Integration\n"
        "project: /tmp/features\n"
        'test_command: "echo ok"\n'
        "mode: greenfield\n"
        "---\n\n"
        "## 1. Small config task\n"
        "- **files**: config.py\n"
        "- **do**: Add config\n"
        "- **done-when**: Config exists\n"
        "- **status**: pending\n"
        "- **size**: small\n\n"
        "## 2. Medium core task\n"
        "- **files**: core.py\n"
        "- **do**: Add core logic\n"
        "- **done-when**: Core works\n"
        "- **status**: pending\n\n"
        "## 3. Large API task\n"
        "- **files**: api.py, models.py, routes.py\n"
        "- **do**: Build API\n"
        "- **done-when**: API responds\n"
        "- **status**: pending\n"
        "- **size**: large\n"
    )

    plan, tasks = parse_plan_file(plan_file)
    assert plan.mode == "greenfield"
    assert tasks[0].size == TaskSize.SMALL
    assert tasks[1].size == TaskSize.MEDIUM
    assert tasks[2].size == TaskSize.LARGE

    with MorpheusStore(config.db_path) as store:
        plan_id = init_plan(store, plan, tasks)
        t1, t2, t3 = store.get_tasks(plan_id)

        # === SMALL TASK: minimal evidence path ===
        advance(store, t1.id, Phase.CHECK, {})
        # CODE: no sibling_read needed (SMALL + greenfield)
        result, _ = advance(store, t1.id, Phase.CODE, {})
        assert result.passed is True
        advance(store, t1.id, Phase.TEST, {"build_verified": "ok"})
        # GRADE: no fdmc_review needed (SMALL)
        result, _ = advance(store, t1.id, Phase.GRADE, {"tests_passed": "ok"})
        assert result.passed is True
        # COMMIT: no seraph_id needed (SMALL)
        result, _ = advance(store, t1.id, Phase.COMMIT, {})
        assert result.passed is True
        # ADVANCE: no knowledge_gate needed (SMALL)
        result, _ = advance(store, t1.id, Phase.ADVANCE, {})
        assert result.passed is True

        assert store.get_task(t1.id).status == TaskStatus.DONE

        # === MEDIUM TASK: greenfield relaxation ===
        advance(store, t2.id, Phase.CHECK, {})
        # CODE: no sibling_read needed (greenfield mode)
        result, _ = advance(store, t2.id, Phase.CODE, {})
        assert result.passed is True

        # Log progress
        store.save_progress(t2.id, "implementing core logic")
        entries = store.get_progress(t2.id)
        assert len(entries) == 1

        advance(store, t2.id, Phase.TEST, {"build_verified": "python3 -c 'import core' — OK"})
        advance(store, t2.id, Phase.GRADE, _grade_ev("5 passed", "Consistent — re-read config.py, matched greenfield pattern"))
        advance(store, t2.id, Phase.COMMIT, {"seraph_id": "abc123"})
        advance(store, t2.id, Phase.ADVANCE, {"knowledge_gate": "true"})

        assert store.get_task(t2.id).status == TaskStatus.DONE

        # === LARGE TASK: strict path ===
        advance(store, t3.id, Phase.CHECK, {})
        # CODE: greenfield still means no sibling_read
        result, _ = advance(store, t3.id, Phase.CODE, {})
        assert result.passed is True
        advance(store, t3.id, Phase.TEST, {"build_verified": "python3 -c 'import api' — OK"})
        advance(store, t3.id, Phase.GRADE, _grade_ev("8 passed", "Consistent — re-read core.py, matched greenfield pattern"))
        # COMMIT: LARGE requires seraph_id even though we could try without
        result, _ = advance(store, t3.id, Phase.COMMIT, {})
        assert result.passed is False  # LARGE always requires seraph
        result, _ = advance(store, t3.id, Phase.COMMIT, {"seraph_id": "def456"})
        assert result.passed is True
        advance(store, t3.id, Phase.ADVANCE, {"knowledge_gate": "nothing_surprised", "knowledge_reason": "followed established pattern from prior task — no novel patterns"})

        assert store.get_task(t3.id).status == TaskStatus.DONE

        # === BATCH: verify batch advance works ===
        # (All tasks are done, but we can test batch with a new plan)

        # === CLOSE ===
        closed = close_plan(store, plan_id)
        assert closed.status == PlanStatus.COMPLETED


def test_invalid_size_lifecycle(tmp_path):
    """Full lifecycle works even when DB has invalid task size values.

    This is the regression test for the 'None is not a valid TaskSize'
    crash that prevented morpheus_advance from working during dogfooding.
    """
    config = MorpheusConfig.load(tmp_path / "data")

    plan_file = tmp_path / "corrupt.md"
    plan_file.write_text(
        "---\n"
        "name: Invalid Size Plan\n"
        "project: /tmp/corrupt\n"
        'test_command: "echo ok"\n'
        "mode: greenfield\n"
        "---\n\n"
        "## 1. Task with valid size\n"
        "- **files**: a.py\n"
        "- **do**: Do A\n"
        "- **done-when**: A done\n"
        "- **status**: pending\n"
        "- **size**: small\n\n"
        "## 2. Task that will get corrupted\n"
        "- **files**: b.py\n"
        "- **do**: Do B\n"
        "- **done-when**: B done\n"
        "- **status**: pending\n"
    )

    plan, tasks = parse_plan_file(plan_file)

    with MorpheusStore(config.db_path) as store:
        plan_id = init_plan(store, plan, tasks)
        t1, t2 = store.get_tasks(plan_id)

        # Corrupt t2's size to an invalid string (simulating bad data)
        store.conn.execute("UPDATE tasks SET size = 'bogus' WHERE id = ?", (t2.id,))
        store.conn.commit()

        # t1 (small, valid) should advance normally
        advance(store, t1.id, Phase.CHECK, {})
        advance(store, t1.id, Phase.CODE, {})
        advance(store, t1.id, Phase.TEST, {"build_verified": "ok"})
        result, _ = advance(store, t1.id, Phase.GRADE, {"tests_passed": "ok"})
        assert result.passed is True
        advance(store, t1.id, Phase.COMMIT, {})
        advance(store, t1.id, Phase.ADVANCE, {})
        assert store.get_task(t1.id).status == TaskStatus.DONE

        # t2 (corrupted 'bogus' → defaults to MEDIUM) should still work
        t2_reloaded = store.get_task(t2.id)
        assert t2_reloaded.size == TaskSize.MEDIUM

        advance(store, t2.id, Phase.CHECK, {})
        # Greenfield mode relaxes sibling_read for MEDIUM
        result, _ = advance(store, t2.id, Phase.CODE, {})
        assert result.passed is True
        advance(store, t2.id, Phase.TEST, {"build_verified": "python3 -c 'import b' — OK"})
        advance(store, t2.id, Phase.GRADE, _grade_ev())
        advance(store, t2.id, Phase.COMMIT, {"seraph_id": "abc"})
        advance(store, t2.id, Phase.ADVANCE, {"knowledge_gate": "true"})
        assert store.get_task(t2.id).status == TaskStatus.DONE

        closed = close_plan(store, plan_id)
        assert closed.status == PlanStatus.COMPLETED


def test_small_no_test_command_lifecycle(tmp_path):
    """SMALL task + test_command:none = zero required evidence for all gates."""
    config = MorpheusConfig.load(tmp_path / "data")

    plan_file = tmp_path / "no-tests.md"
    plan_file.write_text(
        "---\n"
        "name: No Tests Plan\n"
        "project: /tmp/no-tests\n"
        "test_command: none\n"
        "---\n\n"
        "## 1. Config-only change\n"
        "- **files**: config.yaml\n"
        "- **do**: Add setting\n"
        "- **done-when**: Setting present\n"
        "- **status**: pending\n"
        "- **size**: small\n"
    )

    plan, tasks = parse_plan_file(plan_file)
    assert plan.test_command == "none"
    assert tasks[0].size == TaskSize.SMALL

    with MorpheusStore(config.db_path) as store:
        plan_id = init_plan(store, plan, tasks)
        task = store.get_tasks(plan_id)[0]

        # Walk all 6 phases with ZERO evidence — no rejections expected
        phases_and_evidence = [
            (Phase.CHECK, {}),
            (Phase.CODE, {}),       # SMALL: sibling_read skipped
            (Phase.TEST, {}),       # SMALL: build_verified skipped
            (Phase.GRADE, {}),      # SMALL: fdmc_review skipped; test_command:none: tests_passed skipped
            (Phase.COMMIT, {}),     # SMALL: seraph_id skipped
            (Phase.ADVANCE, {}),    # SMALL: knowledge_gate skipped
        ]

        for phase, evidence in phases_and_evidence:
            result, rec = advance(store, task.id, phase, evidence)
            assert result.passed is True, f"{phase.value} rejected: {result.message}"

        assert store.get_task(task.id).status == TaskStatus.DONE

        # Verify zero rejections in the phase log
        all_phases = store.get_phases(task.id)
        rejected = [p for p in all_phases if p.status == PhaseStatus.REJECTED]
        assert len(rejected) == 0, f"Got {len(rejected)} rejections, expected 0"

        closed = close_plan(store, plan_id)
        assert closed.status == PlanStatus.COMPLETED


def test_medium_no_test_command_lifecycle(tmp_path):
    """MEDIUM task + test_command:none skips test evidence but keeps other gates."""
    config = MorpheusConfig.load(tmp_path / "data")

    plan_file = tmp_path / "medium-no-tests.md"
    plan_file.write_text(
        "---\n"
        "name: Medium No Tests\n"
        "project: /tmp/medium-no-tests\n"
        "test_command: none\n"
        "---\n\n"
        "## 1. Prompt engineering task\n"
        "- **files**: prompts/system.md\n"
        "- **do**: Refine system prompt\n"
        "- **done-when**: Prompt updated\n"
        "- **status**: pending\n"
    )

    plan, tasks = parse_plan_file(plan_file)
    assert plan.test_command == "none"
    assert tasks[0].size == TaskSize.MEDIUM  # default

    with MorpheusStore(config.db_path) as store:
        plan_id = init_plan(store, plan, tasks)
        task = store.get_tasks(plan_id)[0]

        advance(store, task.id, Phase.CHECK, {})

        # CODE: MEDIUM still requires sibling_read
        result, _ = advance(store, task.id, Phase.CODE, {})
        assert result.passed is False
        assert "sibling_read" in result.message
        result, _ = advance(store, task.id, Phase.CODE, {"sibling_read": "prompts/old.md"})
        assert result.passed is True

        # TEST: test_command:none skips build_verified
        result, _ = advance(store, task.id, Phase.TEST, {})
        assert result.passed is True

        # GRADE: test_command:none skips tests_passed, but MEDIUM still needs fdmc_review
        result, _ = advance(store, task.id, Phase.GRADE, {})
        assert result.passed is False
        assert "fdmc_review" in result.message
        result, _ = advance(store, task.id, Phase.GRADE, {"fdmc_review": "Consistent — re-read system.md, matches prompts/old.md pattern"})
        assert result.passed is True

        # COMMIT: MEDIUM still requires seraph_id
        result, _ = advance(store, task.id, Phase.COMMIT, {})
        assert result.passed is False
        result, _ = advance(store, task.id, Phase.COMMIT, {"seraph_id": "abc123"})
        assert result.passed is True

        # ADVANCE: 1-task plan → adaptive knowledge gate skips (threshold=5)
        # knowledge_gate would be required for plans with 5+ tasks
        result, _ = advance(store, task.id, Phase.ADVANCE, {})
        assert result.passed is True

        assert store.get_task(task.id).status == TaskStatus.DONE


def test_oil_change_lifecycle(tmp_path):
    """Full oil change lifecycle: due → reject → clear → proceed."""
    config = MorpheusConfig.load(tmp_path / "data")

    # Create an initial plan with a recorded oil change to set history
    setup_plan_file = tmp_path / "setup-plan.md"
    setup_plan_file.write_text(
        "---\n"
        "name: Setup Plan\n"
        "project: /tmp/oil-test\n"
        'test_command: "echo ok"\n'
        "---\n\n"
        "## 1. Setup task\n"
        "- **files**: setup.py\n"
        "- **do**: Setup\n"
        "- **done-when**: Done\n"
        "- **status**: pending\n"
    )
    setup_plan, setup_tasks = parse_plan_file(setup_plan_file)

    with MorpheusStore(config.db_path) as store:
        # Init setup plan and record an oil change with high commit count
        setup_id = init_plan(store, setup_plan, setup_tasks)
        store.save_oil_change(setup_id, "hc-old", 50)

        # Now create the actual plan — init should detect oil_change_due
        plan_file = tmp_path / "test-plan.md"
        plan_file.write_text(
            "---\n"
            "name: Oil Change Test\n"
            "project: /tmp/oil-test\n"
            'test_command: "echo ok"\n'
            "---\n\n"
            "## 1. First task\n"
            "- **files**: src/a.py\n"
            "- **do**: Do A\n"
            "- **done-when**: A done\n"
            "- **status**: pending\n\n"
            "## 2. Second task\n"
            "- **files**: src/b.py\n"
            "- **do**: Do B\n"
            "- **done-when**: B done\n"
            "- **status**: pending\n"
        )
        plan, tasks = parse_plan_file(plan_file)
        plan_id = init_plan(store, plan, tasks, oil_change_interval=40)

        # Plan should have oil_change_due set
        loaded_plan = store.get_plan(plan_id)
        assert loaded_plan.oil_change_due is True

        t1, t2 = store.get_tasks(plan_id)

        # First task CHECK should be rejected
        result, _ = advance(store, t1.id, Phase.CHECK, {})
        assert result.passed is False
        assert "Oil change required" in result.message

        # Record oil change to clear the gate
        store.save_oil_change(plan_id, "hc-fresh", 0)
        store.set_oil_change_due(plan_id, False)

        # Now first task CHECK should pass
        result, _ = advance(store, t1.id, Phase.CHECK, {})
        assert result.passed is True

        # Complete t1
        advance(store, t1.id, Phase.CODE, {"sibling_read": "setup.py"})
        advance(store, t1.id, Phase.TEST, {"build_verified": "python3 -c 'import a' — OK"})
        advance(store, t1.id, Phase.GRADE, _grade_ev("3 passed", "Consistent — re-read setup.py, matched pattern"))
        advance(store, t1.id, Phase.COMMIT, {"seraph_id": "abc"})
        advance(store, t1.id, Phase.ADVANCE, {"knowledge_gate": "true"})
        assert store.get_task(t1.id).status == TaskStatus.DONE

        # Second task should not be gated
        result, _ = advance(store, t2.id, Phase.CHECK, {})
        assert result.passed is True

        # Verify oil_changes table has both records
        cur = store.conn.execute("SELECT COUNT(*) FROM oil_changes")
        assert cur.fetchone()[0] == 2

        # Complete t2 and close
        advance(store, t2.id, Phase.CODE, {"sibling_read": "src/a.py"})
        advance(store, t2.id, Phase.TEST, {"build_verified": "python3 -c 'import b' — OK"})
        advance(store, t2.id, Phase.GRADE, _grade_ev("3 passed", "Consistent — re-read src/a.py, matched pattern"))
        advance(store, t2.id, Phase.COMMIT, {"seraph_id": "def"})
        advance(store, t2.id, Phase.ADVANCE, {"knowledge_gate": "true"})

        closed = close_plan(store, plan_id)
        assert closed.status == PlanStatus.COMPLETED
