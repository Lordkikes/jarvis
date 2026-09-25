"""RSS y Atom: blogs, noticias y todo lo que publique un feed.

La única fuente que no necesita credenciales ni cuenta en ningún sitio: basta
pegar las URLs en `config.yaml`. Entiende los tres formatos que circulan —RSS
2.0, RSS 1.0 (RDF) y Atom— porque en la práctica te vas a encontrar los tres.

Dos cuidados que no son opcionales al leer XML que viene de fuera:

  · **DTD rechazado.** `xml.etree.ElementTree` expande las entidades internas
    (comprobado), así que un feed hostil puede hacer estallar la memoria con
    una «billion laughs». No resuelve entidades externas, o sea que no hay
    lectura de ficheros, pero la bomba sí es real: si el prólogo declara un
    DTD, el documento se descarta entero.
  · **Tope de tamaño.** Un feed legítimo ocupa kilobytes; se corta en 5 MB.

Además se usa GET condicional (ETag / Last-Modified): el servidor responde
304 y Jarvis se ahorra descargar y parsear lo que ya tiene.
"""
from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

from . import Item, html_to_text
from ..http import Client

log = logging.getLogger("jarvis.sources.rss")

MAX_BODY = 3000
MAX_BYTES = 5_000_000

ATOM = "http://www.w3.org/2005/Atom"
RSS1 = "http://purl.org/rss/1.0/"
DC = "http://purl.org/dc/elements/1.1/"
CONTENT = "http://purl.org/rss/1.0/modules/content/"

# "+0200" -> "+02:00"; fromisoformat en 3.10 solo acepta la forma con dos puntos.
_TZ_SIN_DOSPUNTOS = re.compile(r"([+-]\d{2})(\d{2})$")
_FRACCION_LARGA = re.compile(r"\.(\d{1,6})\d*")


def has_doctype(raw: bytes) -> bool:
    """¿Declara el documento un DTD? Solo puede hacerlo antes del elemento raíz.

    Se recorre el prólogo saltando la declaración XML y los comentarios; en
    cuanto aparece una etiqueta normal, el prólogo terminó y no hay DTD.
    """
    i = 0
    while (i := raw.find(b"<", i)) >= 0:
        if raw.startswith(b"<!DOCTYPE", i):
            return True
        if raw.startswith(b"<!--", i):
            if (fin := raw.find(b"-->", i + 4)) < 0:
                return False
            i = fin + 3
        elif raw.startswith(b"<?", i):
            if (fin := raw.find(b"?>", i + 2)) < 0:
                return False
            i = fin + 2
        else:
            return False  # el elemento raíz: se acabó el prólogo
    return False


def parse_date(value: str) -> str:
    """Fecha de un feed a ISO 8601. RSS usa RFC 822 y Atom ISO; se aceptan ambas."""
    value = (value or "").strip()
    if not value:
        return ""

    iso = value[:-1] + "+00:00" if value.endswith(("Z", "z")) else value
    iso = _TZ_SIN_DOSPUNTOS.sub(r"\1:\2", iso)
    iso = _FRACCION_LARGA.sub(r".\1", iso)
    for intento in (lambda: datetime.fromisoformat(iso),
                    lambda: parsedate_to_datetime(value)):
        try:
            fecha = intento()
        except (TypeError, ValueError):
            continue
        if fecha.tzinfo is None:
            fecha = fecha.replace(tzinfo=timezone.utc)
        return fecha.astimezone(timezone.utc).isoformat(timespec="seconds")
    return ""


def _text(element, *paths: str) -> str:
    """El primer camino que exista y traiga texto."""
    for path in paths:
        found = element.find(path)
        if found is not None and (found.text or "").strip():
            return found.text.strip()
    return ""


def _link(entry) -> str:
    """En RSS el enlace es el texto; en Atom, el atributo href de <link>."""
    if directo := _text(entry, "link", f"{{{RSS1}}}link"):
        return directo
    enlaces = entry.findall(f"{{{ATOM}}}link")
    alternativos = [e for e in enlaces if e.get("rel", "alternate") == "alternate"]
    for enlace in alternativos or enlaces:
        if href := enlace.get("href"):
            return href
    return ""


