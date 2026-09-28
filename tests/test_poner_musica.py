"""Pedir una canción concreta: favoritos, Music Assistant y carpeta local."""
from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis.bus import EventBus  # noqa: E402
from jarvis.config import Config  # noqa: E402
from jarvis.llm.tools import Toolbox  # noqa: E402
from jarvis.musica import Biblioteca, busca_favorito  # noqa: E402
from tests.test_musica import FALSO, pista  # noqa: E402

CANCIONES = [
    "Queen/A Night at the Opera/03 Bohemian Rhapsody.mp3",
    "Queen/A Night at the Opera/01 Death on Two Legs.mp3",
    "Queen/Grandes éxitos en directo (disco 1 de 3)/Bohemian Rhapsody.flac",
    "The Beatles/Abbey Road/04 Something.flac",
    "apuntes/leeme.txt",
]


class TestFavoritos(unittest.TestCase):
    FAVORITOS = {"Radio 3": "https://ejemplo/r3.mp3",
                 "Mi lista de los domingos": "spotify:playlist:xyz"}

    def test_exacto(self):
        self.assertEqual(busca_favorito(self.FAVORITOS, "radio 3")[0], "Radio 3")

    def test_parcial(self):
        self.assertEqual(busca_favorito(self.FAVORITOS, "domingos")[0],
                         "Mi lista de los domingos")

    def test_lo_exacto_gana(self):
        favoritos = {"Radio": "a", "Radio 3": "b"}
        self.assertEqual(busca_favorito(favoritos, "radio"), ("Radio", "a"))

    def test_sin_coincidencia(self):
        self.assertIsNone(busca_favorito(self.FAVORITOS, "radio 5"))
        self.assertIsNone(busca_favorito({}, "radio 3"))
        self.assertIsNone(busca_favorito(self.FAVORITOS, ""))


class TestBiblioteca(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.raiz = Path(self.tmp.name)
        for relativa in CANCIONES:
            ruta = self.raiz / relativa
            ruta.parent.mkdir(parents=True, exist_ok=True)
            ruta.write_bytes(b"x")
        self.biblioteca = Biblioteca(str(self.raiz))

    def tearDown(self):
        self.tmp.cleanup()

    def nombres(self, resultados):
        return [str(r.relative_to(self.raiz)) for r in resultados]

    def test_solo_recoge_audio(self):
        self.assertEqual(len(self.biblioteca.ficheros()), 4)

    def test_buscar_por_titulo(self):
        encontrados = self.nombres(self.biblioteca.busca("bohemian rhapsody"))
        self.assertEqual(len(encontrados), 2)

    def test_gana_la_ruta_mas_corta(self):
        """«Queen» debe dar la canción, no la del recopilatorio de tres discos."""
        primero = self.nombres(self.biblioteca.busca("bohemian rhapsody"))[0]
        self.assertIn("A Night at the Opera", primero)

    def test_todas_las_palabras_tienen_que_estar(self):
        self.assertEqual(self.biblioteca.busca("bohemian beatles"), [])

    def test_el_artista_vale_porque_esta_en_la_ruta(self):
        self.assertEqual(len(self.biblioteca.busca("beatles")), 1)

    def test_sin_tildes(self):
        self.assertEqual(len(self.biblioteca.busca("grandes exitos")), 1)

    def test_una_busqueda_vacia_no_devuelve_todo(self):
        self.assertEqual(self.biblioteca.busca(""), [])
        self.assertEqual(self.biblioteca.busca("   "), [])

    def test_sin_carpeta_no_hay_biblioteca(self):
        self.assertFalse(Biblioteca("").disponible)
        self.assertFalse(Biblioteca("/no/existe/esta/carpeta").disponible)
        self.assertEqual(Biblioteca("").busca("queen"), [])

    def test_se_cachea_pero_se_puede_refrescar(self):
        self.biblioteca.ficheros()
        (self.raiz / "Nueva/cancion.mp3").parent.mkdir(parents=True, exist_ok=True)
        (self.raiz / "Nueva/cancion.mp3").write_bytes(b"x")
        self.assertEqual(len(self.biblioteca.ficheros()), 4, "aún cacheada")
        self.assertEqual(len(self.biblioteca.ficheros(refrescar=True)), 5)


class FakeHass(BaseHTTPRequestHandler):
    estados: dict = {}
    servicios: list = []
    peticiones: list = []
    rechaza: bool = False

    def log_message(self, *args):
        pass

    def _responde(self, payload, status=200):
        cuerpo = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)

    def do_GET(self):  # noqa: N802
        if self.path == "/api/states":
            self._responde(list(FakeHass.estados.values()))
        elif self.path == "/api/services":
            self._responde(FakeHass.servicios)
        else:
            self._responde(FakeHass.estados.get(self.path.rsplit("/", 1)[-1]) or {})

    def do_POST(self):  # noqa: N802
        largo = int(self.headers.get("Content-Length", 0))
        datos = json.loads(self.rfile.read(largo).decode()) if largo else {}
        _, _, _, dominio, servicio = self.path.split("/")
        FakeHass.peticiones.append({"servicio": f"{dominio}.{servicio}",
                                    "datos": datos})
        if FakeHass.rechaza and dominio == "music_assistant":
            self._responde({"message": "no es un altavoz de Music Assistant"}, 400)
            return
        self._responde([])


