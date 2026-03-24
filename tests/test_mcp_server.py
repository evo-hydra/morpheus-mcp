"""Tests for the MCP server."""

from __future__ import annotations

import json

import pytest

from morpheus_mcp.config import MorpheusConfig


@pytest.fixture
def server(tmp_path):
    """Create an MCP server with a temp database."""
    config = MorpheusConfig.load(tmp_path)
    from morpheus_mcp.mcp.server import create_server

    return create_server(config)


@pytest.fixture
def plan_file(tmp_path):
    """Create a sample plan file."""
    f = tmp_path / "test-plan.md"
    f.write_text(
        "---\nname: MCP Test Plan\nproject: /tmp\n"
        'test_command: "echo ok"\n---\n\n'
        "## 1. Task one\n- **files**: a.py\n- **do**: do a\n"
        "- **done-when**: a works\n- **status**: pending\n\n"
        "## 2. Task two\n- **files**: b.py\n- **do**: do b\n"
        "- **done-when**: b works\n- **status**: pending\n"
    )
    return f


class TestCreateServer:
    def test_has_7_tools(self, server):
        """Server registers exactly 7 tools."""
        tools = [t.name for t in server._tool_manager.list_tools()]
        assert len(tools) == 7
        assert "morpheus_init" in tools
        assert "morpheus_status" in tools
        assert "morpheus_advance" in tools
        assert "morpheus_advance_batch" in tools
        assert "morpheus_progress" in tools
        assert "morpheus_version" in tools
        assert "morpheus_close" in tools


class TestSelfTest:
    def test_self_test_passes_on_healthy_db(self, tmp_path):
        """Self-test should pass on a fresh database."""
        from morpheus_mcp.mcp.server import _self_test

        db_path = str(tmp_path / "morpheus.db")
        assert _self_test(db_path) is True

    def test_self_test_cleans_up(self, tmp_path):
        """Self-test plan should not remain in the database."""
        from morpheus_mcp.core.store import MorpheusStore
        from morpheus_mcp.mcp.server import _self_test

        db_path = str(tmp_path / "morpheus.db")
        _self_test(db_path)
        with MorpheusStore(db_path) as store:
            plans = store.list_plans()
            assert all(p.id != "__selftest__" for p in plans)


class TestMorpheusInit:
    def test_init_returns_summary(self, server, plan_file):
        """morpheus_init returns a plan summary."""
        # Access the tool function directly
        result = server._tool_manager._tools["morpheus_init"].fn(str(plan_file))
        assert "MCP Test Plan" in result
        assert "Task one" in result
        assert "Task two" in result

    def test_init_nonexistent_file(self, server):
        """morpheus_init returns error for missing file."""
        result = server._tool_manager._tools["morpheus_init"].fn("/nonexistent/plan.md")
        assert "Error" in result


class TestMorpheusStatus:
    def test_status_no_plans(self, server):
        """morpheus_status returns message when no plans exist."""
        result = server._tool_manager._tools["morpheus_status"].fn()
        assert "No plans found" in result

    def test_status_after_init(self, server, plan_file):
        """morpheus_status returns plan info after init."""
        server._tool_manager._tools["morpheus_init"].fn(str(plan_file))
        result = server._tool_manager._tools["morpheus_status"].fn()
        assert "MCP Test Plan" in result
        assert "Task one" in result


class TestMorpheusAdvance:
    def test_advance_invalid_phase(self, server, plan_file):
        """morpheus_advance rejects invalid phase name."""
        result = server._tool_manager._tools["morpheus_advance"].fn("fake_id", "INVALID")
        assert "Error" in result
        assert "Invalid phase" in result

    def test_advance_invalid_json(self, server, plan_file):
        """morpheus_advance rejects invalid JSON evidence."""
        result = server._tool_manager._tools["morpheus_advance"].fn("fake_id", "CHECK", "not json")
        assert "Error" in result

    def test_advance_unknown_task(self, server, plan_file):
        """morpheus_advance rejects unknown task_id."""
        server._tool_manager._tools["morpheus_init"].fn(str(plan_file))
        result = server._tool_manager._tools["morpheus_advance"].fn("unknown", "CHECK")
        assert "REJECTED" in result or "not found" in result


class TestSkipReasonMCP:
    def test_advance_with_skip_reason(self, server, plan_file):
        """morpheus_advance accepts skip_reason and passes the gate."""
        init_result = server._tool_manager._tools["morpheus_init"].fn(str(plan_file))
        # Extract first task_id
        for line in init_result.splitlines():
            if "Task one" in line:
                task_id = line.split("`")[1]
                break

        # CHECK — no gate
        server._tool_manager._tools["morpheus_advance"].fn(task_id, "CHECK")
        # CODE — skip_reason instead of sibling_read
        result = server._tool_manager._tools["morpheus_advance"].fn(
            task_id, "CODE", "{}", "no siblings in greenfield project",
        )
        assert "gate passed" in result


