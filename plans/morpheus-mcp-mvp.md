---
name: Morpheus MCP MVP
project: /home/evo-nirvana/dev/projects/nebuchadnezzar/morpheus-mcp
created: 2026-03-15
test_command: "cd /home/evo-nirvana/dev/projects/nebuchadnezzar/morpheus-mcp && python3 -m pytest tests/ -v --tb=short"
---

## 1. Project scaffold and config
- **files**: pyproject.toml, src/morpheus_mcp/__init__.py, src/morpheus_mcp/config.py
- **do**: Create the project skeleton with hatchling build, src layout. Package name `morpheus_mcp` (underscore for Python, hyphen for PyPI). Write the frozen dataclass config with TOML → env vars → defaults layering. Config fields: `data_dir` (Path), `store.db_name` (str, default "morpheus.db"). Entry points: `morpheus-mcp = "morpheus_mcp.mcp.server:main"`, `morpheus = "morpheus_mcp.cli.app:main"`. Dependencies: typer>=0.12, rich>=13, tomli>=2.0 (python<3.11), mcp>=1.0,<2.0. Dev deps: pytest>=8, pytest-cov>=5, ruff>=0.4, mypy>=1.10.
- **done-when**: `python3 -c "from morpheus_mcp.config import MorpheusConfig; c = MorpheusConfig.load(); print(c.db_path)"` prints a valid path without errors.
- **status**: in_progress

## 2. Models and enums
- **files**: src/morpheus_mcp/models/__init__.py, src/morpheus_mcp/models/enums.py, src/morpheus_mcp/models/plan.py
- **do**: Define frozen dataclass models and str/Enum types. Enums: `PlanStatus(pending, active, completed, failed)`, `TaskStatus(pending, in_progress, done, failed, skipped)`, `Phase(CHECK, CODE, TEST, GRADE, COMMIT, ADVANCE)`. Models: `PlanRecord(id, name, project, test_command, grade_enabled, status, created_at, closed_at)`, `TaskRecord(id, plan_id, seq, title, files_json, do_text, done_when, status, claimed_by)`, `PhaseRecord(id, task_id, phase, status, evidence_json, started_at, completed_at)`. All frozen=True, slots=True. UUID4 hex IDs. UTC timestamps via factory functions.
- **done-when**: Models can be instantiated and their fields accessed. Enums serialize to strings. `python3 -c "from morpheus_mcp.models import PlanRecord, TaskRecord, PhaseRecord; print('OK')"` succeeds.
- **status**: pending

