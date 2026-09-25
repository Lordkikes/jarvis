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


# -- edición ----------------------------------------------------------------
# Mover o cancelar obliga a tocar un iCalendar que ya existe. Se hace a nivel
# de línea, y no reconstruyendo el fichero desde lo que parseamos, por una
# razón concreta: el recurso puede traer VALARM (los recordatorios de la
# persona) y VTIMEZONE (sin los cuales un TZID deja de resolverse). Rehacerlo
# los perdería en silencio. Editando líneas, todo lo que no se toca sobrevive
# byte a byte.

def _componentes(lineas: list[str]) -> list[tuple[int, int, str]]:
    """Los VEVENT del fichero como (primera línea, última línea, nombre)."""
    bloques, pila = [], []
    for indice, linea in enumerate(lineas):
        nombre, _, valor = parse_line(linea)
        if nombre == "BEGIN":
            pila.append((valor.strip().upper(), indice))
        elif nombre == "END" and pila:
            componente, inicio = pila.pop()
            bloques.append((inicio, indice, componente))
    return bloques


def find_vevent(lineas: list[str], uid: str, recurrence_id: datetime | None = None):
    """Localiza el VEVENT de ese UID: el maestro, o una excepción concreta.

    Devuelve (inicio, fin) con los índices de BEGIN y END, o None.
    """
    for inicio, fin, componente in _componentes(lineas):
        if componente != "VEVENT":
            continue
        cuerpo = lineas[inicio:fin + 1]
        propiedades = {}
        for linea in cuerpo:
            nombre, params, valor = parse_line(linea)
            propiedades.setdefault(nombre, (params, valor))
        if unescape(propiedades.get("UID", ({}, ""))[1]).strip() != uid:
            continue

        suya = propiedades.get("RECURRENCE-ID")
        cuando = parse_dt(suya[1], suya[0]) if suya else None
        if recurrence_id is None and cuando is None:
            return inicio, fin
        if recurrence_id is not None and cuando == recurrence_id:
            return inicio, fin
    return None


def _propias(cuerpo: list[str]) -> list[bool]:
    """Qué líneas del bloque son del propio VEVENT y cuáles de algo anidado.

    Importa porque un VALARM dentro lleva su DESCRIPTION y puede llevar su
    DURATION: tocarlas al editar el evento le rompería el recordatorio a la
    persona sin que se note hasta que no suene.
    """
    marcas, hondura = [], 0
    for linea in cuerpo:
        nombre, _, _ = parse_line(linea)
        if nombre == "BEGIN":
            hondura += 1
            marcas.append(hondura == 1)      # el BEGIN:VEVENT sí es suyo
        elif nombre == "END":
            marcas.append(hondura == 1)
            hondura -= 1
        else:
            marcas.append(hondura == 1)
    return marcas


def _sin_propiedad(cuerpo: list[str], *nombres: str) -> list[str]:
    """Quita esas propiedades del evento, sin entrar en lo que tenga dentro."""
    propias = _propias(cuerpo)
    return [linea for linea, suya in zip(cuerpo, propias)
            if not (suya and parse_line(linea)[0] in nombres)]


def _sequence(cuerpo: list[str]) -> int:
    """El número de revisión del evento, que hay que subir al reprogramar."""
    for linea, suya in zip(cuerpo, _propias(cuerpo)):
        nombre, _, valor = parse_line(linea)
        if suya and nombre == "SEQUENCE" and valor.strip().isdigit():
            return int(valor.strip())
    return 0


def reschedule(ics: str, uid: str, inicio: datetime, fin: datetime,
               recurrence_id: datetime | None = None) -> str | None:
    """Cambia la fecha de un evento. None si ese UID no está en el fichero.

    Sube SEQUENCE y refresca DTSTAMP y LAST-MODIFIED, que es lo que mira el
    resto de clientes para saber que la cita se ha reprogramado.
    """
    lineas = unfold(ics)
    if (sitio := find_vevent(lineas, uid, recurrence_id)) is None:
        return None

    desde, hasta = sitio
    cuerpo = lineas[desde:hasta + 1]
    # DURATION y DTEND son excluyentes: al fijar DTEND hay que quitar la otra.
    cuerpo = _sin_propiedad(cuerpo, "DTSTART", "DTEND", "DURATION", "DTSTAMP",
                            "LAST-MODIFIED", "SEQUENCE")
    nuevas = [
        f"DTSTART:{format_dt(inicio)}",
        f"DTEND:{format_dt(fin)}",
        f"DTSTAMP:{format_dt(datetime.now(timezone.utc))}",
        f"LAST-MODIFIED:{format_dt(datetime.now(timezone.utc))}",
        f"SEQUENCE:{_sequence(lineas[desde:hasta + 1]) + 1}",
    ]
    cuerpo = cuerpo[:1] + nuevas + cuerpo[1:]      # justo tras BEGIN:VEVENT
    return _rehacer(lineas, desde, hasta, cuerpo)


