from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os

try:
    import yaml as _yaml
except ImportError:
    _yaml = None


def _load_yaml() -> dict:
    if _yaml is None:
        return {}
    search_paths = [
        Path.cwd() / "config.yaml",
        Path.home() / ".config" / "claw-easa" / "config.yaml",
    ]
    for p in search_paths:
        if p.is_file():
            with open(p) as f:
                return _yaml.safe_load(f) or {}
    return {}


@dataclass
class Settings:
    data_dir: str = "data"
    db_file: str = "claw_easa.db"
    faiss_index_file: str = "claw_easa.faiss"
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_dimensions: int = 384
    easa_base_url: str = "https://www.easa.europa.eu"

    @property
    def db_path(self) -> Path:
        return Path(self.data_dir) / self.db_file

    @property
    def faiss_index_path(self) -> Path:
        return Path(self.data_dir) / self.faiss_index_file


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is not None:
        return _settings

    yaml_cfg = _load_yaml()
    kwargs: dict = {}

    for key in ("data_dir", "db_file", "faiss_index_file", "embedding_model", "easa_base_url"):
        env_key = f"CLAW_EASA_{key.upper()}"
        val = os.environ.get(env_key) or yaml_cfg.get(key)
        if val is not None:
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
