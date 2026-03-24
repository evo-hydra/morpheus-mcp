"""SQLite-backed store with WAL mode for plan state persistence."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from morpheus_mcp.models.enums import Phase, PhaseStatus, PlanStatus, TaskSize, TaskStatus
from morpheus_mcp.models.plan import PhaseRecord, PlanRecord, TaskRecord

SCHEMA_VERSION = "4"

_SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS morpheus_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS plans (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    project       TEXT NOT NULL DEFAULT '',
    test_command  TEXT NOT NULL DEFAULT '',
    grade_enabled INTEGER NOT NULL DEFAULT 1,
    mode          TEXT NOT NULL DEFAULT 'standard',
    status        TEXT NOT NULL DEFAULT 'pending',
    created_at    TEXT NOT NULL,
    closed_at     TEXT
);

CREATE TABLE IF NOT EXISTS tasks (
    id         TEXT PRIMARY KEY,
    plan_id    TEXT NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
    seq        INTEGER NOT NULL,
    title      TEXT NOT NULL,
    files_json TEXT NOT NULL DEFAULT '[]',
    do_text    TEXT NOT NULL DEFAULT '',
    done_when  TEXT NOT NULL DEFAULT '',
    status     TEXT NOT NULL DEFAULT 'pending',
    size       TEXT NOT NULL DEFAULT 'medium',
    claimed_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_tasks_plan ON tasks(plan_id);

CREATE TABLE IF NOT EXISTS phases (
    id            TEXT PRIMARY KEY,
    task_id       TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    phase         TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'started',
    evidence_json TEXT NOT NULL DEFAULT '{}',
    started_at    TEXT NOT NULL,
    completed_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_phases_task ON phases(task_id);

CREATE TABLE IF NOT EXISTS progress_log (
    id         TEXT PRIMARY KEY,
    task_id    TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    message    TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_progress_task ON progress_log(task_id);
"""


def _iso(dt: datetime) -> str:
    """Format datetime as ISO 8601 string."""
    return dt.isoformat()


def _parse_iso(s: str) -> datetime:
    """Parse ISO 8601 string to datetime."""
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