def add_exdate(ics: str, uid: str, cuando: datetime) -> str | None:
    """Excluye una ocurrencia de una serie, que es como se cancela solo una."""
    lineas = unfold(ics)
    if (sitio := find_vevent(lineas, uid)) is None:
        return None

    desde, hasta = sitio
    cuerpo = lineas[desde:hasta + 1]
    ya = _sin_propiedad(cuerpo, "DTSTAMP", "LAST-MODIFIED", "SEQUENCE")
    nuevas = [
        f"EXDATE:{format_dt(cuando)}",
        f"DTSTAMP:{format_dt(datetime.now(timezone.utc))}",
        f"LAST-MODIFIED:{format_dt(datetime.now(timezone.utc))}",
        f"SEQUENCE:{_sequence(cuerpo) + 1}",
    ]
    return _rehacer(lineas, desde, hasta, ya[:1] + nuevas + ya[1:])


def add_override(ics: str, uid: str, original: datetime, inicio: datetime,
                 fin: datetime) -> str | None:
    """Añade una excepción: esa ocurrencia pasa a otra hora, el resto sigue.

    Es la manera que define RFC 5545 de mover un solo día de una serie: un
    VEVENT aparte, con el mismo UID y un RECURRENCE-ID que apunta a la
    ocurrencia original.
    """
    lineas = unfold(ics)
    if (sitio := find_vevent(lineas, uid)) is None:
        return None

    # Si esa ocurrencia ya tenía excepción, se reprograma en vez de duplicarla.
    if find_vevent(lineas, uid, original) is not None:
        return reschedule(ics, uid, inicio, fin, recurrence_id=original)

    desde, hasta = sitio
    maestro = lineas[desde:hasta + 1]
    heredadas = [linea for linea, suya in zip(maestro, _propias(maestro))
                 if suya and parse_line(linea)[0] in (
                     "SUMMARY", "LOCATION", "DESCRIPTION", "ORGANIZER",
                     "ATTENDEE", "CLASS")]
    excepcion = [
        "BEGIN:VEVENT",
        f"UID:{escape(uid)}",
        f"RECURRENCE-ID:{format_dt(original)}",
        f"DTSTAMP:{format_dt(datetime.now(timezone.utc))}",
        f"DTSTART:{format_dt(inicio)}",
        f"DTEND:{format_dt(fin)}",
        "SEQUENCE:1",
        *heredadas,
        "END:VEVENT",
    ]
    # Va justo antes de cerrar el calendario.
    corte = len(lineas) - 1
    for indice, linea in enumerate(lineas):
        if parse_line(linea)[0] == "END" and parse_line(linea)[2].strip().upper() == "VCALENDAR":
            corte = indice
            break
    nuevas = lineas[:corte] + excepcion + lineas[corte:]
    return "\r\n".join(fold(linea) for linea in nuevas) + "\r\n"


def remove_vevent(ics: str, uid: str, recurrence_id: datetime) -> str | None:
    """Quita una excepción concreta, dejando el maestro y el resto en su sitio."""
    lineas = unfold(ics)
    if (sitio := find_vevent(lineas, uid, recurrence_id)) is None:
        return None
    desde, hasta = sitio
    return _rehacer(lineas, desde, hasta, [])


def _rehacer(lineas: list[str], desde: int, hasta: int, cuerpo: list[str]) -> str:
    nuevas = lineas[:desde] + cuerpo + lineas[hasta + 1:]
    return "\r\n".join(fold(linea) for linea in nuevas) + "\r\n"
