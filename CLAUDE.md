# Morpheus — plan state + phase-gate enforcement (MCP server)

## Public-Repo Hygiene — keep this repo agnostic

This is public, project-agnostic infrastructure. Applications built *with* it live in
separate repos and must not leak into it.

**Rule:** test fixtures, docstring/example code, and commit messages use **generic**
examples only — never the real rules, schema, model/field names, file paths, business
logic, or name of the application you're dogfooding against.

- Generic (good): `execute(f"...")` f-string SQL, `func.sum(Order.total)`, `services/db.py`.
- App-specific (bad): a client app's real rules/schema/paths, or a commit message naming it.

**Test:** if you'd be embarrassed to show the fixture or commit to someone building a
*different* app on this server, it's leaked — genericize it.

**Before pushing:** grep the pending diff (content *and* commit messages) for
app-specific nouns; confirm no secrets / `.env` / keys are tracked.
