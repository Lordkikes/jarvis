"""Domótica: cliente de Home Assistant y herramientas, contra un HA falso.

El servidor de estas pruebas guarda los estados y los cambia cuando le llaman
a un servicio, así que se comprueba el efecto y no solo que salga la petición.
"""
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
from jarvis.domotica import (  # noqa: E402
    DOMINIOS_SEGUROS, HomeAssistant, SinConexion, dominio_de, en_palabras,
    nombre_de, normaliza, servicio_para,
)
from jarvis.llm.tools import Toolbox  # noqa: E402


def entidad(entity_id: str, estado: str, nombre: str = "", **atributos) -> dict:
    return {"entity_id": entity_id, "state": estado,
            "attributes": {"friendly_name": nombre or entity_id, **atributos}}


ESTADOS = [
    entidad("light.salon", "off", "Luz del salón"),
    entidad("light.salon_pie", "on", "Lámpara de pie del salón", brightness=128),
    entidad("light.cocina", "off", "Luz de la cocina"),
    entidad("switch.cafetera", "off", "Cafetera"),
    entidad("climate.salon", "heat", "Termostato", temperature=21,
            current_temperature=19.5),
    entidad("cover.garaje", "closed", "Puerta del garaje"),
    entidad("lock.entrada", "locked", "Puerta de la calle"),
    entidad("sensor.humedad", "54", "Humedad", unit_of_measurement="%"),
]


class TestAyudantes(unittest.TestCase):
    def test_el_dominio_sale_del_entity_id(self):
        self.assertEqual(dominio_de("light.salon"), "light")
        self.assertEqual(dominio_de(""), "")

    def test_el_nombre_visible_gana_al_identificador(self):
        self.assertEqual(nombre_de(ESTADOS[0]), "Luz del salón")

    def test_sin_nombre_visible_se_apaña_con_el_id(self):
        self.assertEqual(nombre_de({"entity_id": "light.salon_grande"}),
                         "salon grande")

    def test_cada_dominio_llama_a_su_servicio(self):
        """«Abre la persiana» no puede acabar en un turn_on."""
        self.assertEqual(servicio_para("encender", "light"), "turn_on")
        self.assertEqual(servicio_para("abrir", "cover"), "open_cover")
        self.assertEqual(servicio_para("cerrar", "cover"), "close_cover")
        self.assertEqual(servicio_para("abrir", "lock"), "unlock")
        self.assertEqual(servicio_para("parar", "cover"), "stop_cover")

    def test_encender_una_persiana_se_entiende_como_abrirla(self):
        self.assertEqual(servicio_para("encender", "cover"), "open_cover")

    def test_una_accion_inventada_no_llama_a_nada(self):
        self.assertIsNone(servicio_para("achicharrar", "light"))

    def test_los_estados_se_dicen_en_castellano(self):
        self.assertIn("apagada", en_palabras(ESTADOS[0]))
        self.assertIn("cerrada con llave", en_palabras(ESTADOS[6]))

    def test_el_brillo_se_dice_en_porcentaje(self):
        self.assertIn("50 por ciento", en_palabras(ESTADOS[1]))

    def test_un_sensor_lleva_su_unidad(self):
        self.assertIn("54 %", en_palabras(ESTADOS[7]))

    def test_un_termostato_dice_las_dos_temperaturas(self):
        dicho = en_palabras(ESTADOS[4])
        self.assertIn("21", dicho)
        self.assertIn("19.5", dicho)

    def test_normalizar(self):
        self.assertEqual(normaliza("  Luz del SALÓN "), "luz del salon")


