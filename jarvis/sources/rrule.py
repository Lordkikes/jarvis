"""Expansión de repeticiones (RRULE) para los casos que aparecen de verdad.

Un calendario sin repeticiones no sirve: la reunión de los lunes está una sola
vez en el fichero, con un DTSTART que suele quedar meses atrás. Hay que
generar las ocurrencias dentro de la ventana que interesa.

Se cubren FREQ DAILY, WEEKLY, MONTHLY y YEARLY con INTERVAL, COUNT, UNTIL,
BYDAY (incluida la forma ordinal `3TU`), BYMONTHDAY y BYMONTH, más EXDATE y
RDATE. Lo que queda fuera —BYSETPOS, BYYEARDAY, BYWEEKNO y las combinaciones
retorcidas de RFC 5545— se detecta y se dice: `expand` devuelve solo la
primera aparición y marca la regla como no expandida, que es preferible a
inventarse unas fechas que no son.
"""
from __future__ import annotations

from datetime import datetime, timedelta

# RFC 5545 numera los días empezando en lunes, igual que datetime.weekday().
DIAS = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}
SOPORTADAS = {"FREQ", "INTERVAL", "COUNT", "UNTIL", "BYDAY", "BYMONTHDAY",
              "BYMONTH", "WKST"}
FRECUENCIAS = {"DAILY", "WEEKLY", "MONTHLY", "YEARLY"}

# Tope de seguridad: una regla sin COUNT ni UNTIL es infinita por definición.
# Holgado a propósito: generar fechas es aritmética pura, y un evento diario
# que arrancó hace diez años necesita 3.650 vueltas solo para llegar a hoy.
MAX_OCURRENCIAS = 20_000


def parse_rrule(value: str) -> dict:
    """`FREQ=WEEKLY;BYDAY=MO,WE` -> {'FREQ': 'WEEKLY', 'BYDAY': ['MO', 'WE']}."""
    regla: dict = {}
    for trozo in (value or "").split(";"):
        clave, _, valor = trozo.partition("=")
        clave = clave.strip().upper()
        if not clave:
            continue
        if clave in ("BYDAY", "BYMONTHDAY", "BYMONTH", "BYSETPOS", "BYYEARDAY",
                     "BYWEEKNO", "BYHOUR", "BYMINUTE"):
            regla[clave] = [v.strip().upper() for v in valor.split(",") if v.strip()]
        else:
            regla[clave] = valor.strip().upper()
    return regla


def is_supported(regla: dict) -> bool:
    """¿Sabemos expandir esta regla sin inventarnos nada?"""
    if regla.get("FREQ") not in FRECUENCIAS or (set(regla) - SOPORTADAS):
        return False
    # Las semanas se cuentan desde el lunes. Con WKST distinto e INTERVAL > 1
    # el resultado cambiaría, así que esa combinación se declara no soportada.
    return not (regla.get("WKST", "MO") != "MO"
                and int(regla.get("INTERVAL", 1) or 1) > 1)


def _dia_del_mes(anyo: int, mes: int, dia: int):
    """El día `dia` del mes, o None si ese mes no lo tiene (31 de febrero)."""
    if mes > 12:
        anyo, mes = anyo + (mes - 1) // 12, (mes - 1) % 12 + 1
    try:
        return datetime(anyo, mes, dia)
    except ValueError:
        return None


def _mes_mas(fecha: datetime, meses: int) -> tuple[int, int]:
    total = (fecha.year * 12 + fecha.month - 1) + meses
    return total // 12, total % 12 + 1


def _dias_del_mes_por_byday(anyo: int, mes: int, byday: list) -> list[int]:
    """Los días que casan con BYDAY: `MO` (todos los lunes) o `3TU` (el tercero)."""
    dias = []
    for token in byday:
        ordinal, nombre = token[:-2], token[-2:]
        if nombre not in DIAS:
            continue
        candidatos = []
        dia = 1
        while (fecha := _dia_del_mes(anyo, mes, dia)) is not None:
            if fecha.weekday() == DIAS[nombre]:
                candidatos.append(dia)
            dia += 1
        if not ordinal:
            dias.extend(candidatos)
            continue
        # `3TU` es el tercer martes; `-1FR`, el último viernes. Un mes puede no
        # tener quinto martes, y entonces ese mes sencillamente no toca.
        indice = int(ordinal)
        if 0 < indice <= len(candidatos):
            dias.append(candidatos[indice - 1])
        elif 0 > indice >= -len(candidatos):
            dias.append(candidatos[indice])
    return sorted(set(dias))


