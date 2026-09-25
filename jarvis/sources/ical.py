"""Lector y escritor de iCalendar (RFC 5545), con la biblioteca estándar.

Se implementa aquí en vez de traer `icalendar` + `dateutil` porque lo que
Jarvis necesita es un subconjunto pequeño y bien delimitado: leer los VEVENT
de un calendario y saber cuándo caen. A cambio hay que respetar las cuatro
trampas del formato, que son justo las que rompen los parseos ingenuos:

  · **Plegado de líneas.** Una línea de más de 75 octetos se parte y continúa
    en la siguiente, que empieza por espacio o tabulador. Hay que volver a
    unirlas *antes* de mirar nada.
  · **Parámetros.** `DTSTART;TZID=Europe/Madrid:20260925T100000` — el nombre,
    los parámetros y el valor van separados por `;` y `:`, pero un `:` dentro
    de un parámetro entrecomillado no cuenta.
  · **Escapes.** En los textos, `\\n` es un salto de línea y `\\,` `\;` `\\\\`
    son la coma, el punto y coma y la barra literales.
  · **Componentes anidados.** Un VEVENT puede llevar dentro un VALARM, y el
    calendario entero suele empezar por varios VTIMEZONE. Ni una alarma es un
    evento ni un huso horario lo es.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, time, timedelta, timezone

try:  # pragma: no cover - zoneinfo está en la estándar desde 3.9
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore[assignment]

log = logging.getLogger("jarvis.sources.ical")

# Un `:` o un `;` solo separan si no están dentro de comillas.
_PARAM = re.compile(r';(?=(?:[^"]*"[^"]*")*[^"]*$)')
_ESCAPES = (("\\N", "\n"), ("\\n", "\n"), ("\\,", ","), ("\;", ";"))


def unfold(text: str) -> list[str]:
    """Deshace el plegado: las continuaciones empiezan por espacio o tabulador."""
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        elif raw:
            lines.append(raw)
    return lines


def unescape(value: str) -> str:
    """Devuelve el texto con los escapes de RFC 5545 resueltos."""
    # La barra doble se trata aparte para que `\\n` (barra literal + n) no se
    # convierta en un salto de línea.
    partes = value.split("\\\\")
    resueltas = []
    for parte in partes:
        for escape, real in _ESCAPES:
            parte = parte.replace(escape, real)
        resueltas.append(parte)
    return "\\".join(resueltas)


def parse_line(line: str) -> tuple[str, dict, str]:
    """`DTSTART;TZID=Europe/Madrid:2026…` -> ('DTSTART', {'TZID': …}, '2026…')."""
    head, _, value = line.partition(":")
    name, *params = _PARAM.split(head)
    parametros = {}
    for param in params:
        clave, _, valor = param.partition("=")
        parametros[clave.strip().upper()] = valor.strip().strip('"')
    return name.strip().upper(), parametros, value


def _zone(tzid: str):
    if not (tzid and ZoneInfo):
        return None
    try:
        return ZoneInfo(tzid)
    except Exception:  # noqa: BLE001 - un TZID desconocido no debe tumbar nada
        log.debug("huso horario desconocido: %s", tzid)
        return None


def parse_dt(value: str, params: dict | None = None, default_tz=timezone.utc):
    """Una fecha de iCalendar a datetime consciente, o None si no se entiende.

    Tres formas posibles: `20260925` (día entero), `20260925T100000Z` (UTC) y
    `20260925T100000` (hora local del TZID, o «flotante» si no hay ninguno).
    """
    value = (value or "").strip()
    params = params or {}
    if not value:
        return None

    zona = _zone(params.get("TZID", "")) or default_tz
    try:
        if params.get("VALUE") == "DATE" or len(value) == 8:
            dia = datetime.strptime(value, "%Y%m%d").date()
            # Un evento de día entero empieza a las 00:00 de su zona.
            return datetime.combine(dia, time.min, tzinfo=zona)
        if value.endswith("Z"):
            return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        return datetime.strptime(value, "%Y%m%dT%H%M%S").replace(tzinfo=zona)
    except ValueError:
        log.debug("fecha ilegible en el calendario: %r", value)
        return None


def parse_events(text: str) -> list[dict]:
    """Saca los VEVENT de un iCalendar. Sin red: es testeable.

    Cada evento es un dict de propiedades; el valor es `(params, valor)`, salvo
    las que pueden repetirse (EXDATE, RDATE, CATEGORIES y ATTENDEE, que sale
    una vez por invitado), que van en una lista de esos pares.
    """
    eventos: list[dict] = []
    actual: dict | None = None
    # Pila de componentes: sirve para ignorar lo que hay dentro de un VEVENT
    # (un VALARM) y los VTIMEZONE que preceden a los eventos.
    pila: list[str] = []

    for line in unfold(text):
        name, params, value = parse_line(line)

        if name == "BEGIN":
            pila.append(value.strip().upper())
            if pila[-1] == "VEVENT":
                actual = {}
            continue

        if name == "END":
            cerrado = pila.pop() if pila else ""
            if cerrado == "VEVENT" and actual is not None:
                eventos.append(actual)
                actual = None
            continue

        # Solo interesa lo que cuelga directamente del VEVENT: dentro de un
        # VALARM hay TRIGGER y DESCRIPTION que no son los del evento.
        if actual is None or pila[-1:] != ["VEVENT"]:
            continue

        if name in ("EXDATE", "RDATE", "CATEGORIES", "ATTENDEE"):
            actual.setdefault(name, []).append((params, value))
        else:
            actual[name] = (params, value)

    return eventos


def value_of(event: dict, name: str, default: str = "") -> str:
    """El valor de una propiedad, ya sin escapes."""
    if (par := event.get(name)) is None:
        return default
    return unescape(par[1]).strip()


def datetime_of(event: dict, name: str, default_tz=timezone.utc):
    if (par := event.get(name)) is None:
        return None
    return parse_dt(par[1], par[0], default_tz)


def is_all_day(event: dict) -> bool:
    par = event.get("DTSTART")
    if par is None:
        return False
    params, value = par
    return params.get("VALUE") == "DATE" or len(value.strip()) == 8


def duration_of(event: dict) -> timedelta:
    """Cuánto dura: por DTEND, por DURATION, o el día entero si no hay ninguno."""
    inicio = datetime_of(event, "DTSTART")
    if inicio and (fin := datetime_of(event, "DTEND")):
        return max(timedelta(0), fin - inicio)
    if (par := event.get("DURATION")) is not None:
        return parse_duration(par[1])
    return timedelta(days=1) if is_all_day(event) else timedelta(hours=1)


_DURACION = re.compile(
    r"^(?P<signo>[+-])?P(?:(?P<semanas>\d+)W)?(?:(?P<dias>\d+)D)?"
    r"(?:T(?:(?P<horas>\d+)H)?(?:(?P<minutos>\d+)M)?(?:(?P<segundos>\d+)S)?)?$")


def parse_duration(value: str) -> timedelta:
    """`PT1H30M`, `P2D`, `-PT15M`… a timedelta. Cero si no se entiende."""
    encaje = _DURACION.match((value or "").strip().upper())
    if not encaje:
        return timedelta(0)
    partes = {clave: int(valor) for clave, valor in encaje.groupdict().items()
              if valor and clave != "signo"}
    delta = timedelta(weeks=partes.get("semanas", 0), days=partes.get("dias", 0),
                      hours=partes.get("horas", 0), minutes=partes.get("minutos", 0),
                      seconds=partes.get("segundos", 0))
    return -delta if encaje.group("signo") == "-" else delta


# -- escritura --------------------------------------------------------------
# Al escribir hay que deshacer las mismas trampas, en el otro sentido. La del
# plegado tiene un detalle que se escapa fácil: el límite son 75 **octetos**,
# no caracteres, y una «ñ» ocupa dos. Partir por caracteres genera ficheros
# que algunos servidores rechazan y otros truncan.
MAX_OCTETOS = 75


def escape(value: str) -> str:
    """El inverso de `unescape`: prepara un texto para meterlo en una línea."""
    return (str(value or "")
            .replace("\\", "\\\\")
            .replace("\n", "\\n").replace("\r", "")
            .replace(",", "\\,").replace(";", "\\;"))


def fold(line: str) -> str:
    """Pliega una línea larga sin partir ningún carácter por la mitad."""
    crudo = line.encode()
    if len(crudo) <= MAX_OCTETOS:
        return line

    trozos, actual = [], ""
    # El primer trozo admite 75 octetos; los siguientes, uno menos, porque
    # empiezan por el espacio que los marca como continuación.
    tope = MAX_OCTETOS
    for caracter in line:
        if len((actual + caracter).encode()) > tope:
            trozos.append(actual)
            actual, tope = caracter, MAX_OCTETOS - 1
        else:
            actual += caracter
    trozos.append(actual)
    return "\r\n ".join(trozos)


def format_dt(fecha: datetime) -> str:
    """Un datetime a la forma UTC de iCalendar: `20260925T100000Z`."""
    return fecha.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def build_event(uid: str, inicio: datetime, fin: datetime, summary: str,
                location: str = "", description: str = "",
                prodid: str = "-//jarvis//asistente//ES") -> str:
    """Un VCALENDAR de un solo evento, listo para mandar por CalDAV.

    Las fechas van en UTC a propósito: así el fichero no depende de que el
    servidor conozca el mismo huso horario que nosotros, ni hay que arrastrar
    un VTIMEZONE completo para que sea válido.
    """
    lineas = [
        "BEGIN:VCALENDAR",
        f"PRODID:{prodid}",
        "VERSION:2.0",
        "CALSCALE:GREGORIAN",
        "BEGIN:VEVENT",
        f"UID:{escape(uid)}",
        f"DTSTAMP:{format_dt(datetime.now(timezone.utc))}",
        f"DTSTART:{format_dt(inicio)}",
        f"DTEND:{format_dt(fin)}",
        f"SUMMARY:{escape(summary)}",
    ]
    if location:
        lineas.append(f"LOCATION:{escape(location)}")
    if description:
        lineas.append(f"DESCRIPTION:{escape(description)}")
    lineas += ["END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(fold(linea) for linea in lineas) + "\r\n"
