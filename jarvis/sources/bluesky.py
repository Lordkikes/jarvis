"""Bluesky (AT Protocol): tu línea temporal.

La API es abierta y gratuita. Solo hace falta una contraseña de aplicación
(Ajustes → App Passwords), nunca la de tu cuenta.

El token de acceso caduca en minutos, así que se pide sesión nueva en cada
sincronización en vez de guardarlo: una petición de más cada cuarto de hora
sale más barato que gestionar la renovación.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from . import Item
from ..http import Client

log = logging.getLogger("jarvis.sources.bluesky")

MAX_BODY = 3000


def post_url(uri: str, handle: str) -> str:
    """at://did:plc:xxx/app.bsky.feed.post/rkey → enlace web."""
    rkey = uri.rsplit("/", 1)[-1] if uri else ""
    return f"https://bsky.app/profile/{handle}/post/{rkey}" if rkey and handle else ""


def parse_post(entry: dict) -> Item | None:
    """Convierte un elemento del feed en un item. Sin red: es testeable."""
    post = entry.get("post") or {}
    record = post.get("record") or {}
    text = (record.get("text") or "").strip()
    if not text:
        return None  # una imagen sin texto no aporta nada al índice

    author = post.get("author") or {}
    handle = author.get("handle", "")
    name = author.get("displayName") or handle

    # Un repost lo trae quien lo repostea, pero lo que importa es el original.
    reason = entry.get("reason") or {}
    repost_by = (reason.get("by") or {}).get("handle", "") \
        if reason.get("$type", "").endswith("reasonRepost") else ""

    created = record.get("createdAt") or post.get("indexedAt") or ""
    return Item(
        id=f"bluesky:{post.get('uri', '')}",
        source="bluesky",
        kind="publicación",
        title=f"{name}: {text[:110]}",
        body=text[:MAX_BODY],
        author=handle,
        url=post_url(post.get("uri", ""), handle),
        created_at=created or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        meta={
            "nombre": name,
            "reposteado_por": repost_by,
            "respuestas": post.get("replyCount", 0),
            "me_gusta": post.get("likeCount", 0),
        },
    )


class BlueskySource:
    name = "bluesky"

    def __init__(self, handle: str, service: str = "https://bsky.social", limit: int = 50):
        password = os.getenv("JARVIS_BLUESKY_APP_PASSWORD", "")
        if not (handle and password):
            raise RuntimeError(
                "falta sources.bluesky.handle en config.yaml o "
                "JARVIS_BLUESKY_APP_PASSWORD en .env (una contraseña de aplicación)")
        self.handle = handle
        self.password = password
        self.service = service.rstrip("/")
        self.limit = min(100, limit)

    def sync(self, store) -> int:
        with Client(timeout=30.0) as client:
            session = client.post(
                f"{self.service}/xrpc/com.atproto.server.createSession",
                json={"identifier": self.handle, "password": self.password},
            )
            if session.status_code >= 400:
                log.error("no se pudo iniciar sesión en Bluesky (%s): %s",
                          session.status_code, session.text[:160])
                return 0

            token = session.json().get("accessJwt", "")
            timeline = client.get(
                f"{self.service}/xrpc/app.bsky.feed.getTimeline",
                headers={"Authorization": f"Bearer {token}"},
                params={"limit": self.limit},
            )
            if timeline.status_code >= 400:
                log.error("error leyendo la línea temporal (%s): %s",
                          timeline.status_code, timeline.text[:160])
                return 0

        items = [item for entry in timeline.json().get("feed", [])
                 if (item := parse_post(entry))]
        changed = store.upsert(items)
        log.info("bluesky: %d publicaciones leídas, %d nuevas", len(items), changed)
        return changed
