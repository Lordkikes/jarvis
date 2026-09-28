"""Avisos al móvil: ntfy, Home Assistant, y el temporizador que los usa."""
from __future__ import annotations

import asyncio
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

from jarvis.avisos import Avisos, Ntfy, _bonito  # noqa: E402
from jarvis.bus import EventBus  # noqa: E402
from jarvis.config import Config  # noqa: E402
from jarvis.domotica import HomeAssistant  # noqa: E402
from jarvis.llm.tools import Toolbox  # noqa: E402


class FakeServidor(BaseHTTPRequestHandler):
    """Hace de ntfy y de Home Assistant a la vez, cada uno en su ruta."""

    peticiones: list = []
    ntfy_status: int = 200
    hass_status: int = 200
    servicios: list = []

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
        if self.path == "/api/services":
            self._responde(FakeServidor.servicios)
        elif self.path == "/api/states":
            self._responde([])
        else:
            self._responde({}, 404)

    def do_POST(self):  # noqa: N802
        largo = int(self.headers.get("Content-Length", 0))
        crudo = self.rfile.read(largo).decode() if largo else ""
        FakeServidor.peticiones.append({
            "ruta": self.path,
            "auth": self.headers.get("Authorization", ""),
            "cuerpo": json.loads(crudo) if crudo else None,
            "crudo": crudo,
        })
        if self.path.startswith("/api/services/"):
            self._responde([], FakeServidor.hass_status)
        else:
            self._responde({"id": "abc"}, FakeServidor.ntfy_status)


def servicios_con(*nombres: str) -> list:
    return [{"domain": "notify",
             "services": {n.split(".", 1)[1]: {} for n in nombres}}]


class CasoConServidor(unittest.TestCase):
    def setUp(self):
        FakeServidor.peticiones = []
        FakeServidor.ntfy_status = 200
        FakeServidor.hass_status = 200
        FakeServidor.servicios = servicios_con("notify.notify")
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeServidor)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()

    def hass(self) -> HomeAssistant:
        return HomeAssistant(url=self.base, token="tok")

    def a_ntfy(self) -> list:
        return [p for p in FakeServidor.peticiones
                if not p["ruta"].startswith("/api/")]

    def a_hass(self) -> list:
        return [p for p in FakeServidor.peticiones
                if p["ruta"].startswith("/api/services/")]


class TestNtfy(CasoConServidor):
    def ntfy(self, **kwargs) -> Ntfy:
        kwargs.setdefault("servidor", self.base)
        kwargs.setdefault("topico", "jarvis-de-yelko")
        return Ntfy(**kwargs)

    def test_sin_tema_no_esta_disponible(self):
        self.assertFalse(Ntfy(topico="").disponible)
        self.assertTrue(self.ntfy().disponible)

    def test_publica_contra_la_raiz_no_contra_el_tema(self):
        """En modo JSON el tema va dentro; mandarlo a /tema manda el JSON crudo."""
        ok, _ = self.ntfy().envia("Se acabó el arroz")
        self.assertTrue(ok)
        envio, = self.a_ntfy()
        self.assertEqual(envio["ruta"], "/")
        self.assertEqual(envio["cuerpo"]["topic"], "jarvis-de-yelko")
        self.assertEqual(envio["cuerpo"]["message"], "Se acabó el arroz")

    def test_el_titulo_solo_va_si_lo_hay(self):
        self.ntfy().envia("Hola")
        self.assertNotIn("title", self.a_ntfy()[0]["cuerpo"])
        self.ntfy().envia("Hola", "Jarvis")
        self.assertEqual(self.a_ntfy()[1]["cuerpo"]["title"], "Jarvis")

    def test_la_prioridad_va_en_el_rango_de_ntfy(self):
        self.ntfy().envia("normal")
        self.ntfy().envia("corre", urgente=True)
        prioridades = [p["cuerpo"]["priority"] for p in self.a_ntfy()]
        self.assertEqual(prioridades, [3, 5])
        for prioridad in prioridades:
            self.assertTrue(1 <= prioridad <= 5)

    def test_el_token_viaja_como_bearer(self):
        self.ntfy(token="tk_secreto").envia("Hola")
        self.assertEqual(self.a_ntfy()[0]["auth"], "Bearer tk_secreto")

    def test_sin_token_no_se_manda_cabecera(self):
        self.ntfy().envia("Hola")
        self.assertEqual(self.a_ntfy()[0]["auth"], "")

    def test_el_token_puede_venir_del_entorno(self):
        with mock.patch.dict(os.environ, {"JARVIS_NTFY_TOKEN": "tk_delentorno"}):
            Ntfy(servidor=self.base, topico="t").envia("Hola")
        self.assertEqual(self.a_ntfy()[0]["auth"], "Bearer tk_delentorno")

    def test_unas_credenciales_malas_se_explican(self):
        FakeServidor.ntfy_status = 403
        ok, motivo = self.ntfy().envia("Hola")
        self.assertFalse(ok)
        self.assertIn("credenciales", motivo)

    def test_otro_error_se_cuenta(self):
        FakeServidor.ntfy_status = 500
        ok, motivo = self.ntfy().envia("Hola")
        self.assertFalse(ok)
        self.assertIn("500", motivo)

    def test_el_servidor_caido_no_revienta(self):
        ok, motivo = Ntfy(servidor="http://127.0.0.1:1", topico="t",
                          timeout=1.0).envia("Hola")
        self.assertFalse(ok)
        self.assertIn("no se pudo hablar", motivo)


