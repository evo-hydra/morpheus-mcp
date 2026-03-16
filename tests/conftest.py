"""Shared test fixtures."""

from __future__ import annotations

import pytest

from morpheus_mcp.core.store import MorpheusStore
from morpheus_mcp.models.enums import PlanStatus, TaskStatus
from morpheus_mcp.models.plan import PlanRecord, TaskRecord


SAMPLE_PLAN_MD = """\
---
name: Test Plan
project: /tmp/test-project
test_command: "python3 -m pytest tests/ -v"
---

## 1. First task
- **files**: src/foo.py, src/bar.py
- **do**: Implement foo and bar
- **done-when**: Tests pass
- **status**: pending

## 2. Second task
- **files**: src/baz.py
- **do**: Implement baz
- **done-when**: Baz works correctly
- **status**: pending

## 3. Third task with no grade
- **files**: docs/README.md
- **do**: Write docs
- **done-when**: README exists
- **status**: done
"""

SAMPLE_PLAN_NO_GRADE_MD = """\
---
name: Config Plan
project: /tmp/config
test_command: "echo ok"
grade: false
---

## 1. Add config
- **files**: config.toml
- **do**: Add config file
- **done-when**: Config exists
- **status**: pending
"""


@pytest.fixture
def store(tmp_path):
    """Store backed by temporary database."""
    db_path = tmp_path / "test.db"
    with MorpheusStore(db_path) as s:
        yield s


@pytest.fixture
def sample_plan_file(tmp_path):
    """Write sample plan to a temp file and return path."""
    plan_file = tmp_path / "test-plan.md"
    plan_file.write_text(SAMPLE_PLAN_MD)
    return plan_file


@pytest.fixture
def sample_plan_no_grade_file(tmp_path):
    """Write sample plan with grade=false to a temp file."""
    plan_file = tmp_path / "no-grade-plan.md"
    plan_file.write_text(SAMPLE_PLAN_NO_GRADE_MD)
    return plan_file


@pytest.fixture
def sample_plan_record():
    """A sample PlanRecord for testing."""
    return PlanRecord(name="Test Plan", project="/tmp/test")


@pytest.fixture
def sample_task_records(sample_plan_record):
    """Sample TaskRecords linked to the sample plan."""
    return [
        TaskRecord(plan_id=sample_plan_record.id, seq=1, title="Task 1"),
        TaskRecord(plan_id=sample_plan_record.id, seq=2, title="Task 2"),
        TaskRecord(plan_id=sample_plan_record.id, seq=3, title="Task 3"),
    ]
