"""Calendario: CalDAV con contraseña, o URLs `.ics` sueltas.

Dos maneras de conectarlo, según lo que dé tu proveedor:

  · **CalDAV** (iCloud, Fastmail, Nextcloud, Radicale…): la URL de la colección
    del calendario más usuario y contraseña —de aplicación, no la de la cuenta—.
    Jarvis manda un `REPORT` de tipo `calendar-query` acotado por fechas, así
    que el servidor solo devuelve lo que cae en la ventana.
  · **`.ics`**: la «URL secreta en formato iCal» que ofrecen Google Calendar y
    Outlook. No necesita credenciales y es un simple GET. Es la vía para Google,
    que retiró la autenticación básica de CalDAV y hoy exige OAuth 2.0.

Las repeticiones se expanden aquí (ver `rrule.py`) en vez de pedirle al
servidor que lo haga: así el resultado es el mismo por las dos vías, y no
depende de que el servidor implemente `<C:expand>`.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

from . import Item
from .ical import (
    datetime_of, duration_of, is_all_day, parse_dt, parse_events, unescape,
    value_of,
)
from .rrule import expand, is_supported, parse_rrule
from ..http import Client

log = logging.getLogger("jarvis.sources.calendario")

MAX_BODY = 2000
CALDAV = "urn:ietf:params:xml:ns:caldav"
DAV = "DAV:"

# `calendar-query` acotado por fechas: el servidor criba antes de enviar.
CONSULTA = """<?xml version="1.0" encoding="utf-8"?>
<C:calendar-query xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
  <D:prop><D:getetag/><C:calendar-data/></D:prop>
  <C:filter>
    <C:comp-filter name="VCALENDAR">
      <C:comp-filter name="VEVENT">
        <C:time-range start="{desde}" end="{hasta}"/>
      </C:comp-filter>
    </C:comp-filter>
  </C:filter>
