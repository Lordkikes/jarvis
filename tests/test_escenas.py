"""Escenas: activarlas, guardarlas y que sobrevivan a un reinicio del servidor."""
from __future__ import annotations

import json
import os
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
from jarvis.escenas import Escenas, instantanea, normaliza  # noqa: E402
from jarvis.llm.tools import Toolbox  # noqa: E402


def entidad(entity_id, estado, nombre="", **atributos) -> dict:
    return {"entity_id": entity_id, "state": estado,
            "attributes": {"friendly_name": nombre or entity_id, **atributos}}


ESTADOS = [
    entidad("light.salon", "on", "Luz del salón", brightness=40,
            color_temp_kelvin=2200),
    entidad("light.cocina", "off", "Luz de la cocina"),
    entidad("switch.cafetera", "off", "Cafetera"),
    entidad("climate.salon", "heat", "Termostato", temperature=21),
    entidad("media_player.tele", "playing", "Televisión"),
    entidad("light.pasillo", "unavailable", "Luz del pasillo"),
    entidad("scene.buenas_noches", "unknown", "Buenas noches"),
    entidad("script.llegada", "off", "Llegando a casa"),
]


class TestInstantanea(unittest.TestCase):
    def setUp(self):
        self.foto = instantanea(ESTADOS)

    def test_guarda_el_estado_y_los_atributos_que_importan(self):
        self.assertEqual(self.foto["light.salon"],
                         {"state": "on", "brightness": 40,
                          "color_temp_kelvin": 2200})

    def test_lo_apagado_tambien_entra(self):
        """Una escena que solo enciende no sirve para volver a como estaba."""
        self.assertEqual(self.foto["light.cocina"], {"state": "off"})

    def test_de_lo_apagado_no_se_guarda_el_brillo(self):
        foto = instantanea([entidad("light.x", "off", brightness=200)])
        self.assertEqual(foto["light.x"], {"state": "off"})

    def test_lo_que_no_responde_se_deja_fuera(self):
        self.assertNotIn("light.pasillo", self.foto)

    def test_sin_entidades_no_hay_foto(self):
        self.assertEqual(instantanea([]), {})


