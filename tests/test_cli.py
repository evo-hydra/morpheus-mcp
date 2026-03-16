"""Tests for the CLI."""

from __future__ import annotations

import os

import pytest
from typer.testing import CliRunner

from morpheus_mcp.cli.app import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def temp_data_dir(tmp_path, monkeypatch):
    """Point Morpheus to a temp directory for all CLI tests."""
    monkeypatch.setenv("MORPHEUS_DATA_DIR", str(tmp_path / "data"))


@pytest.fixture
def plan_file(tmp_path):
    """Create a sample plan file."""
    f = tmp_path / "cli-test-plan.md"
    f.write_text(
        "---\nname: CLI Test Plan\nproject: /tmp\n"
        'test_command: "echo ok"\n---\n\n'
        "## 1. First\n- **files**: a.py\n- **do**: do a\n"
        "- **done-when**: a works\n- **status**: pending\n"
    )
    return f


class TestInit:
    def test_init_success(self, plan_file):
        """init command parses and displays plan."""
        result = runner.invoke(app, ["init", str(plan_file)])
        assert result.exit_code == 0
        assert "CLI Test Plan" in result.output

    def test_init_missing_file(self):
        """init fails for nonexistent file."""
        result = runner.invoke(app, ["init", "/nonexistent.md"])
        assert result.exit_code == 1


class TestStatus:
    def test_status_no_plans(self):
        """status shows message when no plans exist."""
        result = runner.invoke(app, ["status"])
        assert result.exit_code == 0
        assert "No plans found" in result.output

    def test_status_after_init(self, plan_file):
        """status shows plan after init."""
        runner.invoke(app, ["init", str(plan_file)])
        result = runner.invoke(app, ["status"])
        assert result.exit_code == 0
        assert "CLI Test Plan" in result.output


class TestAdvance:
    def test_advance_invalid_phase(self):
        """advance rejects invalid phase."""
        result = runner.invoke(app, ["advance", "fake_id", "BOGUS"])
        assert result.exit_code == 1
        assert "Invalid phase" in result.output

    def test_advance_invalid_json(self):
        """advance rejects bad JSON."""
        result = runner.invoke(app, ["advance", "fake_id", "CHECK", "not json"])
        assert result.exit_code == 1
        assert "Invalid JSON" in result.output


class TestClose:
    def test_close_nonexistent(self):
        """close fails for unknown plan."""
        result = runner.invoke(app, ["close", "unknown"])
        assert result.exit_code == 1


class TestList:
    def test_list_empty(self):
        """list shows message when no plans."""
        result = runner.invoke(app, ["list"])
        assert result.exit_code == 0
        assert "No plans found" in result.output

    def test_list_after_init(self, plan_file):
        """list shows plans after init."""
        runner.invoke(app, ["init", str(plan_file)])
        result = runner.invoke(app, ["list"])
        assert result.exit_code == 0
        assert "CLI Test Plan" in result.output
