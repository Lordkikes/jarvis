"""Fuentes de información que Jarvis indexa en segundo plano.

Cada fuente sabe traer sus cosas y dejarlas en el índice local; las
herramientas del asistente consultan el índice, nunca la API. Así responder
es instantáneo, funciona sin conexión y el modelo solo ve lo que pediste.
"""
from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger("jarvis.sources")

_TAG = re.compile(r"<[^>]+>")
_BLANKS = re.compile(r"\n{3,}")


def html_to_text(content: str) -> str:
    """HTML a texto legible. Lo usan el correo y Mastodon, que envían marcado."""
    if not content:
        return ""
    content = re.sub(r"<br\s*/?>", "\n", content, flags=re.IGNORECASE)
    content = re.sub(r"</p\s*>", "\n\n", content, flags=re.IGNORECASE)
    return _BLANKS.sub("\n\n", html.unescape(_TAG.sub("", content))).strip()


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

    if cfg.get("sources.bluesky.enabled", False):
        try:
            from .bluesky import BlueskySource

            sources.append(BlueskySource(
                handle=cfg.get("sources.bluesky.handle", ""),
                service=cfg.get("sources.bluesky.service", "https://bsky.social"),
                limit=int(cfg.get("sources.bluesky.limit", 50)),
            ))
        except Exception as exc:  # noqa: BLE001
            log.warning("fuente 'bluesky' no disponible (%s)", exc)

    if cfg.get("sources.mastodon.enabled", False):
        try:
            from .mastodon import MastodonSource

            sources.append(MastodonSource(
                instance=cfg.get("sources.mastodon.instance", ""),
                limit=int(cfg.get("sources.mastodon.limit", 40)),
            ))
        except Exception as exc:  # noqa: BLE001
            log.warning("fuente 'mastodon' no disponible (%s)", exc)

    if cfg.get("sources.reddit.enabled", False):
        try:
            from .reddit import RedditSource

            sources.append(RedditSource(
                username=cfg.get("sources.reddit.username", ""),
                feed=cfg.get("sources.reddit.feed", "best"),
                subreddits=cfg.get("sources.reddit.subreddits", []),
                limit=int(cfg.get("sources.reddit.limit", 40)),
            ))
        except Exception as exc:  # noqa: BLE001
            log.warning("fuente 'reddit' no disponible (%s)", exc)

    if cfg.get("sources.rss.enabled", False):
        try:
            from .rss import RssSource

            sources.append(RssSource(
                feeds=cfg.get("sources.rss.feeds", []),
                limit=int(cfg.get("sources.rss.limit", 40)),
                interval_minutes=int(cfg.get("sources.rss.interval_minutes", 0)),
            ))
        except Exception as exc:  # noqa: BLE001
            log.warning("fuente 'rss' no disponible (%s)", exc)

    if cfg.get("sources.x.enabled", False):
        try:
            from .x_twitter import XSource

            sources.append(XSource(
                user_id=cfg.get("sources.x.user_id", ""),
                limit=int(cfg.get("sources.x.limit", 40)),
                interval_minutes=int(cfg.get("sources.x.interval_minutes", 0)),
            ))
        except Exception as exc:  # noqa: BLE001
            log.warning("fuente 'x' no disponible (%s)", exc)

    if cfg.get("sources.calendar.enabled", False):
        try:
            from .calendar_dav import CalendarSource

            sources.append(CalendarSource(
                caldav_url=cfg.get("sources.calendar.caldav.url", ""),
                caldav_user=cfg.get("sources.calendar.caldav.user", ""),
                ics=cfg.get("sources.calendar.ics", []),
                days_ahead=int(cfg.get("sources.calendar.days_ahead", 60)),
                days_back=int(cfg.get("sources.calendar.days_back", 7)),
                interval_minutes=int(cfg.get("sources.calendar.interval_minutes", 0)),
                allow_write=bool(cfg.get("sources.calendar.caldav.allow_write", True)),
            ))
        except Exception as exc:  # noqa: BLE001
            log.warning("fuente 'calendario' no disponible (%s)", exc)

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