def altavoz(entity_id, nombre, estado="idle") -> dict:
    return {"entity_id": entity_id, "state": estado,
            "attributes": {"friendly_name": nombre}}


SERVICIOS_CON_MASS = [
    {"domain": "media_player", "services": {"play_media": {}, "media_pause": {}}},
    {"domain": "music_assistant", "services": {"play_media": {}}},
]
SERVICIOS_SIN_MASS = [
    {"domain": "media_player", "services": {"play_media": {}, "media_pause": {}}},
]


class TestPonerMusica(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        FakeHass.estados = {}
        FakeHass.servicios = SERVICIOS_SIN_MASS
        FakeHass.peticiones = []
        FakeHass.rechaza = False
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeHass)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

        self.tmp = tempfile.TemporaryDirectory()
        raiz = Path(self.tmp.name)
        self.musica = raiz / "musica"
        for relativa in CANCIONES:
            ruta = self.musica / relativa
            ruta.parent.mkdir(parents=True, exist_ok=True)
            ruta.write_bytes(b"x")

        # playerctl de mentira, ejecutable, como en test_musica.
        binario = raiz / "playerctl"
        binario.write_text(FALSO, encoding="utf-8")
        binario.chmod(binario.stat().st_mode | stat.S_IEXEC)
        self.estado_json = raiz / "estado.json"
        self.registro = raiz / "registro.log"
        self.estado_json.write_text(json.dumps(
            {"reproductores": ["vlc"], "estados": {"vlc": pista(status="Paused")}}),
            encoding="utf-8")
        self.entorno = mock.patch.dict(os.environ, {
            "PATH": f"{raiz}{os.pathsep}{os.environ['PATH']}",
            "PLAYERCTL_ESTADO": str(self.estado_json),
            "PLAYERCTL_REGISTRO": str(self.registro)})
        self.entorno.start()
        self.raiz = raiz

    def tearDown(self):
        self.entorno.stop()
        self.server.shutdown()
        self.tmp.cleanup()

    def caja(self, con_hass=True, biblioteca="", favoritos=None,
             playerctl="playerctl") -> Toolbox:
        herramientas = {
            "allow_system": True, "playerctl": playerctl,
            "notes_file": str(self.raiz / "n.json"),
            "memory_file": str(self.raiz / "m.json"),
            "lists_file": str(self.raiz / "l.json"),
            "musica": {"biblioteca": biblioteca, "favoritos": favoritos or {}},
        }
        if con_hass:
            herramientas["home_assistant"] = {"url": self.base}
        cfg = Config({"tools": herramientas, "llm": {"web_search": False}})
        with mock.patch.dict(os.environ,
                             {"JARVIS_HASS_TOKEN": "tok" if con_hass else ""}):
            return Toolbox(cfg, EventBus())

    def sin_reproductor_local(self) -> None:
        """Deja solo los altavoces de casa.

        Hace falta porque la regla de «a quién le hablas» es real: con un VLC
        en pausa en el escritorio y un Sonos inactivo, gana el VLC. Para
        probar el camino de Home Assistant hay que quedarse sin el otro.
        """
        self.estado_json.write_text(json.dumps({"reproductores": [],
                                                "estados": {}}), encoding="utf-8")

    def llamadas_playerctl(self) -> list[list[str]]:
        if not self.registro.exists():
            return []
        return [json.loads(l) for l in
                self.registro.read_text(encoding="utf-8").splitlines() if l]

    # -- favoritos ---------------------------------------------------------
    async def test_un_favorito_se_pone_por_home_assistant(self):
        self.sin_reproductor_local()
        FakeHass.estados = {"media_player.salon":
                            altavoz("media_player.salon", "Sonos del salón")}
        caja = self.caja(favoritos={"Radio 3": "https://ejemplo/r3.mp3"})
        salida = await caja.run("poner_musica", {"que": "radio 3"})

        self.assertIn("Radio 3", salida)
        envio = FakeHass.peticiones[-1]
        self.assertEqual(envio["servicio"], "media_player.play_media")
        self.assertEqual(envio["datos"]["media_content_id"], "https://ejemplo/r3.mp3")
        self.assertEqual(envio["datos"]["media_content_type"], "music")

    async def test_un_favorito_manda_sobre_music_assistant(self):
        self.sin_reproductor_local()
        """Una emisora con nombre propio no es una búsqueda: es esa."""
        FakeHass.servicios = SERVICIOS_CON_MASS
        FakeHass.estados = {"media_player.salon":
                            altavoz("media_player.salon", "Sonos del salón")}
        caja = self.caja(favoritos={"Radio 3": "https://ejemplo/r3.mp3"})
        await caja.run("poner_musica", {"que": "Radio 3"})
        self.assertEqual(FakeHass.peticiones[-1]["servicio"],
                         "media_player.play_media")

    async def test_un_favorito_en_el_equipo_se_abre(self):
        caja = self.caja(con_hass=False,
                         favoritos={"Radio 3": "https://ejemplo/r3.mp3"})
        salida = await caja.run("poner_musica", {"que": "radio 3"})
        self.assertIn("Radio 3", salida)
        self.assertIn(["-p", "vlc", "open", "https://ejemplo/r3.mp3"],
                      self.llamadas_playerctl())

    # -- Music Assistant ---------------------------------------------------
    async def test_music_assistant_recibe_el_texto_tal_cual(self):
        self.sin_reproductor_local()
        FakeHass.servicios = SERVICIOS_CON_MASS
        FakeHass.estados = {"media_player.salon":
                            altavoz("media_player.salon", "Sonos del salón")}
        salida = await self.caja().run("poner_musica",
                                       {"que": "Bohemian Rhapsody de Queen"})

        self.assertIn("Bohemian Rhapsody", salida)
        envio = FakeHass.peticiones[-1]
        self.assertEqual(envio["servicio"], "music_assistant.play_media")
        self.assertEqual(envio["datos"]["media_id"], "Bohemian Rhapsody de Queen")
        self.assertNotIn("media_type", envio["datos"])

    async def test_el_tipo_se_pasa_si_se_sabe(self):
        self.sin_reproductor_local()
        FakeHass.servicios = SERVICIOS_CON_MASS
        FakeHass.estados = {"media_player.salon":
                            altavoz("media_player.salon", "Sonos")}
        await self.caja().run("poner_musica", {"que": "Abbey Road",
                                               "tipo": "album"})
        self.assertEqual(FakeHass.peticiones[-1]["datos"]["media_type"], "album")

    async def test_un_altavoz_apagado_sirve_para_poner_musica(self):
        self.sin_reproductor_local()
        """«Pon música en el salón» es justamente encenderlo."""
        FakeHass.servicios = SERVICIOS_CON_MASS
        FakeHass.estados = {"media_player.salon":
                            altavoz("media_player.salon", "Sonos", estado="off")}
        salida = await self.caja().run("poner_musica", {"que": "Queen",
                                                        "donde": "sonos"})
        self.assertIn("Buscando", salida)

    async def test_si_no_es_un_altavoz_de_music_assistant_se_explica(self):
        self.sin_reproductor_local()
        FakeHass.servicios = SERVICIOS_CON_MASS
        FakeHass.rechaza = True
        FakeHass.estados = {"media_player.tele":
                            altavoz("media_player.tele", "Televisión")}
        salida = await self.caja().run("poner_musica", {"que": "Queen"})
        self.assertIn("Music Assistant", salida)
        self.assertIn("Televisión", salida)

    async def test_sin_music_assistant_se_dice_que_falta(self):
        self.sin_reproductor_local()
        FakeHass.estados = {"media_player.salon":
                            altavoz("media_player.salon", "Sonos")}
        salida = await self.caja().run("poner_musica", {"que": "Queen"})
        self.assertIn("Music Assistant", salida)
        self.assertIn("favoritos", salida)
        self.assertEqual(FakeHass.peticiones, [], "no se llama a nada a ciegas")

    # -- carpeta local -----------------------------------------------------
    async def test_la_carpeta_de_musica_abre_el_fichero(self):
        caja = self.caja(con_hass=False, biblioteca=str(self.musica))
        salida = await caja.run("poner_musica", {"que": "bohemian rhapsody"})

        self.assertIn("Bohemian Rhapsody", salida)
        ultima = self.llamadas_playerctl()[-1]
        self.assertEqual(ultima[:3], ["-p", "vlc", "open"])
        self.assertTrue(ultima[3].startswith("file://"))
        self.assertIn("A%20Night%20at%20the%20Opera", ultima[3])

    async def test_lo_que_no_esta_en_la_carpeta(self):
        caja = self.caja(con_hass=False, biblioteca=str(self.musica))
        salida = await caja.run("poner_musica", {"que": "una canción inventada"})
        self.assertIn("No encuentro", salida)

    async def test_sin_carpeta_se_dice_que_falta(self):
        salida = await self.caja(con_hass=False).run("poner_musica",
                                                     {"que": "queen"})
        self.assertIn("biblioteca", salida)

    # -- comunes -----------------------------------------------------------
    async def test_sin_decir_que(self):
        self.assertIn("¿Qué pongo?",
                      await self.caja(con_hass=False).run("poner_musica",
                                                          {"que": "  "}))

    async def test_tras_leer_contenido_externo_no_pone_nada(self):
        caja = self.caja(con_hass=False, favoritos={"Radio 3": "https://x/r3"})
        caja._external("un correo cualquiera")
        salida = await caja.run("poner_musica", {"que": "radio 3"})
        self.assertIn("contenido de fuera", salida)
        self.assertEqual(self.llamadas_playerctl(), [])

    async def test_sin_nada_configurado_no_se_ofrece(self):
        caja = self.caja(con_hass=False, playerctl="playerctl-que-no-existe")
        self.assertNotIn("poner_musica",
                         [s["name"] for s in caja.definitions()])

    async def test_con_algo_si_se_ofrece(self):
        self.assertIn("poner_musica",
                      [s["name"] for s in self.caja(con_hass=False).definitions()])


if __name__ == "__main__":
    unittest.main()
