"""Domótica a través de Home Assistant.

Se habla con Home Assistant y no con cada marca porque es donde ya convergen
Zigbee, Z-Wave, Matter, Hue, Shelly y el resto: un solo cliente REST llega a
todo lo que tengas, y lo que no tengas hoy entra solo mañana.

Hacen falta dos cosas: la URL de tu Home Assistant y un token de acceso de
larga duración (perfil → Seguridad → Tokens de acceso de larga duración).

Lo que no es evidente y decide el diseño:

  · **No todos los dominios pesan lo mismo.** Encender una luz se deshace
    diciendo «apágala»; abrir la puerta de la calle, no. Los dominios
    delicados —cerraduras, persianas de garaje, alarmas— están fuera por
    defecto, hay que nombrarlos en la configuración para que existan, y aun
    así pasan por la confirmación hablada. Una luz, no: pedirle confirmación
    para encender la lámpara haría el asistente insufrible.
  · **Cada dominio llama a su servicio.** «Enciende» es `turn_on` para una
    luz y `open_cover` para una persiana; hablar de «encender la persiana»
    no debe acabar en un error del servidor, sino en la acción que se quería.
  · **Los nombres los pone la persona, no la API.** Se busca por el nombre
    visible («la luz del salón»), sin tildes y sin distinguir mayúsculas.
"""
from __future__ import annotations

import logging
import time
import unicodedata

from .http import Client

log = logging.getLogger("jarvis.domotica")

# Cuánto vale el mapa de nombres antes de volver a pedirlo. Los dispositivos
# se dan de alta de higos a brevas; sus estados, no, y esos no se cachean.
CACHE_SEGUNDOS = 300

# Encender y apagar esto se deshace con otra frase.
DOMINIOS_SEGUROS = ("light", "switch", "fan", "media_player", "scene",
                    "script", "input_boolean", "climate", "humidifier",
                    "vacuum", "automation")
# Esto no. Fuera por defecto; y si se activan, con confirmación.
DOMINIOS_DELICADOS = ("lock", "cover", "alarm_control_panel", "valve")

# Qué servicio toca según lo que se pide y qué clase de trasto es.
SERVICIOS = {
    "encender": {"cover": "open_cover", "lock": "unlock", "valve": "open_valve",
                 None: "turn_on"},
    "apagar": {"cover": "close_cover", "lock": "lock", "valve": "close_valve",
               None: "turn_off"},
    "alternar": {"cover": "toggle", None: "toggle"},
    "abrir": {"cover": "open_cover", "lock": "unlock", "valve": "open_valve",
              None: "turn_on"},
    "cerrar": {"cover": "close_cover", "lock": "lock", "valve": "close_valve",
               None: "turn_off"},
    "parar": {"cover": "stop_cover", "vacuum": "stop", "media_player": "media_stop",
              None: "turn_off"},
}


def normaliza(texto: str) -> str:
    descompuesto = unicodedata.normalize("NFKD", (texto or "").strip().lower())
    return " ".join("".join(c for c in descompuesto
                            if not unicodedata.combining(c)).split())


class SinConexion(RuntimeError):
    """Home Assistant no contesta. Se cuenta, no se disimula."""


