"""Enumerations for Morpheus domain model."""

from __future__ import annotations

from enum import Enum


class PlanStatus(str, Enum):
    """Lifecycle status of a plan."""

    PENDING = "pending"
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"


class TaskStatus(str, Enum):
    """Lifecycle status of a task within a plan."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


class Phase(str, Enum):
    """Phases in the dev loop protocol."""

    CHECK = "CHECK"
    CODE = "CODE"
    TEST = "TEST"
    GRADE = "GRADE"
    COMMIT = "COMMIT"
    ADVANCE = "ADVANCE"


class PhaseStatus(str, Enum):
    """Status of a phase execution."""

    STARTED = "started"
    COMPLETED = "completed"
    REJECTED = "rejected"


class TaskSize(str, Enum):
    """Size tier for a task — controls gate strictness.

    MICRO: do → test → commit. All gates accept empty evidence.
    SMALL: Lightweight path — skips sibling_read, fdmc_review, seraph_id, knowledge_gate.
    MEDIUM: Full protocol (default).
    LARGE: Full protocol + mandatory Seraph grading even when grade=false.
    """

    MICRO = "micro"
    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"


class FeedbackOutcome(str, Enum):
    """Outcome of user feedback."""

    ACCEPTED = "accepted"
    REJECTED = "rejected"
    MODIFIED = "modified"
