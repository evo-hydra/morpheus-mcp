"""Tests for configuration loading."""

from __future__ import annotations

from pathlib import Path

from morpheus_mcp.config import MorpheusConfig


def test_load_defaults(tmp_path):
    """Config loads defaults when no TOML exists."""
    config = MorpheusConfig.load(tmp_path)
    assert config.store.db_name == "morpheus.db"
    assert config.mcp.default_query_limit == 50


def test_db_path(tmp_path):
    """db_path combines data_dir and db_name."""
    config = MorpheusConfig.load(tmp_path)
    assert config.db_path == tmp_path / "morpheus.db"


def test_env_var_override(tmp_path, monkeypatch):
    """Env vars override defaults."""
    monkeypatch.setenv("MORPHEUS_DB_NAME", "custom.db")
    config = MorpheusConfig.load(tmp_path)
    assert config.store.db_name == "custom.db"


def test_env_var_query_limit(tmp_path, monkeypatch):
    """Env var overrides MCP query limit."""
    monkeypatch.setenv("MORPHEUS_DEFAULT_QUERY_LIMIT", "100")
    config = MorpheusConfig.load(tmp_path)
    assert config.mcp.default_query_limit == 100


def test_toml_loading(tmp_path):
    """Config loads from TOML file."""
    toml_path = tmp_path / "config.toml"
    toml_path.write_text('[store]\ndb_name = "from-toml.db"\n')
    config = MorpheusConfig.load(tmp_path)
    assert config.store.db_name == "from-toml.db"


def test_env_overrides_toml(tmp_path, monkeypatch):
    """Env vars take precedence over TOML."""
    toml_path = tmp_path / "config.toml"
    toml_path.write_text('[store]\ndb_name = "from-toml.db"\n')
    monkeypatch.setenv("MORPHEUS_DB_NAME", "from-env.db")
    config = MorpheusConfig.load(tmp_path)
    assert config.store.db_name == "from-env.db"


def test_data_dir_env(tmp_path, monkeypatch):
    """MORPHEUS_DATA_DIR env var sets data directory."""
    monkeypatch.setenv("MORPHEUS_DATA_DIR", str(tmp_path / "custom"))
    config = MorpheusConfig.load()
    assert config.data_dir == tmp_path / "custom"


def test_frozen():
    """Config is immutable."""
    config = MorpheusConfig.load()
    try:
        config.store = None  # type: ignore
        assert False, "Should have raised"
    except AttributeError:
        pass
