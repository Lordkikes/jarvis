"""Escenas: activar las de Home Assistant y guardar las tuyas.

Activarlas ya funcionaba —una escena es un dominio más para
`controlar_dispositivo`—, pero dicho así no se encuentra: nadie piensa que
«modo cine» sea un dispositivo. Aquí se les da nombre propio, se pueden
listar, y se añade lo que de verdad faltaba: **guardar la luz que hay ahora
mismo** para recuperarla después.

Home Assistant tiene `scene.create` para eso, pero **las escenas creadas así
se pierden al reiniciar** —está documentado, y la petición de hacerlas
persistentes se cerró como «no planeado»—. Una escena que se evapora sin
avisar es peor que no tenerla, así que la instantánea se guarda aquí, en un
JSON junto a las listas, y se recupera con `scene.apply`, que acepta los
estados directamente y no necesita que exista ninguna escena en el servidor.
"""
from __future__ import annotations

import json
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

# Lo que compone una escena: la luz y lo que se enciende con ella. Un
# reproductor o una cerradura no pintan nada en un «modo cine».
DOMINIOS = ("light", "switch", "fan", "input_boolean", "climate")

# Atributos que merece la pena recordar además de encendido o apagado.
ATRIBUTOS = ("brightness", "color_temp_kelvin", "rgb_color", "hs_color",
             "xy_color", "effect", "percentage", "temperature", "hvac_mode")

MAX_ESCENAS = 50


def normaliza(texto: str) -> str:
    descompuesto = unicodedata.normalize("NFKD", (texto or "").strip().lower())
    return " ".join("".join(c for c in descompuesto
                            if not unicodedata.combining(c)).split())


def instantanea(entidades: list[dict]) -> dict:
    """El estado de ahora, en la forma que espera `scene.apply`.

    Se guardan también las apagadas: una escena que solo enciende cosas no
    sirve para volver a como estaba, que es justo para lo que se guarda.
    """
    foto = {}
    for entidad in entidades:
        entity_id = entidad.get("entity_id", "")
        estado = (entidad.get("state") or "").lower()
        if not entity_id or estado in ("unavailable", "unknown", ""):
            continue

        atributos = entidad.get("attributes") or {}
        datos = {"state": estado}
        if estado != "off":
            # De lo apagado no hay brillo que recordar, y mandarlo confunde.
            datos.update({clave: atributos[clave] for clave in ATRIBUTOS
                          if atributos.get(clave) is not None})
        foto[entity_id] = datos
    return foto


class Escenas:
    """Las escenas que ha guardado la persona, en un JSON."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def _lee(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            datos = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
        return datos if isinstance(datos, dict) else {}

    def _escribe(self, datos: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(datos, ensure_ascii=False, indent=2),
                             encoding="utf-8")

    def nombres(self) -> list[str]:
        return [datos.get("nombre", clave)
                for clave, datos in sorted(self._lee().items())]

    def ver(self, nombre: str) -> dict | None:
        return self._lee().get(normaliza(nombre))

    def existe(self, nombre: str) -> bool:
        return self.ver(nombre) is not None

    def guarda(self, nombre: str, foto: dict) -> dict:
        datos = self._lee()
        clave = normaliza(nombre)
        if clave not in datos and len(datos) >= MAX_ESCENAS:
            return {"ok": False, "motivo": f"ya tienes {MAX_ESCENAS} escenas"}
        datos[clave] = {
            "nombre": (nombre or "").strip(),
            "entidades": foto,
            "fecha": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        self._escribe(datos)
        return {"ok": True, "cuantas": len(foto)}

    def olvida(self, nombre: str) -> bool:
        datos = self._lee()
        if (clave := normaliza(nombre)) not in datos:
            return False
        del datos[clave]
        self._escribe(datos)
        return True

    def busca(self, texto: str) -> list[dict]:
        """Las escenas guardadas que encajan. Exacto antes que parcial."""
        objetivo = normaliza(texto)
        if not objetivo:
            return []
        datos = self._lee()
        if objetivo in datos:
            return [datos[objetivo]]
        return [valor for clave, valor in sorted(datos.items())
                if objetivo in clave]