class TestElegirServicio(CasoConServidor):
    def test_se_prefiere_la_aplicacion_de_movil(self):
        FakeServidor.servicios = servicios_con("notify.notify",
                                               "notify.mobile_app_pixel")
        self.assertEqual(Avisos(casa=self.hass()).servicio_de_casa(),
                         "notify.mobile_app_pixel")

    def test_sin_app_de_movil_no_hay_servicio(self):
        """`notify.notify` puede ser un correo: no vale como aviso al móvil."""
        FakeServidor.servicios = servicios_con("notify.notify")
        self.assertEqual(Avisos(casa=self.hass()).servicio_de_casa(), "")

    def test_se_puede_nombrar_uno(self):
        avisos = Avisos(casa=self.hass(), servicio="notify.mobile_app_otro")
        self.assertEqual(avisos.servicio_de_casa(), "notify.mobile_app_otro")

    def test_con_varios_se_coge_el_primero_por_orden(self):
        FakeServidor.servicios = servicios_con("notify.mobile_app_zeta",
                                               "notify.mobile_app_alfa")
        self.assertEqual(Avisos(casa=self.hass()).servicio_de_casa(),
                         "notify.mobile_app_alfa")

    def test_sin_home_assistant_no_hay_servicio(self):
        self.assertEqual(Avisos().servicio_de_casa(), "")

    def test_el_nombre_se_dice_en_bonito(self):
        self.assertEqual(_bonito("notify.mobile_app_pixel_de_yelko"),
                         "pixel de yelko")
        self.assertEqual(_bonito("notify.telegram"), "telegram")


class TestMandarPorLosDos(CasoConServidor):
    def avisos(self, con_hass=True, con_ntfy=True) -> Avisos:
        FakeServidor.servicios = servicios_con("notify.mobile_app_pixel")
        return Avisos(
            casa=self.hass() if con_hass else None,
            ntfy=Ntfy(servidor=self.base,
                      topico="jarvis" if con_ntfy else ""))

    def test_si_hay_dos_se_manda_por_los_dos(self):
        """Un aviso que no llega no sirve; duplicarlo molesta menos."""
        resultado = self.avisos().envia("Hola", "Jarvis")
        self.assertTrue(resultado["ok"])
        self.assertEqual(len(resultado["llegaron"]), 2)
        self.assertEqual(len(self.a_ntfy()), 1)
        self.assertEqual(len(self.a_hass()), 1)

    def test_home_assistant_recibe_mensaje_y_titulo(self):
        self.avisos(con_ntfy=False).envia("Se acabó el arroz", "Compra")
        envio, = self.a_hass()
        self.assertTrue(envio["ruta"].endswith("/notify/mobile_app_pixel"))
        self.assertEqual(envio["cuerpo"]["message"], "Se acabó el arroz")
        self.assertEqual(envio["cuerpo"]["title"], "Compra")

    def test_si_uno_falla_el_otro_sigue(self):
        FakeServidor.hass_status = 500
        resultado = self.avisos().envia("Hola")
        self.assertTrue(resultado["ok"], "ntfy sí llegó")
        self.assertEqual(resultado["llegaron"], ["ntfy"])
        self.assertEqual(len(resultado["fallaron"]), 1)

    def test_si_fallan_los_dos_no_esta_bien(self):
        FakeServidor.hass_status = 500
        FakeServidor.ntfy_status = 500
        resultado = self.avisos().envia("Hola")
        self.assertFalse(resultado["ok"])
        self.assertEqual(len(resultado["fallaron"]), 2)

    def test_sin_nada_configurado(self):
        self.assertFalse(Avisos().disponible)
        self.assertFalse(Avisos().envia("Hola")["ok"])

    def test_con_solo_ntfy_ya_vale(self):
        self.assertTrue(self.avisos(con_hass=False).disponible)


