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
    def test_has_6_tools(self, server):
        """Server registers exactly 6 tools."""
        tools = [t.name for t in server._tool_manager.list_tools()]
        assert len(tools) == 6
        assert "morpheus_init" in tools
        assert "morpheus_status" in tools
        assert "morpheus_advance" in tools
        assert "morpheus_advance_batch" in tools
        assert "morpheus_progress" in tools
        assert "morpheus_close" in tools


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
