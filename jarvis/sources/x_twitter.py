"""X (antes Twitter): tu línea temporal cronológica.

Es la única fuente que cuesta dinero. Desde febrero de 2026 la API es de pago
por uso, pero leer *tus propios datos* («Owned Reads») está a 0,001 $ por
recurso y **X deduplica por día UTC**: si una publicación ya se cobró hoy,
volver a leerla sale gratis. El coste real lo marca cuántas publicaciones
distintas pasan por tu timeline al día, no cada cuánto sincroniza Jarvis.

Autenticación por OAuth 1.0a: las cuatro credenciales se generan de una vez en
el portal de desarrolladores y no caducan, así que no hace falta el paseo por
el navegador que exige OAuth 2.0 con PKCE.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from . import Item
from .oauth1 import authorization_header
from ..http import Client

log = logging.getLogger("jarvis.sources.x")

API = "https://api.x.com/2"
MAX_BODY = 3000


def parse_post(tweet: dict, authors: dict | None = None) -> Item | None:
    """Convierte una publicación en un item. Sin red: es testeable."""
    text = (tweet or {}).get("text", "").strip()
    if not text:
        return None

    author = (authors or {}).get(tweet.get("author_id", ""), {})
    handle = author.get("username", "")
    name = author.get("name") or handle or tweet.get("author_id", "")
    metrics = tweet.get("public_metrics") or {}

    return Item(
        id=f"x:{tweet.get('id', '')}",
        source="x",
        kind="publicación",
        title=f"{name}: {text[:110]}",
        body=text[:MAX_BODY],
        author=f"@{handle}" if handle else "",
        url=f"https://x.com/{handle}/status/{tweet.get('id', '')}" if handle else "",
        created_at=tweet.get("created_at")
                   or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        meta={
            "nombre": name,
            "respuestas": metrics.get("reply_count", 0),
            "me_gusta": metrics.get("like_count", 0),
            "republicaciones": metrics.get("retweet_count", 0),
        },
    )


def authors_by_id(payload: dict) -> dict:
    """El texto trae author_id; los nombres vienen aparte, en `includes`."""
    users = ((payload or {}).get("includes") or {}).get("users") or []
    return {user.get("id", ""): user for user in users}


class XSource:
    name = "x"

    def __init__(self, user_id: str = "", limit: int = 40,
                 interval_minutes: int = 0):
        self.api_key = os.getenv("JARVIS_X_API_KEY", "")
        self.api_secret = os.getenv("JARVIS_X_API_SECRET", "")
        self.token = os.getenv("JARVIS_X_ACCESS_TOKEN", "")
        self.token_secret = os.getenv("JARVIS_X_ACCESS_SECRET", "")
        if not all((self.api_key, self.api_secret, self.token, self.token_secret)):
            raise RuntimeError(
                "faltan credenciales de X: JARVIS_X_API_KEY / _API_SECRET / "
                "_ACCESS_TOKEN / _ACCESS_SECRET en .env")
        # Conocer tu id ahorra una petición (y su recurso) en cada ciclo.
        self.user_id = str(user_id or "")
        self.limit = max(5, min(100, limit))
        self.interval_minutes = interval_minutes

    def _get(self, client, url: str, params: dict) -> dict | None:
        header = authorization_header(
            "GET", url, self.api_key, self.api_secret,
            self.token, self.token_secret, params=params)
        response = client.get(url, params=params, headers={"Authorization": header})
        if response.status_code >= 400:
            log.error("error de la API de X (%s) en %s: %s",
                      response.status_code, url.rsplit("/2", 1)[-1],
                      response.text[:160])
            return None
        return response.json()

    def _resolve_user_id(self, client) -> str:
        if self.user_id:
            return self.user_id
        payload = self._get(client, f"{API}/users/me", {})
        self.user_id = str(((payload or {}).get("data") or {}).get("id", ""))
        if self.user_id:
            log.info("id de usuario de X resuelto (%s); fíjalo en config.yaml "
                     "para ahorrar una petición por ciclo", self.user_id)
        return self.user_id

    def sync(self, store) -> int:
        with Client(timeout=30.0) as client:
            user_id = self._resolve_user_id(client)
            if not user_id:
                return 0

            payload = self._get(
                client,
                f"{API}/users/{user_id}/timelines/reverse_chronological",
                {
                    "max_results": str(self.limit),
                    "tweet.fields": "created_at,public_metrics",
                    "expansions": "author_id",
                    "user.fields": "username,name",
                },
            )

        if payload is None:
            return 0

        authors = authors_by_id(payload)
        items = [item for tweet in (payload.get("data") or [])
                 if (item := parse_post(tweet, authors))]
        changed = store.upsert(items)
        log.info("x: %d publicaciones leídas, %d nuevas", len(items), changed)
        return changed
