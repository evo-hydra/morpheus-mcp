"""Tests for plan file parser."""

from __future__ import annotations

import json

import pytest

from morpheus_mcp.core.parser import parse_plan_file
from morpheus_mcp.models.enums import TaskStatus


class TestParsePlanFile:
    def test_valid_plan(self, sample_plan_file):
        """Parses a valid plan with 3 tasks."""
        plan, tasks = parse_plan_file(sample_plan_file)
        assert plan.name == "Test Plan"
        assert plan.project == "/tmp/test-project"
        assert plan.test_command == "python3 -m pytest tests/ -v"
        assert plan.grade_enabled is True
        assert len(tasks) == 3

    def test_task_fields(self, sample_plan_file):
        """Tasks have correct fields."""
        _, tasks = parse_plan_file(sample_plan_file)
        t1 = tasks[0]
        assert t1.seq == 1
        assert t1.title == "First task"
        assert t1.do_text == "Implement foo and bar"
        assert t1.done_when == "Tests pass"
        assert t1.status == TaskStatus.PENDING

        files = json.loads(t1.files_json)
        assert files == ["src/foo.py", "src/bar.py"]

    def test_task_status_parsing(self, sample_plan_file):
        """Status strings map to enums."""
        _, tasks = parse_plan_file(sample_plan_file)
        assert tasks[0].status == TaskStatus.PENDING
        assert tasks[2].status == TaskStatus.DONE

    def test_plan_ids_linked(self, sample_plan_file):
        """All tasks reference the plan's ID."""
        plan, tasks = parse_plan_file(sample_plan_file)
        for task in tasks:
            assert task.plan_id == plan.id

    def test_grade_false(self, sample_plan_no_grade_file):
        """grade: false disables grading."""
        plan, _ = parse_plan_file(sample_plan_no_grade_file)
        assert plan.grade_enabled is False

    def test_missing_file(self, tmp_path):
        """Raises FileNotFoundError for missing file."""
        with pytest.raises(FileNotFoundError):
            parse_plan_file(tmp_path / "nonexistent.md")

    def test_no_frontmatter(self, tmp_path):
        """Raises ValueError if no frontmatter."""
        f = tmp_path / "bad.md"
        f.write_text("## 1. Task\n- **do**: stuff\n")
        with pytest.raises(ValueError, match="No frontmatter"):
            parse_plan_file(f)

    def test_no_tasks(self, tmp_path):
        """Raises ValueError if no task sections."""
        f = tmp_path / "empty.md"
        f.write_text("---\nname: Empty\n---\n\nJust text.\n")
        with pytest.raises(ValueError, match="No task sections"):
            parse_plan_file(f)

    def test_single_file_task(self, tmp_path):
        """Tasks with a single file parse correctly."""
        f = tmp_path / "single.md"
        f.write_text("---\nname: Single\n---\n\n## 1. One\n- **files**: src/x.py\n- **do**: x\n- **done-when**: y\n- **status**: pending\n")
        _, tasks = parse_plan_file(f)
        files = json.loads(tasks[0].files_json)
        assert files == ["src/x.py"]
