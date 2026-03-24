"""Layered configuration: .morpheus/config.toml -> MORPHEUS_* env vars -> defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore[no-redef]


def _default_data_dir() -> Path:
    """Return the default data directory (XDG-compliant).

    Morpheus manages plan state across projects, so data lives in a
    user-level directory rather than per-project.

    Resolution order:
      1. $MORPHEUS_DATA_DIR (explicit override)
      2. $XDG_DATA_HOME/morpheus
      3. ~/.local/share/morpheus
    """
    env_dir = os.environ.get("MORPHEUS_DATA_DIR")
    if env_dir:
        return Path(env_dir)

    xdg_data = os.environ.get("XDG_DATA_HOME")
    if xdg_data:
        return Path(xdg_data) / "morpheus"

    return Path.home() / ".local" / "share" / "morpheus"


@dataclass(frozen=True, slots=True)
class StoreConfig:
    """SQLite store configuration."""

    db_name: str = "morpheus.db"


@dataclass(frozen=True, slots=True)
class McpConfig:
    """MCP server configuration."""

    default_query_limit: int = 50


@dataclass(frozen=True, slots=True)
class GateConfig:
    """Gate enforcement configuration."""

    knowledge_gate_task_threshold: int = 5


@dataclass(frozen=True, slots=True)
class MorpheusConfig:
    """Top-level configuration container."""

    data_dir: Path = field(default_factory=_default_data_dir)
    store: StoreConfig = field(default_factory=StoreConfig)
    mcp: McpConfig = field(default_factory=McpConfig)
    gates: GateConfig = field(default_factory=GateConfig)

    @property
    def morpheus_dir(self) -> Path:
        """Directory for Morpheus data files."""
        return self.data_dir

    @property
    def db_path(self) -> Path:
        """Full path to the SQLite database."""
        return self.morpheus_dir / self.store.db_name

    @classmethod
    def load(cls, data_dir: Path | None = None) -> MorpheusConfig:
        """Load config: TOML file -> env vars -> defaults."""
        resolved_dir = Path(data_dir) if data_dir else _default_data_dir()
        toml_path = resolved_dir / "config.toml"

        toml_data: dict = {}
        if toml_path.is_file():
            with open(toml_path, "rb") as f:
                toml_data = tomllib.load(f)

        store_data = toml_data.get("store", {})
        mcp_data = toml_data.get("mcp", {})
        gates_data = toml_data.get("gates", {})

        _store_defaults = StoreConfig()
        _mcp_defaults = McpConfig()
        _gate_defaults = GateConfig()

        store = StoreConfig(
            db_name=os.environ.get(
                "MORPHEUS_DB_NAME",
                store_data.get("db_name", _store_defaults.db_name),
            ),
        )

        mcp = McpConfig(
            default_query_limit=int(
                os.environ.get(
                    "MORPHEUS_DEFAULT_QUERY_LIMIT",
                    mcp_data.get("default_query_limit", _mcp_defaults.default_query_limit),
                )
            ),
        )

        gates = GateConfig(
            knowledge_gate_task_threshold=int(
                os.environ.get(
                    "MORPHEUS_KNOWLEDGE_GATE_TASK_THRESHOLD",
                    gates_data.get(
                        "knowledge_gate_task_threshold",
                        _gate_defaults.knowledge_gate_task_threshold,
                    ),
                )
            ),
        )

        return cls(data_dir=resolved_dir, store=store, mcp=mcp, gates=gates)