class FakeHass(BaseHTTPRequestHandler):
    estados: dict = {}
    peticiones: list = []
    status: int | None = None

    def log_message(self, *args):
        pass

    def _responde(self, payload, status=200):
        cuerpo = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)

    def _registra(self, cuerpo=""):
        FakeHass.peticiones.append({
            "metodo": self.command, "ruta": self.path,
            "auth": self.headers.get("Authorization", ""),
            "cuerpo": json.loads(cuerpo) if cuerpo else None,
        })

    def do_GET(self):  # noqa: N802
        self._registra()
        if FakeHass.status:
            self._responde({"message": "no"}, FakeHass.status)
            return
        if self.path == "/api/states":
            self._responde(list(FakeHass.estados.values()))
            return
        if self.path.startswith("/api/states/"):
            entity_id = self.path.rsplit("/", 1)[-1]
            if (datos := FakeHass.estados.get(entity_id)) is None:
                self._responde({"message": "no encontrado"}, 404)
            else:
                self._responde(datos)
            return
        self._responde({"message": "?"}, 404)

    def do_POST(self):  # noqa: N802
        largo = int(self.headers.get("Content-Length", 0))
        crudo = self.rfile.read(largo).decode() if largo else ""
        self._registra(crudo)
        if FakeHass.status:
            self._responde({"message": "no"}, FakeHass.status)
            return

        _, _, _, dominio, servicio = self.path.split("/")
        datos = json.loads(crudo) if crudo else {}
        entity_id = datos.get("entity_id", "")
        actual = FakeHass.estados.get(entity_id)
        if actual is None:
            self._responde({"message": "no existe"}, 400)
            return

        nuevo = {"turn_on": "on", "turn_off": "off",
                 "open_cover": "open", "close_cover": "closed",
                 "lock": "locked", "unlock": "unlocked"}.get(servicio)
        if servicio == "toggle":
            nuevo = "off" if actual["state"] == "on" else "on"
        if nuevo:
            actual["state"] = nuevo
        if "brightness_pct" in datos:
            actual["attributes"]["brightness"] = round(
                datos["brightness_pct"] / 100 * 255)
        if "temperature" in datos:
            actual["attributes"]["temperature"] = datos["temperature"]
        self._responde([actual])


class CasoConHass(unittest.TestCase):
    def setUp(self):
        FakeHass.estados = {e["entity_id"]: json.loads(json.dumps(e))
                            for e in ESTADOS}
        FakeHass.peticiones = []
        FakeHass.status = None
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeHass)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()

    def hass(self, **kwargs) -> HomeAssistant:
        return HomeAssistant(url=self.base, token="tok", **kwargs)

    def estado(self, entity_id: str) -> str:
        return FakeHass.estados[entity_id]["state"]


class TestCliente(CasoConHass):
    def test_lee_los_estados_con_su_token(self):
        entidades = self.hass().estados()
        self.assertTrue(entidades)
        self.assertEqual(FakeHass.peticiones[0]["auth"], "Bearer tok")

    def test_solo_salen_los_dominios_permitidos(self):
        """Una cerradura no existe para Jarvis si no se autoriza."""
        ids = [e["entity_id"] for e in self.hass().estados()]
        self.assertIn("light.salon", ids)
        self.assertNotIn("lock.entrada", ids)
        self.assertNotIn("cover.garaje", ids)

    def test_autorizar_un_dominio_delicado_lo_hace_aparecer(self):
        ids = [e["entity_id"] for e in
               self.hass(dominios=[*DOMINIOS_SEGUROS, "lock"]).estados()]
        self.assertIn("lock.entrada", ids)

    def test_el_mapa_se_cachea(self):
        hass = self.hass()
        hass.estados()
        hass.estados()
        self.assertEqual(len([p for p in FakeHass.peticiones
                              if p["ruta"] == "/api/states"]), 1)

    def test_pero_el_estado_de_uno_nunca(self):
        hass = self.hass()
        hass.estado("light.salon")
        hass.estado("light.salon")
        self.assertEqual(len(FakeHass.peticiones), 2)

    def test_llamar_a_un_servicio_cambia_el_estado(self):
        self.hass().llama("light", "turn_on", "light.salon")
        self.assertEqual(self.estado("light.salon"), "on")

    def test_el_entity_id_viaja_en_el_cuerpo(self):
        self.hass().llama("light", "turn_on", "light.salon", brightness_pct=40)
        envio = FakeHass.peticiones[-1]
        self.assertEqual(envio["metodo"], "POST")
        self.assertEqual(envio["ruta"], "/api/services/light/turn_on")
        self.assertEqual(envio["cuerpo"]["entity_id"], "light.salon")
        self.assertEqual(envio["cuerpo"]["brightness_pct"], 40)

    def test_un_token_malo_se_explica(self):
        FakeHass.status = 401
        with self.assertRaises(SinConexion) as caso:
            self.hass().estados()
        self.assertIn("token", str(caso.exception))

    def test_si_no_contesta_se_dice(self):
        self.server.shutdown()
        with self.assertRaises(SinConexion):
            HomeAssistant(url="http://127.0.0.1:1", token="t",
                          timeout=1.0).estados()

    def test_una_entidad_que_no_existe_da_none(self):
        self.assertIsNone(self.hass().estado("light.inventada"))

    def test_buscar_por_nombre(self):
        encontradas = self.hass().busca("luz de la cocina")
        self.assertEqual([e["entity_id"] for e in encontradas], ["light.cocina"])

    def test_buscar_sin_tildes(self):
        self.assertEqual([e["entity_id"] for e in self.hass().busca("luz del salon")],
                         ["light.salon"], "exacto gana a parcial")

    def test_una_busqueda_ambigua_devuelve_todo(self):
        encontradas = self.hass().busca("salón")
        self.assertGreater(len(encontradas), 1)

    def test_tambien_vale_el_entity_id(self):
        self.assertEqual([e["entity_id"] for e in self.hass().busca("light.cocina")],
                         ["light.cocina"])

    def test_si_no_encuentra_nada_reintenta_con_la_lista_fresca(self):
        """Un aparato recién dado de alta no puede quedar invisible."""
        hass = self.hass()
        hass.estados()
        FakeHass.estados["light.nueva"] = entidad("light.nueva", "off", "Luz nueva")
        self.assertEqual([e["entity_id"] for e in hass.busca("luz nueva")],
                         ["light.nueva"])


