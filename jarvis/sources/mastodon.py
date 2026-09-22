"""Mastodon: tu línea temporal de inicio.

Necesita un token de acceso personal: Preferencias → Desarrollo → Nueva
aplicación, con el permiso `read` (o `read:statuses`). No hay OAuth que montar
porque el propio Mastodon te da el token ya emitido.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from . import Item, html_to_text
from ..http import Client

log = logging.getLogger("jarvis.sources.mastodon")

MAX_BODY = 3000


def parse_status(status: dict, instance: str = "") -> Item | None:
    """Convierte un estado en un item. Sin red: es testeable."""
    # Un impulso (reblog) envuelve el estado original: lo que interesa es el de dentro.
    boosted_by = ""
    if inner := status.get("reblog"):
        boosted_by = (status.get("account") or {}).get("acct", "")
        status = inner

    text = html_to_text(status.get("content", ""))
    if warning := (status.get("spoiler_text") or "").strip():
        text = f"[{warning}]\n{text}"
    if not text:
        return None

    account = status.get("account") or {}
    handle = account.get("acct", "")
    name = account.get("display_name") or handle
    host = (instance or "").replace("https://", "").replace("http://", "").strip("/")

    return Item(
        id=f"mastodon:{host}:{status.get('id', '')}",
        source="mastodon",
        kind="publicación",
        title=f"{name}: {text[:110]}",
        body=text[:MAX_BODY],
        author=handle,
        url=status.get("url") or "",
        created_at=status.get("created_at")
                   or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        meta={
            "nombre": name,
            "impulsado_por": boosted_by,
            "respuestas": status.get("replies_count", 0),
            "favoritos": status.get("favourites_count", 0),
        },
    )


class MastodonSource:
    name = "mastodon"

    def __init__(self, instance: str, limit: int = 40):
        token = os.getenv("JARVIS_MASTODON_TOKEN", "")
        if not (instance and token):
            raise RuntimeError(
                "falta sources.mastodon.instance en config.yaml o "
                "JARVIS_MASTODON_TOKEN en .env")
        self.instance = instance.rstrip("/")
        self.token = token
        self.limit = min(40, limit)   # el máximo que acepta la API

    def sync(self, store) -> int:
        with Client(timeout=30.0) as client:
            response = client.get(
                f"{self.instance}/api/v1/timelines/home",
                headers={"Authorization": f"Bearer {self.token}"},
                params={"limit": self.limit},
            )
        if response.status_code >= 400:
            log.error("error leyendo Mastodon (%s): %s",
                      response.status_code, response.text[:160])
            return 0

        items = [item for status in response.json()
                 if (item := parse_status(status, self.instance))]
        changed = store.upsert(items)
        log.info("mastodon: %d publicaciones leídas, %d nuevas", len(items), changed)
        return changed
