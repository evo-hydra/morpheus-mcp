"""Tests for the SQLite store."""

from __future__ import annotations

import pytest

from morpheus_mcp.core.store import MorpheusStore
from morpheus_mcp.models.enums import Phase, PhaseStatus, PlanStatus, TaskSize, TaskStatus
from morpheus_mcp.models.plan import PhaseRecord, PlanRecord, TaskRecord


class TestStoreLifecycle:
    def test_context_manager(self, tmp_path):
        """Store opens/closes correctly."""
        db_path = tmp_path / "ctx.db"
        with MorpheusStore(db_path) as s:
            assert s.conn is not None
        assert s._conn is None

    def test_conn_guard(self, tmp_path):
        """Accessing conn before open() raises."""
        s = MorpheusStore(tmp_path / "guard.db")
        with pytest.raises(RuntimeError, match="Store is not open"):
            _ = s.conn

    def test_creates_parent_dirs(self, tmp_path):
        """Parent directories are created."""
        db_path = tmp_path / "nested" / "dir" / "test.db"
        with MorpheusStore(db_path):
            assert db_path.exists()

    def test_wal_mode(self, store):
        """WAL mode is active."""
        mode = store.conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"

    def test_foreign_keys_on(self, store):
        """Foreign keys are enabled."""
        fk = store.conn.execute("PRAGMA foreign_keys").fetchone()[0]
        assert fk == 1


class TestSchemaVersion:
    def test_initial_version(self, store):
        """Schema version is set on first open."""
        cur = store.conn.execute(
            "SELECT value FROM morpheus_meta WHERE key='schema_version'"
        )
        assert cur.fetchone()[0] == "4"

    def test_idempotent_open(self, tmp_path):
        """Opening twice doesn't change schema version."""
        db_path = tmp_path / "idem.db"
        with MorpheusStore(db_path):
            pass
        with MorpheusStore(db_path) as s:
            cur = s.conn.execute(
                "SELECT value FROM morpheus_meta WHERE key='schema_version'"
            )
            assert cur.fetchone()[0] == "4"


class TestPlanCRUD:
    def test_save_and_get(self, store):
        """Save and retrieve a plan."""
        plan = PlanRecord(name="Test", project="/tmp")
        store.save_plan(plan)
        retrieved = store.get_plan(plan.id)
        assert retrieved is not None
        assert retrieved.name == "Test"
        assert retrieved.status == PlanStatus.PENDING

    def test_get_nonexistent(self, store):
        """Getting a nonexistent plan returns None."""
        assert store.get_plan("nonexistent") is None

    def test_update_status(self, store):
        """Update plan status."""
        plan = PlanRecord(name="Test", project="/tmp")
        store.save_plan(plan)
        store.update_plan_status(plan.id, PlanStatus.ACTIVE)
        retrieved = store.get_plan(plan.id)
        assert retrieved.status == PlanStatus.ACTIVE

    def test_completed_sets_closed_at(self, store):
        """Completing a plan sets closed_at."""
        plan = PlanRecord(name="Test", project="/tmp")
        store.save_plan(plan)
        store.update_plan_status(plan.id, PlanStatus.COMPLETED)
        retrieved = store.get_plan(plan.id)
        assert retrieved.closed_at is not None

    def test_mode_roundtrip(self, store):
        """Plan mode field roundtrips through store."""
        plan = PlanRecord(name="GF", project="/tmp", mode="greenfield")
        store.save_plan(plan)
        retrieved = store.get_plan(plan.id)
        assert retrieved.mode == "greenfield"

    def test_mode_defaults_to_standard(self, store):
        """Plans without explicit mode default to standard."""
        plan = PlanRecord(name="Default", project="/tmp")
        store.save_plan(plan)
        retrieved = store.get_plan(plan.id)
        assert retrieved.mode == "standard"

    def test_list_plans(self, store):
        """List all plans."""
        store.save_plan(PlanRecord(name="A", project="/a"))
        store.save_plan(PlanRecord(name="B", project="/b"))
        plans = store.list_plans()
        assert len(plans) == 2


