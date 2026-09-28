"""Alarmas: las siete de la mañana, de lunes a viernes, hasta que la pares.

Ya había recordatorios —«el jueves a las nueve, llamar al fontanero»— y
temporizadores —«diez minutos para el arroz»—. Una alarma no es ninguno de
los dos, y lo que la distingue no es la hora sino **la insistencia**: un
recordatorio dice su frase una vez y se calla, y eso es exactamente lo que
no quieres a las siete de la mañana. Una alarma vuelve cada medio minuto
hasta que alguien diga que ya.

De ahí salen las tres diferencias con un recordatorio:

- **Se repite por días de la semana**, que es como se piensan: «de lunes a
  viernes», no «cada 24 horas».
- **Se puede posponer**, que es la mitad de para qué existe una alarma.
- **Lo que se perdió estando apagado no se dispara.** Un recordatorio de hace
  tres horas todavía sirve; una alarma de hace tres horas no despierta a
  nadie, solo asusta. Pasados cinco minutos se da por perdida y se dice al
  preguntar.

Cuentan con la hora de la pared, al revés que los temporizadores: las siete
son las siete aunque el reloj se haya corregido por el camino.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, time, timedelta
from pathlib import Path

from .listas import normaliza

MAX_ALARMAS = 20
# Más vieja que esto, no suena: nadie se levanta con la alarma de hace una hora.
RETRASO_MAXIMO = timedelta(minutes=5)
# Cada cuánto vuelve a sonar, y cuántas veces antes de rendirse (unos 5 minutos).
CADA = timedelta(seconds=30)
REPETICIONES = 10
POSPONER = timedelta(minutes=9)  # los nueve minutos de los radiodespertadores

DIAS = ("lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo")
DIAS_DICHOS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado",
               "domingo")
LABORABLES = (0, 1, 2, 3, 4)
FIN_DE_SEMANA = (5, 6)
TODOS = tuple(range(7))

GRUPOS = {
    "diario": TODOS, "todos los dias": TODOS, "cada dia": TODOS,
    "laborables": LABORABLES, "entre semana": LABORABLES,
    "dias laborables": LABORABLES, "de lunes a viernes": LABORABLES,
    "fin de semana": FIN_DE_SEMANA, "fines de semana": FIN_DE_SEMANA,
    "findes": FIN_DE_SEMANA,
}


def parse_hora(valor) -> time | None:
    """«07:00», «7:00» y «7» valen; «las siete» no, que para eso está el modelo."""
    texto = str(valor or "").strip().replace(".", ":")
    if not texto:
        return None
    partes = texto.split(":")
    if len(partes) > 2:
        return None
    try:
        hora = int(partes[0])
        minuto = int(partes[1]) if len(partes) == 2 else 0
    except ValueError:
        return None
    if not (0 <= hora <= 23 and 0 <= minuto <= 59):
        return None
    return time(hora, minuto)


def parse_dias(valor) -> tuple[int, ...]:
    """Días de la semana como números. Vacío significa «una sola vez».

    Acepta la lista que pide el esquema, un grupo («laborables») o una cadena
    con comas, porque el modelo manda las tres cosas.
    """
    if valor is None:
        return ()
    crudo = valor.split(",") if isinstance(valor, str) else list(valor)
    dias: list[int] = []
    for trozo in crudo:
        if not isinstance(trozo, str):
            continue
        nombre = normaliza(trozo)
        if grupo := GRUPOS.get(nombre):
            dias.extend(grupo)
        elif nombre in DIAS:
            dias.append(DIAS.index(nombre))
        elif nombre.endswith("s") and nombre[:-1] in DIAS:
            dias.append(DIAS.index(nombre[:-1]))  # «los lunes»
    return tuple(sorted(set(dias)))


def dias_en_palabras(dias) -> str:
    """Cómo se dicen: «de lunes a viernes», no «lunes, martes, miércoles…»."""
    dias = tuple(sorted(dias))
    if not dias:
        return "una vez"
    if dias == TODOS:
        return "todos los días"
    if dias == LABORABLES:
        return "de lunes a viernes"
    if dias == FIN_DE_SEMANA:
        return "los fines de semana"
    nombres = [DIAS_DICHOS[d] for d in dias]
    if len(nombres) == 1:
        return f"los {nombres[0]}"
    return "los " + ", ".join(nombres[:-1]) + " y " + nombres[-1]


def hora_en_palabras(hora: time) -> str:
    return f"{hora.hour:02d}:{hora.minute:02d}"


def siguiente_ocurrencia(hora: time, dias, desde: datetime) -> datetime:
    """La próxima vez que toca esa hora, contando desde `desde`.

    Se calcula desde el reloj y no sumándole un día a la anterior: así una
    alarma pospuesta, o una que lleve una semana sin sonar, vuelve a su sitio
    sola en vez de arrastrar el desfase.
    """
    dias = tuple(dias)
    for salto in range(8):
        fecha = (desde + timedelta(days=salto)).date()
        candidata = datetime.combine(fecha, hora).astimezone()
        if candidata <= desde:
            continue
        if not dias or candidata.weekday() in dias:
            return candidata
    # Inalcanzable con siete días de margen, pero mejor eso que devolver None.
    return datetime.combine((desde + timedelta(days=1)).date(), hora).astimezone()


class Alarmas:
    """Las alarmas de la persona, en un JSON que se relee cada vez.

    Persisten porque una alarma para mañana a las siete tiene que seguir ahí
    aunque el proceso se reinicie a medianoche. Incluso el «está sonando» se
    guarda: reiniciar a las siete y cinco no la calla.
    """

    def __init__(self, path: str | Path, posponer: timedelta = POSPONER):
        self.path = Path(path)
        self.posponer_por_defecto = posponer

    # -- persistencia ------------------------------------------------------
    def _lee(self) -> list:
        if not self.path.exists():
            return []
        try:
            datos = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []
        return datos if isinstance(datos, list) else []

    def _escribe(self, datos: list) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(datos, ensure_ascii=False, indent=2),
                             encoding="utf-8")

    # -- consulta ----------------------------------------------------------
    def lista(self) -> list[dict]:
        """Todas, de la más próxima a la más lejana; las apagadas al final."""
        return sorted(self._lee(),
                      key=lambda a: (not a.get("activa", True),
                                     a.get("proxima", "")))

    def sonando(self) -> list[dict]:
        return [a for a in self._lee() if a.get("sonando")]

    def busca(self, texto: str) -> list[dict]:
        """Por etiqueta, por hora («la de las 7:00») o por día."""
        objetivo = normaliza(texto)
        if not objetivo:
            return []
        alarmas = self.lista()

        # «la de las 7:00» trae la hora al final; se prueba antes que nada.
        if dicha := parse_hora(objetivo.split()[-1]):
            if coinciden := [a for a in alarmas
                             if a.get("hora") == hora_en_palabras(dicha)]:
                return coinciden

        if exactas := [a for a in alarmas
                       if normaliza(a.get("etiqueta", "")) == objetivo]:
            return exactas
        return [a for a in alarmas
                if (a.get("etiqueta")
                    and objetivo in normaliza(a["etiqueta"]))
                or objetivo in normaliza(dias_en_palabras(a.get("dias", [])))]

    # -- modificación ------------------------------------------------------
    def pon(self, hora: time, dias=(), etiqueta: str = "",
            ahora: datetime | None = None) -> dict:
        datos = self._lee()
        if len(datos) >= MAX_ALARMAS:
            return {"ok": False, "motivo": f"ya tienes {MAX_ALARMAS}"}

        ahora = ahora or datetime.now().astimezone()
        nueva = {
            "id": uuid.uuid4().hex[:8],
            "hora": hora_en_palabras(hora),
            "dias": list(dias),
            "etiqueta": (etiqueta or "").strip(),
            "activa": True,
            "proxima": siguiente_ocurrencia(hora, dias, ahora).isoformat(
                timespec="seconds"),
            "sonando": None,
        }
        datos.append(nueva)
        self._escribe(datos)
        return {"ok": True, "alarma": nueva}

    def quita(self, identificador: str) -> bool:
        datos = self._lee()
        quedan = [a for a in datos if a.get("id") != identificador]
        if len(quedan) == len(datos):
            return False
        self._escribe(quedan)
        return True

    def activa(self, identificador: str, encendida: bool,
               ahora: datetime | None = None) -> dict | None:
        datos = self._lee()
        for alarma in datos:
            if alarma.get("id") != identificador:
                continue
            alarma["activa"] = encendida
            alarma["sonando"] = None
            if encendida:
                # Al encenderla, su próxima vez se recalcula desde ahora: si
                # llevaba apagada una semana, no arrastra la de entonces.
                alarma["proxima"] = self._recoloca(alarma, ahora)
            self._escribe(datos)
            return alarma
        return None

    def para(self, identificador: str | None = None,
             ahora: datetime | None = None) -> list[dict]:
        """Calla la que suena —o todas— y la deja lista para la próxima vez."""
        datos = self._lee()
        paradas = []
        for alarma in datos:
            if not alarma.get("sonando"):
                continue
            if identificador and alarma.get("id") != identificador:
                continue
            alarma["sonando"] = None
            alarma["proxima"] = self._recoloca(alarma, ahora)
            paradas.append(alarma)
        if paradas:
            self._escribe(datos)
        return paradas

    def pospon(self, identificador: str | None = None,
               minutos: int | None = None,
               ahora: datetime | None = None) -> list[dict]:
        """Calla la que suena y la trae de vuelta dentro de un rato."""
        ahora = ahora or datetime.now().astimezone()
        espera = (timedelta(minutes=minutos) if minutos
                  else self.posponer_por_defecto)
        datos = self._lee()
        pospuestas = []
        for alarma in datos:
            if not alarma.get("sonando"):
                continue
            if identificador and alarma.get("id") != identificador:
                continue
            alarma["sonando"] = None
            # Se pisa `proxima` sin miedo: al pararla se recalcula desde la
            # hora y los días, así que la de mañana no se mueve de las siete.
            alarma["proxima"] = (ahora + espera).isoformat(timespec="seconds")
            alarma["pospuesta"] = True
            pospuestas.append({**alarma, "espera": espera})
        if pospuestas:
            self._escribe(datos)
        return pospuestas

    def _recoloca(self, alarma: dict, ahora: datetime | None) -> str:
        ahora = ahora or datetime.now().astimezone()
        hora = parse_hora(alarma.get("hora")) or time(0, 0)
        alarma.pop("pospuesta", None)
        return siguiente_ocurrencia(hora, alarma.get("dias", []),
                                    ahora).isoformat(timespec="seconds")

    # -- el tic ------------------------------------------------------------
    def revisa(self, ahora: datetime | None = None) -> list[dict]:
        """Qué hay que decir ahora mismo. Lo llama el bucle de la tubería.

        Devuelve un aviso por alarma con `vez` (la primera es la 1) y, si
        llega tarde, `retraso` en segundos. Escribe antes de devolver, como
        los recordatorios: más vale callar una vuelta que quedarse gritando
        en bucle si algo falla al decirla.
        """
        ahora = ahora or datetime.now().astimezone()
        datos = self._lee()
        avisos, cambiado = [], False

        for alarma in datos:
            if not alarma.get("activa", True):
                continue

            if sonando := alarma.get("sonando"):
                cuando = _fecha(sonando.get("siguiente"))
                if cuando is None or cuando > ahora:
                    continue
                vez = int(sonando.get("vez", 1)) + 1
                cambiado = True
                if (ahora - cuando) > RETRASO_MAXIMO:
                    # Estaba sonando cuando se apagó el proceso y ha vuelto
                    # horas después. Retomarla ahora es el mismo susto inútil
                    # que disparar la que se perdió.
                    alarma["sonando"] = None
                    alarma["perdida"] = cuando.isoformat(timespec="minutes")
                    alarma["proxima"] = self._recoloca(alarma, ahora)
                    continue
                if vez > REPETICIONES:
                    # Se ha dado por vencida: nadie la para, nadie la oye.
                    alarma["sonando"] = None
                    alarma["proxima"] = self._recoloca(alarma, ahora)
                    continue
                alarma["sonando"] = {
                    "vez": vez,
                    "siguiente": (ahora + CADA).isoformat(timespec="seconds"),
                }
                avisos.append({**alarma, "vez": vez, "retraso": 0.0})
                continue

            proxima = _fecha(alarma.get("proxima"))
            if proxima is None or proxima > ahora:
                continue

            cambiado = True
            retraso = (ahora - proxima).total_seconds()
            if retraso > RETRASO_MAXIMO.total_seconds():
                # Estaba apagado. Sonar ahora no despierta a nadie.
                alarma["perdida"] = proxima.isoformat(timespec="minutes")
                alarma["proxima"] = self._recoloca(alarma, ahora)
                continue

            alarma.pop("perdida", None)
            alarma["sonando"] = {
                "vez": 1,
                "siguiente": (ahora + CADA).isoformat(timespec="seconds"),
            }
            avisos.append({**alarma, "vez": 1, "retraso": retraso})

        if cambiado:
            self._escribe(datos)
        return avisos


def _fecha(valor) -> datetime | None:
    try:
        fecha = datetime.fromisoformat((valor or "").strip())
    except (TypeError, ValueError):
        return None
    return fecha.astimezone() if fecha.tzinfo is None else fecha
