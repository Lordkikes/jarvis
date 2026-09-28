"""Control de música: el reproductor local y los altavoces de Home Assistant.

El `playerctl` de estas pruebas es un guion de verdad puesto en el PATH: se
comprueba la conversación real por subproceso, con sus códigos de salida y su
salida por consola, no una función parcheada que siempre dice que sí.
"""
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
from jarvis.musica import (  # noqa: E402
    ACCIONES, Local, busca, desde_home_assistant, elige, en_palabras, suena,
)

FALSO = r"""#!/usr/bin/env python3
import json, os, sys
ESTADO = os.environ["PLAYERCTL_ESTADO"]
REGISTRO = os.environ["PLAYERCTL_REGISTRO"]

args = sys.argv[1:]
with open(REGISTRO, "a", encoding="utf-8") as f:
    f.write(json.dumps(args) + "\n")

datos = json.loads(open(ESTADO, encoding="utf-8").read())
if args and args[0] in ("-p", "--player"):
    nombre, args = args[1], args[2:]
else:
    nombre = datos["reproductores"][0] if datos["reproductores"] else ""

if args and args[0] == "--list-all":
    print("\n".join(datos["reproductores"]))
    sys.exit(0)
if not datos["reproductores"] or nombre not in datos["reproductores"]:
    print("No players found", file=sys.stderr)
    sys.exit(1)

suyo = datos["estados"][nombre]
orden = args[0] if args else ""
if orden == "status":
    print(suyo["status"])
elif orden == "metadata":
    formato = args[args.index("--format") + 1]
    print(formato.replace("{{title}}", suyo["title"])
                 .replace("{{artist}}", suyo["artist"]))
elif orden == "volume":
    if len(args) > 1:
        nivel = args[1]
        if nivel.endswith(("+", "-")):
            paso = float(nivel[:-1]) * (1 if nivel[-1] == "+" else -1)
            suyo["volume"] = max(0.0, min(1.0, suyo["volume"] + paso))
        else:
            suyo["volume"] = float(nivel)
    else:
        print(suyo["volume"])
elif orden == "open":
    suyo["opened"] = args[1] if len(args) > 1 else ""
    suyo["status"] = "Playing"
elif orden in ("play", "pause", "play-pause", "stop", "next", "previous"):
    suyo["status"] = {"play": "Playing", "pause": "Paused", "stop": "Stopped",
                      "next": "Playing", "previous": "Playing"}.get(
        orden, "Paused" if suyo["status"] == "Playing" else "Playing")
else:
    print("unknown command", file=sys.stderr)
    sys.exit(1)
open(ESTADO, "w", encoding="utf-8").write(json.dumps(datos))
"""


def pista(titulo="Bohemian Rhapsody", artista="Queen", status="Playing",
          volumen=0.6) -> dict:
    return {"title": titulo, "artist": artista, "status": status,
            "volume": volumen}


