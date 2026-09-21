"""Carga de configuración: config.yaml + variables de entorno + .env."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config.yaml"


class Config:
    """Diccionario anidado con acceso por ruta: cfg.get("llm.model")."""

    def __init__(self, data: dict[str, Any]):
        self._data = data

    def get(self, path: str, default: Any = None) -> Any:
        node: Any = self._data
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node if node is not None else default

    def section(self, path: str) -> dict[str, Any]:
        value = self.get(path, {})
        return value if isinstance(value, dict) else {}

    def as_dict(self) -> dict[str, Any]:
        return self._data


def _coerce(raw: str) -> Any:
    """Convierte el texto de una variable de entorno al tipo YAML equivalente."""
    try:
        return yaml.safe_load(raw)
    except yaml.YAMLError:
        return raw


def _apply_env_overrides(data: dict[str, Any]) -> None:
    """JARVIS_LLM__MODEL=claude-sonnet-5  ->  data["llm"]["model"]."""
    for key, raw in os.environ.items():
        if not key.startswith("JARVIS_") or "__" not in key:
            continue
        parts = key[len("JARVIS_"):].lower().split("__")
        node = data
        for part in parts[:-1]:
            node = node.setdefault(part, {})
            if not isinstance(node, dict):
                break
        else:
            node[parts[-1]] = _coerce(raw)


def load_config(path: str | Path | None = None) -> Config:
    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env")
    except ImportError:  # python-dotenv es opcional
        pass

    config_path = Path(path or os.getenv("JARVIS_CONFIG") or DEFAULT_CONFIG)
    data: dict[str, Any] = {}
    if config_path.exists():
        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}

    _apply_env_overrides(data)
    return Config(data)


def data_path(relative: str) -> Path:
    """Ruta absoluta dentro del proyecto, creando el directorio padre."""
    path = Path(relative)
    if not path.is_absolute():
        path = ROOT / path
    path.parent.mkdir(parents=True, exist_ok=True)
    return path
