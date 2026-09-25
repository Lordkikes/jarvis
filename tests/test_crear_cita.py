"""Crear citas: el escritor de iCalendar, el PUT y la confirmación obligatoria."""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis.bus import EventBus  # noqa: E402
from jarvis.config import Config  # noqa: E402
from jarvis.llm.tools import Toolbox  # noqa: E402
from jarvis.sources.calendar_dav import CalendarSource  # noqa: E402
from jarvis.sources.ical import (  # noqa: E402
    build_event, escape, fold, parse_events, unfold, value_of,
)
from jarvis.sources.store import Store  # noqa: E402

UTC = timezone.utc
INICIO = datetime(2026, 9, 29, 17, 0, tzinfo=UTC)


class TestEscapado(unittest.TestCase):
    def test_los_caracteres_especiales_se_escapan(self):
        self.assertEqual(escape("a,b;c"), "a\\,b\;c")
        self.assertEqual(escape("uno\ndos"), "uno\\ndos")
        self.assertEqual(escape("a\\b"), "a\\\\b")

    def test_el_retorno_de_carro_se_va(self):
        self.assertEqual(escape("a\r\nb"), "a\\nb")


class TestPlegado(unittest.TestCase):
    def test_una_linea_corta_no_se_toca(self):
        self.assertEqual(fold("UID:abc"), "UID:abc")

    def test_el_limite_son_octetos_no_caracteres(self):
        """Una «ñ» ocupa dos octetos: partir por caracteres genera líneas largas."""
        plegada = fold("SUMMARY:" + "ñ" * 120)
        for trozo in plegada.split("\r\n"):
            self.assertLessEqual(len(trozo.encode()), 75)

    def test_el_plegado_se_deshace_sin_perder_nada(self):
        linea = "DESCRIPTION:" + "Una descripción con acentos y ñ. " * 8
        self.assertEqual(unfold(fold(linea)), [linea])

    def test_las_continuaciones_empiezan_por_espacio(self):
        trozos = fold("SUMMARY:" + "a" * 200).split("\r\n")
        self.assertGreater(len(trozos), 1)
        for trozo in trozos[1:]:
            self.assertTrue(trozo.startswith(" "))


class TestConstruccionDelEvento(unittest.TestCase):
    def setUp(self):
        self.ics = build_event("uid-1", INICIO, INICIO + timedelta(minutes=45),
                               "Reunión con Ana; y Luis", "Sala 2, planta 3",
                               "Repasar\nel presupuesto")

    def test_es_un_calendario_valido(self):
        self.assertTrue(self.ics.startswith("BEGIN:VCALENDAR"))
        self.assertTrue(self.ics.endswith("END:VCALENDAR\r\n"))
        for obligatoria in ("VERSION:2.0", "PRODID:", "DTSTAMP:", "UID:uid-1"):
            self.assertIn(obligatoria, self.ics)

    def test_las_fechas_van_en_utc(self):
        self.assertIn("DTSTART:20260929T170000Z", self.ics)
        self.assertIn("DTEND:20260929T174500Z", self.ics)

    def test_ida_y_vuelta_por_nuestro_propio_lector(self):
        """Lo que escribimos tiene que poder leerse con lo que ya teníamos."""
        evento, = parse_events(self.ics)
        self.assertEqual(value_of(evento, "SUMMARY"), "Reunión con Ana; y Luis")
        self.assertEqual(value_of(evento, "LOCATION"), "Sala 2, planta 3")
        self.assertEqual(value_of(evento, "DESCRIPTION"), "Repasar\nel presupuesto")

    def test_los_campos_opcionales_se_omiten(self):
        ics = build_event("u", INICIO, INICIO + timedelta(hours=1), "Solo título")
        self.assertNotIn("LOCATION", ics)
        self.assertNotIn("DESCRIPTION", ics)

    def test_una_hora_local_se_convierte(self):
        madrid = timezone(timedelta(hours=2))
        ics = build_event("u", datetime(2026, 9, 29, 17, 0, tzinfo=madrid),
                          datetime(2026, 9, 29, 18, 0, tzinfo=madrid), "T")
        self.assertIn("DTSTART:20260929T150000Z", ics)


class FakeCalDav(BaseHTTPRequestHandler):
    peticiones: list = []
    status = 201

    def log_message(self, *args):
        pass

    def do_PUT(self):  # noqa: N802
        largo = int(self.headers.get("Content-Length", 0))
        FakeCalDav.peticiones.append({
            "ruta": self.path,
            "tipo": self.headers.get("Content-Type", ""),
            "if_none_match": self.headers.get("If-None-Match", ""),
            "auth": self.headers.get("Authorization", ""),
            "cuerpo": self.rfile.read(largo).decode() if largo else "",
        })
        self.send_response(FakeCalDav.status)
        self.send_header("Content-Length", "0")
        self.end_headers()


