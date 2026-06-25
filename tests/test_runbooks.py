"""Tests for runbook-freshness detection (core/runbooks.py) and its gate validator."""

from __future__ import annotations

from pathlib import Path

import pytest

from morpheus_mcp.core.engine import _validate_runbook_fresh
from morpheus_mcp.core.runbooks import (
    find_touched_runbooks,
    gate_enabled,
    parse_covers,
)


# --- parse_covers ---

def test_parse_covers_block_list():
    text = (
        "---\n"
        "surface: auth\n"
        "covers:\n"
        "  - backend/app/api/auth/*.py\n"
        "  - frontend/src/auth/*.tsx\n"
        "last_verified: 2026-06-25\n"
        "---\n\n# Auth runbook\n"
    )
    assert parse_covers(text) == [
        "backend/app/api/auth/*.py",
        "frontend/src/auth/*.tsx",
    ]


def test_parse_covers_inline_list():
    text = "---\ncovers: [a/*.py, b/*.ts]\n---\n"
    assert parse_covers(text) == ["a/*.py", "b/*.ts"]


def test_parse_covers_single_value():
    text = "---\ncovers: backend/app/api/auth.py\n---\n"
    assert parse_covers(text) == ["backend/app/api/auth.py"]


def test_parse_covers_quoted_items():
    text = "---\ncovers:\n  - 'a/*.py'\n  - \"b/*.ts\"\n---\n"
    assert parse_covers(text) == ["a/*.py", "b/*.ts"]


def test_parse_covers_absent_or_no_frontmatter():
    assert parse_covers("---\nsurface: x\n---\n") == []
    assert parse_covers("# no frontmatter here\n") == []


# --- find_touched_runbooks ---

def _make_runbook(tmp_path: Path, name: str, covers_block: str) -> None:
    rb_dir = tmp_path / "docs" / "runbooks"
    rb_dir.mkdir(parents=True, exist_ok=True)
    (rb_dir / name).write_text(
        f"---\nsurface: {name}\n{covers_block}\n---\n\n# {name}\n",
        encoding="utf-8",
    )


def test_find_touched_matches(tmp_path: Path):
    _make_runbook(tmp_path, "auth.md", "covers:\n  - backend/app/api/auth/*.py")
    touched = find_touched_runbooks(
        ["backend/app/api/auth/login.py"], str(tmp_path),
    )
    assert touched == ["auth.md"]


def test_find_touched_no_match(tmp_path: Path):
    _make_runbook(tmp_path, "auth.md", "covers:\n  - backend/app/api/auth/*.py")
    touched = find_touched_runbooks(
        ["backend/app/services/spending.py"], str(tmp_path),
    )
    assert touched == []


def test_find_touched_no_runbook_dir(tmp_path: Path):
    # Project without docs/runbooks is unaffected.
    assert find_touched_runbooks(["anything.py"], str(tmp_path)) == []


def test_find_touched_no_target_files(tmp_path: Path):
    _make_runbook(tmp_path, "auth.md", "covers:\n  - '*.py'")
    assert find_touched_runbooks([], str(tmp_path)) == []


def test_find_touched_runbook_without_covers_ignored(tmp_path: Path):
    _make_runbook(tmp_path, "stub.md", "note: no covers here")
    assert find_touched_runbooks(["x.py"], str(tmp_path)) == []


def test_find_touched_multiple_runbooks_sorted(tmp_path: Path):
    _make_runbook(tmp_path, "b.md", "covers:\n  - 'src/*.py'")
    _make_runbook(tmp_path, "a.md", "covers:\n  - 'src/*.py'")
    assert find_touched_runbooks(["src/x.py"], str(tmp_path)) == ["a.md", "b.md"]


def test_kill_switch_disables(tmp_path: Path, monkeypatch):
    _make_runbook(tmp_path, "auth.md", "covers:\n  - '*.py'")
    monkeypatch.setenv("MORPHEUS_RUNBOOK_GATE", "off")
    assert not gate_enabled()
    assert find_touched_runbooks(["x.py"], str(tmp_path)) == []
    monkeypatch.setenv("MORPHEUS_RUNBOOK_GATE", "true")
    assert gate_enabled()
    assert find_touched_runbooks(["x.py"], str(tmp_path)) == ["auth.md"]


# --- _validate_runbook_fresh ---

def test_validator_blocks_when_missing():
    result = _validate_runbook_fresh("", "", ["auth.md"])
    assert result is not None and not result.passed
    assert "auth.md" in result.message


def test_validator_blocks_whitespace_only():
    # Whitespace-only evidence is treated as missing.
    result = _validate_runbook_fresh("   ", "", ["auth.md"])
    assert result is not None and not result.passed


def test_validator_passes_with_path():
    assert _validate_runbook_fresh("docs/runbooks/auth.md — updated", "", ["auth.md"]) is None