class TestTaskCRUD:
    def test_save_and_get(self, store, sample_plan_record, sample_task_records):
        """Save and retrieve tasks."""
        store.save_plan(sample_plan_record)
        for t in sample_task_records:
            store.save_task(t)
        tasks = store.get_tasks(sample_plan_record.id)
        assert len(tasks) == 3
        assert tasks[0].seq == 1

    def test_get_task_by_id(self, store, sample_plan_record):
        """Retrieve a single task by ID."""
        store.save_plan(sample_plan_record)
        task = TaskRecord(plan_id=sample_plan_record.id, seq=1, title="T1")
        store.save_task(task)
        retrieved = store.get_task(task.id)
        assert retrieved is not None
        assert retrieved.title == "T1"

    def test_get_task_nonexistent(self, store):
        """Getting a nonexistent task returns None."""
        assert store.get_task("nonexistent") is None

    def test_update_status(self, store, sample_plan_record):
        """Update task status."""
        store.save_plan(sample_plan_record)
        task = TaskRecord(plan_id=sample_plan_record.id, seq=1, title="T1")
        store.save_task(task)
        store.update_task_status(task.id, TaskStatus.DONE)
        retrieved = store.get_task(task.id)
        assert retrieved.status == TaskStatus.DONE

    def test_next_pending(self, store, sample_plan_record, sample_task_records):
        """Get next pending task (lowest seq)."""
        store.save_plan(sample_plan_record)
        for t in sample_task_records:
            store.save_task(t)
        nxt = store.get_next_pending_task(sample_plan_record.id)
        assert nxt.seq == 1
        store.update_task_status(nxt.id, TaskStatus.DONE)
        nxt2 = store.get_next_pending_task(sample_plan_record.id)
        assert nxt2.seq == 2

    def test_next_pending_none(self, store, sample_plan_record, sample_task_records):
        """Returns None when no pending tasks."""
        store.save_plan(sample_plan_record)
        for t in sample_task_records:
            store.save_task(t)
            store.update_task_status(t.id, TaskStatus.DONE)
        assert store.get_next_pending_task(sample_plan_record.id) is None

    def test_cascade_delete(self, store, sample_plan_record):
        """Deleting a plan cascades to tasks."""
        store.save_plan(sample_plan_record)
        store.save_task(TaskRecord(plan_id=sample_plan_record.id, seq=1, title="T1"))
        store.conn.execute("DELETE FROM plans WHERE id = ?", (sample_plan_record.id,))
        store.conn.commit()
        assert store.get_tasks(sample_plan_record.id) == []

    def test_save_and_get_with_size(self, store, sample_plan_record):
        """Tasks persist size field correctly."""
        store.save_plan(sample_plan_record)
        task = TaskRecord(
            plan_id=sample_plan_record.id, seq=1, title="Small",
            size=TaskSize.SMALL,
        )
        store.save_task(task)
        retrieved = store.get_task(task.id)
        assert retrieved.size == TaskSize.SMALL

    def test_size_defaults_to_medium(self, store, sample_plan_record):
        """Tasks without explicit size default to MEDIUM."""
        store.save_plan(sample_plan_record)
        task = TaskRecord(plan_id=sample_plan_record.id, seq=1, title="Default")
        store.save_task(task)
        retrieved = store.get_task(task.id)
        assert retrieved.size == TaskSize.MEDIUM

    def test_size_roundtrip_all_values(self, store, sample_plan_record):
        """All TaskSize values roundtrip through the store."""
        store.save_plan(sample_plan_record)
        for i, size in enumerate(TaskSize, start=1):
            task = TaskRecord(
                plan_id=sample_plan_record.id, seq=i, title=f"Size {size.value}",
                size=size,
            )
            store.save_task(task)
            retrieved = store.get_task(task.id)
            assert retrieved.size == size


class TestPhaseCRUD:
    def test_save_and_get(self, store, sample_plan_record):
        """Save and retrieve phases."""
        store.save_plan(sample_plan_record)
        task = TaskRecord(plan_id=sample_plan_record.id, seq=1, title="T1")
        store.save_task(task)
        phase = PhaseRecord(task_id=task.id, phase=Phase.CHECK)
        store.save_phase(phase)
        phases = store.get_phases(task.id)
        assert len(phases) == 1
        assert phases[0].phase == Phase.CHECK

    def test_update_phase(self, store, sample_plan_record):
        """Update phase status and evidence."""
        store.save_plan(sample_plan_record)
        task = TaskRecord(plan_id=sample_plan_record.id, seq=1, title="T1")
        store.save_task(task)
        phase = PhaseRecord(task_id=task.id, phase=Phase.CHECK)
        store.save_phase(phase)
        store.update_phase(phase.id, PhaseStatus.COMPLETED, '{"result": "ok"}')
        phases = store.get_phases(task.id)
        assert phases[0].status == PhaseStatus.COMPLETED
        assert phases[0].completed_at is not None