class TestPutContraUnServidorFalso(unittest.TestCase):
    def setUp(self):
        FakeCalDav.peticiones = []
        FakeCalDav.status = 201
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeCalDav)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()

    def fuente(self, **kwargs):
        with mock.patch.dict(os.environ, {"JARVIS_CALDAV_PASSWORD": "secreto"}):
            return CalendarSource(caldav_url=f"{self.base}/calendars/yelko/personal",
                                  caldav_user="yelko", **kwargs)

    def crea(self, fuente=None):
        return (fuente or self.fuente()).create_event(
            "Dentista", INICIO, INICIO + timedelta(minutes=30), "Calle Mayor 1")

    def test_crea_y_devuelve_el_uid(self):
        resultado = self.crea()
        self.assertTrue(resultado["ok"])
        self.assertIn("@jarvis", resultado["uid"])

    def test_el_recurso_es_nuevo_y_no_pisa_nada(self):
        """`If-None-Match: *` hace que el servidor rechace si ya existiera."""
        self.crea()
        peticion, = FakeCalDav.peticiones
        self.assertEqual(peticion["if_none_match"], "*")
        self.assertTrue(peticion["ruta"].endswith(".ics"))
        self.assertIn("text/calendar", peticion["tipo"])
        self.assertTrue(peticion["auth"].startswith("Basic "))

    def test_el_cuerpo_lleva_la_cita(self):
        self.crea()
        cuerpo = FakeCalDav.peticiones[0]["cuerpo"]
        self.assertIn("SUMMARY:Dentista", cuerpo)
        self.assertIn("DTSTART:20260929T170000Z", cuerpo)
        self.assertIn("LOCATION:Calle Mayor 1", cuerpo)

    def test_un_204_tambien_vale(self):
        FakeCalDav.status = 204
        self.assertTrue(self.crea()["ok"])

    def test_un_412_se_explica(self):
        FakeCalDav.status = 412
        resultado = self.crea()
        self.assertFalse(resultado["ok"])
        self.assertIn("ya existe", resultado["motivo"])

    def test_un_error_del_servidor_se_cuenta_no_se_reintenta(self):
        FakeCalDav.status = 507
        resultado = self.crea()
        self.assertFalse(resultado["ok"])
        self.assertIn("507", resultado["motivo"])
        self.assertEqual(len(FakeCalDav.peticiones), 1, "un reintento podría duplicar")

    def test_el_servidor_caido_no_revienta(self):
        # Un puerto donde no escucha nadie: la conexión se rechaza al momento,
        # sin esperar a que venza el tiempo de espera.
        import socket

        with socket.socket() as sonda:
            sonda.bind(("127.0.0.1", 0))
            muerto = sonda.getsockname()[1]
        with mock.patch.dict(os.environ, {"JARVIS_CALDAV_PASSWORD": "secreto"}):
            fuente = CalendarSource(caldav_url=f"http://127.0.0.1:{muerto}/cal",
                                    caldav_user="yelko")
        resultado = self.crea(fuente)
        self.assertFalse(resultado["ok"])
        self.assertIn("servidor", resultado["motivo"])

    def test_con_la_escritura_apagada_no_se_manda_nada(self):
        resultado = self.crea(self.fuente(allow_write=False))
        self.assertFalse(resultado["ok"])
        self.assertEqual(FakeCalDav.peticiones, [])

    def test_un_ics_no_puede_escribir(self):
        fuente = CalendarSource(ics=["https://ejemplo.com/c.ics"])
        self.assertFalse(fuente.allow_write)
        self.assertFalse(fuente.create_event("X", INICIO, INICIO, "")["ok"])