def expand(inicio: datetime, regla: dict, desde: datetime, hasta: datetime,
           exdate: set | None = None, rdate: list | None = None) -> list[datetime]:
    """Ocurrencias que caen en [desde, hasta). La primera es siempre `inicio`.

    Si la regla usa algo que no sabemos expandir, se devuelve solo `inicio`
    (cuando cae en la ventana): mejor quedarse corto que mentir.
    """
    excluidas = exdate or set()
    extra = [f for f in (rdate or []) if desde <= f < hasta]

    def dentro(fecha):
        return desde <= fecha < hasta and fecha not in excluidas

    if not regla:
        return sorted(([inicio] if dentro(inicio) else []) + extra)
    if not is_supported(regla):
        return sorted(([inicio] if dentro(inicio) else []) + extra)

    freq = regla["FREQ"]
    intervalo = max(1, int(regla.get("INTERVAL", 1) or 1))
    tope = int(regla["COUNT"]) if regla.get("COUNT", "").isdigit() else None
    hasta_regla = _hasta(regla, inicio)
    meses = {int(m) for m in regla.get("BYMONTH", []) if m.isdigit()}

    ocurrencias: list[datetime] = []
    generadas = 0
    for fecha in _generar(inicio, freq, intervalo, regla):
        if generadas >= MAX_OCURRENCIAS:
            break
        if hasta_regla and fecha > hasta_regla:
            break
        if fecha >= hasta:
            break
        # BYMONTH criba antes de contar: COUNT se aplica al conjunto final.
        if meses and fecha.month not in meses:
            continue
        generadas += 1
        if tope is not None and generadas > tope:
            break
        if dentro(fecha):
            ocurrencias.append(fecha)

    return sorted(set(ocurrencias + extra))


def _hasta(regla: dict, inicio: datetime):
    valor = regla.get("UNTIL", "")
    if not valor:
        return None
    from .ical import parse_dt  # local: evita un import circular

    fecha = parse_dt(valor, {}, inicio.tzinfo)
    return fecha


def _generar(inicio: datetime, freq: str, intervalo: int, regla: dict):
    """Va soltando fechas candidatas en orden creciente, sin filtrar."""
    if freq == "DAILY":
        fecha = inicio
        while True:
            yield fecha
            fecha += timedelta(days=intervalo)

    elif freq == "WEEKLY":
        dias = sorted({DIAS[d[-2:]] for d in regla.get("BYDAY", [])
                       if d[-2:] in DIAS}) or [inicio.weekday()]
        # El lunes de la semana de inicio: desde ahí se avanza de semana en semana.
        lunes = inicio - timedelta(days=inicio.weekday())
        while True:
            for dia in dias:
                fecha = lunes + timedelta(days=dia)
                if fecha >= inicio:
                    yield fecha
            lunes += timedelta(weeks=intervalo)

    elif freq == "MONTHLY":
        paso = 0
        while True:
            anyo, mes = _mes_mas(inicio, paso * intervalo)
            if byday := regla.get("BYDAY"):
                dias = _dias_del_mes_por_byday(anyo, mes, byday)
            elif bymonthday := regla.get("BYMONTHDAY"):
                dias = sorted({int(d) for d in bymonthday
                               if d.lstrip("-").isdigit() and int(d) > 0})
            else:
                dias = [inicio.day]
            for dia in dias:
                if (fecha := _dia_del_mes(anyo, mes, dia)) is None:
                    continue
                fecha = fecha.replace(tzinfo=inicio.tzinfo)
                fecha = fecha.replace(hour=inicio.hour, minute=inicio.minute,
                                      second=inicio.second)
                if fecha >= inicio:
                    yield fecha
            paso += 1

    else:  # YEARLY
        paso = 0
        while True:
            try:
                fecha = inicio.replace(year=inicio.year + paso * intervalo)
            except ValueError:
                # 29 de febrero en un año que no es bisiesto: ese año no toca.
                paso += 1
                continue
            yield fecha
            paso += 1
