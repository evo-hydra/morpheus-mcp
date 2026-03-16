"""End-to-end integration test: full Morpheus lifecycle."""

from __future__ import annotations

import json

import pytest

from morpheus_mcp.config import MorpheusConfig
from morpheus_mcp.core.engine import advance, close_plan, init_plan
from morpheus_mcp.core.parser import parse_plan_file
from morpheus_mcp.core.store import MorpheusStore
from morpheus_mcp.models.enums import Phase, PhaseStatus, PlanStatus, TaskStatus


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

        # Phase: CODE (requires FDMC with sibling_read)
        fdmc = {
            "fdmc_preflight": json.dumps({
                "consistent": {
                    "sibling_read": "merovingian/src/merovingian/config.py",
                    "note": "matched frozen dataclass pattern",
                },
                "future_proof": "no caller assumptions",
                "dynamic": "all values configurable",
                "modular": "single responsibility",
            })
        }
        result, phase = advance(store, task1.id, Phase.CODE, fdmc)
        assert result.passed is True

        # Phase: TEST (requires build_verified)
        result, phase = advance(
            store, task1.id, Phase.TEST, {"build_verified": "python3 -c 'import config'"}
        )
        assert result.passed is True

        # Phase: GRADE (requires tests_passed)
        result, phase = advance(
            store, task1.id, Phase.GRADE, {"tests_passed": "12 tests passed in 0.3s"}
        )
        assert result.passed is True

        # Phase: COMMIT (requires seraph_id)
        result, phase = advance(
            store, task1.id, Phase.COMMIT, {"seraph_id": "abc123def456"}
        )
        assert result.passed is True

        # Phase: ADVANCE (requires knowledge_gate)
        result, phase = advance(
            store, task1.id, Phase.ADVANCE, {"knowledge_gate": "nothing_surprised"}
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
        advance(store, task2.id, Phase.CODE, fdmc)
        advance(store, task2.id, Phase.TEST, {"build_verified": "ok"})
        advance(store, task2.id, Phase.GRADE, {"tests_passed": "ok"})
        advance(store, task2.id, Phase.COMMIT, {"seraph_id": "def789"})
        advance(store, task2.id, Phase.ADVANCE, {"knowledge_gate": "nothing_surprised"})

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

        # Try CODE without FDMC — should be rejected
        result, phase = advance(store, task.id, Phase.CODE, {})
        assert result.passed is False
        assert "fdmc_preflight" in result.message

        # Retry with proper evidence — should pass
        fdmc = {
            "fdmc_preflight": json.dumps({
                "consistent": {"sibling_read": "some/file.py"},
                "future_proof": "ok",
                "dynamic": "ok",
                "modular": "ok",
            })
        }
        result, phase = advance(store, task.id, Phase.CODE, fdmc)
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
        advance(store, task.id, Phase.CODE, {
            "fdmc_preflight": json.dumps({
                "consistent": {"sibling_read": "x.py"},
                "future_proof": "ok", "dynamic": "ok", "modular": "ok",
            })
        })
        advance(store, task.id, Phase.TEST, {"build_verified": "ok"})
        advance(store, task.id, Phase.GRADE, {"tests_passed": "ok"})

        # COMMIT without seraph_id — should pass because grade=false
        result, _ = advance(store, task.id, Phase.COMMIT, {})
        assert result.passed is True
