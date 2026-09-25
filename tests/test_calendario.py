"""Fuente de calendario: CalDAV y .ics contra un servidor falso."""
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
from jarvis.sources.calendar_dav import (  # noqa: E402
    CalendarSource, calendar_data, event_items,
)
from jarvis.sources.store import Store  # noqa: E402

UTC = timezone.utc
DESDE = datetime(2026, 9, 1, tzinfo=UTC)
HASTA = datetime(2026, 12, 1, tzinfo=UTC)


def calendario(*eventos: str) -> str:
    return ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\n"
            + "\r\n".join(e.replace("\n", "\r\n") for e in eventos)
            + "\r\nEND:VCALENDAR\r\n")


REUNION = """BEGIN:VEVENT
UID:reunion-1
SUMMARY:Revisión con el cliente
DTSTART:20260925T100000Z
DTEND:20260925T113000Z
LOCATION:Sala 2
DESCRIPTION:Repasar el presupuesto
ATTENDEE;CN=Ana:mailto:ana@ejemplo.com
END:VEVENT"""

SEMANAL = """BEGIN:VEVENT
UID:standup
SUMMARY:Standup
DTSTART:20260907T083000Z
DTEND:20260907T084500Z
RRULE:FREQ=WEEKLY;BYDAY=MO
END:VEVENT"""

FESTIVO = """BEGIN:VEVENT
UID:festivo
SUMMARY:Día libre
DTSTART;VALUE=DATE:20261012
END:VEVENT"""

MULTISTATUS = """<?xml version="1.0" encoding="utf-8"?>
<D:multistatus xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
  <D:response>
    <D:href>/calendars/yelko/personal/reunion.ics</D:href>
    <D:propstat><D:prop>
      <D:getetag>"1"</D:getetag>
      <C:calendar-data>{ics}</C:calendar-data>
    </D:prop><D:status>HTTP/1.1 200 OK</D:status></D:propstat>
  </D:response>
</D:multistatus>"""


