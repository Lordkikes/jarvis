"""Temporizadores: el arroz, la colada, los diez minutos de horno.

Un recordatorio es absoluto —«el jueves a las nueve»— y persistente; un
temporizador es relativo —«dentro de diez minutos»— y muere con el proceso,
a propósito: soltar «ya está el arroz» media hora tarde porque Jarvis se
reinició es peor que callarse, y para lo que tiene que sobrevivir al reinicio
ya están los recordatorios.

De ahí sale la decisión menos evidente: **cuentan con reloj monótono, no con
la hora del sistema**. Diez minutos son diez minutos aunque el NTP corrija el
reloj a mitad de la cuenta o entre el cambio de hora. Los recordatorios
necesitan justo lo contrario, la hora de la pared, porque las nueve de la
mañana son las nueve pase lo que pase con el reloj.

Lo demás es lo que hace falta para manejarlos hablando: varios a la vez, con
nombre para poder referirse a ellos, y pausa, prórroga y cancelación, que es
lo que de verdad se pide cuando llaman al timbre a mitad de la pasta.
"""
from __future__ import annotations

import time
import uuid

from .listas import normaliza

# Diez a la vez son más de los que caben en una cocina. El tope existe para
# que nada —ni un modelo perdido en un bucle— pueda llenar la casa de pitidos.
MAX_TEMPORIZADORES = 10
MAX_SEGUNDOS = 24 * 3600


def en_palabras(segundos: float) -> str:
    """El tiempo dicho como lo diría una persona, no como lo mide un reloj.

    En cuanto pasa de la hora se redondea al minuto: a quien le quedan dos
    horas no le importan los segundos, y decírselos solo alarga la frase.
    """
    total = max(0, round(segundos))
    if total == 0 and segundos > 0:
        total = 1  # Aún corre: «quedan 0 segundos» sería mentira.

    if total < 60:
        return "1 segundo" if total == 1 else f"{total} segundos"

    if total < 3600:
        minutos, resto = divmod(total, 60)
        cabeza = "1 minuto" if minutos == 1 else f"{minutos} minutos"
        if resto == 0:
            return cabeza
        cola = "1 segundo" if resto == 1 else f"{resto} segundos"
        return f"{cabeza} y {cola}"

    horas, resto = divmod(round(total / 60), 60)
    cabeza = "1 hora" if horas == 1 else f"{horas} horas"
    if resto == 0:
        return cabeza
    cola = "1 minuto" if resto == 1 else f"{resto} minutos"
    return f"{cabeza} y {cola}"


def nombre(temporizador: dict) -> str:
    """Cómo llamarlo en voz alta: por su etiqueta, o por lo que duraba."""
    if etiqueta := (temporizador.get("etiqueta") or "").strip():
        return etiqueta
    return f"el de {en_palabras(temporizador.get('total', 0))}"


def al(dicho: str) -> str:
    """«al arroz», no «a el arroz»: la contracción que obliga el castellano."""
    if dicho[:3].lower() == "el ":
        return "al " + dicho[3:]
    return f"a {dicho}"


def describe(temporizador: dict) -> str:
    """«el arroz, 3 minutos» o «el arroz, en pausa con 3 minutos»."""
    queda = en_palabras(temporizador.get("restante", 0))
    if temporizador.get("pausado"):
        return f"{nombre(temporizador)}, en pausa con {queda}"
    return f"{nombre(temporizador)}, {queda}"