## 3. Plan parser
- **files**: src/morpheus_mcp/core/__init__.py, src/morpheus_mcp/core/parser.py
- **do**: Parse the existing `/morpheus:plan` markdown format into model objects. Parse YAML frontmatter (between `---` markers) for `name`, `project`, `test_command`, `grade` (optional, default true). Parse each `## N. Title` section extracting `files:`, `do:`, `done-when:`, `status:` fields. Return a `PlanRecord` and list of `TaskRecord` objects. Handle edge cases: missing fields, extra whitespace, different markdown heading styles. No external YAML dependency — use a simple regex-based parser for the frontmatter (it's always flat key-value), or use the stdlib if available. Keep it minimal.
- **done-when**: Parser correctly parses a sample plan file with 3+ tasks and returns correct PlanRecord + TaskRecords. Test with a fixture plan file that exercises edge cases (missing optional fields, multi-line `do:` text).
- **status**: pending

## 4. Store (SQLite + WAL)
- **files**: src/morpheus_mcp/core/store.py
- **do**: SQLite-backed store with WAL mode, foreign keys, schema versioning, context manager pattern. Three tables: `plans` (id, name, project, test_command, grade_enabled, status, created_at, closed_at), `tasks` (id, plan_id FK, seq, title, files_json, do_text, done_when, status, claimed_by), `phases` (id, task_id FK, phase, status, evidence_json, started_at, completed_at). Plus `morpheus_meta` for schema version. Indexes on tasks(plan_id), phases(task_id). Methods: `save_plan(PlanRecord)`, `save_task(TaskRecord)`, `save_phase(PhaseRecord)`, `get_plan(plan_id)`, `get_tasks(plan_id)`, `get_phases(task_id)`, `update_task_status(task_id, status)`, `update_phase(phase_id, status, evidence_json)`, `get_next_pending_task(plan_id)`. No FTS5 needed — plans are small and queried by ID, not searched.
- **done-when**: Store creates tables, saves/retrieves plan+tasks+phases round-trip, handles schema versioning, WAL mode is active (`PRAGMA journal_mode` returns "wal"), foreign keys enforced (deleting a plan with tasks raises).
- **status**: pending

## 5. Gate engine
- **files**: src/morpheus_mcp/core/engine.py
- **do**: The core business logic. Define gate requirements as a frozen dataclass or dict mapping Phase → required evidence keys. Gates: CODE requires `fdmc_preflight` (dict with consistent, future_proof, dynamic, modular keys — consistent must include `sibling_read` path), TEST requires `build_verified` (str), GRADE requires `tests_passed` (str), COMMIT requires `seraph_id` (str) OR plan has `grade_enabled=false`, ADVANCE requires `knowledge_gate` (str: solution_id or "nothing_surprised"). CHECK has no gate (it's the first phase). Implement `validate_advance(task_id, phase, evidence) → (bool, str)` that checks whether the evidence satisfies the gate. Implement `advance(store, task_id, phase, evidence) → PhaseRecord` that validates and persists. Implement `init_plan(store, plan_record, task_records) → str` that saves plan and tasks, returns plan_id. Implement `close_plan(store, plan_id) → PlanRecord` that marks plan completed and sets closed_at.
- **done-when**: Gate validation correctly accepts valid evidence and rejects missing/incomplete evidence for each phase. Advancing through all phases in order succeeds with proper evidence. Attempting to skip a phase or provide incomplete evidence returns clear rejection message.
- **status**: pending

## 6. Tests for core (parser, store, engine)
- **files**: tests/__init__.py, tests/conftest.py, tests/test_config.py, tests/test_parser.py, tests/test_store.py, tests/test_engine.py
- **do**: Write comprehensive tests. conftest.py: fixtures for `store` (tmp_path backed), `sample_plan_md` (fixture plan markdown string), `sample_plan_record`, `sample_task_records`. test_config.py: defaults, env var override, TOML loading. test_parser.py: valid plan, missing fields, edge cases. test_store.py: lifecycle (open/close/guard), CRUD for plans/tasks/phases, schema versioning, WAL mode, foreign key enforcement. test_engine.py: gate validation for each phase (happy path + rejection), full advance sequence, init_plan, close_plan, incomplete evidence rejection with clear error messages. Target 80%+ coverage.
- **done-when**: `python3 -m pytest tests/ -v --tb=short` passes with 80%+ coverage on core modules. All gate validations tested (both accept and reject paths).
- **status**: pending

## 7. MCP server (4 tools)
- **files**: src/morpheus_mcp/mcp/__init__.py, src/morpheus_mcp/mcp/server.py, src/morpheus_mcp/mcp/formatters.py, src/morpheus_mcp/mcp/__main__.py
- **do**: FastMCP server with `create_server()` factory, deferred imports. 4 tools: `morpheus_init(plan_file: str) → str` reads plan file from disk, parses it, saves to store, returns markdown summary (plan name, task count, task list). `morpheus_status(plan_id: str | None = None) → str` returns plan progress, current task, phase states as markdown. `morpheus_advance(task_id: str, phase: str, evidence: str) → str` validates gate (evidence is a JSON string), advances phase, returns next phase instructions or rejection reason. `morpheus_close(plan_id: str) → str` marks plan complete, returns summary. Per-tool store lifecycle. Error strings, never exceptions. Formatters in separate file for markdown output. `__main__.py` for `python3 -m morpheus_mcp.mcp` support.
- **done-when**: `python3 -c "from morpheus_mcp.mcp.server import create_server; s = create_server(); print([t.name for t in s._tool_manager.list_tools()])"` lists all 4 tools (or equivalent introspection). Each tool returns well-formatted markdown.
- **status**: pending

## 8. CLI (Typer + Rich)
- **files**: src/morpheus_mcp/cli/__init__.py, src/morpheus_mcp/cli/app.py
- **do**: Typer CLI with Rich Console(stderr=True). Commands: `morpheus init <plan-file>` — parse and load a plan, print summary. `morpheus status [plan-id]` — show plan progress. `morpheus advance <task-id> <phase> <evidence-json>` — advance a phase with evidence. `morpheus close <plan-id>` — close a plan. `morpheus list` — list all plans. All output via Rich to stderr. Exit code 1 on errors. Re-use the same engine functions as MCP tools (no logic duplication).
- **done-when**: `morpheus --help` shows all commands. `morpheus init <test-plan>` parses and displays plan summary. `morpheus status` shows progress.
- **status**: pending

## 9. MCP and CLI tests
- **files**: tests/test_mcp_server.py, tests/test_cli.py
- **do**: test_mcp_server.py: test create_server() returns server with 4 tools, test each tool end-to-end (init a plan file, check status, advance through phases with evidence, close). Use tmp_path for plan files and database. test_cli.py: test CLI commands via `typer.testing.CliRunner`. Test init, status, advance, close, list. Verify stderr output, exit codes.
- **done-when**: All MCP and CLI tests pass. Combined coverage across all test files is 80%+.
- **status**: pending

## 10. Integration test and packaging
- **files**: tests/test_integration.py, README.md
- **do**: Write one end-to-end integration test that simulates a full Morpheus lifecycle: init a plan → claim first task (advance CHECK) → advance through CODE (with FDMC evidence including sibling_read) → TEST → GRADE → COMMIT → ADVANCE → next task → close plan. Verify the full sequence works and all gates enforce properly. Write a minimal README with: what it is (one paragraph), install (`pipx install morpheus-mcp`), quick start (init a plan, check status), MCP config snippet for `.mcp.json`. Verify `python3 -m build` produces a wheel. Verify `morpheus-mcp` and `morpheus` entry points resolve.
- **done-when**: Integration test passes end-to-end. `python3 -m build` succeeds. README exists with install and MCP config instructions. Entry points resolve: `python3 -c "from morpheus_mcp.mcp.server import main; print('OK')"` and `python3 -c "from morpheus_mcp.cli.app import main; print('OK')"`.
- **status**: pending
