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

Crear citas solo funciona por CalDAV: una URL `.ics` es un fichero que se
descarga, no un sitio donde escribir. La creación es un `PUT` del evento en
un recurso nuevo, con `If-None-Match: *` para que el servidor rechace la
petición si ese recurso ya existiera en vez de pisarlo.
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

from . import Item
from .ical import (
    build_event, datetime_of, duration_of, is_all_day, parse_dt, parse_events,
    unescape, value_of,
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


def calendar_data(xml: bytes) -> list[dict]:
    """Los recursos de una respuesta 207: su href, su ETag y su iCalendar.

    El href hace falta para poder volver a ese recurso (moverlo o borrarlo) y
    el ETag para hacerlo de forma condicional: si alguien lo cambió desde el
    móvil mientras tanto, el servidor rechaza en vez de pisarlo.
    """
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        log.error("respuesta CalDAV ilegible: %s", exc)
        return []

    recursos = []
    for respuesta in root.iter(f"{{{DAV}}}response"):
        datos = respuesta.find(f".//{{{CALDAV}}}calendar-data")
        if datos is None or not (datos.text or "").strip():
            continue
        href = respuesta.find(f"{{{DAV}}}href")
        etag = respuesta.find(f".//{{{DAV}}}getetag")
        recursos.append({
            "href": (href.text or "").strip() if href is not None else "",
            "etag": (etag.text or "").strip() if etag is not None else "",
            "ics": datos.text,
        })
    return recursos


def _separa_excepciones(eventos: list[dict]) -> tuple[list[dict], dict]:
    """Aparta los VEVENT con RECURRENCE-ID: son excepciones, no eventos sueltos.

    Cuando una ocurrencia de una serie se mueve o se cancela, el fichero pasa
    a tener dos VEVENT con el mismo UID: el maestro con su RRULE y otro que
    dice «este día concreto, en realidad, así». Tratarlos como independientes
    duplicaría ese día en la agenda.
    """
    maestros, excepciones = [], {}
    for evento in eventos:
        uid = value_of(evento, "UID")
        cuando = datetime_of(evento, "RECURRENCE-ID")
        if cuando is not None:
            excepciones[(uid, cuando)] = evento
        else:
            maestros.append(evento)
    return maestros, excepciones


def event_items(text: str, calendario: str, desde: datetime, hasta: datetime,
                origen: str = "", href: str = "", etag: str = "") -> list[Item]:
    """Convierte un iCalendar en un item por cada ocurrencia en la ventana."""
    items: list[Item] = []
    maestros, excepciones = _separa_excepciones(parse_events(text))
    referencia = {"href": href, "etag": etag}

    for evento in maestros:
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
        uid = value_of(evento, "UID")
        ocurrencias = expand(inicio, regla, margen, hasta, excluidas, extra)
        for cuando in ocurrencias:
            if cuando + duracion <= desde:
                continue
            # Si ese día tiene excepción, manda la excepción; se emite aparte.
            if (uid, cuando) in excepciones:
                continue
            items.append(_item(evento, cuando, duracion, calendario, origen,
                               regla, referencia))

    # Las excepciones van por su propia fecha, que es justamente la que cambió.
    for (_, original), excepcion in excepciones.items():
        if value_of(excepcion, "STATUS").upper() == "CANCELLED":
            continue
        cuando = datetime_of(excepcion, "DTSTART")
        duracion = duration_of(excepcion)
        if cuando is None or cuando + duracion <= desde or cuando >= hasta:
            continue
        items.append(_item(excepcion, cuando, duracion, calendario, origen,
                           {}, referencia, original))
    return items


def _item(evento: dict, cuando: datetime, duracion: timedelta,
          calendario: str, origen: str, regla: dict,
          referencia: dict | None = None,
          recurrence_id: datetime | None = None) -> Item:
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
            "uid": uid,
            # Con qué volver a este recurso para moverlo o cancelarlo.
            "href": (referencia or {}).get("href", ""),
            "etag": (referencia or {}).get("etag", ""),
            "recurrence_id": recurrence_id.isoformat() if recurrence_id else "",
            "ocurrencia": cuando.isoformat(),
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
                 days_back: int = 7, interval_minutes: int = 0,
                 allow_write: bool = True):
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
        # Escribir solo tiene sentido por CalDAV, y solo si se permite.
        self.allow_write = bool(allow_write) and bool(self.caldav_url)
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

        nombre = self.nombre_calendario
        items = []
        for recurso in calendar_data(response.content):
            items.extend(event_items(
                recurso["ics"], nombre, desde, hasta, self.caldav_url,
                href=self._absoluta(recurso["href"]), etag=recurso["etag"]))
        return items

    def _absoluta(self, href: str) -> str:
        """El href de CalDAV viene relativo a la raíz del servidor."""
        if not href or href.startswith("http"):
            return href
        partes = urlsplit(self.caldav_url)
        return f"{partes.scheme}://{partes.netloc}{href}"

    def _ics(self, client, url: str, desde, hasta) -> list[Item]:
        response = client.get(url, headers={"User-Agent": "jarvis-asistente/0.1"})
        if response.status_code >= 400:
            log.error("error leyendo %s (%s)", url, response.status_code)
            return []
        nombre = urlsplit(url).netloc or "ics"
        return event_items(response.text, nombre, desde, hasta, url)

    def create_event(self, titulo: str, inicio: datetime, fin: datetime,
                     lugar: str = "", descripcion: str = "") -> dict:
        """Crea una cita por CalDAV. Devuelve qué pasó, sin lanzar excepciones.

        No hay reintentos a propósito: si algo sale mal, es preferible decirlo
        y que la persona decida, antes que arriesgarse a crear la cita dos
        veces en un calendario de verdad.
        """
        if not self.allow_write:
            return {"ok": False, "motivo": (
                "crear citas necesita CalDAV configurado y con escritura "
                "permitida; una URL .ics es de solo lectura")}

        uid = f"{uuid.uuid4()}@jarvis"
        cuerpo = build_event(uid, inicio, fin, titulo, lugar, descripcion)
        url = f"{self.caldav_url}/{uid}.ics"
        try:
            with Client(timeout=30.0, follow_redirects=True) as client:
                response = client.put(
                    url, content=cuerpo.encode(),
                    headers={"Content-Type": 'text/calendar; charset="utf-8"',
                             # El recurso tiene que ser nuevo: si existiera,
                             # que falle en vez de pisar lo que hubiera.
                             "If-None-Match": "*"},
                    auth=(self.caldav_user, self.caldav_password),
                )
        except Exception as exc:  # noqa: BLE001 - la red falla; hay que contarlo
            log.warning("no se pudo crear la cita (%s)", exc)
            return {"ok": False, "motivo": f"no se pudo hablar con el servidor: {exc}"}

        if response.status_code in (200, 201, 204):
            log.info("cita creada: %s (%s)", titulo, uid)
            # El ETag, si el servidor lo devuelve, permite moverla o
            # cancelarla ya, sin esperar a la siguiente sincronización.
            return {"ok": True, "uid": uid, "url": url, "ics": cuerpo,
                    "etag": response.headers.get("ETag", "")}
        if response.status_code == 412:
            return {"ok": False, "motivo": "ya existe un evento con ese identificador"}
        log.error("el servidor rechazó la cita (%s): %s",
                  response.status_code, response.text[:160])
        return {"ok": False,
                "motivo": f"el servidor respondió {response.status_code}"}

    def fetch_event(self, href: str) -> dict:
        """Trae un recurso tal como está ahora, con su ETag fresco.

        Se relee en vez de fiarse de lo indexado: entre la sincronización y
        ahora la cita ha podido cambiar desde el móvil, y el ETag de entonces
        ya no valdría.
        """
        if not self.allow_write:
            return {"ok": False, "motivo": "el calendario es de solo lectura"}
        try:
            with Client(timeout=30.0, follow_redirects=True) as client:
                response = client.get(
                    href, headers={"Accept": "text/calendar"},
                    auth=(self.caldav_user, self.caldav_password))
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "motivo": f"no se pudo hablar con el servidor: {exc}"}

        if response.status_code == 404:
            return {"ok": False, "motivo": "esa cita ya no está en el servidor"}
        if response.status_code >= 400:
            return {"ok": False,
                    "motivo": f"el servidor respondió {response.status_code}"}
        return {"ok": True, "ics": response.text,
                "etag": response.headers.get("ETag", "")}

    def update_event(self, href: str, etag: str, ics: str) -> dict:
        """Reemplaza un recurso, solo si nadie lo ha tocado desde ese ETag."""
        if not self.allow_write:
            return {"ok": False, "motivo": "el calendario es de solo lectura"}
        # Sin ETag no hay forma de escribir sin riesgo de pisar a otro.
        if not etag:
            return {"ok": False, "motivo": "no tengo la versión de esa cita"}
        try:
            with Client(timeout=30.0, follow_redirects=True) as client:
                response = client.put(
                    href, content=ics.encode(),
                    headers={"Content-Type": 'text/calendar; charset="utf-8"',
                             "If-Match": etag},
                    auth=(self.caldav_user, self.caldav_password))
        except Exception as exc:  # noqa: BLE001
            log.warning("no se pudo actualizar la cita (%s)", exc)
            return {"ok": False, "motivo": f"no se pudo hablar con el servidor: {exc}"}
        return self._resultado(response, "actualizar")

    def delete_event(self, href: str, etag: str) -> dict:
        """Borra un recurso, solo si nadie lo ha tocado desde ese ETag."""
        if not self.allow_write:
            return {"ok": False, "motivo": "el calendario es de solo lectura"}
        if not etag:
            return {"ok": False, "motivo": "no tengo la versión de esa cita"}
        try:
            with Client(timeout=30.0, follow_redirects=True) as client:
                response = client.request(
                    "DELETE", href, headers={"If-Match": etag},
                    auth=(self.caldav_user, self.caldav_password))
        except Exception as exc:  # noqa: BLE001
            log.warning("no se pudo borrar la cita (%s)", exc)
            return {"ok": False, "motivo": f"no se pudo hablar con el servidor: {exc}"}
        return self._resultado(response, "borrar")

    @staticmethod
    def _resultado(response, verbo: str) -> dict:
        if response.status_code in (200, 201, 204):
            return {"ok": True, "etag": response.headers.get("ETag", "")}
        if response.status_code == 412:
            # Justo lo que queríamos que pasara: alguien la cambió mientras tanto.
            return {"ok": False, "motivo": (
                "esa cita ha cambiado desde la última sincronización; "
                "vuelve a preguntármelo y la miro otra vez")}
        if response.status_code == 404:
            return {"ok": False, "motivo": "esa cita ya no está en el servidor"}
        log.error("el servidor rechazó %s (%s): %s",
                  verbo, response.status_code, response.text[:160])
        return {"ok": False, "motivo": f"el servidor respondió {response.status_code}"}

    def forget_event(self, store, calendario: str, uid: str) -> int:
        """Quita del índice todas las ocurrencias de un UID."""
        return store.delete_prefix(f"calendario:{calendario}:{uid}:")

    @property
    def nombre_calendario(self) -> str:
        return self.caldav_url.rstrip("/").rsplit("/", 1)[-1] or "calendario"

    def index_event(self, store, ics: str, inicio: datetime, fin: datetime,
                    href: str = "", etag: str = "") -> int:
        """Mete en el índice lo que se acaba de crear, sin esperar al siguiente ciclo.

        La ventana se ensancha hasta abarcar la cita: si alguien pone algo
        para dentro de un año, tiene que constar igual.
        """
        desde, hasta = self.ventana()
        nombre = self.nombre_calendario
        return store.upsert(event_items(
            ics, nombre, min(desde, inicio), max(hasta, fin + timedelta(seconds=1)),
            self.caldav_url, href=href, etag=etag))

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