class TestAlmacen(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fichero = Path(self.tmp.name) / "escenas.json"
        self.escenas = Escenas(self.fichero)

    def tearDown(self):
        self.tmp.cleanup()

    def test_guardar_y_recuperar(self):
        self.escenas.guarda("Modo cine", {"light.salon": {"state": "off"}})
        self.assertEqual(self.escenas.ver("modo cine")["nombre"], "Modo cine")
        self.assertEqual(self.escenas.nombres(), ["Modo cine"])

    def test_el_nombre_no_distingue_tildes(self):
        self.escenas.guarda("Cena romántica", {"light.x": {"state": "on"}})
        self.assertTrue(self.escenas.existe("cena romantica"))

    def test_volver_a_guardar_pisa(self):
        self.escenas.guarda("Cine", {"light.a": {"state": "on"}})
        self.escenas.guarda("Cine", {"light.b": {"state": "off"}})
        self.assertEqual(list(self.escenas.ver("cine")["entidades"]), ["light.b"])

    def test_olvidar(self):
        self.escenas.guarda("Cine", {"light.a": {"state": "on"}})
        self.assertTrue(self.escenas.olvida("cine"))
        self.assertEqual(self.escenas.nombres(), [])
        self.assertFalse(self.escenas.olvida("cine"))

    def test_buscar_exacto_antes_que_parcial(self):
        self.escenas.guarda("Cine", {"light.a": {"state": "on"}})
        self.escenas.guarda("Cine de tarde", {"light.b": {"state": "on"}})
        self.assertEqual([e["nombre"] for e in self.escenas.busca("cine")],
                         ["Cine"])
        self.assertEqual(len(self.escenas.busca("cine de")), 1)

    def test_sobrevive_al_reinicio(self):
        self.escenas.guarda("Cine", {"light.a": {"state": "on"}})
        self.assertTrue(Escenas(self.fichero).existe("cine"))

    def test_un_json_roto_no_revienta(self):
        self.fichero.write_text("{roto", encoding="utf-8")
        self.assertEqual(self.escenas.nombres(), [])
        self.escenas.guarda("Cine", {"light.a": {"state": "on"}})
        self.assertEqual(self.escenas.nombres(), ["Cine"])

    def test_hay_un_tope(self):
        from jarvis.escenas import MAX_ESCENAS

        for n in range(MAX_ESCENAS):
            self.escenas.guarda(f"escena {n}", {"light.a": {"state": "on"}})
        resultado = self.escenas.guarda("una más", {"light.a": {"state": "on"}})
        self.assertFalse(resultado["ok"])
        # Pisar una que ya existe sí se puede, aunque se esté en el tope.
        self.assertTrue(self.escenas.guarda("escena 0",
                                            {"light.b": {"state": "on"}})["ok"])

    def test_normalizar(self):
        self.assertEqual(normaliza("  Modo   CINE "), "modo cine")


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
        elif self.path == "/api/services":
            self._responde([{"domain": "scene",
                             "services": {"turn_on": {}, "apply": {}}}])
        else:
            self._responde(FakeHass.estados.get(self.path.rsplit("/", 1)[-1]) or {})

    def do_POST(self):  # noqa: N802
        largo = int(self.headers.get("Content-Length", 0))
        datos = json.loads(self.rfile.read(largo).decode()) if largo else {}
        _, _, _, dominio, servicio = self.path.split("/")
        FakeHass.peticiones.append({"servicio": f"{dominio}.{servicio}",
                                    "datos": datos})
        # `scene.apply` cambia de verdad lo que le mandan.
        for entity_id, quiere in (datos.get("entities") or {}).items():
            if (actual := FakeHass.estados.get(entity_id)) is not None:
                actual["state"] = quiere.get("state", actual["state"])
                for clave, valor in quiere.items():
                    if clave != "state":
                        actual["attributes"][clave] = valor
        self._responde([])


class TestHerramientas(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        FakeHass.estados = {e["entity_id"]: json.loads(json.dumps(e))
                            for e in ESTADOS}
        FakeHass.peticiones = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeHass)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.tmp = tempfile.TemporaryDirectory()
        self.escenas_json = Path(self.tmp.name) / "escenas.json"

    def tearDown(self):
        self.server.shutdown()
        self.tmp.cleanup()

    def caja(self, con_hass=True) -> Toolbox:
        herramientas = {
            "allow_system": True,
            "notes_file": str(Path(self.tmp.name) / "n.json"),
            "memory_file": str(Path(self.tmp.name) / "m.json"),
            "lists_file": str(Path(self.tmp.name) / "l.json"),
            "scenes_file": str(self.escenas_json),
        }
        if con_hass:
            herramientas["home_assistant"] = {"url": self.base}
        cfg = Config({"tools": herramientas, "llm": {"web_search": False}})
        with mock.patch.dict(os.environ,
                             {"JARVIS_HASS_TOKEN": "tok" if con_hass else ""}):
            return Toolbox(cfg, EventBus())

    def estado(self, entity_id: str) -> str:
        return FakeHass.estados[entity_id]["state"]

    # -- activar -----------------------------------------------------------
    async def test_activar_una_escena_de_home_assistant(self):
        salida = await self.caja().run("activar_escena", {"cual": "buenas noches"})
        self.assertIn("Buenas noches", salida)
        self.assertEqual(FakeHass.peticiones[-1]["servicio"], "scene.turn_on")
        self.assertEqual(FakeHass.peticiones[-1]["datos"]["entity_id"],
                         "scene.buenas_noches")

    async def test_un_guion_tambien_vale(self):
        """Para una persona, «llegando a casa» es una escena aunque sea script."""
        salida = await self.caja().run("activar_escena", {"cual": "llegando"})
        self.assertIn("Llegando a casa", salida)
        self.assertEqual(FakeHass.peticiones[-1]["servicio"], "script.turn_on")

    async def test_la_que_no_existe(self):
        salida = await self.caja().run("activar_escena", {"cual": "modo fiesta"})
        self.assertIn("No encuentro", salida)

    async def test_sin_nombre_las_enumera(self):
        caja = self.caja()
        await caja.run("guardar_escena", {"nombre": "Modo cine"})
        salida = await caja.run("activar_escena", {})
        self.assertIn("Modo cine", salida)
        self.assertIn("Buenas noches", salida)

    async def test_sin_ninguna_escena_lo_dice(self):
        FakeHass.estados = {k: v for k, v in FakeHass.estados.items()
                            if not k.startswith(("scene.", "script."))}
        salida = await self.caja().run("activar_escena", {})
        self.assertIn("guarda esto como", salida)

    # -- guardar y recuperar ----------------------------------------------
    async def test_guardar_recoge_la_luz_de_ahora(self):
        salida = await self.caja().run("guardar_escena", {"nombre": "Modo cine"})
        self.assertIn("Guardada", salida)

        guardada = Escenas(self.escenas_json).ver("modo cine")["entidades"]
        self.assertEqual(guardada["light.salon"]["brightness"], 40)
        self.assertEqual(guardada["light.cocina"]["state"], "off")

    async def test_la_tele_no_entra_en_una_escena(self):
        await self.caja().run("guardar_escena", {"nombre": "Cine"})
        guardada = Escenas(self.escenas_json).ver("cine")["entidades"]
        self.assertNotIn("media_player.tele", guardada)
        self.assertIn("climate.salon", guardada)

    async def test_recuperarla_devuelve_la_casa_a_como_estaba(self):
        caja = self.caja()
        await caja.run("guardar_escena", {"nombre": "Modo cine"})

        # Alguien cambia las luces después de guardar.
        FakeHass.estados["light.salon"]["state"] = "off"
        FakeHass.estados["light.cocina"]["state"] = "on"

        salida = await caja.run("activar_escena", {"cual": "modo cine"})
        self.assertIn("Modo cine", salida)
        self.assertEqual(self.estado("light.salon"), "on")
        self.assertEqual(self.estado("light.cocina"), "off")
        self.assertEqual(FakeHass.estados["light.salon"]["attributes"]["brightness"],
                         40)

    async def test_se_recupera_con_apply_y_sin_entity_id(self):
        """`scene.apply` recibe los estados; por eso no hace falta el servidor."""
        caja = self.caja()
        await caja.run("guardar_escena", {"nombre": "Cine"})
        await caja.run("activar_escena", {"cual": "cine"})

        envio = FakeHass.peticiones[-1]
        self.assertEqual(envio["servicio"], "scene.apply")
        self.assertIn("entities", envio["datos"])
        self.assertNotIn("entity_id", envio["datos"],
                         "scene.apply no apunta a ninguna entidad")

    async def test_sobrevive_a_un_reinicio_de_home_assistant(self):
        """Lo que `scene.create` no da: la instantánea la tenemos nosotros."""
        await self.caja().run("guardar_escena", {"nombre": "Modo cine"})
        # Se reinicia el servidor: ninguna escena dinámica sobreviviría.
        FakeHass.estados = {k: v for k, v in FakeHass.estados.items()
                            if not k.startswith("scene.")}
        FakeHass.estados["light.salon"]["state"] = "off"

        salida = await self.caja().run("activar_escena", {"cual": "modo cine"})
        self.assertIn("Modo cine", salida)
        self.assertEqual(self.estado("light.salon"), "on")

    async def test_la_tuya_manda_sobre_la_del_servidor(self):
        caja = self.caja()
        await caja.run("guardar_escena", {"nombre": "Buenas noches"})
        await caja.run("activar_escena", {"cual": "buenas noches"})
        self.assertEqual(FakeHass.peticiones[-1]["servicio"], "scene.apply")

    # -- pisar y borrar ----------------------------------------------------
    async def test_guardar_una_nueva_no_pide_permiso(self):
        salida = await self.caja().run("guardar_escena", {"nombre": "Nueva"})
        self.assertIn("Guardada", salida)

    async def test_pisar_una_que_existe_si(self):
        caja = self.caja()
        await caja.run("guardar_escena", {"nombre": "Cine"})
        FakeHass.estados["light.salon"]["state"] = "off"

        salida = await caja.run("guardar_escena", {"nombre": "Cine"})
        self.assertIn("Sin guardar todavía", salida)
        self.assertEqual(
            Escenas(self.escenas_json).ver("cine")["entidades"]["light.salon"]["state"],
            "on", "la de antes sigue intacta")

    async def test_y_confirmando_se_pisa(self):
        caja = self.caja()
        await caja.run("guardar_escena", {"nombre": "Cine"})
        FakeHass.estados["light.salon"]["state"] = "off"
        await caja.run("guardar_escena", {"nombre": "Cine"})
        salida = await caja.run("guardar_escena", {"nombre": "Cine",
                                                   "confirmar": True})
        self.assertIn("Guardada", salida)
        self.assertEqual(
            Escenas(self.escenas_json).ver("cine")["entidades"]["light.salon"]["state"],
            "off")

    async def test_olvidar_pide_confirmacion(self):
        caja = self.caja()
        await caja.run("guardar_escena", {"nombre": "Cine"})
        salida = await caja.run("olvidar_escena", {"nombre": "cine"})
        self.assertIn("Sin borrar todavía", salida)
        self.assertTrue(Escenas(self.escenas_json).existe("cine"))

    async def test_y_confirmando_se_borra(self):
        caja = self.caja()
        await caja.run("guardar_escena", {"nombre": "Cine"})
        await caja.run("olvidar_escena", {"nombre": "cine"})
        salida = await caja.run("olvidar_escena", {"nombre": "cine",
                                                   "confirmar": True})
        self.assertIn("Olvidada", salida)
        self.assertFalse(Escenas(self.escenas_json).existe("cine"))

    async def test_no_se_pueden_borrar_las_de_home_assistant(self):
        salida = await self.caja().run("olvidar_escena",
                                       {"nombre": "buenas noches"})
        self.assertIn("no las toco", salida)

    # -- comunes -----------------------------------------------------------
    async def test_tras_leer_contenido_externo_no_activa(self):
        caja = self.caja()
        caja._external("un correo cualquiera")
        salida = await caja.run("activar_escena", {"cual": "buenas noches"})
        self.assertIn("contenido de fuera", salida)
        self.assertEqual(FakeHass.peticiones, [])

    async def test_enumerar_no_es_actuar(self):
        caja = self.caja()
        caja._external("un correo cualquiera")
        salida = await caja.run("activar_escena", {})
        self.assertIn("Buenas noches", salida)

    async def test_sin_home_assistant_no_se_ofrecen(self):
        caja = self.caja(con_hass=False)
        nombres = [s["name"] for s in caja.definitions()]
        for herramienta in ("activar_escena", "guardar_escena", "olvidar_escena"):
            self.assertNotIn(herramienta, nombres)
        self.assertIn("No tengo Home Assistant",
                      await caja.run("activar_escena", {"cual": "x"}))

    async def test_con_home_assistant_si(self):
        nombres = [s["name"] for s in self.caja().definitions()]
        for herramienta in ("activar_escena", "guardar_escena", "olvidar_escena"):
            self.assertIn(herramienta, nombres)


if __name__ == "__main__":
    unittest.main()
