"""Avisos al móvil, por dos caminos que no se estorban.

  · **Home Assistant**, si tienes su aplicación de móvil: no hay nada que
    montar, porque la app ya está registrada como un servicio `notify`. Jarvis
    los descubre solo preguntando qué servicios existen.
  · **ntfy**, si no tienes Home Assistant o prefieres algo aparte: eliges un
    tema, lo sigues desde su app y ya. Sin cuenta, y con servidor propio si
    quieres.

Si hay los dos configurados, se manda por los dos: un aviso que no llega no
sirve de nada, y duplicarlo molesta menos que perderlo.

La trampa de ntfy está en la URL. El modo JSON **publica contra la raíz del
servidor**, no contra la del tema: mandarlo a `/mi-tema` con el cuerpo JSON
hace que el mensaje que llegue sea el propio JSON, en crudo. Lo avisan en su
documentación porque todo el mundo se lo come una vez.
"""
from __future__ import annotations

import logging
import os

from .http import Client

log = logging.getLogger("jarvis.avisos")

TIEMPO_LIMITE = 10.0
# ntfy: de 1 (mínima) a 5 (urgente). 3 es lo normal.
PRIORIDAD_NORMAL = 3
PRIORIDAD_URGENTE = 5
PREFIJO_MOVIL = "notify.mobile_app_"


class Ntfy:
    """Publicación en ntfy.sh o en tu propio servidor."""

    def __init__(self, servidor: str = "https://ntfy.sh", topico: str = "",
                 token: str = "", timeout: float = TIEMPO_LIMITE):
        self.servidor = (servidor or "https://ntfy.sh").rstrip("/")
        self.topico = (topico or "").strip()
        self.token = token or os.getenv("JARVIS_NTFY_TOKEN", "")
        self.timeout = timeout

    @property
    def disponible(self) -> bool:
        return bool(self.topico)

    def envia(self, mensaje: str, titulo: str = "",
              urgente: bool = False) -> tuple[bool, str]:
        if not self.disponible:
            return False, "no hay tema de ntfy configurado"

        cuerpo = {
            "topic": self.topico,
            "message": mensaje,
            "priority": PRIORIDAD_URGENTE if urgente else PRIORIDAD_NORMAL,
        }
        if titulo:
            cuerpo["title"] = titulo
        cabeceras = {"Authorization": f"Bearer {self.token}"} if self.token else {}

        try:
            with Client(timeout=self.timeout) as client:
                # A la raíz, no a /{topico}: en modo JSON el tema va dentro.
                respuesta = client.post(f"{self.servidor}/", json=cuerpo,
                                        headers=cabeceras)
        except Exception as exc:  # noqa: BLE001 - la red falla; hay que contarlo
            log.warning("no se pudo avisar por ntfy (%s)", exc)
            return False, f"no se pudo hablar con {self.servidor}"

        if respuesta.status_code in (401, 403):
            return False, "ntfy rechazó las credenciales de ese tema"
        if respuesta.status_code >= 400:
            log.error("ntfy respondió %s: %s", respuesta.status_code,
                      respuesta.text[:160])
            return False, f"ntfy respondió {respuesta.status_code}"
        return True, "ntfy"


class Avisos:
    """Manda por lo que haya configurado, y cuenta qué llegó y qué no."""

    def __init__(self, casa=None, servicio: str = "", ntfy: Ntfy | None = None):
        self.casa = casa
        # Si no se nombra ninguno, se busca el de la aplicación de móvil.
        self.servicio = (servicio or "").strip()
        self.ntfy = ntfy or Ntfy()

    def servicio_de_casa(self) -> str:
        """El `notify` que se va a usar, o cadena vacía si no hay ninguno."""
        if not (self.casa and getattr(self.casa, "disponible", False)):
            return ""
        if self.servicio:
            return self.servicio
        try:
            servicios = self.casa.servicios()
        except Exception:  # noqa: BLE001 - sin Home Assistant, no hay servicio
            return ""
        # Los de la app de móvil primero: son los que llegan a un bolsillo.
        moviles = sorted(s for s in servicios if s.startswith(PREFIJO_MOVIL))
        return moviles[0] if moviles else ""

    @property
    def disponible(self) -> bool:
        return bool(self.ntfy.disponible or self.servicio_de_casa())

    def envia(self, mensaje: str, titulo: str = "",
              urgente: bool = False) -> dict:
        """Manda por todos los caminos configurados. No lanza excepciones."""
        llegaron, fallaron = [], []

        if servicio := self.servicio_de_casa():
            _, nombre = servicio.split(".", 1)
            datos = {"message": mensaje}
            if titulo:
                datos["title"] = titulo
            try:
                self.casa.llama("notify", nombre, **datos)
                llegaron.append(_bonito(servicio))
            except Exception as exc:  # noqa: BLE001
                log.warning("no se pudo avisar por Home Assistant (%s)", exc)
                fallaron.append(f"{_bonito(servicio)}: {exc}")

        if self.ntfy.disponible:
            ok, detalle = self.ntfy.envia(mensaje, titulo, urgente)
            (llegaron if ok else fallaron).append(detalle)

        return {"ok": bool(llegaron), "llegaron": llegaron, "fallaron": fallaron}


def _bonito(servicio: str) -> str:
    """`notify.mobile_app_pixel_de_yelko` -> `pixel de yelko`."""
    nombre = servicio.split(".", 1)[-1]
    if nombre.startswith("mobile_app_"):
        nombre = nombre[len("mobile_app_"):]
    return nombre.replace("_", " ")
