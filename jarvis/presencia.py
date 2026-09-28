"""Presencia: si hay alguien en casa, para disparar el salir y el llegar.

Una rutina de mañana sabe cuándo toca porque son las siete. Una de salir de
casa no tiene hora: pasa cuando pasa. Lo que la dispara es que la casa se
quede vacía, y eso hay que preguntárselo a alguien.

**Se pregunta por entidades nombradas, no por un dominio entero.** El resto
de Home Assistant se autoriza por dominios en `config.yaml`, pero abrir el
dominio `person` de golpe es decirle a Jarvis dónde está todo el mundo, y
además no distingue entre quien vive aquí y el móvil del cuñado que se dejó
conectado. Así que se listan las entidades a mano:

```yaml
tools:
  presencia:
    entidades: [person.yelko]
```

Con varias, la casa está vacía solo cuando lo están todas.

Lo menos evidente: **al arrancar no se dispara nada.** El primer vistazo solo
apunta cómo está la casa. Si no, reiniciar mientras estás fuera dispararía la
rutina de salir, y volver a casa con las luces apagándose solas tiene poca
gracia.
"""
from __future__ import annotations

import logging

from .domotica import SinConexion

log = logging.getLogger("jarvis.presencia")

EN_CASA = "casa"
FUERA = "fuera"
# Lo que Home Assistant dice cuando alguien está en casa. Todo lo demás —el
# nombre de otra zona, «not_home»— cuenta como fuera.
ESTADOS_EN_CASA = ("home", "casa")
# Y lo que dice cuando no lo sabe: eso no es estar fuera, es no saberlo.
SIN_SABER = ("unknown", "unavailable", "none", "")


class Presencia:
    """Quién está en casa, según las entidades que hayas nombrado tú."""

    def __init__(self, casa, entidades=()):
        self.casa = casa
        self.entidades = tuple(e for e in (entidades or ()) if e)
        self._ultimo: str = ""

    @property
    def disponible(self) -> bool:
        return bool(self.entidades and self.casa.disponible)

    def estado(self) -> str:
        """`casa`, `fuera`, o vacío si no se ha podido saber.

        Basta con que uno esté en casa para que la casa no esté vacía; y si de
        ninguno se sabe nada, no se inventa: se devuelve vacío y nadie se
        entera de nada, que es mejor que apagarlo todo por un sensor caído.
        """
        if not self.disponible:
            return ""

        alguno = False
        for entity_id in self.entidades:
            try:
                entidad = self.casa.estado(entity_id)
            except SinConexion as exc:
                log.debug("no se puede mirar %s (%s)", entity_id, exc)
                continue
            if entidad is None:
                continue
            valor = (entidad.get("state") or "").strip().lower()
            if valor in SIN_SABER:
                continue
            alguno = True
            if valor in ESTADOS_EN_CASA:
                return EN_CASA
        return FUERA if alguno else ""

    def cambio(self) -> str:
        """`salir`, `llegar`, o vacío si no ha cambiado nada.

        El primer vistazo nunca devuelve nada: solo apunta cómo está la casa.
        """
        ahora = self.estado()
        if not ahora:
            return ""

        antes, self._ultimo = self._ultimo, ahora
        if not antes or antes == ahora:
            return ""
        return "salir" if ahora == FUERA else "llegar"

    def olvida(self) -> None:
        """Vuelve a empezar: el siguiente vistazo será el primero otra vez."""
        self._ultimo = ""