class TestEventosAItems(unittest.TestCase):
    def test_una_reunion_suelta(self):
        item, = event_items(calendario(REUNION), "personal", DESDE, HASTA)
        self.assertEqual(item.source, "calendario")
        self.assertEqual(item.kind, "evento")
        self.assertEqual(item.title, "Revisión con el cliente")
        self.assertEqual(item.created_at, "2026-09-25T10:00:00+00:00")
        self.assertIn("10:00–11:30", item.body)
        self.assertIn("Sala 2", item.body)
        self.assertIn("Repasar el presupuesto", item.body)
        self.assertEqual(item.meta["minutos"], 90)
        self.assertEqual(item.meta["invitados"], ["ana@ejemplo.com"])
        self.assertFalse(item.meta["se_repite"])

    def test_el_id_incluye_la_ocurrencia(self):
        """Dos lunes distintos del mismo standup no pueden pisarse."""
        items = event_items(calendario(SEMANAL), "personal", DESDE, HASTA)
        self.assertEqual(len(items), len({i.id for i in items}))
        self.assertIn("standup", items[0].id)

    def test_una_repeticion_semanal_se_expande(self):
        items = event_items(calendario(SEMANAL), "personal", DESDE, HASTA)
        # Del 7 de septiembre al 30 de noviembre hay 13 lunes.
        self.assertEqual(len(items), 13)
        self.assertTrue(all(i.meta["se_repite"] for i in items))
        self.assertTrue(all(i.title == "Standup" for i in items))

    def test_un_dia_entero_se_marca(self):
        item, = event_items(calendario(FESTIVO), "personal", DESDE, HASTA)
        self.assertTrue(item.meta["todo_el_dia"])
        self.assertIn("todo el día", item.body)

    def test_fuera_de_la_ventana_no_entra(self):
        self.assertEqual(
            event_items(calendario(REUNION), "personal",
                        datetime(2027, 1, 1, tzinfo=UTC),
                        datetime(2027, 2, 1, tzinfo=UTC)),
            [])

    def test_un_evento_en_marcha_sigue_contando(self):
        """Si empezó hace media hora y dura una, todavía es «lo de ahora»."""
        items = event_items(calendario(REUNION), "personal",
                            datetime(2026, 9, 25, 10, 30, tzinfo=UTC), HASTA)
        self.assertEqual(len(items), 1)

    def test_uno_ya_terminado_no(self):
        items = event_items(calendario(REUNION), "personal",
                            datetime(2026, 9, 25, 12, 0, tzinfo=UTC), HASTA)
        self.assertEqual(items, [])

    def test_exdate_quita_ese_lunes(self):
        con_hueco = SEMANAL.replace("RRULE:", "EXDATE:20260914T083000Z\nRRULE:")
        items = event_items(calendario(con_hueco), "personal", DESDE, HASTA)
        self.assertEqual(len(items), 12)
        self.assertNotIn("2026-09-14T08:30:00+00:00", [i.created_at for i in items])

    def test_una_regla_que_no_sabemos_expandir_se_señala(self):
        rara = SEMANAL.replace("RRULE:FREQ=WEEKLY;BYDAY=MO",
                               "RRULE:FREQ=MONTHLY;BYDAY=MO;BYSETPOS=-1")
        item, = event_items(calendario(rara), "personal", DESDE, HASTA)
        self.assertTrue(item.meta["repeticion_sin_expandir"],
                        "hay que avisar de que solo consta la primera aparición")

    def test_un_evento_sin_titulo_no_queda_en_blanco(self):
        sin = "BEGIN:VEVENT\nUID:x\nDTSTART:20260925T100000Z\nEND:VEVENT"
        item, = event_items(calendario(sin), "personal", DESDE, HASTA)
        self.assertEqual(item.title, "(sin título)")

    def test_sin_dtstart_se_descarta(self):
        sin = "BEGIN:VEVENT\nUID:x\nSUMMARY:Cuándo\nEND:VEVENT"
        self.assertEqual(event_items(calendario(sin), "personal", DESDE, HASTA), [])

    def test_un_calendario_roto_da_lista_vacia(self):
        self.assertEqual(event_items("no soy un calendario", "p", DESDE, HASTA), [])


class TestRespuestaCalDav(unittest.TestCase):
    def test_se_extrae_el_ical(self):
        xml = MULTISTATUS.format(ics=calendario(REUNION)).encode()
        datos = calendar_data(xml)
        self.assertEqual(len(datos), 1)
        self.assertIn("reunion-1", datos[0])

    def test_un_xml_ilegible_no_revienta(self):
        self.assertEqual(calendar_data(b"<D:multi"), [])


class FakeCalDav(BaseHTTPRequestHandler):
    peticiones: list = []
    status = 207

    def log_message(self, *args):
        pass

    def _responde(self, cuerpo: bytes, status: int):
        self.send_response(status)
        self.send_header("Content-Type", "application/xml; charset=utf-8")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)

    def _registra(self, cuerpo=b""):
        FakeCalDav.peticiones.append({
            "metodo": self.command, "ruta": self.path,
            "depth": self.headers.get("Depth", ""),
            "auth": self.headers.get("Authorization", ""),
            "cuerpo": cuerpo.decode() if cuerpo else "",
        })

    def do_REPORT(self):  # noqa: N802
        largo = int(self.headers.get("Content-Length", 0))
        self._registra(self.rfile.read(largo) if largo else b"")
        if FakeCalDav.status >= 400:
            self._responde(b"no", FakeCalDav.status)
            return
        self._responde(MULTISTATUS.format(ics=calendario(REUNION)).encode(), 207)

    def do_GET(self):  # noqa: N802
        self._registra()
        if self.path == "/roto":
            self._responde(b"esto no es un calendario", 200)
        elif FakeCalDav.status >= 400:
            self._responde(b"no", FakeCalDav.status)
        else:
            self._responde(calendario(SEMANAL).encode(), 200)