def parse_feed(raw: bytes, feed_url: str = "") -> list[Item]:
    """Convierte un feed en items. Sin red: es testeable.

    Devuelve lista vacía si el XML no se puede leer o declara un DTD; un feed
    roto no debe tumbar la sincronización de los demás.
    """
    if has_doctype(raw):
        log.warning("feed descartado por declarar un DTD: %s", feed_url or "?")
        return []
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        log.warning("feed ilegible (%s): %s", feed_url or "?", exc)
        return []

    # RSS 2.0 y RSS 1.0 cuelgan la cabecera de <channel>; Atom, de la raíz.
    canal = root
    for camino in ("channel", f"{{{RSS1}}}channel"):
        if (encontrado := root.find(camino)) is not None:
            canal = encontrado
            break
    nombre = _text(canal, "title", f"{{{ATOM}}}title", f"{{{RSS1}}}title") \
        or urlsplit(feed_url).netloc or "feed"
    host = urlsplit(feed_url).netloc or nombre

    entradas = (root.findall(".//item") + root.findall(f".//{{{RSS1}}}item")
                + root.findall(f".//{{{ATOM}}}entry"))
    return [item for entrada in entradas
            if (item := _parse_entry(entrada, nombre, host))]


def _parse_entry(entry, nombre: str, host: str) -> Item | None:
    titulo = _text(entry, "title", f"{{{ATOM}}}title", f"{{{RSS1}}}title")
    enlace = _link(entry)
    cuerpo = html_to_text(_text(
        entry,
        f"{{{CONTENT}}}encoded", "description", f"{{{RSS1}}}description",
        f"{{{ATOM}}}content", f"{{{ATOM}}}summary",
    ))
    if not (titulo or cuerpo):
        return None

    # Sin guid ni enlace, el propio título sirve de identidad estable.
    identidad = _text(entry, "guid", f"{{{ATOM}}}id") or enlace or titulo
    fecha = parse_date(_text(
        entry, "pubDate", f"{{{DC}}}date",
        f"{{{ATOM}}}published", f"{{{ATOM}}}updated",
    ))
    autor = _text(entry, f"{{{DC}}}creator", "author") \
        or _text(entry, f"{{{ATOM}}}author/{{{ATOM}}}name")

    etiquetas = [(c.text or c.get("term") or "").strip()
                 for c in entry.findall("category") + entry.findall(f"{{{ATOM}}}category")]

    return Item(
        id=f"rss:{host}:{identidad}",
        source="rss",
        kind="artículo",
        title=f"{nombre}: {(titulo or cuerpo)[:110]}",
        body=cuerpo[:MAX_BODY],
        author=autor or nombre,
        url=enlace,
        created_at=fecha or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        meta={"feed": nombre, "etiquetas": [e for e in etiquetas if e]},
    )


class RssSource:
    name = "rss"

    def __init__(self, feeds: list | None = None, limit: int = 40,
                 interval_minutes: int = 0):
        self.feeds = [f for f in (feeds or []) if f]
        if not self.feeds:
            raise RuntimeError("no hay ninguna URL en sources.rss.feeds")
        self.limit = max(1, limit)
        self.interval_minutes = interval_minutes
        # ETag / Last-Modified por feed, para pedir solo lo que haya cambiado.
        self._cache: dict[str, dict] = {}

    def _fetch(self, client, url: str) -> bytes | None:
        """Descarga un feed, o None si no cambió, falló o es demasiado grande."""
        response = client.get(url, headers={
            "User-Agent": "jarvis-asistente/0.1 (+lector de feeds personal)",
            **self._cache.get(url, {}),
        })
        if response.status_code == 304:
            return None
        if response.status_code >= 400:
            log.error("error leyendo %s (%s)", url, response.status_code)
            return None

        raw = response.content
        if len(raw) > MAX_BYTES:
            log.warning("feed demasiado grande, descartado (%s): %d bytes", url, len(raw))
            return None

        condicional = {}
        if etag := response.headers.get("ETag"):
            condicional["If-None-Match"] = etag
        if modificado := response.headers.get("Last-Modified"):
            condicional["If-Modified-Since"] = modificado
        self._cache[url] = condicional
        return raw

    def sync(self, store) -> int:
        items: list[Item] = []
        with Client(timeout=30.0, follow_redirects=True) as client:
            for url in self.feeds:
                try:
                    if (raw := self._fetch(client, url)) is None:
                        continue
                except Exception as exc:  # noqa: BLE001 - un feed caído no para al resto
                    log.warning("no se pudo leer %s (%s)", url, exc)
                    continue
                # Los feeds vienen del más nuevo al más viejo.
                items.extend(parse_feed(raw, url)[:self.limit])

        changed = store.upsert(items)
        log.info("rss: %d entradas leídas de %d feeds, %d nuevas",
                 len(items), len(self.feeds), changed)
        return changed