class Temporizadores:
    """Las cuentas atrás vivas. En memoria, que es donde les toca estar.

    El reloj se inyecta para que las pruebas puedan saltar media hora sin
    dormir medio segundo siquiera.
    """

    def __init__(self, reloj=time.monotonic):
        self._reloj = reloj
        self._vivos: dict[str, dict] = {}

    # -- consulta ----------------------------------------------------------
    def _vista(self, temporizador: dict) -> dict:
        """Una copia con el tiempo que queda ya calculado."""
        pausado = temporizador["vence"] is None
        restante = (temporizador["restante"] if pausado
                    else temporizador["vence"] - self._reloj())
        return {**temporizador, "restante": max(0.0, restante), "pausado": pausado}

    def lista(self) -> list[dict]:
        """Todos, primero los que corren y de más cerca a más lejos."""
        return sorted((self._vista(t) for t in self._vivos.values()),
                      key=lambda t: (t["pausado"], t["restante"]))

    def busca(self, texto: str) -> list[dict]:
        """Resuelve «el del arroz». Exacto gana a parcial, como en las listas.

        Devuelve varios cuando de verdad hay varios que encajan: quien llama
        decide si eso es suficiente o hay que preguntar cuál.
        """
        objetivo = normaliza(texto)
        if not objetivo:
            return []
        activos = self.lista()
        if exactos := [t for t in activos if normaliza(nombre(t)) == objetivo]:
            return exactos
        return [t for t in activos if objetivo in normaliza(nombre(t))]

    def proximo(self) -> float | None:
        """Segundos hasta el primero que vence, o None si ninguno corre."""
        corriendo = [t["restante"] for t in self.lista() if not t["pausado"]]
        return min(corriendo) if corriendo else None

    # -- modificación ------------------------------------------------------
    def pon(self, segundos: float, etiqueta: str = "",
            accion: str = "") -> dict:
        """`accion` lo convierte en un temporizador que hace algo al vencer.

        Es lo que separa «avísame en diez minutos» de «apaga la música en
        media hora»: el segundo no tiene que decir nada, solo hacerlo.
        """
        if segundos > MAX_SEGUNDOS:
            return {"ok": False, "motivo": "no paso de un día"}
        if len(self._vivos) >= MAX_TEMPORIZADORES:
            return {"ok": False,
                    "motivo": f"ya tienes {MAX_TEMPORIZADORES} en marcha"}

        segundos = max(0.0, float(segundos))
        nuevo = {
            "id": uuid.uuid4().hex[:8],
            "etiqueta": (etiqueta or "").strip(),
            "total": segundos,
            "accion": (accion or "").strip(),
            "vence": self._reloj() + segundos,
            "restante": segundos,
        }
        self._vivos[nuevo["id"]] = nuevo
        return {"ok": True, "temporizador": self._vista(nuevo)}

    def cancela(self, identificador: str) -> dict | None:
        if (temporizador := self._vivos.pop(identificador, None)) is None:
            return None
        return self._vista(temporizador)

    def cancela_todos(self) -> list[dict]:
        cancelados = self.lista()
        self._vivos.clear()
        return cancelados

    def pausa(self, identificador: str) -> dict | None:
        if (temporizador := self._vivos.get(identificador)) is None:
            return None
        if temporizador["vence"] is not None:
            # Se congela lo que quedaba; al reanudar se cuenta desde ahí, no
            # desde el final original, que es lo que uno espera al pausar.
            temporizador["restante"] = max(0.0,
                                           temporizador["vence"] - self._reloj())
            temporizador["vence"] = None
        return self._vista(temporizador)

    def reanuda(self, identificador: str) -> dict | None:
        if (temporizador := self._vivos.get(identificador)) is None:
            return None
        if temporizador["vence"] is None:
            temporizador["vence"] = self._reloj() + temporizador["restante"]
        return self._vista(temporizador)

    def anade(self, identificador: str, segundos: float) -> dict:
        """Alarga o acorta uno en marcha. Negativo quita tiempo."""
        if (temporizador := self._vivos.get(identificador)) is None:
            return {"ok": False, "motivo": "ese ya no está"}

        actual = self._vista(temporizador)["restante"]
        nuevo_restante = actual + float(segundos)
        if nuevo_restante <= 0:
            # Quitarle más de lo que queda no es acortarlo: es terminarlo
            # antes de tiempo, y eso se pide cancelando, no restando.
            return {"ok": False,
                    "motivo": f"solo le quedan {en_palabras(actual)}"}
        if nuevo_restante > MAX_SEGUNDOS:
            return {"ok": False, "motivo": "no paso de un día"}

        temporizador["total"] += float(segundos)
        if temporizador["vence"] is None:
            temporizador["restante"] = nuevo_restante
        else:
            temporizador["vence"] = self._reloj() + nuevo_restante
        return {"ok": True, "temporizador": self._vista(temporizador)}

    def vencidos(self) -> list[dict]:
        """Los que tocan ya, quitándolos de en medio antes de devolverlos.

        Se quitan antes de avisar, como los recordatorios: más vale callar
        uno si algo falla al decirlo que dejarlo sonando en bucle.
        """
        ahora = self._reloj()
        fuera = sorted((t for t in self._vivos.values()
                        if t["vence"] is not None and t["vence"] <= ahora),
                       key=lambda t: t["vence"])
        for temporizador in fuera:
            del self._vivos[temporizador["id"]]
        return [{**t, "restante": 0.0, "pausado": False} for t in fuera]