class TestFuenteContraUnServidorFalso(unittest.TestCase):
    def setUp(self):
        FakeCalDav.peticiones = []
        FakeCalDav.status = 207
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeCalDav)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "index.db")

    def tearDown(self):
        self.server.shutdown()
        self.store.close()
        self.tmp.cleanup()

    def caldav(self, **kwargs):
        with mock.patch.dict(os.environ, {"JARVIS_CALDAV_PASSWORD": "secreto"}):
            return CalendarSource(caldav_url=f"{self.base}/calendars/yelko/personal",
                                  caldav_user="yelko", **kwargs)

    def test_lee_por_caldav_e_indexa(self):
        # La ventana por defecto es de hoy en adelante; la reunión de prueba
        # está en una fecha fija, así que se abre la ventana a propósito.
        fuente = self.caldav(days_back=4000, days_ahead=4000)
        self.assertEqual(fuente.sync(self.store), 1)
        self.assertEqual(self.store.counts(), {"calendario": 1})

    def test_la_peticion_es_un_report_acotado_y_autenticado(self):
        self.caldav(days_back=4000, days_ahead=4000).sync(self.store)
        peticion, = FakeCalDav.peticiones
        self.assertEqual(peticion["metodo"], "REPORT")
        self.assertEqual(peticion["depth"], "1")
        self.assertTrue(peticion["auth"].startswith("Basic "))
        self.assertIn("calendar-query", peticion["cuerpo"])
        self.assertIn("time-range", peticion["cuerpo"])

    def test_la_ventana_del_report_sale_de_la_configuracion(self):
        self.caldav(days_back=0, days_ahead=30).sync(self.store)
        cuerpo = FakeCalDav.peticiones[0]["cuerpo"]
        ahora = datetime.now(UTC)
        self.assertIn(ahora.strftime("%Y%m%d"), cuerpo)
        self.assertIn((ahora + timedelta(days=30)).strftime("%Y%m%d"), cuerpo)

    def test_un_error_del_servidor_no_revienta(self):
        FakeCalDav.status = 401
        self.assertEqual(self.caldav().sync(self.store), 0)
        self.assertEqual(self.store.counts(), {})

    def test_lee_un_ics_sin_credenciales(self):
        fuente = CalendarSource(ics=[f"{self.base}/calendario.ics"],
                                days_back=4000, days_ahead=4000)
        self.assertGreater(fuente.sync(self.store), 1, "el standup se repite")
        self.assertEqual(FakeCalDav.peticiones[0]["metodo"], "GET")
        self.assertEqual(FakeCalDav.peticiones[0]["auth"], "",
                         "un .ics no lleva credenciales")

    def test_un_ics_roto_no_impide_leer_los_demas(self):
        fuente = CalendarSource(ics=[f"{self.base}/roto", f"{self.base}/bueno"],
                                days_back=4000, days_ahead=4000)
        self.assertGreater(fuente.sync(self.store), 0)
        self.assertEqual(len(FakeCalDav.peticiones), 2)

    def test_resincronizar_no_duplica(self):
        fuente = self.caldav(days_back=4000, days_ahead=4000)
        self.assertEqual(fuente.sync(self.store), 1)
        self.assertEqual(fuente.sync(self.store), 0)
        self.assertEqual(self.store.counts(), {"calendario": 1})


class TestConfiguracion(unittest.TestCase):
    def test_sin_caldav_ni_ics_avisa(self):
        with self.assertRaises(RuntimeError):
            CalendarSource()

    def test_caldav_sin_contraseña_avisa(self):
        with mock.patch.dict(os.environ, {"JARVIS_CALDAV_PASSWORD": ""}):
            with self.assertRaises(RuntimeError):
                CalendarSource(caldav_url="https://dav/x", caldav_user="yelko")

    def test_solo_con_ics_no_hacen_falta_credenciales(self):
        fuente = CalendarSource(ics=["https://ejemplo.com/c.ics"])
        self.assertEqual(fuente.caldav_url, "")

    def test_la_ventana_se_calcula_desde_ahora(self):
        fuente = CalendarSource(ics=["https://a/c.ics"], days_back=1, days_ahead=2)
        desde, hasta = fuente.ventana()
        self.assertAlmostEqual((hasta - desde).days, 3)

    def test_intervalo_propio(self):
        self.assertEqual(CalendarSource(ics=["https://a/c.ics"],
                                        interval_minutes=30).interval_minutes, 30)


