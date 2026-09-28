"""Control de música, por dos caminos que se eligen solos.

  · **Home Assistant**: los altavoces de casa —Sonos, Chromecast, un receptor,
    el televisor—. Ya hay cliente, así que aquí solo se traduce «pon la
    música» al servicio que toca.
  · **El reproductor del propio equipo**, vía `playerctl`: Spotify, VLC, mpv,
    Rhythmbox o la pestaña del navegador. Es opcional; si `playerctl` no está
    instalado, esa mitad simplemente no existe y se dice.

La parte que no es evidente es **a quién le hablas cuando no lo dices**. Si
hay un Sonos sonando en el salón y el Spotify del escritorio en pausa, «pausa»
tiene que parar el Sonos. Así que manda lo que está sonando, y solo si no hay
nada sonando se cae al reproductor local o al único altavoz que haya.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import time
import unicodedata
from pathlib import Path

log = logging.getLogger("jarvis.musica")

TIEMPO_LIMITE = 5.0
# Cuánto vale la lista de ficheros de la biblioteca antes de releerla.
CACHE_SEGUNDOS = 300

# Lo que se puede pedir, y cómo se dice en cada sitio. None = no aplica.
ACCIONES = {
    "reproducir": ("media_play", "play"),
    "pausa": ("media_pause", "pause"),
    "alternar": ("media_play_pause", "play-pause"),
    "siguiente": ("media_next_track", "next"),
    "anterior": ("media_previous_track", "previous"),
    "parar": ("media_stop", "stop"),
    "subir": ("volume_up", None),
    "bajar": ("volume_down", None),
    "volumen": ("volume_set", None),
}

SONANDO = ("playing",)


def normaliza(texto: str) -> str:
    descompuesto = unicodedata.normalize("NFKD", (texto or "").strip().lower())
    return " ".join("".join(c for c in descompuesto
                            if not unicodedata.combining(c)).split())


class Local:
    """El reproductor del equipo, a través de `playerctl`.

    Se llama al binario en vez de hablar D-Bus directamente para no arrastrar
    una dependencia más: `playerctl` está en los repositorios de cualquier
    distribución y aquí es opcional de verdad, no un requisito disfrazado.
    """

    def __init__(self, binario: str = "playerctl", timeout: float = TIEMPO_LIMITE):
        self.binario = binario
        self.timeout = timeout

    @property
    def disponible(self) -> bool:
        return bool(shutil.which(self.binario))

    def _ejecuta(self, *argumentos: str) -> tuple[bool, str]:
        try:
            resultado = subprocess.run(
                [self.binario, *argumentos], capture_output=True, text=True,
                timeout=self.timeout, check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            log.debug("playerctl falló (%s)", exc)
            return False, str(exc)
        salida = (resultado.stdout or "").strip()
        if resultado.returncode != 0:
            return False, (resultado.stderr or "").strip() or salida
        return True, salida

    def reproductores(self) -> list[str]:
        ok, salida = self._ejecuta("--list-all")
        return [linea.strip() for linea in salida.splitlines() if linea.strip()] \
            if ok else []

    def estado(self, reproductor: str = "") -> dict | None:
        """Qué suena y en qué estado, o None si no hay nadie reproduciendo."""
        destino = ["-p", reproductor] if reproductor else []
        ok, estado = self._ejecuta(*destino, "status")
        if not ok:
            return None
        _, titulo = self._ejecuta(
            *destino, "metadata", "--format", "{{title}}")
        _, artista = self._ejecuta(
            *destino, "metadata", "--format", "{{artist}}")
        _, volumen = self._ejecuta(*destino, "volume")
        return {
            "fuente": "local",
            "nombre": reproductor or (self.reproductores() or ["el equipo"])[0],
            "estado": estado.lower(),
            "titulo": titulo,
            "artista": artista,
            "volumen": _a_porcentaje(volumen),
        }

    def abre(self, uri: str, reproductor: str = "") -> bool:
        """Le dice al reproductor que ponga eso: un fichero o una URL."""
        destino = ["-p", reproductor] if reproductor else []
        ok, _ = self._ejecuta(*destino, "open", uri)
        return ok

    def ejecuta(self, accion: str, reproductor: str = "",
                volumen: int | None = None) -> bool:
        destino = ["-p", reproductor] if reproductor else []
        if accion == "volumen" and volumen is not None:
            ok, _ = self._ejecuta(*destino, "volume", f"{volumen / 100:.2f}")
            return ok
        if accion in ("subir", "bajar"):
            signo = "+" if accion == "subir" else "-"
            ok, _ = self._ejecuta(*destino, "volume", f"0.1{signo}")
            return ok

        orden = ACCIONES.get(accion, (None, None))[1]
        if orden is None:
            return False
        ok, _ = self._ejecuta(*destino, orden)
        return ok


def _a_porcentaje(valor: str) -> int | None:
    try:
        return round(float(valor) * 100)
    except (TypeError, ValueError):
        return None


def desde_home_assistant(entidad: dict) -> dict:
    """Un media_player de Home Assistant, en el mismo formato que el local."""
    atributos = entidad.get("attributes") or {}
    return {
        "fuente": "home_assistant",
        "entity_id": entidad.get("entity_id", ""),
        "nombre": atributos.get("friendly_name") or entidad.get("entity_id", ""),
        "estado": (entidad.get("state") or "").lower(),
        "titulo": atributos.get("media_title") or "",
        "artista": atributos.get("media_artist") or atributos.get("media_album_artist") or "",
        "volumen": (round(atributos["volume_level"] * 100)
                    if atributos.get("volume_level") is not None else None),
    }


def suena(reproductor: dict | None) -> bool:
    return bool(reproductor) and reproductor.get("estado") in SONANDO


def en_palabras(reproductor: dict | None) -> str:
    """Lo que suena, dicho como lo diría una persona."""
    if not reproductor:
        return "No hay nada sonando."

    nombre = reproductor.get("nombre", "el reproductor")
    estado = reproductor.get("estado", "")
    titulo = (reproductor.get("titulo") or "").strip()
    artista = (reproductor.get("artista") or "").strip()

    if estado not in SONANDO and estado != "paused":
        return f"{nombre} está parado."
    if not titulo:
        cabecera = "Sonando" if suena(reproductor) else "En pausa"
        return f"{cabecera} en {nombre}, pero no sé qué."

    pieza = f"{titulo}, de {artista}" if artista else titulo
    cabecera = "Suena" if suena(reproductor) else "En pausa"
    texto = f"{cabecera} {pieza}, en {nombre}"
    if (volumen := reproductor.get("volumen")) is not None:
        texto += f", al {volumen} por ciento"
    return texto + "."


def elige(candidatos: list[dict]) -> dict | None:
    """A quién le hablas cuando no dices dónde.

    Manda lo que está sonando: si el salón suena y el escritorio está en
    pausa, «pausa» tiene que parar el salón. Después, lo que esté en pausa
    —seguir escuchando ahí es lo más probable—, y si no, lo que haya.
    """
    if not candidatos:
        return None
    for estado in (SONANDO, ("paused",)):
        for candidato in candidatos:
            if candidato.get("estado") in estado:
                return candidato
    return candidatos[0]


def busca(candidatos: list[dict], texto: str) -> list[dict]:
    objetivo = normaliza(texto)
    if not objetivo:
        return []
    exactos = [c for c in candidatos if normaliza(c.get("nombre", "")) == objetivo]
    return exactos or [c for c in candidatos
                       if objetivo in normaliza(c.get("nombre", ""))]


# -- pedir una canción concreta ---------------------------------------------
# Hay tres maneras de conseguir que suene algo que no estaba sonando, y
# ninguna sirve para todo el mundo, así que se prueban por orden:
#
#   1. **Favoritos**: un nombre y una URL en config.yaml. Es lo que resuelve
#      «pon Radio 3», que no es una búsqueda sino una emisora concreta.
#   2. **Music Assistant**: si está instalado en Home Assistant, su servicio
#      `play_media` acepta texto libre y busca en todo lo que tengas dado de
#      alta —Spotify, la biblioteca local, lo que sea—. Es la única vía que
#      de verdad «busca», así que cuando está, manda.
#   3. **Una carpeta de música**: para quien no tenga Music Assistant. Se
#      recorre, se busca por nombre de fichero y se abre en el reproductor
#      del equipo.

EXTENSIONES = (".mp3", ".flac", ".m4a", ".ogg", ".opus", ".wav", ".aac",
               ".wma", ".aiff", ".alac")
MAX_FICHEROS = 50_000
SERVICIO_MASS = "music_assistant.play_media"
TIPOS_MASS = ("artist", "album", "playlist", "track", "radio", "podcast",
              "audiobook", "folder")


class Biblioteca:
    """Una carpeta de música, recorrida y buscada por nombre de fichero.

    No se leen las etiquetas ID3: haría falta otra dependencia y, en la
    práctica, quien tiene una carpeta de música la tiene ordenada por
    artista y disco, así que la ruta ya dice lo que hay que saber.
    """

    def __init__(self, carpeta: str = "", cache_segundos: int = CACHE_SEGUNDOS):
        self.carpeta = Path(carpeta).expanduser() if carpeta else None
        self.cache_segundos = cache_segundos
        self._ficheros: list[Path] = []
        self._leida_en = 0.0

    @property
    def disponible(self) -> bool:
        return bool(self.carpeta and self.carpeta.is_dir())

    def ficheros(self, refrescar: bool = False) -> list[Path]:
        if not self.disponible:
            return []
        if not refrescar and self._ficheros and \
                time.time() - self._leida_en < self.cache_segundos:
            return self._ficheros

        encontrados: list[Path] = []
        for ruta in self.carpeta.rglob("*"):
            if ruta.suffix.lower() in EXTENSIONES and ruta.is_file():
                encontrados.append(ruta)
                if len(encontrados) >= MAX_FICHEROS:
                    log.warning("la biblioteca tiene más de %d ficheros; "
                                "se busca solo entre los primeros", MAX_FICHEROS)
                    break
        self._ficheros = encontrados
        self._leida_en = time.time()
        return self._ficheros

    def busca(self, texto: str, limite: int = 5) -> list[Path]:
        """Los ficheros que contienen todas las palabras de lo que se ha pedido.

        Gana la ruta más corta: «Queen» debe dar la canción de Queen antes que
        la versión en directo del disco recopilatorio de tres discos.
        """
        palabras = [p for p in normaliza(texto).split() if p]
        if not palabras:
            return []
        encaje = []
        for ruta in self.ficheros():
            relativa = normaliza(str(ruta.relative_to(self.carpeta)))
            if all(palabra in relativa for palabra in palabras):
                encaje.append(ruta)
        return sorted(encaje, key=lambda r: (len(str(r)), str(r)))[:limite]


def busca_favorito(favoritos: dict, texto: str) -> tuple[str, str] | None:
    """(nombre, URL) del favorito que encaje, o None. Exacto antes que parcial."""
    objetivo = normaliza(texto)
    if not (objetivo and favoritos):
        return None
    for nombre, url in favoritos.items():
        if normaliza(nombre) == objetivo:
            return nombre, url
    for nombre, url in favoritos.items():
        if objetivo in normaliza(nombre):
            return nombre, url
    return None
