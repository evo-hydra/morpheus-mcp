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
        """Server registers exactly 6 core tools (v4 surface collapse)."""
        tools = [t.name for t in server._tool_manager.list_tools()]
        assert len(tools) == 6
        assert "morpheus_init" in tools
        assert "morpheus_status" in tools
        assert "morpheus_advance" in tools
        assert "morpheus_oil_change" in tools
        assert "morpheus_gate_summary" in tools
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


@pytest.mark.skip(reason="morpheus_version removed from MCP surface in v4 collapse")
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


@pytest.mark.skip(reason="morpheus_progress removed from MCP surface in v4 collapse")
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


@pytest.mark.skip(reason="morpheus_advance_batch removed from MCP surface in v4 collapse")
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


@pytest.mark.skip(reason="morpheus_reflect removed from MCP surface in v4 collapse — use inline reflect via advance")
class TestMorpheusReflect:
    def test_reflect_records_outcome(self, server, plan_file):
        """morpheus_reflect records a gate outcome and returns confirmation."""
        init_result = server._tool_manager._tools["morpheus_init"].fn(str(plan_file))
        plan_id = _extract_plan_id(init_result)
        task_id = _extract_task_id(init_result, "Task one")
        result = server._tool_manager._tools["morpheus_reflect"].fn(
            plan_id, task_id, "sibling_read",
            caught_issue=True, changed_code=True,
            detail="Matched singleton pattern from sibling",
        )
        assert "Reflect recorded" in result
        assert "CAUGHT" in result
        assert "code changed" in result

    def test_reflect_clear_outcome(self, server, plan_file):
        """morpheus_reflect records a clear (no issue) outcome."""
        init_result = server._tool_manager._tools["morpheus_init"].fn(str(plan_file))
        plan_id = _extract_plan_id(init_result)
        task_id = _extract_task_id(init_result, "Task one")
        result = server._tool_manager._tools["morpheus_reflect"].fn(
            plan_id, task_id, "seraph_assess",
            caught_issue=False, changed_code=False,
            detail="Grade A, no action needed",
        )
        assert "Reflect recorded" in result
        assert "CLEAR" in result
        assert "code changed" not in result


class TestMorpheusGateSummary:
    def test_empty_summary(self, server, plan_file):
        """morpheus_gate_summary returns message when no outcomes exist."""
        result = server._tool_manager._tools["morpheus_gate_summary"].fn()
        assert "No gate outcomes" in result

    def test_summary_after_inline_reflect(self, server, plan_file):
        """morpheus_gate_summary returns table after recording inline reflect outcomes."""
        init_result = server._tool_manager._tools["morpheus_init"].fn(str(plan_file))
        plan_id = _extract_plan_id(init_result)
        task_id = _extract_task_id(init_result, "Task one")

        # Advance through CHECK then CODE with inline reflect data
        advance = server._tool_manager._tools["morpheus_advance"].fn
        advance(task_id, "CHECK", "{}")
        advance(task_id, "CODE", json.dumps({
            "sibling_read": "src/sibling.py",
            "reflect_caught_issue": True,
            "reflect_changed_code": True,
            "reflect_detail": "caught duplicate type",
        }))

        result = server._tool_manager._tools["morpheus_gate_summary"].fn()
        assert "Gate Outcomes" in result or "sibling_read" in result

    def test_summary_scoped_to_plan(self, server, plan_file):
        """morpheus_gate_summary can be scoped to a specific plan."""
        init_result = server._tool_manager._tools["morpheus_init"].fn(str(plan_file))
        plan_id = _extract_plan_id(init_result)
        task_id = _extract_task_id(init_result, "Task one")

        # Advance with inline reflect to generate gate outcome
        advance = server._tool_manager._tools["morpheus_advance"].fn
        advance(task_id, "CHECK", "{}")
        advance(task_id, "CODE", json.dumps({
            "sibling_read": "src/sibling.py",
            "reflect_caught_issue": True,
            "reflect_changed_code": True,
            "reflect_detail": "found issue",
        }))

        result = server._tool_manager._tools["morpheus_gate_summary"].fn(plan_id)
        assert "sibling_read" in result


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