class TestHerramientaCrearCita(unittest.IsolatedAsyncioTestCase):
    """La confirmación la exige el código, no la buena voluntad del modelo."""

    def setUp(self):
        FakeCalDav.peticiones = []
        FakeCalDav.status = 201
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeCalDav)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "index.db")
        with mock.patch.dict(os.environ, {"JARVIS_CALDAV_PASSWORD": "secreto"}):
            self.calendario = CalendarSource(caldav_url=f"{base}/calendars/yelko/x",
                                             caldav_user="yelko")
        cfg = Config({
            "tools": {"allow_system": True,
                      "notes_file": str(Path(self.tmp.name) / "n.json"),
                      "memory_file": str(Path(self.tmp.name) / "m.json")},
            "llm": {"web_search": False},
        })
        self.toolbox = Toolbox(cfg, EventBus(), store=self.store,
                               calendar=self.calendario)
        manyana = datetime.now().astimezone() + timedelta(days=1)
        self.cita = {"titulo": "Dentista",
                     "inicio": manyana.replace(microsecond=0).isoformat(timespec="minutes"),
                     "duracion_minutos": 30}

    def tearDown(self):
        self.server.shutdown()
        self.store.close()
        self.tmp.cleanup()

    async def test_la_primera_llamada_no_crea_nada(self):
        salida = await self.toolbox.run("crear_cita", dict(self.cita))
        self.assertIn("Sin crear todavía", salida)
        self.assertIn("Dentista", salida)
        self.assertEqual(FakeCalDav.peticiones, [], "no debe tocar el servidor")

    async def test_confirmar_de_entrada_tampoco_crea(self):
        """Si el modelo se salta el paso, el código lo devuelve al principio."""
        salida = await self.toolbox.run("crear_cita", {**self.cita, "confirmar": True})
        self.assertIn("Sin crear todavía", salida)
        self.assertEqual(FakeCalDav.peticiones, [])

    async def test_el_segundo_paso_si_crea(self):
        await self.toolbox.run("crear_cita", dict(self.cita))
        salida = await self.toolbox.run("crear_cita", {**self.cita, "confirmar": True})
        self.assertIn("Creada", salida)
        self.assertEqual(len(FakeCalDav.peticiones), 1)
        self.assertIn("SUMMARY:Dentista", FakeCalDav.peticiones[0]["cuerpo"])

    async def test_cambiar_los_datos_obliga_a_confirmar_otra_vez(self):
        """Si la hora cambia entre la lectura y el sí, hay que releerlo."""
        await self.toolbox.run("crear_cita", dict(self.cita))
        salida = await self.toolbox.run("crear_cita", {
            **self.cita, "duracion_minutos": 120, "confirmar": True})
        self.assertIn("Sin crear todavía", salida)
        self.assertEqual(FakeCalDav.peticiones, [])

    async def test_no_se_puede_crear_dos_veces_con_una_confirmacion(self):
        await self.toolbox.run("crear_cita", dict(self.cita))
        await self.toolbox.run("crear_cita", {**self.cita, "confirmar": True})
        salida = await self.toolbox.run("crear_cita", {**self.cita, "confirmar": True})
        self.assertIn("Sin crear todavía", salida)
        self.assertEqual(len(FakeCalDav.peticiones), 1)

    async def test_la_lectura_lleva_dia_de_la_semana_y_hora(self):
        salida = await self.toolbox.run("crear_cita", dict(self.cita))
        self.assertTrue(any(dia in salida for dia in Toolbox.DIAS), salida)
        self.assertIn("30 minutos", salida)

    async def test_una_fecha_pasada_se_avisa(self):
        ayer = (datetime.now().astimezone() - timedelta(days=1))
        salida = await self.toolbox.run("crear_cita", {
            "titulo": "Tarde", "inicio": ayer.isoformat(timespec="minutes")})
        self.assertIn("ya ha pasado", salida)

    async def test_una_fecha_ilegible_se_rechaza(self):
        salida = await self.toolbox.run("crear_cita",
                                        {"titulo": "X", "inicio": "el martes"})
        self.assertIn("ISO 8601", salida)
        self.assertEqual(FakeCalDav.peticiones, [])

    async def test_sin_titulo_pregunta(self):
        salida = await self.toolbox.run("crear_cita",
                                        {"titulo": "  ", "inicio": self.cita["inicio"]})
        self.assertIn("¿Cómo se llama", salida)

    async def test_tras_leer_contenido_externo_no_escribe(self):
        """Un correo podría estar dictando la cita: no se escribe en ese turno."""
        await self.toolbox.run("crear_cita", dict(self.cita))
        self.toolbox._external("un correo cualquiera")
        salida = await self.toolbox.run("crear_cita", {**self.cita, "confirmar": True})
        self.assertIn("contenido de fuera", salida)
        self.assertEqual(FakeCalDav.peticiones, [])

    async def test_leer_antes_de_proponer_si_se_permite(self):
        """Mirar la agenda y proponer es legítimo: lo que se frena es escribir."""
        self.toolbox._external("la agenda")
        salida = await self.toolbox.run("crear_cita", dict(self.cita))
        self.assertIn("Sin crear todavía", salida)

    async def test_la_cita_creada_entra_en_el_indice(self):
        await self.toolbox.run("crear_cita", dict(self.cita))
        await self.toolbox.run("crear_cita", {**self.cita, "confirmar": True})
        self.assertEqual(self.store.counts(), {"calendario": 1})
        agenda = await self.toolbox.run("agenda", {"dias": 7})
        self.assertIn("Dentista", agenda)

    async def test_un_fallo_del_servidor_se_cuenta(self):
        FakeCalDav.status = 507
        await self.toolbox.run("crear_cita", dict(self.cita))
        salida = await self.toolbox.run("crear_cita", {**self.cita, "confirmar": True})
        self.assertIn("No he podido crear", salida)
        self.assertEqual(self.store.counts(), {})

    async def test_la_herramienta_se_ofrece_si_hay_escritura(self):
        self.assertIn("crear_cita",
                      [spec["name"] for spec in self.toolbox.definitions()])

    async def test_y_se_esconde_si_no(self):
        cfg = Config({"tools": {"allow_system": True}, "llm": {"web_search": False}})
        sin_calendario = Toolbox(cfg, EventBus(), store=self.store)
        self.assertNotIn("crear_cita",
                         [spec["name"] for spec in sin_calendario.definitions()])
        salida = await sin_calendario.run("crear_cita", dict(self.cita))
        self.assertIn("No puedo crear citas", salida)


if __name__ == "__main__":
    unittest.main()
