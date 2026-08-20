from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import os

try:
    import yaml as _yaml
except ImportError:
    _yaml = None

CONFIG_FILE_NAME = "config.yaml"


def default_data_dir() -> Path:
    """Runtime data directory used when nothing is configured.

    Anchored to the user's home directory rather than the working directory:
    the SQLite database and the FAISS index must resolve to the same location
    whether ``claw-easa`` is started from the repository, from ``~`` or from
    an OpenClaw skill workspace.
    """
    return Path.home() / ".local" / "share" / "claw-easa"


def _config_search_paths() -> list[Path]:
    return [
        Path.cwd() / CONFIG_FILE_NAME,
        Path.home() / ".config" / "claw-easa" / CONFIG_FILE_NAME,
    ]


def _load_yaml() -> tuple[dict, Path | None]:
    """Return the first configuration file found and the path it came from."""
    if _yaml is None:
        return {}, None
    for p in _config_search_paths():
        if p.is_file():
            with open(p) as f:
                return (_yaml.safe_load(f) or {}), p
    return {}, None


def _anchor(value: str, base: Path) -> Path:
    """Resolve *value* against *base* when it is not already absolute."""
    path = Path(value).expanduser()
    return path if path.is_absolute() else base / path


@dataclass
class Settings:
    data_dir: str = field(default_factory=lambda: str(default_data_dir()))
    db_file: str = "claw_easa.db"
    faiss_index_file: str = "claw_easa.faiss"
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_dimensions: int = 384
    easa_base_url: str = "https://www.easa.europa.eu"

    @property
    def data_path(self) -> Path:
        """The data directory as an absolute path."""
        return _anchor(self.data_dir, Path.cwd())

    @property
    def db_path(self) -> Path:
        return self.data_path / self.db_file

    @property
    def faiss_index_path(self) -> Path:
        return self.data_path / self.faiss_index_file


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is not None:
        return _settings

    yaml_cfg, yaml_path = _load_yaml()
    kwargs: dict = {}

    for key in ("data_dir", "db_file", "faiss_index_file", "embedding_model", "easa_base_url"):
        env_key = f"CLAW_EASA_{key.upper()}"
        env_val = os.environ.get(env_key)
        val = env_val or yaml_cfg.get(key)
        if val is None:
            continue
        if key == "data_dir" and not env_val and yaml_path is not None:
            # A relative data_dir in a config file belongs to that file's
            # directory, not to wherever the process happens to be started.
            val = str(_anchor(val, yaml_path.parent))
        kwargs[key] = val

    if "embedding_dimensions" in yaml_cfg:
        kwargs["embedding_dimensions"] = int(yaml_cfg["embedding_dimensions"])

    if os.environ.get("CLAW_EASA_POSTGRES_DSN"):
        import warnings

        warnings.warn(
            "CLAW_EASA_POSTGRES_DSN is deprecated. clawEASA now uses SQLite. "
            "Set CLAW_EASA_DATA_DIR and CLAW_EASA_DB_FILE instead.",
            DeprecationWarning,
            stacklevel=2,
        )

    _settings = Settings(**kwargs)
    return _settings


def reset_settings() -> None:
    global _settings
    _settings = None