class TestHerramientas(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        FakeHass.estados = {e["entity_id"]: json.loads(json.dumps(e))
                            for e in ESTADOS}
        FakeHass.peticiones = []
        FakeHass.status = None
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeHass)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.server.shutdown()
        self.tmp.cleanup()

    def caja(self, dominios=None) -> Toolbox:
        cfg = Config({
            "tools": {"allow_system": True,
                      "notes_file": str(Path(self.tmp.name) / "n.json"),
                      "memory_file": str(Path(self.tmp.name) / "m.json"),
                      "lists_file": str(Path(self.tmp.name) / "l.json"),
                      "home_assistant": {"url": self.base,
                                         **({"dominios": dominios} if dominios else {})}},
            "llm": {"web_search": False},
        })
        with mock.patch.dict(os.environ, {"JARVIS_HASS_TOKEN": "tok"}):
            return Toolbox(cfg, EventBus())

    def estado(self, entity_id: str) -> str:
        return FakeHass.estados[entity_id]["state"]

    async def test_encender_una_luz_no_pide_confirmacion(self):
        """Pedirla para la lámpara haría el asistente insufrible."""
        salida = await self.caja().run("controlar_dispositivo",
                                       {"que": "luz de la cocina",
                                        "accion": "encender"})
        self.assertIn("Hecho", salida)
        self.assertEqual(self.estado("light.cocina"), "on")

    async def test_apagar(self):
        await self.caja().run("controlar_dispositivo",
                              {"que": "lámpara de pie", "accion": "apagar"})
        self.assertEqual(self.estado("light.salon_pie"), "off")

    async def test_el_brillo_llega_como_porcentaje(self):
        await self.caja().run("controlar_dispositivo",
                              {"que": "luz de la cocina", "accion": "encender",
                               "brillo": 40})
        self.assertEqual(FakeHass.peticiones[-1]["cuerpo"]["brightness_pct"], 40)

    async def test_la_temperatura_llama_a_otro_servicio(self):
        salida = await self.caja().run("controlar_dispositivo",
                                       {"que": "termostato", "accion": "encender",
                                        "temperatura": 22.5})
        self.assertIn("22.5 grados", salida)
        self.assertEqual(FakeHass.peticiones[-1]["ruta"],
                         "/api/services/climate/set_temperature")

    async def test_lo_que_no_se_encuentra_se_dice(self):
        salida = await self.caja().run("controlar_dispositivo",
                                       {"que": "el jacuzzi", "accion": "encender"})
        self.assertIn("No encuentro", salida)

    async def test_si_hay_varios_pregunta(self):
        salida = await self.caja().run("controlar_dispositivo",
                                       {"que": "salón", "accion": "apagar"})
        self.assertIn("Hay varios", salida)
        self.assertEqual(self.estado("light.salon_pie"), "on", "no toca nada")

    async def test_una_cerradura_no_existe_si_no_se_autoriza(self):
        salida = await self.caja().run("controlar_dispositivo",
                                       {"que": "puerta de la calle",
                                        "accion": "abrir"})
        self.assertIn("No encuentro", salida)
        self.assertEqual(self.estado("lock.entrada"), "locked")

    async def test_autorizada_sigue_pidiendo_confirmacion(self):
        caja = self.caja(dominios=[*DOMINIOS_SEGUROS, "lock"])
        salida = await caja.run("controlar_dispositivo",
                                {"que": "puerta de la calle", "accion": "abrir"})
        self.assertIn("Sin hacer nada con eso todavía", salida)
        self.assertEqual(self.estado("lock.entrada"), "locked")

    async def test_confirmar_de_entrada_no_abre_la_puerta(self):
        caja = self.caja(dominios=[*DOMINIOS_SEGUROS, "lock"])
        salida = await caja.run("controlar_dispositivo",
                                {"que": "puerta de la calle", "accion": "abrir",
                                 "confirmar": True})
        self.assertIn("Sin hacer nada con eso todavía", salida)
        self.assertEqual(self.estado("lock.entrada"), "locked")

    async def test_el_segundo_paso_si_abre(self):
        caja = self.caja(dominios=[*DOMINIOS_SEGUROS, "lock"])
        await caja.run("controlar_dispositivo",
                       {"que": "puerta de la calle", "accion": "abrir"})
        salida = await caja.run("controlar_dispositivo",
                                {"que": "puerta de la calle", "accion": "abrir",
                                 "confirmar": True})
        self.assertIn("Hecho", salida)
        self.assertEqual(self.estado("lock.entrada"), "unlocked")

    async def test_una_persiana_se_abre_con_su_servicio(self):
        caja = self.caja(dominios=[*DOMINIOS_SEGUROS, "cover"])
        await caja.run("controlar_dispositivo",
                       {"que": "garaje", "accion": "abrir"})
        await caja.run("controlar_dispositivo",
                       {"que": "garaje", "accion": "abrir", "confirmar": True})
        self.assertEqual(FakeHass.peticiones[-1]["ruta"],
                         "/api/services/cover/open_cover")
        self.assertEqual(self.estado("cover.garaje"), "open")

    async def test_tras_leer_contenido_externo_no_toca_nada(self):
        """Un correo podría estar diciendo «enciende el horno»."""
        caja = self.caja()
        caja._external("un correo cualquiera")
        salida = await caja.run("controlar_dispositivo",
                                {"que": "cafetera", "accion": "encender"})
        self.assertIn("contenido de fuera", salida)
        self.assertEqual(self.estado("switch.cafetera"), "off")

    async def test_una_accion_que_no_existe(self):
        salida = await self.caja().run("controlar_dispositivo",
                                       {"que": "cafetera", "accion": "achicharrar"})
        self.assertIn("No sé qué es", salida)

    async def test_si_home_assistant_no_contesta_se_dice(self):
        FakeHass.status = 500
        salida = await self.caja().run("controlar_dispositivo",
                                       {"que": "cafetera", "accion": "encender"})
        self.assertIn("Home Assistant", salida)

    async def test_el_estado_de_uno(self):
        salida = await self.caja().run("estado_de_la_casa", {"que": "termostato"})
        self.assertIn("Termostato", salida)
        self.assertIn("21", salida)

    async def test_el_resumen_de_la_casa(self):
        salida = await self.caja().run("estado_de_la_casa", {})
        self.assertIn("Lámpara de pie del salón", salida)
        self.assertNotIn("Luz de la cocina", salida, "esa está apagada")

    async def test_con_todo_apagado_lo_dice(self):
        for datos in FakeHass.estados.values():
            datos["state"] = "off"
        self.assertIn("todo apagado",
                      await self.caja().run("estado_de_la_casa", {}))

    async def test_mirar_no_es_tocar(self):
        """Preguntar por la casa no bloquea nada: no actúa."""
        caja = self.caja()
        await caja.run("estado_de_la_casa", {})
        self.assertFalse(caja.external_content_seen)

    async def test_sin_configurar_las_herramientas_no_se_ofrecen(self):
        cfg = Config({"tools": {"allow_system": True}, "llm": {"web_search": False}})
        with mock.patch.dict(os.environ, {"JARVIS_HASS_TOKEN": ""}):
            caja = Toolbox(cfg, EventBus())
        nombres = [spec["name"] for spec in caja.definitions()]
        self.assertNotIn("controlar_dispositivo", nombres)
        self.assertIn("No tengo Home Assistant",
                      await caja.run("controlar_dispositivo",
                                     {"que": "x", "accion": "encender"}))

    async def test_configurado_si_se_ofrecen(self):
        nombres = [spec["name"] for spec in self.caja().definitions()]
        self.assertIn("controlar_dispositivo", nombres)
        self.assertIn("estado_de_la_casa", nombres)


if __name__ == "__main__":
    unittest.main()
