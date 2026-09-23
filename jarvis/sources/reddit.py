"""Reddit: tu portada o los subreddits que elijas.

La API es gratuita para uso personal (100 consultas por minuto; Jarvis hace
dos por sincronización). Necesita una aplicación de tipo «script» creada en
https://www.reddit.com/prefs/apps, que da un client_id y un client_secret.

Dos detalles que Reddit exige y que si fallan se traducen en un 401 o en un
429 difíciles de diagnosticar:

  · el User-Agent tiene que seguir el formato <plataforma>:<app>:<versión>
    (by /u/<usuario>); los genéricos los estrangulan;
  · el token dura una hora, así que se pide uno nuevo en cada sincronización
    en lugar de guardarlo, igual que en Bluesky.

La contraseña solo funciona si la cuenta no tiene verificación en dos pasos.
Con 2FA activo hay que añadirle el código al final (`contraseña:123456`), que
caduca, así que para ese caso conviene una cuenta dedicada al asistente.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from . import Item
from ..http import Client

log = logging.getLogger("jarvis.sources.reddit")

MAX_BODY = 3000
TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
API = "https://oauth.reddit.com"


def parse_post(child: dict) -> Item | None:
    """Convierte un elemento de un Listing en un item. Sin red: es testeable."""
    data = (child or {}).get("data") or {}
    title = (data.get("title") or "").strip()
    if not title:
        return None

    subreddit = data.get("subreddit") or ""
    body = (data.get("selftext") or "").strip()
    if not body and (link := data.get("url")):
        # Una publicación de enlace no trae texto: al menos que conste adónde va.
        body = f"Enlace: {link}"

    when = ""
    if created := data.get("created_utc"):
        try:
            when = datetime.fromtimestamp(float(created), timezone.utc) \
                .isoformat(timespec="seconds")
        except (TypeError, ValueError, OSError):
            when = ""

    permalink = data.get("permalink") or ""
    return Item(
        id=f"reddit:{data.get('name') or 't3_' + str(data.get('id', ''))}",
        source="reddit",
        kind="publicación",
        title=f"r/{subreddit}: {title[:110]}" if subreddit else title[:110],
        body=body[:MAX_BODY],
        author=f"u/{data.get('author', '')}",
        url=f"https://www.reddit.com{permalink}" if permalink else (data.get("url") or ""),
        created_at=when or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        meta={
            "subreddit": subreddit,
            "votos": data.get("score", 0),
            "comentarios": data.get("num_comments", 0),
            "etiqueta": data.get("link_flair_text") or "",
            "nsfw": bool(data.get("over_18")),
        },
    )


class RedditSource:
    name = "reddit"

    def __init__(self, username: str, feed: str = "best",
                 subreddits: list | None = None, limit: int = 40):
        client_id = os.getenv("JARVIS_REDDIT_CLIENT_ID", "")
        secret = os.getenv("JARVIS_REDDIT_CLIENT_SECRET", "")
        password = os.getenv("JARVIS_REDDIT_PASSWORD", "")
        if not (username and client_id and secret and password):
            raise RuntimeError(
                "falta sources.reddit.username en config.yaml o alguna de "
                "JARVIS_REDDIT_CLIENT_ID / _CLIENT_SECRET / _PASSWORD en .env")
        self.username = username
        self.client_id = client_id
        self.secret = secret
        self.password = password
        self.feed = feed if feed in ("best", "hot", "new", "top") else "best"
        self.subreddits = [s for s in (subreddits or []) if s]
        self.limit = min(100, limit)
        # Formato exigido por Reddit; uno genérico acaba estrangulado.
        self.user_agent = f"python:jarvis-asistente:0.1 (by /u/{username})"

    @property
    def path(self) -> str:
        """Los subreddits elegidos, o tu portada personalizada si no hay."""
        if self.subreddits:
            return f"/r/{'+'.join(self.subreddits)}/{self.feed}"
        return f"/{self.feed}"

    def sync(self, store) -> int:
        with Client(timeout=30.0) as client:
            token_response = client.post(
                TOKEN_URL,
                auth=(self.client_id, self.secret),
                data={"grant_type": "password", "username": self.username,
                      "password": self.password},
                headers={"User-Agent": self.user_agent},
            )
            if token_response.status_code >= 400:
                log.error("no se pudo autenticar en Reddit (%s): %s",
                          token_response.status_code, token_response.text[:160])
                return 0

            token = token_response.json().get("access_token", "")
            listing = client.get(
                f"{API}{self.path}",
                headers={"Authorization": f"bearer {token}",
                         "User-Agent": self.user_agent},
                params={"limit": self.limit},
            )
            if listing.status_code >= 400:
                log.error("error leyendo %s (%s): %s", self.path,
                          listing.status_code, listing.text[:160])
                return 0

        children = ((listing.json() or {}).get("data") or {}).get("children") or []
        items = [item for child in children if (item := parse_post(child))]
        changed = store.upsert(items)
        log.info("reddit: %d publicaciones leídas de %s, %d nuevas",
                 len(items), self.path, changed)
        return changed
