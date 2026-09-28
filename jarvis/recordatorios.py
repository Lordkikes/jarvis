"""Recordatorios: lo que hay que decirle a una hora concreta.

No son temporizadores. Un temporizador es un `sleep` que muere con el
proceso; un recordatorio es «mañana a las nueve, llamar al fontanero», y eso
tiene que seguir ahí aunque Jarvis se reinicie tres veces por medio. Así que
viven en un JSON, como las listas, y un bucle los mira cada poco.

De ahí sale la decisión menos evidente: **qué hacer con los que vencieron
mientras Jarvis estaba apagado**. Soltarlos todos de golpe al arrancar es una
avalancha inútil; tragárselos en silencio es peor, porque para eso no lo
habrías pedido. Se avisa de los recientes diciendo que van tarde, y los muy
viejos se marcan como perdidos y se cuentan cuando preguntes.
"""
from __future__ import annotations

import json
import unicodedata
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

MAX_RECORDATORIOS = 200
# Un recordatorio que venció hace menos de esto se dice, avisando del retraso.
# Más viejo, se marca como perdido: soltar el de anteayer no ayuda a nadie.
RETRASO_MAXIMO = timedelta(hours=24)

REPETICIONES = {
    "diario": timedelta(days=1),
    "semanal": timedelta(weeks=1),
}
# Los meses no duran lo mismo, así que ese se calcula aparte.
MENSUAL = "mensual"


def normaliza(texto: str) -> str:
    descompuesto = unicodedata.normalize("NFKD", (texto or "").strip().lower())
    return " ".join("".join(c for c in descompuesto
                            if not unicodedata.combining(c)).split())


def siguiente(cuando: datetime, repeticion: str) -> datetime | None:
    """La próxima vez que toca, o None si no se repite."""
    repeticion = normaliza(repeticion)
    if delta := REPETICIONES.get(repeticion):
        return cuando + delta
    if repeticion != MENSUAL:
        return None

    # Mismo día del mes que viene; si ese mes no lo tiene —el 31 en febrero—,
    # el último que sí tenga. Se cuenta hacia abajo, así que el primero que
    # encaja es el bueno, y para cualquier día del 1 al 28 es el propio.
    anyo, mes = (cuando.year + 1, 1) if cuando.month == 12 else (cuando.year,
                                                                 cuando.month + 1)
    for dia in range(cuando.day, 0, -1):
        try:
            return cuando.replace(year=anyo, month=mes, day=dia)
        except ValueError:
            continue
    return None


class Recordatorios:
    """Los recordatorios de la persona, en un JSON que se relee cada vez."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

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
    def pendientes(self) -> list[dict]:
        """Los que quedan por avisar, del más próximo al más lejano."""
        return sorted((r for r in self._lee() if not r.get("hecho")),
                      key=lambda r: r.get("cuando", ""))

    def perdidos(self) -> list[dict]:
        return [r for r in self._lee() if r.get("perdido")]

    def busca(self, texto: str) -> list[dict]:
        objetivo = normaliza(texto)
        if not objetivo:
            return []
        pendientes = self.pendientes()
        exactos = [r for r in pendientes if normaliza(r.get("texto", "")) == objetivo]
        return exactos or [r for r in pendientes
                           if objetivo in normaliza(r.get("texto", ""))]

    # -- modificación ------------------------------------------------------
    def pon(self, texto: str, cuando: datetime, repetir: str = "") -> dict:
        datos = self._lee()
        if len([r for r in datos if not r.get("hecho")]) >= MAX_RECORDATORIOS:
            return {"ok": False, "motivo": f"ya tienes {MAX_RECORDATORIOS}"}

        nuevo = {
            "id": uuid.uuid4().hex[:8],
            "texto": (texto or "").strip(),
            "cuando": cuando.astimezone(timezone.utc).isoformat(timespec="seconds"),
            "repetir": normaliza(repetir) if normaliza(repetir) in
                       (*REPETICIONES, MENSUAL) else "",
            "hecho": False,
        }
        datos.append(nuevo)
        self._escribe(datos)
        return {"ok": True, "recordatorio": nuevo}

    def borra(self, identificador: str) -> bool:
        datos = self._lee()
        quedan = [r for r in datos if r.get("id") != identificador]
        if len(quedan) == len(datos):
            return False
        self._escribe(quedan)
        return True

    def vencidos(self, ahora: datetime | None = None) -> list[dict]:
        """Los que tocan ya, marcándolos para no repetirlos.

        Devuelve cada uno con `retraso` en segundos y `perdido` si venció hace
        demasiado. Es la única operación que escribe al vencer, y lo hace
        antes de avisar: más vale callar un recordatorio que soltarlo en bucle
        si algo falla al decirlo.
        """
        ahora = ahora or datetime.now(timezone.utc)
        datos = self._lee()
        disparados, cambiado = [], False

        for recordatorio in datos:
            if recordatorio.get("hecho"):
                continue
            try:
                cuando = datetime.fromisoformat(recordatorio.get("cuando", ""))
            except (TypeError, ValueError):
                continue
            if cuando > ahora:
                continue

            retraso = (ahora - cuando).total_seconds()
            perdido = retraso > RETRASO_MAXIMO.total_seconds()
            cambiado = True

            if (proxima := siguiente(cuando, recordatorio.get("repetir", ""))):
                # Los que se repiten no se cierran: se mueven a la siguiente.
                # Si estuvo apagado varios días, se adelanta hasta el futuro
                # en vez de encadenar todas las que se perdieron.
                while proxima <= ahora:
                    proxima = siguiente(proxima, recordatorio["repetir"])
                recordatorio["cuando"] = proxima.isoformat(timespec="seconds")
            else:
                recordatorio["hecho"] = True
                recordatorio["perdido"] = perdido

            if not perdido:
                disparados.append({**recordatorio, "retraso": retraso})

        if cambiado:
            self._escribe(datos)
        return disparados
