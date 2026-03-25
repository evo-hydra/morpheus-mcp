# morpheus-mcp

Plan state management and phase gate enforcement for AI dev loops via MCP. Morpheus tracks plan progress, enforces phase gates with evidence requirements, and coordinates task lifecycle across agents.

## Part of EvoIntel

Morpheus is one server in a suite designed to work together. Each handles a different concern in the AI development loop:

| Server | Role |
|--------|------|
| **[Morpheus](https://github.com/evo-hydra/morpheus-mcp)** | Plan state & phase gate enforcement *(you are here)* |
| **[Sentinel](https://github.com/evo-hydra/sentinel)** | Persistent project intelligence — conventions, pitfalls, co-changes |
| **[Seraph](https://github.com/evo-hydra/seraph)** | Verification intelligence for AI-generated code |
| **[Niobe](https://github.com/evo-hydra/niobe)** | Runtime intelligence & log analysis |
| **[Merovingian](https://github.com/evo-hydra/merovingian)** | Cross-repo dependency tracking & contract management |
| **[Anno](https://github.com/evo-hydra/anno)** | Web content extraction via stealth browser |

Each server runs independently, but they reinforce each other. Morpheus orchestrates the dev loop. Sentinel and Seraph provide the intelligence Morpheus gates on. Niobe watches runtime. Merovingian tracks what breaks across repos.

→ [github.com/evo-hydra](https://github.com/evo-hydra)

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