class TestAgendaEnElIndice(unittest.TestCase):
    """`upcoming` ordena hacia adelante; `recent`, hacia atrás."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "index.db")
        self.store.upsert(event_items(calendario(SEMANAL), "personal", DESDE, HASTA))

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_lo_primero_es_lo_mas_proximo(self):
        filas = self.store.upcoming(source="calendario", limit=3)
        fechas = [f["created_at"] for f in filas]
        self.assertEqual(fechas, sorted(fechas))
        self.assertTrue(fechas[0].startswith("2026-09-07"))

    def test_la_ventana_acota(self):
        filas = self.store.upcoming(source="calendario",
                                    since="2026-10-01T00:00:00+00:00",
                                    until="2026-10-15T00:00:00+00:00", limit=20)
        self.assertEqual(len(filas), 2)

    def test_recent_sigue_yendo_al_reves(self):
        filas = self.store.recent(source="calendario", limit=3)
        fechas = [f["created_at"] for f in filas]
        self.assertEqual(fechas, sorted(fechas, reverse=True))


class TestHerramientaAgenda(unittest.IsolatedAsyncioTestCase):
    """De punta a punta: del iCalendar al texto que ve el modelo."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "index.db")
        # Relativo a ahora, porque la herramienta mira hacia adelante desde hoy.
        ahora = datetime.now(UTC)
        self.store.upsert(event_items(calendario(
            self.evento("pasado", "Lo de ayer", ahora - timedelta(days=1)),
            self.evento("pronto", "Dentista", ahora + timedelta(hours=3)),
            self.evento("lejos", "Revisión anual", ahora + timedelta(days=30)),
        ), "personal", ahora - timedelta(days=2), ahora + timedelta(days=60)))
        cfg = Config({
            "tools": {"allow_system": True,
                      "notes_file": str(Path(self.tmp.name) / "n.json"),
                      "memory_file": str(Path(self.tmp.name) / "m.json")},
            "llm": {"web_search": False},
        })
        self.toolbox = Toolbox(cfg, EventBus(), store=self.store)

    @staticmethod
    def evento(uid: str, titulo: str, cuando: datetime) -> str:
        marca = cuando.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
        return (f"BEGIN:VEVENT\nUID:{uid}\nSUMMARY:{titulo}\n"
                f"DTSTART:{marca}\nDURATION:PT1H\nEND:VEVENT")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    async def test_la_agenda_empieza_por_lo_mas_proximo(self):
        salida = await self.toolbox.run("agenda", {"dias": 7})
        self.assertIn("Dentista", salida)
        self.assertNotIn("Lo de ayer", salida, "lo pasado no es agenda")
        self.assertNotIn("Revisión anual", salida, "está fuera de los siete días")

    async def test_la_ventana_se_puede_ampliar(self):
        salida = await self.toolbox.run("agenda", {"dias": 60})
        self.assertIn("Revisión anual", salida)

    async def test_la_agenda_es_contenido_externo(self):
        """Una cita puede llevar texto de terceros: se entrega vallada."""
        salida = await self.toolbox.run("agenda", {})
        self.assertIn("DATOS EXTERNOS", salida)
        self.assertTrue(self.toolbox.external_content_seen)

    async def test_sin_nada_en_el_calendario_lo_dice(self):
        vacio = Store(Path(self.tmp.name) / "otro.db")
        cfg = Config({"tools": {"allow_system": True}, "llm": {"web_search": False}})
        salida = await Toolbox(cfg, EventBus(), store=vacio).run("agenda", {})
        self.assertIn("No hay nada en el calendario", salida)
        vacio.close()

    async def test_la_herramienta_se_ofrece_cuando_hay_indice(self):
        nombres = [spec["name"] for spec in self.toolbox.definitions()]
        self.assertIn("agenda", nombres)


if __name__ == "__main__":
    unittest.main()
