"""X (antes Twitter): lo tuyo, que es lo único que sale a cuenta leer.

Es la única fuente que cuesta dinero. Desde febrero de 2026 la API es de pago
por uso, pero leer *tus propios datos* («Owned Reads») está a 0,001 $ por
recurso y **X deduplica por día UTC**: si una publicación ya se cobró hoy,
volver a leerla sale gratis. El coste real lo marca cuántas publicaciones
distintas pasan al día, no cada cuánto sincroniza Jarvis.

Se leen tres corrientes, y las tres son Owned Reads:

- **timeline**: tu línea temporal cronológica.
- **menciones**: lo que te nombra a ti, que es lo que de verdad quieres oír
  en voz alta.
- **propias**: tus publicaciones, para poder preguntar qué escribiste.

Cada una es una petición aparte y se cobra aparte, así que vienen apagadas
menos el timeline. Los **marcadores**, que también son Owned Reads, se quedan
fuera a propósito: la documentación y los foros no se ponen de acuerdo sobre
si aceptan OAuth 1.0a, y hay informes de 403 pidiendo OAuth 2.0. Prefiero no
añadir un camino que igual no funciona.

Y una condición que se cobra cara si se incumple: el precio de Owned Read
solo aplica cuando el usuario autenticado es el dueño de la app. Si pones en
`user_id` el de otra persona, esas lecturas salen por la tarifa normal.

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

# Qué se puede leer de lo tuyo: el camino, cómo se llama lo que trae y qué
# parámetros propios lleva. `exclude` deja fuera las respuestas y los reenvíos
# de las tuyas, que es lo que uno entiende por «lo que he publicado».
CORRIENTES = {
    "timeline": {"ruta": "timelines/reverse_chronological",
                 "kind": "publicación", "params": {}},
    "menciones": {"ruta": "mentions", "kind": "mención", "params": {}},
    "propias": {"ruta": "tweets", "kind": "publicación propia",
                "params": {"exclude": "retweets,replies"}},
}
POR_DEFECTO = ("timeline",)
# Entre corrientes, la más concreta manda: si algo aparece en el timeline y
# además te menciona, es una mención.
PRECEDENCIA = ("timeline", "propias", "menciones")


def corrientes_validas(pedidas) -> tuple[str, ...]:
    """Las que existen, en orden de precedencia. Sin nada válido, el timeline."""
    if isinstance(pedidas, str):
        pedidas = pedidas.split(",")
    pedidas = {str(c).strip().lower() for c in (pedidas or ())}
    validas = tuple(c for c in PRECEDENCIA if c in pedidas)
    return validas or POR_DEFECTO


def parse_post(tweet: dict, authors: dict | None = None,
               corriente: str = "timeline") -> Item | None:
    """Convierte una publicación en un item. Sin red: es testeable."""
    text = (tweet or {}).get("text", "").strip()
    if not text:
        return None

    author = (authors or {}).get(tweet.get("author_id", ""), {})
    handle = author.get("username", "")
    name = author.get("name") or handle or tweet.get("author_id", "")
    metrics = tweet.get("public_metrics") or {}

    corriente = corriente if corriente in CORRIENTES else "timeline"
    return Item(
        id=f"x:{tweet.get('id', '')}",
        source="x",
        kind=CORRIENTES[corriente]["kind"],
        title=f"{name}: {text[:110]}",
        body=text[:MAX_BODY],
        author=f"@{handle}" if handle else "",
        url=f"https://x.com/{handle}/status/{tweet.get('id', '')}" if handle else "",
        created_at=tweet.get("created_at")
                   or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        meta={
            "nombre": name,
            "corriente": corriente,
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
                 interval_minutes: int = 0, lee=POR_DEFECTO):
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
        self.lee = corrientes_validas(lee)

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
        por_id: dict[str, Item] = {}
        with Client(timeout=30.0) as client:
            user_id = self._resolve_user_id(client)
            if not user_id:
                return 0

            for corriente in self.lee:
                payload = self._get(
                    client,
                    f"{API}/users/{user_id}/{CORRIENTES[corriente]['ruta']}",
                    {
                        "max_results": str(self.limit),
                        "tweet.fields": "created_at,public_metrics",
                        "expansions": "author_id",
                        "user.fields": "username,name",
                        **CORRIENTES[corriente]["params"],
                    },
                )
                if payload is None:
                    # Una corriente caída no se lleva por delante a las otras.
                    continue

                authors = authors_by_id(payload)
                for tweet in (payload.get("data") or []):
                    # `self.lee` va en orden de precedencia, así que lo que
                    # llega después pisa a lo anterior con buen criterio.
                    if (item := parse_post(tweet, authors, corriente)):
                        por_id[item.id] = item

        items = list(por_id.values())
        changed = store.upsert(items)
        log.info("x: %d publicaciones leídas de %s, %d nuevas",
                 len(items), ", ".join(self.lee), changed)
        return changed
