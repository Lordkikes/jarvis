"""Fuentes de información que Jarvis indexa en segundo plano.

Cada fuente sabe traer sus cosas y dejarlas en el índice local; las
herramientas del asistente consultan el índice, nunca la API. Así responder
es instantáneo, funciona sin conexión y el modelo solo ve lo que pediste.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger("jarvis.sources")


@dataclass
class Item:
    """Una cosa indexable: un correo, una sesión de Claude Code, un mensaje."""

    id: str
    source: str
    title: str
    body: str = ""
    kind: str = ""
    author: str = ""
    url: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    meta: dict[str, Any] = field(default_factory=dict)


def make_sources(cfg) -> list:
    """Crea las fuentes activadas en la configuración."""
    sources = []
    if not cfg.get("sources.enabled", True):
        return sources

    if cfg.get("sources.claude_code.enabled", True):
        try:
            from .claude_code import ClaudeCodeSource

            sources.append(ClaudeCodeSource(
                path=cfg.get("sources.claude_code.path", "~/.claude/projects"),
                max_sessions=int(cfg.get("sources.claude_code.max_sessions", 200)),
            ))
        except Exception as exc:  # noqa: BLE001
            log.warning("fuente 'claude_code' no disponible (%s)", exc)

    if cfg.get("sources.email.enabled", False):
        try:
            from .email_imap import EmailSource

            sources.append(EmailSource(
                host=cfg.get("sources.email.host", ""),
                port=int(cfg.get("sources.email.port", 993)),
                user=cfg.get("sources.email.user", ""),
                folder=cfg.get("sources.email.folder", "INBOX"),
                days=int(cfg.get("sources.email.days", 7)),
                limit=int(cfg.get("sources.email.limit", 200)),
            ))
        except Exception as exc:  # noqa: BLE001
            log.warning("fuente 'correo' no disponible (%s)", exc)

    return sources