</C:calendar-query>"""


def _marca(fecha: datetime) -> str:
    return fecha.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def calendar_data(xml: bytes) -> list[str]:
    """Saca los iCalendar de una respuesta 207 Multi-Status de CalDAV."""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        log.error("respuesta CalDAV ilegible: %s", exc)
        return []
    return [nodo.text for nodo in root.iter(f"{{{CALDAV}}}calendar-data")
            if (nodo.text or "").strip()]


def event_items(text: str, calendario: str, desde: datetime, hasta: datetime,
                origen: str = "") -> list[Item]:
    """Convierte un iCalendar en un item por cada ocurrencia en la ventana."""
    items: list[Item] = []
    for evento in parse_events(text):
        inicio = datetime_of(evento, "DTSTART")
        if inicio is None:
            continue

        regla = parse_rrule(evento.get("RRULE", ({}, ""))[1])
        excluidas = {fecha for params, valor in evento.get("EXDATE", [])
                     for trozo in valor.split(",")
                     if (fecha := parse_dt(trozo, params, inicio.tzinfo))}
        extra = [fecha for params, valor in evento.get("RDATE", [])
                 for trozo in valor.split(",")
                 if (fecha := parse_dt(trozo, params, inicio.tzinfo))]

        duracion = duration_of(evento)
        # Un evento que empezó antes de la ventana pero aún no ha terminado
        # sigue siendo «lo que tengo ahora»: se mira desde su principio.
        margen = desde - duracion
        ocurrencias = expand(inicio, regla, margen, hasta, excluidas, extra)
        for cuando in ocurrencias:
            if cuando + duracion <= desde:
                continue
            items.append(_item(evento, cuando, duracion, calendario, origen, regla))
    return items


def _item(evento: dict, cuando: datetime, duracion: timedelta,
          calendario: str, origen: str, regla: dict) -> Item:
    titulo = value_of(evento, "SUMMARY") or "(sin título)"
    lugar = value_of(evento, "LOCATION")
    descripcion = value_of(evento, "DESCRIPTION")
    todo_el_dia = is_all_day(evento)

    fin = cuando + duracion
    if todo_el_dia:
        horario = "todo el día"
    elif fin.date() == cuando.date():
        horario = f"{cuando:%H:%M}–{fin:%H:%M}"
    else:
        horario = f"{cuando:%d/%m %H:%M} – {fin:%d/%m %H:%M}"

    cuerpo = " · ".join(p for p in (horario, lugar) if p)
    if descripcion:
        cuerpo += f"\n{descripcion}"

    uid = value_of(evento, "UID") or titulo
    # ATTENDEE se repite una vez por invitado, así que llega como lista.
    invitados = [unescape(valor).replace("mailto:", "").replace("MAILTO:", "")
                 for _, valor in evento.get("ATTENDEE", [])]

    return Item(
        id=f"calendario:{calendario}:{uid}:{cuando.isoformat()}",
        source="calendario",
        kind="evento",
        title=titulo[:110],
        body=cuerpo[:MAX_BODY],
        author=calendario,
        url=origen if origen.startswith("http") else "",
        created_at=cuando.astimezone(timezone.utc).isoformat(timespec="seconds"),
        meta={
            "calendario": calendario,
            "lugar": lugar,
            "todo_el_dia": todo_el_dia,
            "minutos": int(duracion.total_seconds() // 60),
            "se_repite": bool(regla),
            # Si la regla usa algo que no sabemos expandir hay que decirlo:
            # de ese evento solo consta la primera aparición.
            "repeticion_sin_expandir": bool(regla) and not is_supported(regla),
            "invitados": invitados[:10],
        },
    )


class CalendarSource:
    name = "calendario"

    def __init__(self, caldav_url: str = "", caldav_user: str = "",
                 ics: list | None = None, days_ahead: int = 60,
                 days_back: int = 7, interval_minutes: int = 0):
        self.caldav_url = (caldav_url or "").rstrip("/")
        self.caldav_user = caldav_user or ""
        self.caldav_password = os.getenv("JARVIS_CALDAV_PASSWORD", "")
        self.ics = [u for u in (ics or []) if u]
        if not (self.caldav_url or self.ics):
            raise RuntimeError(
                "el calendario necesita sources.calendar.caldav.url o alguna "
                "URL en sources.calendar.ics")
        if self.caldav_url and not (self.caldav_user and self.caldav_password):
            raise RuntimeError(
                "falta sources.calendar.caldav.user en config.yaml o "
                "JARVIS_CALDAV_PASSWORD en .env")
        self.days_ahead = max(1, days_ahead)
        self.days_back = max(0, days_back)
        self.interval_minutes = interval_minutes

    def ventana(self) -> tuple[datetime, datetime]:
        ahora = datetime.now(timezone.utc)
        return (ahora - timedelta(days=self.days_back),
                ahora + timedelta(days=self.days_ahead))

    def _caldav(self, client, desde, hasta) -> list[Item]:
        response = client.request(
            "REPORT", self.caldav_url,
            content=CONSULTA.format(desde=_marca(desde), hasta=_marca(hasta)),
            headers={"Content-Type": 'application/xml; charset="utf-8"',
                     "Depth": "1"},
            auth=(self.caldav_user, self.caldav_password),
        )
        if response.status_code >= 400:
            log.error("error de CalDAV (%s): %s",
                      response.status_code, response.text[:160])
            return []

        nombre = self.caldav_url.rstrip("/").rsplit("/", 1)[-1] or "calendario"
        items = []
        for texto in calendar_data(response.content):
            items.extend(event_items(texto, nombre, desde, hasta, self.caldav_url))
        return items

    def _ics(self, client, url: str, desde, hasta) -> list[Item]:
        response = client.get(url, headers={"User-Agent": "jarvis-asistente/0.1"})
        if response.status_code >= 400:
            log.error("error leyendo %s (%s)", url, response.status_code)
            return []
        nombre = urlsplit(url).netloc or "ics"
        return event_items(response.text, nombre, desde, hasta, url)

    def sync(self, store) -> int:
        desde, hasta = self.ventana()
        items: list[Item] = []
        with Client(timeout=30.0, follow_redirects=True) as client:
            if self.caldav_url:
                try:
                    items.extend(self._caldav(client, desde, hasta))
                except Exception as exc:  # noqa: BLE001 - un calendario caído no para al resto
                    log.warning("no se pudo leer el CalDAV (%s)", exc)
            for url in self.ics:
                try:
                    items.extend(self._ics(client, url, desde, hasta))
                except Exception as exc:  # noqa: BLE001
                    log.warning("no se pudo leer %s (%s)", url, exc)

        changed = store.upsert(items)
        log.info("calendario: %d ocurrencias en la ventana, %d nuevas",
                 len(items), changed)
        return changed
