"""Frozen dataclass models for Morpheus domain objects."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from morpheus_mcp.models.enums import (
    FeedbackOutcome,
    Phase,
    PhaseStatus,
    PlanStatus,
    TaskStatus,
)


def _now() -> datetime:
    """UTC now factory for dataclass defaults."""
    return datetime.now(timezone.utc)


def _uuid_hex() -> str:
    """Generate a UUID4 hex string."""
    return uuid.uuid4().hex


@dataclass(frozen=True, slots=True)
class PlanRecord:
    """A development plan loaded from a plan file."""

    id: str = field(default_factory=_uuid_hex)
    name: str = ""
    project: str = ""
    test_command: str = ""
    grade_enabled: bool = True
    status: PlanStatus = PlanStatus.PENDING
    created_at: datetime = field(default_factory=_now)
    closed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class TaskRecord:
    """A single task within a plan."""

    id: str = field(default_factory=_uuid_hex)
    plan_id: str = ""
    seq: int = 0
    title: str = ""
    files_json: str = "[]"
    do_text: str = ""
    done_when: str = ""
    status: TaskStatus = TaskStatus.PENDING
    claimed_by: str | None = None


@dataclass(frozen=True, slots=True)
class PhaseRecord:
    """A phase execution record for a task."""

    id: str = field(default_factory=_uuid_hex)
    task_id: str = ""
    phase: Phase = Phase.CHECK
    status: PhaseStatus = PhaseStatus.STARTED
    evidence_json: str = "{}"
    started_at: datetime = field(default_factory=_now)
    completed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class Feedback:
    """User feedback on a plan or assessment."""

    target_id: str = ""
    outcome: FeedbackOutcome = FeedbackOutcome.ACCEPTED
    context: str = ""
    created_at: datetime = field(default_factory=_now)