class TestMorpheusClose:
    def test_close_nonexistent(self, server):
        """morpheus_close returns error for unknown plan."""
        result = server._tool_manager._tools["morpheus_close"].fn("unknown")
        assert "Error" in result or "not found" in result

    def test_close_after_init(self, server, plan_file):
        """morpheus_close marks plan complete."""
        # Init first
        init_result = server._tool_manager._tools["morpheus_init"].fn(str(plan_file))
        # Extract plan_id from the summary (it's in the ID line)
        # Parse the ID from the markdown output
        for line in init_result.splitlines():
            if "**ID:**" in line:
                plan_id = line.split("`")[1]
                break

        result = server._tool_manager._tools["morpheus_close"].fn(plan_id)
        assert "Plan Complete" in result


class TestMorpheusVersion:
    def test_version_returns_json(self, server):
        """morpheus_version returns valid JSON with expected fields."""
        result = server._tool_manager._tools["morpheus_version"].fn()
        data = json.loads(result)
        assert "server_version" in data
        assert "schema_version" in data
        assert "python_version" in data

    def test_version_matches_package(self, server):
        """Server version matches __init__.py version."""
        result = server._tool_manager._tools["morpheus_version"].fn()
        data = json.loads(result)
        from morpheus_mcp import __version__
        assert data["server_version"] == __version__

    def test_schema_version_matches_store(self, server):
        """Schema version matches store constant."""
        result = server._tool_manager._tools["morpheus_version"].fn()
        data = json.loads(result)
        from morpheus_mcp.core.store import SCHEMA_VERSION
        assert data["schema_version"] == SCHEMA_VERSION


def _extract_task_id(init_result: str, task_name: str) -> str:
    """Extract task_id from morpheus_init output by task name."""
    for line in init_result.splitlines():
        if task_name in line:
            return line.split("`")[1]
    raise ValueError(f"Task '{task_name}' not found in init output")


def _extract_plan_id(init_result: str) -> str:
    """Extract plan_id from morpheus_init output."""
    for line in init_result.splitlines():
        if "**ID:**" in line:
            return line.split("`")[1]
    raise ValueError("Plan ID not found in init output")


class TestMorpheusProgress:
    def test_progress_valid_task(self, server, plan_file):
        """morpheus_progress records a message for a valid task."""
        init_result = server._tool_manager._tools["morpheus_init"].fn(str(plan_file))
        task_id = _extract_task_id(init_result, "Task one")
        result = server._tool_manager._tools["morpheus_progress"].fn(task_id, "halfway done")
        assert "Progress logged" in result
        assert "halfway done" in result

    def test_progress_unknown_task(self, server, plan_file):
        """morpheus_progress returns error for unknown task_id."""
        server._tool_manager._tools["morpheus_init"].fn(str(plan_file))
        result = server._tool_manager._tools["morpheus_progress"].fn("nonexistent", "msg")
        assert "Error" in result or "not found" in result

    def test_progress_empty_message(self, server, plan_file):
        """morpheus_progress accepts an empty message."""
        init_result = server._tool_manager._tools["morpheus_init"].fn(str(plan_file))
        task_id = _extract_task_id(init_result, "Task one")
        result = server._tool_manager._tools["morpheus_progress"].fn(task_id, "")
        assert "Progress logged" in result


class TestMorpheusAdvanceBatch:
    def test_batch_valid(self, server, plan_file):
        """morpheus_advance_batch processes a valid batch array."""
        init_result = server._tool_manager._tools["morpheus_init"].fn(str(plan_file))
        t1 = _extract_task_id(init_result, "Task one")
        t2 = _extract_task_id(init_result, "Task two")
        batch = json.dumps([
            {"task_id": t1, "phase": "CHECK", "evidence": {}},
            {"task_id": t2, "phase": "CHECK", "evidence": {}},
        ])
        result = server._tool_manager._tools["morpheus_advance_batch"].fn(batch)
        assert "Batch Advance" in result
        assert "PASSED" in result

    def test_batch_empty_array(self, server, plan_file):
        """morpheus_advance_batch rejects empty array."""
        server._tool_manager._tools["morpheus_init"].fn(str(plan_file))
        result = server._tool_manager._tools["morpheus_advance_batch"].fn("[]")
        assert "Error" in result

    def test_batch_invalid_json(self, server, plan_file):
        """morpheus_advance_batch rejects invalid JSON."""
        server._tool_manager._tools["morpheus_init"].fn(str(plan_file))
        result = server._tool_manager._tools["morpheus_advance_batch"].fn("not json")
        assert "Error" in result


class TestMorpheusCloseEdgeCases:
    def test_double_close(self, server, plan_file):
        """Closing an already-closed plan returns a sensible result."""
        init_result = server._tool_manager._tools["morpheus_init"].fn(str(plan_file))
        plan_id = _extract_plan_id(init_result)
        # First close
        result1 = server._tool_manager._tools["morpheus_close"].fn(plan_id)
        assert "Plan Complete" in result1
        # Second close — should not crash (either idempotent success or clear error)
        result2 = server._tool_manager._tools["morpheus_close"].fn(plan_id)
        assert "Plan Complete" in result2 or "Error" in result2