class MorpheusStore:
    """SQLite-backed store for plan state persistence."""

    def __init__(self, db_path: Path | str) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None

    def __enter__(self) -> MorpheusStore:
        self.open()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def open(self) -> None:
        """Open the database connection and initialize schema."""
        self._conn = sqlite3.connect(str(self._db_path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(_SCHEMA_SQL)
        self._ensure_schema_version()

    def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None

    @property
    def conn(self) -> sqlite3.Connection:
        """Guarded access to the connection."""
        if self._conn is None:
            raise RuntimeError("Store is not open")
        return self._conn

    def _ensure_schema_version(self) -> None:
        cur = self.conn.execute(
            "SELECT value FROM morpheus_meta WHERE key='schema_version'"
        )
        row = cur.fetchone()
        if row is None:
            self.conn.execute(
                "INSERT INTO morpheus_meta(key, value) VALUES ('schema_version', ?)",
                (SCHEMA_VERSION,),
            )
            self.conn.commit()
        else:
            existing = row[0]
            if existing != SCHEMA_VERSION:
                self._run_migrations(existing)

    def _run_migrations(self, from_version: str) -> None:
        """Run schema migrations sequentially."""
        migrations: dict[str, str] = {
            "1": (
                "ALTER TABLE tasks ADD COLUMN size TEXT NOT NULL DEFAULT 'medium';"
                " UPDATE morpheus_meta SET value='2' WHERE key='schema_version';"
            ),
            "2": (
                "ALTER TABLE plans ADD COLUMN mode TEXT NOT NULL DEFAULT 'standard';"
                " UPDATE morpheus_meta SET value='3' WHERE key='schema_version';"
            ),
            "3": (
                "CREATE TABLE IF NOT EXISTS progress_log ("
                "id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE, "
                "message TEXT NOT NULL, created_at TEXT NOT NULL);"
                " CREATE INDEX IF NOT EXISTS idx_progress_task ON progress_log(task_id);"
                " UPDATE morpheus_meta SET value='4' WHERE key='schema_version';"
            ),
        }
        current = from_version
        while current != SCHEMA_VERSION:
            if current not in migrations:
                raise RuntimeError(
                    f"Cannot migrate from v{current} to v{SCHEMA_VERSION}. "
                    "Back up and recreate database."
                )
            self.conn.executescript(migrations[current])
            self.conn.commit()
            cur = self.conn.execute(
                "SELECT value FROM morpheus_meta WHERE key='schema_version'"
            )
            row = cur.fetchone()
            current = row[0] if row else SCHEMA_VERSION

    # --- Plan CRUD ---

    def _row_to_plan(self, row: tuple) -> PlanRecord:  # type: ignore[type-arg]
        """Convert a database row to a PlanRecord."""
        return PlanRecord(
            id=row[0],
            name=row[1],
            project=row[2],
            test_command=row[3],
            grade_enabled=bool(row[4]),
            mode=row[5],
            status=PlanStatus(row[6]),
            created_at=_parse_iso(row[7]),
            closed_at=_parse_iso(row[8]) if row[8] else None,
        )

    def save_plan(self, plan: PlanRecord) -> None:
        """Insert or replace a plan record."""
        self.conn.execute(
            "INSERT OR REPLACE INTO plans"
            "(id, name, project, test_command, grade_enabled, mode, status, created_at, closed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                plan.id,
                plan.name,
                plan.project,
                plan.test_command,
                1 if plan.grade_enabled else 0,
                plan.mode,
                plan.status.value,
                _iso(plan.created_at),
                _iso(plan.closed_at) if plan.closed_at else None,
            ),
        )
        self.conn.commit()

    def get_plan(self, plan_id: str) -> PlanRecord | None:
        """Retrieve a plan by ID."""
        cur = self.conn.execute("SELECT * FROM plans WHERE id = ?", (plan_id,))
        row = cur.fetchone()
        if row is None:
            return None
        return self._row_to_plan(row)

    def update_plan_status(self, plan_id: str, status: PlanStatus) -> None:
        """Update a plan's status."""
        extra = ""
        params: tuple = (status.value, plan_id)
        if status == PlanStatus.COMPLETED:
            extra = ", closed_at = ?"
            params = (status.value, _iso(datetime.now(timezone.utc)), plan_id)
        self.conn.execute(
            f"UPDATE plans SET status = ?{extra} WHERE id = ?",
            params,
        )
        self.conn.commit()

    def list_plans(self) -> list[PlanRecord]:
        """List all plans ordered by creation time."""
        cur = self.conn.execute("SELECT * FROM plans ORDER BY created_at DESC")
        return [self._row_to_plan(row) for row in cur.fetchall()]

    # --- Task CRUD ---

    def save_task(self, task: TaskRecord) -> None:
        """Insert or replace a task record."""
        self.conn.execute(
            "INSERT OR REPLACE INTO tasks"
            "(id, plan_id, seq, title, files_json, do_text, done_when, status, size, claimed_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                task.id,
                task.plan_id,
                task.seq,
                task.title,
                task.files_json,
                task.do_text,
                task.done_when,
                task.status.value,
                task.size.value,
                task.claimed_by,
            ),
        )
        self.conn.commit()

    def _row_to_task(self, row: tuple) -> TaskRecord:  # type: ignore[type-arg]
        """Convert a database row to a TaskRecord."""
        return TaskRecord(
            id=row[0],
            plan_id=row[1],
            seq=row[2],
            title=row[3],
            files_json=row[4],
            do_text=row[5],
            done_when=row[6],
            status=TaskStatus(row[7]),
            size=TaskSize(row[8]),
            claimed_by=row[9],
        )

    def get_tasks(self, plan_id: str) -> list[TaskRecord]:
        """Get all tasks for a plan, ordered by sequence."""
        cur = self.conn.execute(
            "SELECT * FROM tasks WHERE plan_id = ? ORDER BY seq", (plan_id,)
        )
        return [self._row_to_task(row) for row in cur.fetchall()]

    def get_task(self, task_id: str) -> TaskRecord | None:
        """Retrieve a single task by ID or prefix (min 8 chars)."""
        cur = self.conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
        row = cur.fetchone()
        if row is None and len(task_id) >= 8:
            cur = self.conn.execute(
                "SELECT * FROM tasks WHERE id LIKE ? LIMIT 1",
                (task_id + "%",),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return self._row_to_task(row)

    def update_task_status(self, task_id: str, status: TaskStatus) -> None:
        """Update a task's status."""
        self.conn.execute(
            "UPDATE tasks SET status = ? WHERE id = ?",
            (status.value, task_id),
        )
        self.conn.commit()

    def get_next_pending_task(self, plan_id: str) -> TaskRecord | None:
        """Get the next pending task for a plan (lowest seq)."""
        cur = self.conn.execute(
            "SELECT * FROM tasks WHERE plan_id = ? AND status = ? ORDER BY seq LIMIT 1",
            (plan_id, TaskStatus.PENDING.value),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return self._row_to_task(row)

    def get_tasks_by_status(
        self, plan_id: str, status: TaskStatus
    ) -> list[TaskRecord]:
        """Get all tasks for a plan with a specific status, ordered by seq."""
        cur = self.conn.execute(
            "SELECT * FROM tasks WHERE plan_id = ? AND status = ? ORDER BY seq",
            (plan_id, status.value),
        )
        return [self._row_to_task(row) for row in cur.fetchall()]

    def count_tasks_by_status(self, plan_id: str) -> dict[TaskStatus, int]:
        """Count tasks grouped by status for a plan."""
        cur = self.conn.execute(
            "SELECT status, COUNT(*) FROM tasks WHERE plan_id = ? GROUP BY status",
            (plan_id,),
        )
        return {TaskStatus(row[0]): row[1] for row in cur.fetchall()}

    # --- Phase CRUD ---

    def save_phase(self, phase: PhaseRecord) -> None:
        """Insert or replace a phase record."""
        self.conn.execute(
            "INSERT OR REPLACE INTO phases"
            "(id, task_id, phase, status, evidence_json, started_at, completed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                phase.id,
                phase.task_id,
                phase.phase.value,
                phase.status.value,
                phase.evidence_json,
                _iso(phase.started_at),
                _iso(phase.completed_at) if phase.completed_at else None,
            ),
        )
        self.conn.commit()

    def get_phases(self, task_id: str) -> list[PhaseRecord]:
        """Get all phases for a task."""
        cur = self.conn.execute(
            "SELECT * FROM phases WHERE task_id = ? ORDER BY started_at", (task_id,)
        )
        return [
            PhaseRecord(
                id=row[0],
                task_id=row[1],
                phase=Phase(row[2]),
                status=PhaseStatus(row[3]),
                evidence_json=row[4],
                started_at=_parse_iso(row[5]),
                completed_at=_parse_iso(row[6]) if row[6] else None,
            )
            for row in cur.fetchall()
        ]

    def update_phase(
        self, phase_id: str, status: PhaseStatus, evidence_json: str = "{}"
    ) -> None:
        """Update a phase's status and evidence."""
        completed = _iso(datetime.now(timezone.utc)) if status == PhaseStatus.COMPLETED else None
        self.conn.execute(
            "UPDATE phases SET status = ?, evidence_json = ?, completed_at = ? WHERE id = ?",
            (status.value, evidence_json, completed, phase_id),
        )
        self.conn.commit()

    # --- Progress Log ---

    def save_progress(self, task_id: str, message: str) -> str:
        """Log a progress entry for a task. Returns the entry ID."""
        import uuid

        entry_id = uuid.uuid4().hex
        self.conn.execute(
            "INSERT INTO progress_log(id, task_id, message, created_at) VALUES (?, ?, ?, ?)",
            (entry_id, task_id, message, _iso(datetime.now(timezone.utc))),
        )
        self.conn.commit()
        return entry_id

    def get_progress(self, task_id: str, limit: int = 5) -> list[tuple[str, str, str]]:
        """Get recent progress entries for a task.

        Returns list of (id, message, created_at) tuples.
        """
        cur = self.conn.execute(
            "SELECT id, message, created_at FROM progress_log "
            "WHERE task_id = ? ORDER BY created_at DESC LIMIT ?",
            (task_id, limit),
        )
        return [(row[0], row[1], row[2]) for row in cur.fetchall()]
