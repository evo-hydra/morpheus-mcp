"""Runbook freshness detection for the ADVANCE gate.

A *runbook* is a markdown file (default under ``docs/runbooks/``) whose
frontmatter declares ``covers:`` path globs — the code surfaces it documents.
When a task's target files intersect a runbook's covers, that runbook is
"touched" and the agent must refresh it (or justify no change) before the task
can ADVANCE.

This module is deliberately project-agnostic: it knows only about files, globs,
and a runbook directory. No application-specific concepts. A project with no
``docs/runbooks/*.md`` carrying ``covers:`` is entirely unaffected — the gate
activates by the *presence* of runbooks, which is the opt-in.
"""

from __future__ import annotations

import fnmatch
import os
import re
from pathlib import Path

# Frontmatter between leading --- markers (mirrors parser._FRONTMATTER_RE).
_FRONTMATTER_RE = re.compile(r"\A\s*---\s*\n(.*?)\n---\s*\n", re.DOTALL)

_DEFAULT_RUNBOOK_DIR = "docs/runbooks"
_DISABLE_VALUES = {"off", "0", "false", "no"}


def gate_enabled() -> bool:
    """True unless explicitly disabled via the ``MORPHEUS_RUNBOOK_GATE`` kill-switch."""
    return (
        os.environ.get("MORPHEUS_RUNBOOK_GATE", "").strip().lower()
        not in _DISABLE_VALUES
    )


def _clean(item: str) -> str:
    return item.strip().strip("'\"").strip()


def parse_covers(text: str) -> list[str]:
    """Extract ``covers:`` path globs from a runbook's frontmatter.

    Supports inline form (``covers: [a, b]``), a single value (``covers: a``),
    and YAML block-list form::

        covers:
          - a
          - b
    """
    match = _FRONTMATTER_RE.search(text)
    if not match:
        return []
    lines = match.group(1).splitlines()
    globs: list[str] = []

    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith("covers:"):
            continue
        rest = stripped[len("covers:"):].strip()
        if rest.startswith("[") and rest.endswith("]"):
            globs.extend(_clean(x) for x in rest[1:-1].split(",") if _clean(x))
        elif rest:
            globs.append(_clean(rest))
        else:
            # Block list: collect following `- item` lines until the next key.
            for follow in lines[i + 1:]:
                fstr = follow.strip()
                if fstr.startswith("- "):
                    globs.append(_clean(fstr[2:]))
                elif not fstr:
                    continue
                else:
                    break
        break

    return [g for g in globs if g]


def find_touched_runbooks(
    target_files: list[str],
    project_root: str,
    runbook_dir: str = _DEFAULT_RUNBOOK_DIR,
) -> list[str]:
    """Return runbook filenames whose ``covers:`` globs match any target file.

    Returns ``[]`` (no gate) when the kill-switch is set, there are no target
    files, the runbook directory does not exist, or nothing matches — so any
    project without runbooks is unaffected.
    """
    if not gate_enabled() or not target_files or not project_root:
        return []
    rb_dir = Path(project_root) / runbook_dir
    if not rb_dir.is_dir():
        return []

    touched: list[str] = []
    for rb in sorted(rb_dir.glob("*.md")):
        try:
            covers = parse_covers(rb.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        if covers and any(
            fnmatch.fnmatch(tf, glob) for tf in target_files for glob in covers
        ):
            touched.append(rb.name)
    return touched