class TestHerramienta(CasoConServidor, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        CasoConServidor.setUp(self)
        FakeServidor.servicios = servicios_con("notify.mobile_app_pixel")
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()
        CasoConServidor.tearDown(self)

    def caja(self, topico="jarvis", con_hass=False, temporizadores=True,
             on_announce=None) -> Toolbox:
        herramientas = {
            "allow_system": True,
            "notes_file": str(Path(self.tmp.name) / "n.json"),
            "memory_file": str(Path(self.tmp.name) / "m.json"),
            "lists_file": str(Path(self.tmp.name) / "l.json"),
            "avisos": {"temporizadores": temporizadores,
                       "ntfy": {"servidor": self.base, "topico": topico}},
        }
        if con_hass:
            herramientas["home_assistant"] = {"url": self.base}
        cfg = Config({"tools": herramientas, "llm": {"web_search": False}})
        with mock.patch.dict(os.environ,
                             {"JARVIS_HASS_TOKEN": "tok" if con_hass else "",
                              "JARVIS_NTFY_TOKEN": ""}):
            return Toolbox(cfg, EventBus(), on_announce=on_announce)

    async def test_avisar(self):
        salida = await self.caja().run("avisar_al_movil",
                                       {"mensaje": "Recoge el paquete"})
        self.assertIn("Avisado", salida)
        self.assertEqual(self.a_ntfy()[0]["cuerpo"]["message"],
                         "Recoge el paquete")

    async def test_urgente_sube_la_prioridad(self):
        await self.caja().run("avisar_al_movil", {"mensaje": "Corre",
                                                  "urgente": True})
        self.assertEqual(self.a_ntfy()[0]["cuerpo"]["priority"], 5)

    async def test_sin_mensaje(self):
        self.assertIn("¿Qué le aviso?",
                      await self.caja().run("avisar_al_movil", {"mensaje": " "}))

    async def test_si_uno_de_los_dos_falla_se_dice(self):
        FakeServidor.hass_status = 500
        salida = await self.caja(con_hass=True).run("avisar_al_movil",
                                                    {"mensaje": "Hola"})
        self.assertIn("Avisado por ntfy", salida)
        self.assertIn("falló", salida)

    async def test_si_fallan_todos_se_dice(self):
        FakeServidor.ntfy_status = 500
        salida = await self.caja().run("avisar_al_movil", {"mensaje": "Hola"})
        self.assertIn("No he podido avisar", salida)

    async def test_tras_leer_contenido_externo_no_manda(self):
        """Un correo podría estar dictando el aviso, o pidiendo cien."""
        caja = self.caja()
        caja._external("un correo cualquiera")
        salida = await caja.run("avisar_al_movil", {"mensaje": "Hola"})
        self.assertIn("contenido de fuera", salida)
        self.assertEqual(self.a_ntfy(), [])

    async def test_sin_configurar_no_se_ofrece(self):
        caja = self.caja(topico="")
        self.assertNotIn("avisar_al_movil",
                         [s["name"] for s in caja.definitions()])
        self.assertIn("No tengo por dónde avisar",
                      await caja.run("avisar_al_movil", {"mensaje": "Hola"}))

    async def test_configurado_si_se_ofrece(self):
        self.assertIn("avisar_al_movil",
                      [s["name"] for s in self.caja().definitions()])

    # -- el temporizador ---------------------------------------------------
    async def test_un_temporizador_tambien_avisa_al_movil(self):
        dicho = []
        caja = self.caja(on_announce=lambda m: _guarda(dicho, m))
        await caja.run("poner_temporizador", {"segundos": 0, "etiqueta": "el arroz"})
        await _espera(lambda: self.a_ntfy())

        self.assertEqual(dicho, ["Ha terminado el arroz."], "sigue diciéndolo")
        envio = self.a_ntfy()[0]
        self.assertEqual(envio["cuerpo"]["message"], "Ha terminado el arroz.")
        self.assertEqual(envio["cuerpo"]["title"], "Temporizador")

    async def test_se_puede_apagar(self):
        caja = self.caja(temporizadores=False)
        await caja.run("poner_temporizador", {"segundos": 0})
        await asyncio.sleep(0.3)
        self.assertEqual(self.a_ntfy(), [])

    async def test_sin_avisos_configurados_el_temporizador_sigue_igual(self):
        dicho = []
        caja = self.caja(topico="", on_announce=lambda m: _guarda(dicho, m))
        await caja.run("poner_temporizador", {"segundos": 0})
        await asyncio.sleep(0.3)
        self.assertEqual(len(dicho), 1)


async def _guarda(donde: list, mensaje: str) -> None:
    donde.append(mensaje)


async def _espera(condicion, limite: float = 3.0) -> None:
    """Espera a que el aviso llegue al servidor, sin dormir a ciegas."""
    for _ in range(int(limite / 0.05)):
        if condicion():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("el aviso no llegó a tiempo")


if __name__ == "__main__":
    unittest.main()
