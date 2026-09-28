"""Listas de la compra y cualquier otra lista que se dicte en voz alta.

Vive fuera de `tools.py` porque lo que tiene enjundia no es leer un JSON, sino
lo que pasa cuando alguien habla:

  · **«añade leche» dos veces** no son dos leches. Se comparan sin tildes ni
    mayúsculas, así que «Leche» y «leche» son lo mismo. Si el artículo ya
    estaba pero tachado, se destacha en vez de duplicarse: quien lo vuelve a
    decir es que lo quiere otra vez.
  · **«quita la leche»** tiene que encontrar «leche entera» sin que haya que
    decir el nombre exacto. Se busca por contenido, pero una coincidencia
    exacta siempre gana a una parcial.
  · **El plural del habla**: «añade leche, pan y huevos» es una frase y tres
    artículos. Las herramientas reciben listas, no cadenas sueltas.

Es todo local: un fichero JSON al lado de las notas. Nada sale del equipo.
"""
from __future__ import annotations

import json
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

POR_DEFECTO = "la compra"
MAX_ARTICULOS = 200


def normaliza(texto: str) -> str:
    """Para comparar lo que se dice con lo que está escrito."""
    descompuesto = unicodedata.normalize("NFKD", (texto or "").strip().lower())
    return " ".join("".join(c for c in descompuesto
                            if not unicodedata.combining(c)).split())


def _ahora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Listas:
    """Las listas de la persona, guardadas en un JSON.

    Se relee en cada operación en vez de mantenerlo en memoria: son unos
    kilobytes y así el fichero se puede editar a mano sin que Jarvis pise el
    cambio la próxima vez que hable.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)

    # -- persistencia ------------------------------------------------------
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

    # -- consulta ----------------------------------------------------------
    def nombres(self) -> list[str]:
        return [datos.get("nombre", clave) for clave, datos in self._lee().items()]

    def ver(self, lista: str = POR_DEFECTO) -> dict:
        """La lista entera: su nombre tal cual se dijo y sus artículos."""
        datos = self._lee().get(normaliza(lista) or normaliza(POR_DEFECTO))
        if not datos:
            return {"nombre": (lista or POR_DEFECTO).strip(), "articulos": []}
        return datos

    @staticmethod
    def pendientes(lista: dict) -> list[dict]:
        return [a for a in lista.get("articulos", []) if not a.get("tachado")]

    @staticmethod
    def tachados(lista: dict) -> list[dict]:
        return [a for a in lista.get("articulos", []) if a.get("tachado")]

    # -- modificación ------------------------------------------------------
    def _con_lista(self, lista: str, crear: bool = True):
        """El fichero y la lista pedida. Sin `crear`, None si no existía.

        Importa para no dejar listas fantasma: tachar algo en una lista mal
        entendida no debe inventarse esa lista y dejarla ahí para siempre.
        """
        datos = self._lee()
        clave = normaliza(lista) or normaliza(POR_DEFECTO)
        if clave not in datos and not crear:
            return datos, clave, None
        actual = datos.setdefault(clave, {"nombre": (lista or POR_DEFECTO).strip(),
                                          "articulos": []})
        return datos, clave, actual

    def anade(self, articulos: list[str], lista: str = POR_DEFECTO) -> dict:
        """Añade lo que falte. Devuelve qué se añadió, qué se destachó y qué ya estaba."""
        datos, _, actual = self._con_lista(lista)
        nuevos, destachados, repetidos = [], [], []

        for crudo in articulos:
            texto = (crudo or "").strip()
            if not texto:
                continue
            existente = _busca_exacto(actual["articulos"], texto)
            if existente is None:
                if len(actual["articulos"]) >= MAX_ARTICULOS:
                    break
                actual["articulos"].append(
                    {"texto": texto, "tachado": False, "fecha": _ahora()})
                nuevos.append(texto)
            elif existente.get("tachado"):
                # Ya se compró, pero lo vuelve a pedir: lo quiere otra vez.
                existente["tachado"] = False
                existente["fecha"] = _ahora()
                destachados.append(existente["texto"])
            else:
                repetidos.append(existente["texto"])

        self._escribe(datos)
        return {"nuevos": nuevos, "destachados": destachados,
                "repetidos": repetidos, "lista": actual["nombre"]}

    def tacha(self, articulos: list[str], lista: str = POR_DEFECTO,
              quitar: bool = False) -> dict:
        """Marca como comprados —o borra, si `quitar`— los que encuentre."""
        datos, _, actual = self._con_lista(lista, crear=False)
        if actual is None:
            return {"hechos": [], "no_estan": [a for a in articulos if a],
                    "lista": (lista or POR_DEFECTO).strip(), "no_existe": True}
        hechos, no_estan = [], []

        for crudo in articulos:
            texto = (crudo or "").strip()
            if not texto:
                continue
            encontrado = _busca_parecido(actual["articulos"], texto)
            if encontrado is None:
                no_estan.append(texto)
                continue
            if quitar:
                actual["articulos"].remove(encontrado)
            else:
                encontrado["tachado"] = True
                encontrado["fecha"] = _ahora()
            hechos.append(encontrado["texto"])

        self._escribe(datos)
        return {"hechos": hechos, "no_estan": no_estan, "lista": actual["nombre"]}

    def vacia(self, lista: str = POR_DEFECTO, solo_tachados: bool = False) -> dict:
        """Deja la lista a cero, o solo quita lo ya comprado."""
        datos, _, actual = self._con_lista(lista, crear=False)
        if actual is None:
            return {"quitados": 0, "quedan": 0,
                    "lista": (lista or POR_DEFECTO).strip(), "no_existe": True}
        antes = len(actual["articulos"])
        if solo_tachados:
            actual["articulos"] = self.pendientes(actual)
        else:
            actual["articulos"] = []
        self._escribe(datos)
        return {"quitados": antes - len(actual["articulos"]),
                "quedan": len(actual["articulos"]), "lista": actual["nombre"]}


def _busca_exacto(articulos: list[dict], texto: str) -> dict | None:
    objetivo = normaliza(texto)
    for articulo in articulos:
        if normaliza(articulo.get("texto", "")) == objetivo:
            return articulo
    return None


def _busca_parecido(articulos: list[dict], texto: str) -> dict | None:
    """Exacto primero; si no, el que contenga lo dicho («leche» → «leche entera»).

    Entre varios parciales gana el más corto: es el que menos añade por su
    cuenta a lo que se pidió.
    """
    if (exacto := _busca_exacto(articulos, texto)) is not None:
        return exacto
    objetivo = normaliza(texto)
    if not objetivo:
        return None
    parciales = [a for a in articulos
                 if objetivo in normaliza(a.get("texto", ""))]
    return min(parciales, key=lambda a: len(a.get("texto", "")), default=None)
