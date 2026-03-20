"""Parse Morpheus plan files (markdown with YAML frontmatter) into model objects."""

from __future__ import annotations

import json
import re
from pathlib import Path

from morpheus_mcp.models.enums import PlanStatus, TaskSize, TaskStatus
from morpheus_mcp.models.plan import PlanRecord, TaskRecord

# Frontmatter: content between --- markers at the top of the file
_FRONTMATTER_RE = re.compile(r"\A\s*---\s*\n(.*?)\n---\s*\n", re.DOTALL)

# Task heading: ## N. Title (with optional whitespace variations)
_TASK_HEADING_RE = re.compile(r"^##\s+(\d+)\.\s+(.+)$", re.MULTILINE)

# Field extraction: - **key**: value
_FIELD_RE = re.compile(r"^-\s+\*\*(\S+?)\*\*:\s*(.+)$", re.MULTILINE)


def _parse_frontmatter(text: str) -> dict[str, str]:
    """Extract frontmatter key-value pairs from simple YAML.

    Only handles flat key: value pairs (no nesting, no lists).
    Strips surrounding quotes from values.
    """
    match = _FRONTMATTER_RE.search(text)
    if not match:
        return {}

    result: dict[str, str] = {}
    for line in match.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        colon_idx = line.find(":")
        if colon_idx < 0:
            continue
        key = line[:colon_idx].strip()
        value = line[colon_idx + 1 :].strip()
        # Strip surrounding quotes
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        result[key] = value

    return result


def _parse_task_section(section_text: str, seq: int, title: str) -> dict[str, str]:
    """Extract fields from a task section body."""
    fields: dict[str, str] = {"seq": str(seq), "title": title}

    for match in _FIELD_RE.finditer(section_text):
        key = match.group(1).rstrip(":")
        value = match.group(2).strip()
        fields[key] = value

    return fields


def parse_plan_file(path: str | Path) -> tuple[PlanRecord, list[TaskRecord]]:
    """Parse a plan markdown file into a PlanRecord and list of TaskRecords.

    Args:
        path: Path to the plan markdown file.

    Returns:
        Tuple of (PlanRecord, list[TaskRecord]).

    Raises:
        FileNotFoundError: If the plan file does not exist.
        ValueError: If the plan file has no frontmatter or no tasks.
    """
    path = Path(path)
    text = path.read_text(encoding="utf-8")

    # Parse frontmatter
    fm = _parse_frontmatter(text)
    if not fm:
        raise ValueError(f"No frontmatter found in {path}")

    grade_raw = fm.get("grade", "true").lower()
    grade_enabled = grade_raw not in ("false", "no", "0")

    mode_raw = fm.get("mode", "standard").strip().lower()
    mode = mode_raw if mode_raw in ("standard", "greenfield") else "standard"

    plan = PlanRecord(
        name=fm.get("name", path.stem),
        project=fm.get("project", ""),
        test_command=fm.get("test_command", ""),
        grade_enabled=grade_enabled,
        mode=mode,
        status=PlanStatus.PENDING,
    )

    # Split into task sections by ## headings
    headings = list(_TASK_HEADING_RE.finditer(text))
    if not headings:
        raise ValueError(f"No task sections found in {path}")

    tasks: list[TaskRecord] = []
    for i, heading in enumerate(headings):
        seq = int(heading.group(1))
        title = heading.group(2).strip()

        # Section body extends from after heading to next heading (or end)
        start = heading.end()
        end = headings[i + 1].start() if i + 1 < len(headings) else len(text)
        section_body = text[start:end]

        fields = _parse_task_section(section_body, seq, title)

        # Parse files list into JSON array
        files_raw = fields.get("files", "")
        files_list = [f.strip() for f in files_raw.split(",") if f.strip()]

        # Map status string to enum
        status_raw = fields.get("status", "pending").strip().lower()
        try:
            status = TaskStatus(status_raw)
        except ValueError:
            status = TaskStatus.PENDING

        # Map size string to enum
        size_raw = fields.get("size", "medium").strip().lower()
        try:
            size = TaskSize(size_raw)
        except ValueError:
            size = TaskSize.MEDIUM

        task = TaskRecord(
            plan_id=plan.id,
            seq=seq,
            title=title,
            files_json=json.dumps(files_list),
            do_text=fields.get("do", ""),
            done_when=fields.get("done-when", ""),
            status=status,
            size=size,
        )
        tasks.append(task)

    return plan, tasks