class CasoConPlayerctl(unittest.TestCase):
    """Monta un `playerctl` falso, ejecutable, en un PATH temporal."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        raiz = Path(self.tmp.name)
        self.binario = raiz / "playerctl"
        self.binario.write_text(FALSO, encoding="utf-8")
        self.binario.chmod(self.binario.stat().st_mode | stat.S_IEXEC)
        self.estado_json = raiz / "estado.json"
        self.registro = raiz / "registro.log"
        self.pon({"reproductores": ["spotify"],
                  "estados": {"spotify": pista()}})
        self.entorno = mock.patch.dict(os.environ, {
            "PATH": f"{raiz}{os.pathsep}{os.environ['PATH']}",
            "PLAYERCTL_ESTADO": str(self.estado_json),
            "PLAYERCTL_REGISTRO": str(self.registro)})
        self.entorno.start()

    def tearDown(self):
        self.entorno.stop()
        self.tmp.cleanup()

    def pon(self, datos: dict) -> None:
        self.estado_json.write_text(json.dumps(datos), encoding="utf-8")

    def lee(self) -> dict:
        return json.loads(self.estado_json.read_text(encoding="utf-8"))

    def llamadas(self) -> list[list[str]]:
        if not self.registro.exists():
            return []
        return [json.loads(l) for l in
                self.registro.read_text(encoding="utf-8").splitlines() if l]


class TestLocal(CasoConPlayerctl):
    def test_se_detecta_si_esta_instalado(self):
        self.assertTrue(Local().disponible)
        self.assertFalse(Local("playerctl-que-no-existe").disponible)

    def test_lista_los_reproductores(self):
        self.pon({"reproductores": ["spotify", "vlc"],
                  "estados": {"spotify": pista(), "vlc": pista(status="Paused")}})
        self.assertEqual(Local().reproductores(), ["spotify", "vlc"])

    def test_el_estado_trae_pista_artista_y_volumen(self):
        estado = Local().estado()
        self.assertEqual(estado["fuente"], "local")
        self.assertEqual(estado["titulo"], "Bohemian Rhapsody")
        self.assertEqual(estado["artista"], "Queen")
        self.assertEqual(estado["estado"], "playing")
        self.assertEqual(estado["volumen"], 60)

    def test_sin_ningun_reproductor_no_hay_estado(self):
        self.pon({"reproductores": [], "estados": {}})
        self.assertIsNone(Local().estado())

    def test_pausar(self):
        self.assertTrue(Local().ejecuta("pausa"))
        self.assertEqual(self.lee()["estados"]["spotify"]["status"], "Paused")

    def test_alternar(self):
        Local().ejecuta("alternar")
        self.assertEqual(self.lee()["estados"]["spotify"]["status"], "Paused")

    def test_poner_volumen_en_porcentaje(self):
        self.assertTrue(Local().ejecuta("volumen", volumen=35))
        self.assertAlmostEqual(self.lee()["estados"]["spotify"]["volume"], 0.35)

    def test_subir_y_bajar(self):
        Local().ejecuta("subir")
        self.assertAlmostEqual(self.lee()["estados"]["spotify"]["volume"], 0.7)
        Local().ejecuta("bajar")
        self.assertAlmostEqual(self.lee()["estados"]["spotify"]["volume"], 0.6)

    def test_se_puede_apuntar_a_un_reproductor(self):
        self.pon({"reproductores": ["spotify", "vlc"],
                  "estados": {"spotify": pista(), "vlc": pista(status="Paused")}})
        Local().ejecuta("reproducir", reproductor="vlc")
        self.assertEqual(self.lee()["estados"]["vlc"]["status"], "Playing")
        self.assertEqual(self.lee()["estados"]["spotify"]["status"], "Playing")
        self.assertIn(["-p", "vlc", "play"], self.llamadas())

    def test_abrir_un_fichero_o_una_url(self):
        self.assertTrue(Local().abre("https://ejemplo/radio.mp3"))
        self.assertEqual(self.lee()["estados"]["spotify"]["opened"],
                         "https://ejemplo/radio.mp3")
        self.assertIn(["open", "https://ejemplo/radio.mp3"], self.llamadas())

    def test_una_orden_que_no_entiende_devuelve_falso(self):
        self.assertFalse(Local().ejecuta("achicharrar"))

    def test_si_el_binario_no_esta_no_revienta(self):
        fantasma = Local("playerctl-que-no-existe")
        self.assertFalse(fantasma.ejecuta("pausa"))
        self.assertIsNone(fantasma.estado())
        self.assertEqual(fantasma.reproductores(), [])


class TestFormato(unittest.TestCase):
    def test_un_media_player_de_home_assistant_se_traduce(self):
        traducido = desde_home_assistant({
            "entity_id": "media_player.salon", "state": "playing",
            "attributes": {"friendly_name": "Sonos del salón",
                           "media_title": "Blackbird", "media_artist": "The Beatles",
                           "volume_level": 0.25}})
        self.assertEqual(traducido["nombre"], "Sonos del salón")
        self.assertEqual(traducido["volumen"], 25)
        self.assertTrue(suena(traducido))

    def test_sin_artista_concreto_vale_el_del_album(self):
        traducido = desde_home_assistant({
            "entity_id": "media_player.x", "state": "playing",
            "attributes": {"media_album_artist": "Varios"}})
        self.assertEqual(traducido["artista"], "Varios")

    def test_lo_que_suena_se_dice_entero(self):
        dicho = en_palabras({"nombre": "Sonos", "estado": "playing",
                             "titulo": "Blackbird", "artista": "The Beatles",
                             "volumen": 25})
        self.assertIn("Suena Blackbird, de The Beatles", dicho)
        self.assertIn("en Sonos", dicho)
        self.assertIn("25 por ciento", dicho)

    def test_en_pausa_se_dice_distinto(self):
        self.assertIn("En pausa", en_palabras({"nombre": "Sonos",
                                               "estado": "paused",
                                               "titulo": "Blackbird"}))

    def test_sin_metadatos_se_admite(self):
        self.assertIn("no sé qué", en_palabras({"nombre": "VLC",
                                                "estado": "playing"}))

    def test_parado(self):
        self.assertIn("parado", en_palabras({"nombre": "VLC", "estado": "stopped"}))

    def test_sin_nada(self):
        self.assertIn("nada sonando", en_palabras(None))


class TestAQuienSeLeHabla(unittest.TestCase):
    """Sin decir dónde, manda lo que está sonando."""

    def reproductor(self, nombre, estado):
        return {"nombre": nombre, "estado": estado, "fuente": "x"}

    def test_lo_que_suena_gana_a_lo_que_esta_en_pausa(self):
        elegido = elige([self.reproductor("escritorio", "paused"),
                         self.reproductor("salón", "playing")])
        self.assertEqual(elegido["nombre"], "salón")

    def test_si_nada_suena_manda_lo_que_esta_en_pausa(self):
        elegido = elige([self.reproductor("cocina", "idle"),
                         self.reproductor("escritorio", "paused")])
        self.assertEqual(elegido["nombre"], "escritorio")

    def test_si_no_hay_ni_eso_el_primero(self):
        self.assertEqual(elige([self.reproductor("cocina", "idle")])["nombre"],
                         "cocina")

    def test_sin_candidatos_ninguno(self):
        self.assertIsNone(elige([]))

    def test_buscar_por_nombre_sin_tildes(self):
        candidatos = [self.reproductor("Sonos del salón", "playing"),
                      self.reproductor("Cocina", "idle")]
        self.assertEqual(busca(candidatos, "salon")[0]["nombre"], "Sonos del salón")

    def test_lo_exacto_gana(self):
        candidatos = [self.reproductor("Salón", "idle"),
                      self.reproductor("Salón de arriba", "idle")]
        self.assertEqual([c["nombre"] for c in busca(candidatos, "salón")], ["Salón"])


class FakeHass(BaseHTTPRequestHandler):
    estados: dict = {}
    peticiones: list = []

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
        else:
            entity_id = self.path.rsplit("/", 1)[-1]
            self._responde(FakeHass.estados.get(entity_id) or {}, 200)

    def do_POST(self):  # noqa: N802
        largo = int(self.headers.get("Content-Length", 0))
        datos = json.loads(self.rfile.read(largo).decode()) if largo else {}
        _, _, _, dominio, servicio = self.path.split("/")
        FakeHass.peticiones.append({"servicio": f"{dominio}.{servicio}",
                                    "datos": datos})
        entidad = FakeHass.estados.get(datos.get("entity_id", ""))
        if entidad is not None:
            if servicio == "media_pause":
                entidad["state"] = "paused"
            elif servicio == "media_play":
                entidad["state"] = "playing"
            elif servicio == "volume_set":
                entidad["attributes"]["volume_level"] = datos["volume_level"]
        self._responde([entidad or {}])


def altavoz(entity_id, nombre, estado="playing", **atributos) -> dict:
    return {"entity_id": entity_id, "state": estado,
            "attributes": {"friendly_name": nombre, "media_title": "Blackbird",
                           "media_artist": "The Beatles", "volume_level": 0.5,
                           **atributos}}


class TestHerramientas(CasoConPlayerctl, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        CasoConPlayerctl.setUp(self)
        FakeHass.estados = {}
        FakeHass.peticiones = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeHass)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        CasoConPlayerctl.tearDown(self)

    def caja(self, con_hass=True, playerctl="playerctl") -> Toolbox:
        herramientas = {"allow_system": True, "playerctl": playerctl,
                        "notes_file": str(Path(self.tmp.name) / "n.json"),
                        "memory_file": str(Path(self.tmp.name) / "m.json"),
                        "lists_file": str(Path(self.tmp.name) / "l.json")}
        if con_hass:
            herramientas["home_assistant"] = {"url": self.base}
        cfg = Config({"tools": herramientas, "llm": {"web_search": False}})
        with mock.patch.dict(os.environ,
                             {"JARVIS_HASS_TOKEN": "tok" if con_hass else ""}):
            return Toolbox(cfg, EventBus())

    async def test_solo_con_el_equipo(self):
        salida = await self.caja(con_hass=False).run("que_suena", {})
        self.assertIn("Bohemian Rhapsody", salida)

    async def test_pausar_el_equipo(self):
        salida = await self.caja(con_hass=False).run("controlar_musica",
                                                     {"accion": "pausa"})
        self.assertIn("Hecho", salida)
        self.assertEqual(self.lee()["estados"]["spotify"]["status"], "Paused")

    async def test_el_altavoz_que_suena_gana_al_equipo_en_pausa(self):
        """Si el salón suena, «pausa» tiene que parar el salón."""
        FakeHass.estados = {"media_player.salon":
                            altavoz("media_player.salon", "Sonos del salón")}
        self.pon({"reproductores": ["spotify"],
                  "estados": {"spotify": pista(status="Paused")}})

        salida = await self.caja().run("controlar_musica", {"accion": "pausa"})
        self.assertIn("Sonos del salón", salida)
        self.assertEqual(FakeHass.peticiones[-1]["servicio"],
                         "media_player.media_pause")
        self.assertEqual(self.lee()["estados"]["spotify"]["status"], "Paused",
                         "el equipo no se toca")

    async def test_decir_donde_manda(self):
        FakeHass.estados = {"media_player.salon":
                            altavoz("media_player.salon", "Sonos del salón")}
        salida = await self.caja().run("controlar_musica",
                                       {"accion": "pausa", "donde": "spotify"})
        self.assertIn("spotify", salida.lower())
        self.assertEqual(self.lee()["estados"]["spotify"]["status"], "Paused")

    async def test_el_volumen_va_de_cero_a_uno_en_home_assistant(self):
        FakeHass.estados = {"media_player.salon":
                            altavoz("media_player.salon", "Sonos del salón")}
        salida = await self.caja().run("controlar_musica",
                                       {"accion": "volumen", "volumen": 30,
                                        "donde": "sonos"})
        self.assertIn("30 por ciento", salida)
        self.assertEqual(FakeHass.peticiones[-1]["datos"]["volume_level"], 0.3)

    async def test_pedir_volumen_sin_decir_cuanto(self):
        self.assertIn("¿A qué volumen?",
                      await self.caja(con_hass=False).run("controlar_musica",
                                                          {"accion": "volumen"}))

    async def test_un_altavoz_apagado_no_cuenta(self):
        FakeHass.estados = {"media_player.tele":
                            altavoz("media_player.tele", "Televisión", estado="off")}
        self.pon({"reproductores": [], "estados": {}})
        salida = await self.caja().run("que_suena", {})
        self.assertIn("No encuentro", salida)

    async def test_un_nombre_que_no_existe(self):
        salida = await self.caja(con_hass=False).run(
            "controlar_musica", {"accion": "pausa", "donde": "el tocadiscos"})
        self.assertIn("No encuentro", salida)

    async def test_si_hay_varios_que_encajan_pregunta(self):
        FakeHass.estados = {
            "media_player.salon": altavoz("media_player.salon", "Sonos salón"),
            "media_player.salon2": altavoz("media_player.salon2", "Sonos salón grande"),
        }
        salida = await self.caja().run("controlar_musica",
                                       {"accion": "pausa", "donde": "sonos"})
        self.assertIn("Hay varios", salida)
        self.assertEqual(FakeHass.peticiones, [])

    async def test_una_accion_inventada(self):
        self.assertIn("No sé qué es",
                      await self.caja(con_hass=False).run("controlar_musica",
                                                          {"accion": "bailar"}))

    async def test_tras_leer_contenido_externo_no_toca_nada(self):
        caja = self.caja(con_hass=False)
        caja._external("un correo cualquiera")
        salida = await caja.run("controlar_musica", {"accion": "pausa"})
        self.assertIn("contenido de fuera", salida)
        self.assertEqual(self.lee()["estados"]["spotify"]["status"], "Playing")

    async def test_preguntar_que_suena_no_es_tocar(self):
        caja = self.caja(con_hass=False)
        await caja.run("que_suena", {})
        self.assertFalse(caja.external_content_seen)

    async def test_sin_playerctl_ni_home_assistant_no_se_ofrecen(self):
        caja = self.caja(con_hass=False, playerctl="playerctl-que-no-existe")
        nombres = [spec["name"] for spec in caja.definitions()]
        self.assertNotIn("controlar_musica", nombres)
        self.assertIn("No tengo ni Home Assistant",
                      await caja.run("controlar_musica", {"accion": "pausa"}))

    async def test_con_solo_playerctl_ya_se_ofrecen(self):
        nombres = [spec["name"] for spec in self.caja(con_hass=False).definitions()]
        self.assertIn("controlar_musica", nombres)
        self.assertIn("que_suena", nombres)

    async def test_todas_las_acciones_tienen_traduccion(self):
        for accion, (servicio, _) in ACCIONES.items():
            self.assertTrue(servicio.startswith(("media_", "volume_")), accion)


if __name__ == "__main__":
    unittest.main()