class HomeAssistant:
    """Cliente REST mínimo: estados y llamadas a servicios."""

    def __init__(self, url: str, token: str, verify_ssl: bool = True,
                 dominios: list | None = None, timeout: float = 10.0):
        self.url = (url or "").rstrip("/")
        self.token = token or ""
        self.verify_ssl = verify_ssl
        self.timeout = timeout
        # Lo que la persona ha autorizado. Por defecto, solo lo reversible.
        self.dominios = tuple(dominios) if dominios else DOMINIOS_SEGUROS
        self._cache: list[dict] = []
        self._cache_en = 0.0
        # Los servicios de una instalación no cambian salvo que se instale
        # algo, así que se piden una vez y se guardan.
        self._servicios: set | None = None

    @property
    def disponible(self) -> bool:
        return bool(self.url and self.token)

    def _cliente(self) -> Client:
        return Client(timeout=self.timeout, verify=self.verify_ssl,
                      headers={"Authorization": f"Bearer {self.token}",
                               "Content-Type": "application/json"})

    # -- lectura -----------------------------------------------------------
    def estados(self, refrescar: bool = False) -> list[dict]:
        """Todas las entidades. Se cachea el mapa, no lo que valen ahora."""
        if not refrescar and self._cache and \
                time.time() - self._cache_en < CACHE_SEGUNDOS:
            return self._cache
        try:
            with self._cliente() as client:
                respuesta = client.get(f"{self.url}/api/states")
        except Exception as exc:  # noqa: BLE001 - la red de casa también falla
            raise SinConexion(f"no contesta ({exc})") from exc

        if respuesta.status_code == 401:
            raise SinConexion("el token no vale o ha caducado")
        if respuesta.status_code >= 400:
            raise SinConexion(f"respondió {respuesta.status_code}")

        self._cache = [e for e in respuesta.json()
                       if dominio_de(e.get("entity_id", "")) in self.dominios]
        self._cache_en = time.time()
        return self._cache

    def estado(self, entity_id: str) -> dict | None:
        """El estado de ahora mismo de una entidad. Nunca cacheado."""
        try:
            with self._cliente() as client:
                respuesta = client.get(f"{self.url}/api/states/{entity_id}")
        except Exception as exc:  # noqa: BLE001
            raise SinConexion(f"no contesta ({exc})") from exc
        if respuesta.status_code == 404:
            return None
        if respuesta.status_code >= 400:
            raise SinConexion(f"respondió {respuesta.status_code}")
        return respuesta.json()

    def servicios(self) -> set:
        """Qué servicios existen en esta instalación, como «dominio.servicio».

        Sirve para saber si hay Music Assistant sin preguntárselo a nadie: si
        `music_assistant.play_media` está, es que está.
        """
        if self._servicios is not None:
            return self._servicios
        try:
            with self._cliente() as client:
                respuesta = client.get(f"{self.url}/api/services")
        except Exception as exc:  # noqa: BLE001
            raise SinConexion(f"no contesta ({exc})") from exc
        if respuesta.status_code >= 400:
            raise SinConexion(f"respondió {respuesta.status_code}")

        self._servicios = {
            f"{bloque.get('domain', '')}.{nombre}"
            for bloque in respuesta.json()
            for nombre in (bloque.get("services") or {})
        }
        return self._servicios

    # -- escritura ---------------------------------------------------------
    def llama(self, dominio: str, servicio: str, entity_id: str,
              **datos) -> list:
        """Llama a un servicio. Devuelve las entidades que cambiaron."""
        try:
            with self._cliente() as client:
                respuesta = client.post(
                    f"{self.url}/api/services/{dominio}/{servicio}",
                    json={"entity_id": entity_id, **datos})
        except Exception as exc:  # noqa: BLE001
            raise SinConexion(f"no contesta ({exc})") from exc
        if respuesta.status_code >= 400:
            raise SinConexion(
                f"rechazó {dominio}.{servicio} ({respuesta.status_code})")
        try:
            return respuesta.json()
        except ValueError:
            return []

    # -- búsqueda por nombre ----------------------------------------------
    def busca(self, texto: str) -> list[dict]:
        """Las entidades cuyo nombre visible encaja con lo que se ha dicho.

        Exacto primero; si no, las que contengan lo dicho. Se reintenta con
        la lista fresca si no sale nada: puede ser un aparato recién puesto.
        """
        objetivo = normaliza(texto)
        if not objetivo:
            return []
        for refrescar in (False, True):
            candidatas = _encaja(self.estados(refrescar=refrescar), objetivo)
            if candidatas or refrescar:
                return candidatas
        return []


def dominio_de(entity_id: str) -> str:
    return (entity_id or "").split(".", 1)[0]


def nombre_de(entidad: dict) -> str:
    atributos = entidad.get("attributes") or {}
    return (atributos.get("friendly_name")
            or (entidad.get("entity_id", "").split(".", 1)[-1].replace("_", " ")))


def _encaja(entidades: list[dict], objetivo: str) -> list[dict]:
    exactas = [e for e in entidades if normaliza(nombre_de(e)) == objetivo]
    if exactas:
        return exactas
    # También vale el propio entity_id, por si lo dice tal cual.
    return [e for e in entidades
            if objetivo in normaliza(nombre_de(e))
            or objetivo in normaliza(e.get("entity_id", "").replace("_", " "))]


def servicio_para(accion: str, dominio: str) -> str | None:
    """Qué servicio de Home Assistant corresponde a lo que se ha pedido."""
    tabla = SERVICIOS.get(normaliza(accion))
    if tabla is None:
        return None
    return tabla.get(dominio, tabla.get(None))


def en_palabras(entidad: dict) -> str:
    """El estado de un trasto, dicho como lo diría una persona."""
    estado = (entidad.get("state") or "").lower()
    atributos = entidad.get("attributes") or {}
    nombre = nombre_de(entidad)

    traduccion = {"on": "encendida", "off": "apagada", "open": "abierta",
                  "closed": "cerrada", "locked": "cerrada con llave",
                  "unlocked": "abierta", "home": "en casa", "not_home": "fuera",
                  "unavailable": "sin conexión", "unknown": "sin saber",
                  "idle": "en reposo", "playing": "reproduciendo",
                  "paused": "en pausa", "heat": "calentando", "cool": "enfriando"}
    dicho = traduccion.get(estado, estado)

    extras = []
    if (brillo := atributos.get("brightness")) is not None:
        extras.append(f"al {round(int(brillo) / 255 * 100)} por ciento")
    if (temperatura := atributos.get("temperature")) is not None:
        extras.append(f"a {temperatura} grados")
    if (actual := atributos.get("current_temperature")) is not None:
        extras.append(f"ahora {actual}")
    if unidad := atributos.get("unit_of_measurement"):
        dicho = f"{estado} {unidad}"

    return f"{nombre}: {dicho}{(' ' + ', '.join(extras)) if extras else ''}"