def test_validator_fast_pass_requires_reason():
    blocked = _validate_runbook_fresh("no_behavior_change", "", ["auth.md"])
    assert blocked is not None and not blocked.passed
    assert "runbook_reason" in blocked.message
    ok = _validate_runbook_fresh(
        "no_behavior_change", "internal refactor only", ["auth.md"],
    )
    assert ok is None


# --- end-to-end: advance() blocks at ADVANCE until runbook_fresh ---

def test_advance_blocks_until_runbook_fresh(tmp_path):
    from morpheus_mcp.config import MorpheusConfig
    from morpheus_mcp.core.engine import advance, init_plan
    from morpheus_mcp.core.parser import parse_plan_file
    from morpheus_mcp.core.store import MorpheusStore
    from morpheus_mcp.models.enums import Phase

    project = tmp_path / "proj"
    (project / "docs" / "runbooks").mkdir(parents=True)
    (project / "docs" / "runbooks" / "auth.md").write_text(
        "---\nsurface: auth\ncovers:\n  - backend/auth.py\n---\n\n# Auth runbook\n",
        encoding="utf-8",
    )
    (project / "sibling.py").write_text("x = 1\n", encoding="utf-8")

    plan_file = tmp_path / "plan.md"
    plan_file.write_text(
        "---\n"
        "name: RB Test\n"
        f"project: {project}\n"
        'test_command: "echo ok"\n'
        "---\n\n"
        "## 1. Touch auth\n"
        "- **files**: backend/auth.py\n"
        "- **do**: change the auth login behavior\n"
        "- **done-when**: login works\n"
        "- **status**: pending\n",
        encoding="utf-8",
    )

    config = MorpheusConfig.load(tmp_path / "data")
    plan, tasks = parse_plan_file(plan_file)
    with MorpheusStore(config.db_path) as store:
        plan_id = init_plan(store, plan, tasks)
        task = store.get_next_pending_task(plan_id)

        advance(store, task.id, Phase.CHECK,
                {"summary": "change the auth login behavior substantially in this task"})
        advance(store, task.id, Phase.CODE, {"sibling_read": "sibling.py"})
        advance(store, task.id, Phase.TEST, {"build_verified": "compiles"})
        advance(store, task.id, Phase.GRADE,
                {"tests_passed": "1 passed", "quality_review": "Consistent — re-read sibling.py, matched pattern"})
        advance(store, task.id, Phase.COMMIT, {"seraph_id": "seraph_unavailable"})

        # ADVANCE without runbook_fresh — blocked, names the runbook.
        result, rec = advance(store, task.id, Phase.ADVANCE, {})
        assert not result.passed
        assert "auth.md" in result.message
        assert rec is None

        # ADVANCE with runbook_fresh — passes.
        result2, rec2 = advance(
            store, task.id, Phase.ADVANCE,
            {"runbook_fresh": "docs/runbooks/auth.md — updated steps + re-stamped"},
        )
        assert result2.passed
        assert rec2 is not None


def test_advance_not_blocked_when_no_runbook_covers(tmp_path):
    """A task whose files no runbook covers advances normally."""
    from morpheus_mcp.config import MorpheusConfig
    from morpheus_mcp.core.engine import advance, init_plan
    from morpheus_mcp.core.parser import parse_plan_file
    from morpheus_mcp.core.store import MorpheusStore
    from morpheus_mcp.models.enums import Phase

    project = tmp_path / "proj"
    project.mkdir()
    (project / "sibling.py").write_text("x = 1\n", encoding="utf-8")
    plan_file = tmp_path / "plan.md"
    plan_file.write_text(
        "---\nname: NoRB\n"
        f"project: {project}\n"
        'test_command: "echo ok"\n---\n\n'
        "## 1. Touch spending\n"
        "- **files**: backend/spending.py\n"
        "- **do**: change spending\n"
        "- **done-when**: works\n"
        "- **status**: pending\n",
        encoding="utf-8",
    )
    config = MorpheusConfig.load(tmp_path / "data")
    plan, tasks = parse_plan_file(plan_file)
    with MorpheusStore(config.db_path) as store:
        plan_id = init_plan(store, plan, tasks)
        task = store.get_next_pending_task(plan_id)
        advance(store, task.id, Phase.CHECK, {"summary": "change the spending rollup behavior in this task"})
        advance(store, task.id, Phase.CODE, {"sibling_read": "sibling.py"})
        advance(store, task.id, Phase.TEST, {"build_verified": "compiles"})
        advance(store, task.id, Phase.GRADE,
                {"tests_passed": "1 passed", "quality_review": "Consistent — re-read sibling.py, matched pattern"})
        advance(store, task.id, Phase.COMMIT, {"seraph_id": "seraph_unavailable"})
        result, rec = advance(store, task.id, Phase.ADVANCE, {})
        assert result.passed  # no runbook covers backend/spending.py
        assert rec is not None
