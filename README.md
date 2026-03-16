# morpheus-mcp

Plan state management and phase gate enforcement for AI dev loops via MCP. Morpheus tracks plan progress, enforces phase gates with evidence requirements, and coordinates task lifecycle across agents.

Part of the [EvoIntel](https://github.com/evo-hydra) suite: Sentinel, Seraph, Niobe, Merovingian, Morpheus, Anno.

## Install

```bash
pipx install morpheus-mcp
```

## MCP Tools

| Tool | Description |
|------|-------------|
| `morpheus_init` | Load a plan file, parse tasks, begin tracking |
| `morpheus_status` | Plan progress, task states, active phase |
| `morpheus_advance` | Advance a task through a phase gate with evidence |
| `morpheus_close` | Mark a plan as completed |

## Phase Gates

Each phase requires evidence before advancing:

| Phase | Required Evidence |
|-------|-------------------|
| CHECK | *(none)* |
| CODE | `fdmc_preflight` with 4 lenses (consistent must include `sibling_read`) |
| TEST | `build_verified` |
| GRADE | `tests_passed` |
| COMMIT | `seraph_id` (skipped if plan has `grade: false`) |
| ADVANCE | `knowledge_gate` |

## CLI

```bash
morpheus init plans/my-plan.md    # Load a plan
morpheus status                   # Show progress
morpheus advance <task-id> CHECK  # Advance a phase
morpheus close <plan-id>          # Close the plan
morpheus list                     # List all plans
```

## MCP Configuration

Add to your `.mcp.json` or `~/.claude.json`:

```json
{
  "mcpServers": {
    "morpheus": {
      "command": "morpheus-mcp",
      "args": []
    }
  }
}
```

## License

MIT
