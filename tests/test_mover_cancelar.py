"""Mover y cancelar citas, contra un CalDAV falso que sí guarda lo que recibe.

El servidor de estas pruebas no es un decorado: almacena los recursos, cambia
el ETag en cada escritura y responde a REPORT con lo que tenga guardado. Así
se puede comprobar el ciclo completo —sincronizar, mover, volver a
sincronizar— y no solo que salga la petición correcta.
"""
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
    add_exdate, add_override, find_vevent, parse_events, remove_vevent,
    reschedule, unfold, value_of,
)
from jarvis.sources.store import Store  # noqa: E402

UTC = timezone.utc


def calendario(*trozos: str) -> str:
    return ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\n"
            + "\r\n".join(t.replace("\n", "\r\n") for t in trozos)
            + "\r\nEND:VCALENDAR\r\n")


SUELTA = """BEGIN:VEVENT
UID:dentista
SUMMARY:Dentista
DTSTART:{inicio}
DTEND:{fin}
LOCATION:Calle Mayor 1
BEGIN:VALARM
ACTION:DISPLAY
TRIGGER:-PT30M
DESCRIPTION:Recuerda el dentista
END:VALARM
END:VEVENT"""

SERIE = """BEGIN:VEVENT
UID:standup
SUMMARY:Standup
DTSTART:{inicio}
DTEND:{fin}
RRULE:FREQ=WEEKLY;BYDAY=MO
END:VEVENT"""


def marca(fecha: datetime) -> str:
    return fecha.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


class TestEdicionDelIcs(unittest.TestCase):
    """Las tres operaciones sobre el fichero, sin red de por medio."""

    def setUp(self):
        self.inicio = datetime(2026, 9, 7, 8, 30, tzinfo=UTC)
        self.ics = calendario(SERIE.format(inicio=marca(self.inicio),
                                           fin=marca(self.inicio + timedelta(minutes=15))))

    def test_reprogramar_cambia_las_fechas_y_sube_la_revision(self):
        nuevo = reschedule(self.ics, "standup",
                           datetime(2026, 9, 7, 10, 0, tzinfo=UTC),
                           datetime(2026, 9, 7, 10, 30, tzinfo=UTC))
        self.assertIn("DTSTART:20260907T100000Z", nuevo)
        self.assertIn("DTEND:20260907T103000Z", nuevo)
        self.assertIn("SEQUENCE:1", nuevo)
        self.assertIn("LAST-MODIFIED:", nuevo)

    def test_reprogramar_no_deja_dos_finales(self):
        """DTEND y DURATION son excluyentes: fijar uno obliga a quitar el otro."""
        con_duracion = calendario(
            "BEGIN:VEVENT\nUID:x\nSUMMARY:X\nDTSTART:20260907T083000Z\n"
            "DURATION:PT15M\nEND:VEVENT")
        nuevo = reschedule(con_duracion, "x", self.inicio,
                           self.inicio + timedelta(hours=1))
        self.assertNotIn("DURATION", nuevo)
        self.assertIn("DTEND:", nuevo)

    def test_lo_anidado_sobrevive(self):
        """Un VALARM tiene su DESCRIPTION y puede tener su DURATION."""
        suelta = calendario(SUELTA.format(inicio=marca(self.inicio),
                                          fin=marca(self.inicio + timedelta(hours=1))))
        nuevo = reschedule(suelta, "dentista", self.inicio,
                           self.inicio + timedelta(hours=2))
        self.assertIn("BEGIN:VALARM", nuevo)
        self.assertIn("DESCRIPTION:Recuerda el dentista", nuevo)
        self.assertIn("TRIGGER:-PT30M", nuevo)

    def test_excluir_un_dia(self):
        quitado = self.inicio + timedelta(days=7)
        nuevo = add_exdate(self.ics, "standup", quitado)
        self.assertIn(f"EXDATE:{marca(quitado)}", nuevo)
        self.assertIn("RRULE:", nuevo, "la serie sigue existiendo")

    def test_una_excepcion_es_un_vevent_aparte(self):
        original = self.inicio + timedelta(days=7)
        nuevo = add_override(self.ics, "standup", original,
                             original + timedelta(hours=4),
                             original + timedelta(hours=4, minutes=15))
        self.assertEqual(len(parse_events(nuevo)), 2)
        self.assertIn(f"RECURRENCE-ID:{marca(original)}", nuevo)
        self.assertIn("SUMMARY:Standup", nuevo, "la excepción hereda el título")

    def test_mover_dos_veces_el_mismo_dia_no_duplica_la_excepcion(self):
        original = self.inicio + timedelta(days=7)
        una = add_override(self.ics, "standup", original,
                           original + timedelta(hours=4),
                           original + timedelta(hours=4, minutes=15))
        otra = add_override(una, "standup", original,
                            original + timedelta(hours=6),
                            original + timedelta(hours=6, minutes=15))
        self.assertEqual(len(parse_events(otra)), 2)
        self.assertIn(marca(original + timedelta(hours=6)), otra)

    def test_quitar_una_excepcion(self):
        original = self.inicio + timedelta(days=7)
        con = add_override(self.ics, "standup", original,
                           original + timedelta(hours=4),
                           original + timedelta(hours=4, minutes=15))
        sin = remove_vevent(con, "standup", original)
        self.assertEqual(len(parse_events(sin)), 1)

    def test_un_uid_que_no_esta_devuelve_none(self):
        self.assertIsNone(reschedule(self.ics, "otro", self.inicio, self.inicio))
        self.assertIsNone(add_exdate(self.ics, "otro", self.inicio))
        self.assertIsNone(add_override(self.ics, "otro", self.inicio,
                                       self.inicio, self.inicio))

    def test_find_vevent_distingue_maestro_de_excepcion(self):
        original = self.inicio + timedelta(days=7)
        con = add_override(self.ics, "standup", original,
                           original + timedelta(hours=4),
                           original + timedelta(hours=4, minutes=15))
        lineas = unfold(con)
        self.assertIsNotNone(find_vevent(lineas, "standup"))
        self.assertIsNotNone(find_vevent(lineas, "standup", original))
        self.assertIsNone(find_vevent(lineas, "standup",
                                      original + timedelta(days=7)))


class FakeCalDav(BaseHTTPRequestHandler):
    """Un CalDAV mínimo pero de verdad: guarda, versiona y responde."""

    recursos: dict = {}
    versiones: int = 0
    peticiones: list = []
    forzar: int | None = None

    def log_message(self, *args):
        pass

    def _responde(self, cuerpo: bytes, status: int, tipo="application/xml",
                  etag: str = ""):
        self.send_response(status)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(cuerpo)))
        if etag:
            self.send_header("ETag", etag)
        self.end_headers()
        self.wfile.write(cuerpo)

    def _cuerpo(self) -> str:
        largo = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(largo).decode() if largo else ""

    def _registra(self, cuerpo=""):
        FakeCalDav.peticiones.append({
            "metodo": self.command, "ruta": self.path,
            "if_match": self.headers.get("If-Match", ""),
            "if_none_match": self.headers.get("If-None-Match", ""),
            "cuerpo": cuerpo,
        })

    @classmethod
    def guarda(cls, ruta: str, ics: str) -> str:
        cls.versiones += 1
        etag = f'"v{cls.versiones}"'
        cls.recursos[ruta] = {"ics": ics, "etag": etag}
        return etag

    def do_REPORT(self):  # noqa: N802
        self._registra(self._cuerpo())
        respuestas = "".join(
            f"<D:response><D:href>{ruta}</D:href><D:propstat><D:prop>"
            f"<D:getetag>{datos['etag']}</D:getetag>"
            f"<C:calendar-data>{datos['ics']}</C:calendar-data>"
            f"</D:prop><D:status>HTTP/1.1 200 OK</D:status></D:propstat>"
            f"</D:response>"
            for ruta, datos in FakeCalDav.recursos.items())
        xml = ('<?xml version="1.0" encoding="utf-8"?>'
               '<D:multistatus xmlns:D="DAV:" '
               'xmlns:C="urn:ietf:params:xml:ns:caldav">'
               f"{respuestas}</D:multistatus>")
        self._responde(xml.encode(), 207)

    def do_GET(self):  # noqa: N802
        self._registra()
        if (datos := FakeCalDav.recursos.get(self.path)) is None:
            self._responde(b"no", 404)
            return
        self._responde(datos["ics"].encode(), 200, "text/calendar", datos["etag"])

    def do_PUT(self):  # noqa: N802
        cuerpo = self._cuerpo()
        self._registra(cuerpo)
        if FakeCalDav.forzar:
            self._responde(b"no", FakeCalDav.forzar)
            return
        existente = FakeCalDav.recursos.get(self.path)
        if (etiqueta := self.headers.get("If-Match")) and (
                existente is None or existente["etag"] != etiqueta):
            self._responde(b"cambiada", 412)
            return
        if self.headers.get("If-None-Match") == "*" and existente is not None:
            self._responde(b"existe", 412)
            return
        etag = FakeCalDav.guarda(self.path, cuerpo)
        self._responde(b"", 201, etag=etag)

    def do_DELETE(self):  # noqa: N802
        self._registra()
        if FakeCalDav.forzar:
            self._responde(b"no", FakeCalDav.forzar)
            return
        existente = FakeCalDav.recursos.get(self.path)
        if (etiqueta := self.headers.get("If-Match")) and (
                existente is None or existente["etag"] != etiqueta):
            self._responde(b"cambiada", 412)
            return
        FakeCalDav.recursos.pop(self.path, None)
        self._responde(b"", 204)


class CasoConServidor(unittest.IsolatedAsyncioTestCase):
    """Monta el servidor, la fuente, el índice y la caja de herramientas."""

    def setUp(self):
        FakeCalDav.recursos = {}
        FakeCalDav.peticiones = []
        FakeCalDav.versiones = 0
        FakeCalDav.forzar = None
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeCalDav)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.coleccion = "/calendars/yelko/personal"

        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "index.db")
        with mock.patch.dict(os.environ, {"JARVIS_CALDAV_PASSWORD": "secreto"}):
            self.calendario = CalendarSource(
                caldav_url=f"{self.base}{self.coleccion}", caldav_user="yelko",
                days_back=1, days_ahead=60)
        cfg = Config({
            "tools": {"allow_system": True,
                      "notes_file": str(Path(self.tmp.name) / "n.json"),
                      "memory_file": str(Path(self.tmp.name) / "m.json")},
            "llm": {"web_search": False},
        })
        self.toolbox = Toolbox(cfg, EventBus(), store=self.store,
                               calendar=self.calendario)

    def tearDown(self):
        self.server.shutdown()
        self.store.close()
        self.tmp.cleanup()

    # -- utilidades --------------------------------------------------------
    def proximo_lunes(self, hora=8, minuto=30) -> datetime:
        ahora = datetime.now(UTC)
        dias = (7 - ahora.weekday()) % 7 or 7
        return (ahora + timedelta(days=dias)).replace(
            hour=hora, minute=minuto, second=0, microsecond=0)

    def pon_suelta(self, cuando: datetime) -> str:
        ics = calendario(SUELTA.format(inicio=marca(cuando),
                                       fin=marca(cuando + timedelta(hours=1))))
        FakeCalDav.guarda(f"{self.coleccion}/dentista.ics", ics)
        self.calendario.sync(self.store)
        return ics

    def pon_serie(self, cuando: datetime) -> str:
        ics = calendario(SERIE.format(inicio=marca(cuando),
                                      fin=marca(cuando + timedelta(minutes=15))))
        FakeCalDav.guarda(f"{self.coleccion}/standup.ics", ics)
        self.calendario.sync(self.store)
        return ics

    def guardado(self, nombre: str) -> str:
        return FakeCalDav.recursos[f"{self.coleccion}/{nombre}"]["ics"]

    async def confirma(self, herramienta: str, args: dict) -> str:
        """Los dos pasos seguidos, que es lo que hace Jarvis tras oír el sí."""
        await self.toolbox.run(herramienta, dict(args))
        return await self.toolbox.run(herramienta, {**args, "confirmar": True})


class TestSincronizarGuardaLaReferencia(CasoConServidor):
    async def test_el_indice_guarda_href_y_etag(self):
        self.pon_suelta(self.proximo_lunes())
        fila, = self.store.recent(source="calendario", limit=5)
        meta = self.toolbox._referencia(fila)
        self.assertTrue(meta["href"].endswith("/dentista.ics"))
        self.assertTrue(meta["href"].startswith("http"), "el href viene relativo")
        self.assertTrue(meta["etag"])
        self.assertEqual(meta["uid"], "dentista")

    async def test_una_excepcion_no_duplica_el_dia(self):
        """El maestro y su excepción tienen el mismo UID: es un día, no dos."""
        lunes = self.proximo_lunes()
        ics = self.pon_serie(lunes)
        movido = lunes + timedelta(days=7)
        FakeCalDav.guarda(f"{self.coleccion}/standup.ics",
                          add_override(ics, "standup", movido,
                                       movido + timedelta(hours=4),
                                       movido + timedelta(hours=4, minutes=15)))
        self.store.delete_prefix("calendario:")
        self.calendario.sync(self.store)

        fechas = [f["created_at"][:10]
                  for f in self.store.upcoming(source="calendario", limit=50)]
        self.assertEqual(len(fechas), len(set(fechas)), "un lunes, una entrada")
        horas = {f["created_at"] for f in self.store.upcoming(source="calendario",
                                                              limit=50)}
        self.assertIn(movido.replace(hour=12, minute=30).isoformat(), horas)

    async def test_una_excepcion_cancelada_desaparece(self):
        lunes = self.proximo_lunes()
        ics = self.pon_serie(lunes)
        quitado = lunes + timedelta(days=7)
        con = add_override(ics, "standup", quitado, quitado, quitado)
        con = con.replace("RECURRENCE-ID:", "STATUS:CANCELLED\r\nRECURRENCE-ID:")
        FakeCalDav.guarda(f"{self.coleccion}/standup.ics", con)
        self.store.delete_prefix("calendario:")
        self.calendario.sync(self.store)

        fechas = [f["created_at"][:10]
                  for f in self.store.upcoming(source="calendario", limit=50)]
        self.assertNotIn(quitado.date().isoformat(), fechas)


class TestMover(CasoConServidor):
    async def test_el_primer_paso_no_toca_el_servidor(self):
        cuando = self.proximo_lunes(17)
        self.pon_suelta(cuando)
        FakeCalDav.peticiones = []
        nuevo = (cuando + timedelta(days=1)).isoformat(timespec="minutes")

        salida = await self.toolbox.run("mover_cita",
                                        {"cual": "dentista", "nuevo_inicio": nuevo})
        self.assertIn("Sin mover todavía", salida)
        self.assertIn("Dentista", salida)
        self.assertEqual([p["metodo"] for p in FakeCalDav.peticiones], [])

    async def test_confirmar_de_entrada_no_mueve_nada(self):
        cuando = self.proximo_lunes(17)
        self.pon_suelta(cuando)
        FakeCalDav.peticiones = []
        nuevo = (cuando + timedelta(days=1)).isoformat(timespec="minutes")

        salida = await self.toolbox.run("mover_cita", {
            "cual": "dentista", "nuevo_inicio": nuevo, "confirmar": True})
        self.assertIn("Sin mover todavía", salida)
        self.assertNotIn("PUT", [p["metodo"] for p in FakeCalDav.peticiones])

    async def test_mueve_una_cita_suelta(self):
        cuando = self.proximo_lunes(17)
        self.pon_suelta(cuando)
        nuevo = cuando + timedelta(days=1)

        salida = await self.confirma("mover_cita", {
            "cual": "dentista",
            "nuevo_inicio": nuevo.isoformat(timespec="minutes")})
        self.assertIn("Movida", salida)
        guardado = self.guardado("dentista.ics")
        self.assertIn(f"DTSTART:{marca(nuevo)}", guardado)
        self.assertIn("BEGIN:VALARM", guardado, "el recordatorio sigue ahí")

    async def test_el_put_va_condicionado_al_etag(self):
        cuando = self.proximo_lunes(17)
        self.pon_suelta(cuando)
        await self.confirma("mover_cita", {
            "cual": "dentista",
            "nuevo_inicio": (cuando + timedelta(days=1)).isoformat(timespec="minutes")})
        put = [p for p in FakeCalDav.peticiones if p["metodo"] == "PUT"][-1]
        self.assertTrue(put["if_match"], "sin If-Match se pisaría a quien haya tocado")

    async def test_si_cambio_por_otro_lado_no_se_pisa(self):
        """Alguien la mueve desde el móvil entre la lectura y el sí."""
        cuando = self.proximo_lunes(17)
        self.pon_suelta(cuando)
        nuevo = (cuando + timedelta(days=1)).isoformat(timespec="minutes")
        await self.toolbox.run("mover_cita", {"cual": "dentista",
                                              "nuevo_inicio": nuevo})
        FakeCalDav.forzar = 412

        salida = await self.toolbox.run("mover_cita", {
            "cual": "dentista", "nuevo_inicio": nuevo, "confirmar": True})
        self.assertIn("ha cambiado", salida)

    async def test_el_indice_queda_como_el_servidor(self):
        cuando = self.proximo_lunes(17)
        self.pon_suelta(cuando)
        nuevo = cuando + timedelta(days=1)
        await self.confirma("mover_cita", {
            "cual": "dentista",
            "nuevo_inicio": nuevo.isoformat(timespec="minutes")})

        fechas = [f["created_at"] for f in self.store.upcoming(source="calendario",
                                                               limit=10)]
        self.assertEqual(fechas, [nuevo.isoformat()],
                         "la ocurrencia vieja no puede quedarse de fantasma")

    async def test_mover_un_dia_de_una_serie_no_toca_el_resto(self):
        lunes = self.proximo_lunes()
        self.pon_serie(lunes)
        objetivo = lunes + timedelta(days=7)
        nuevo = objetivo.replace(hour=12)

        salida = await self.confirma("mover_cita", {
            "cual": "standup", "fecha": objetivo.date().isoformat(),
            "nuevo_inicio": nuevo.isoformat(timespec="minutes")})
        self.assertIn("Movida", salida)

        guardado = self.guardado("standup.ics")
        self.assertIn("RRULE:", guardado, "la serie sigue")
        self.assertIn(f"RECURRENCE-ID:{marca(objetivo)}", guardado)
        fechas = {f["created_at"] for f in self.store.upcoming(source="calendario",
                                                               limit=50)}
        self.assertIn(nuevo.isoformat(), fechas)
        self.assertIn(lunes.isoformat(), fechas, "el primer lunes no se movió")

    async def test_la_lectura_avisa_de_que_es_solo_ese_dia(self):
        lunes = self.proximo_lunes()
        self.pon_serie(lunes)
        salida = await self.toolbox.run("mover_cita", {
            "cual": "standup",
            "nuevo_inicio": lunes.replace(hour=12).isoformat(timespec="minutes")})
        self.assertIn("solo ese día", salida)

    async def test_si_no_encuentra_la_cita_lo_dice(self):
        salida = await self.toolbox.run("mover_cita", {
            "cual": "masaje", "nuevo_inicio": self.proximo_lunes().isoformat()})
        self.assertIn("No encuentro", salida)

    async def test_si_hay_varias_pregunta(self):
        lunes = self.proximo_lunes(17)
        self.pon_suelta(lunes)
        otra = calendario(SUELTA.format(inicio=marca(lunes + timedelta(days=2)),
                                        fin=marca(lunes + timedelta(days=2, hours=1)))
                          .replace("UID:dentista", "UID:dentista-2"))
        FakeCalDav.guarda(f"{self.coleccion}/dentista2.ics", otra)
        self.calendario.sync(self.store)

        salida = await self.toolbox.run("mover_cita", {
            "cual": "dentista", "nuevo_inicio": lunes.isoformat(timespec="minutes")})
        self.assertIn("Hay varias", salida)

    async def test_las_tildes_no_estorban(self):
        cuando = self.proximo_lunes(17)
        ics = calendario(SUELTA.format(inicio=marca(cuando),
                                       fin=marca(cuando + timedelta(hours=1)))
                         .replace("SUMMARY:Dentista", "SUMMARY:Revisión médica"))
        FakeCalDav.guarda(f"{self.coleccion}/dentista.ics", ics)
        self.calendario.sync(self.store)

        salida = await self.toolbox.run("mover_cita", {
            "cual": "revision medica",
            "nuevo_inicio": cuando.isoformat(timespec="minutes")})
        self.assertIn("Sin mover todavía", salida)

    async def test_una_fecha_ilegible_se_rechaza(self):
        self.pon_suelta(self.proximo_lunes(17))
        salida = await self.toolbox.run("mover_cita", {"cual": "dentista",
                                                       "nuevo_inicio": "mañana"})
        self.assertIn("ISO 8601", salida)

    async def test_tras_leer_contenido_externo_no_escribe(self):
        cuando = self.proximo_lunes(17)
        self.pon_suelta(cuando)
        nuevo = (cuando + timedelta(days=1)).isoformat(timespec="minutes")
        await self.toolbox.run("mover_cita", {"cual": "dentista",
                                              "nuevo_inicio": nuevo})
        self.toolbox._external("un correo cualquiera")
        FakeCalDav.peticiones = []

        salida = await self.toolbox.run("mover_cita", {
            "cual": "dentista", "nuevo_inicio": nuevo, "confirmar": True})
        self.assertIn("contenido de fuera", salida)
        self.assertNotIn("PUT", [p["metodo"] for p in FakeCalDav.peticiones])


class TestCancelar(CasoConServidor):
    async def test_el_primer_paso_no_borra_nada(self):
        self.pon_suelta(self.proximo_lunes(17))
        salida = await self.toolbox.run("cancelar_cita", {"cual": "dentista"})
        self.assertIn("Sin cancelar todavía", salida)
        self.assertIn(f"{self.coleccion}/dentista.ics", FakeCalDav.recursos)

    async def test_cancela_una_cita_suelta(self):
        self.pon_suelta(self.proximo_lunes(17))
        salida = await self.confirma("cancelar_cita", {"cual": "dentista"})
        self.assertIn("Cancelada", salida)
        self.assertNotIn(f"{self.coleccion}/dentista.ics", FakeCalDav.recursos)
        self.assertEqual(self.store.counts(), {})

    async def test_el_delete_va_condicionado_al_etag(self):
        self.pon_suelta(self.proximo_lunes(17))
        await self.confirma("cancelar_cita", {"cual": "dentista"})
        borrado = [p for p in FakeCalDav.peticiones if p["metodo"] == "DELETE"][-1]
        self.assertTrue(borrado["if_match"])

    async def test_cancelar_un_dia_de_una_serie_no_borra_la_serie(self):
        lunes = self.proximo_lunes()
        self.pon_serie(lunes)
        objetivo = lunes + timedelta(days=7)

        salida = await self.confirma("cancelar_cita", {
            "cual": "standup", "fecha": objetivo.date().isoformat()})
        self.assertIn("solo la del", salida)
        guardado = self.guardado("standup.ics")
        self.assertIn("RRULE:", guardado)
        self.assertIn(f"EXDATE:{marca(objetivo)}", guardado)

        fechas = {f["created_at"][:10]
                  for f in self.store.upcoming(source="calendario", limit=50)}
        self.assertNotIn(objetivo.date().isoformat(), fechas)
        self.assertIn(lunes.date().isoformat(), fechas)

    async def test_la_lectura_dice_que_es_solo_ese_dia(self):
        self.pon_serie(self.proximo_lunes())
        salida = await self.toolbox.run("cancelar_cita", {"cual": "standup"})
        self.assertIn("el resto de la serie se queda", salida)

    async def test_toda_la_serie_avisa_en_mayusculas(self):
        self.pon_serie(self.proximo_lunes())
        salida = await self.toolbox.run("cancelar_cita", {"cual": "standup",
                                                          "toda_la_serie": True})
        self.assertIn("TODAS sus repeticiones", salida)

    async def test_toda_la_serie_si_borra_el_recurso(self):
        self.pon_serie(self.proximo_lunes())
        salida = await self.confirma("cancelar_cita", {"cual": "standup",
                                                       "toda_la_serie": True})
        self.assertIn("Cancelada", salida)
        self.assertNotIn(f"{self.coleccion}/standup.ics", FakeCalDav.recursos)
        self.assertEqual(self.store.counts(), {})

    async def test_pedir_toda_la_serie_cambia_la_propuesta(self):
        """Confirmar «solo ese día» no puede valer para borrar la serie entera."""
        self.pon_serie(self.proximo_lunes())
        await self.toolbox.run("cancelar_cita", {"cual": "standup"})
        salida = await self.toolbox.run("cancelar_cita", {
            "cual": "standup", "toda_la_serie": True, "confirmar": True})
        self.assertIn("Sin cancelar todavía", salida)
        self.assertIn(f"{self.coleccion}/standup.ics", FakeCalDav.recursos)

    async def test_cancelar_un_dia_ya_movido(self):
        """Ese día tenía excepción: hay que quitarla y además excluirlo."""
        lunes = self.proximo_lunes()
        self.pon_serie(lunes)
        objetivo = lunes + timedelta(days=7)
        await self.confirma("mover_cita", {
            "cual": "standup", "fecha": objetivo.date().isoformat(),
            "nuevo_inicio": objetivo.replace(hour=12).isoformat(timespec="minutes")})

        salida = await self.confirma("cancelar_cita", {
            "cual": "standup", "fecha": objetivo.date().isoformat()})
        self.assertIn("Cancelada", salida)
        guardado = self.guardado("standup.ics")
        self.assertEqual(len(parse_events(guardado)), 1, "la excepción se va")
        self.assertIn(f"EXDATE:{marca(objetivo)}", guardado)
        fechas = {f["created_at"][:10]
                  for f in self.store.upcoming(source="calendario", limit=50)}
        self.assertNotIn(objetivo.date().isoformat(), fechas)

    async def test_una_confirmacion_no_sirve_dos_veces(self):
        self.pon_suelta(self.proximo_lunes(17))
        await self.confirma("cancelar_cita", {"cual": "dentista"})
        salida = await self.toolbox.run("cancelar_cita", {"cual": "dentista",
                                                          "confirmar": True})
        self.assertIn("No encuentro", salida)

    async def test_tras_leer_contenido_externo_no_borra(self):
        self.pon_suelta(self.proximo_lunes(17))
        await self.toolbox.run("cancelar_cita", {"cual": "dentista"})
        self.toolbox._external("un correo cualquiera")
        salida = await self.toolbox.run("cancelar_cita", {"cual": "dentista",
                                                          "confirmar": True})
        self.assertIn("contenido de fuera", salida)
        self.assertIn(f"{self.coleccion}/dentista.ics", FakeCalDav.recursos)


class TestSoloLectura(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "index.db")
        self.calendario = CalendarSource(ics=["https://ejemplo.com/c.ics"])
        cfg = Config({"tools": {"allow_system": True}, "llm": {"web_search": False}})
        self.toolbox = Toolbox(cfg, EventBus(), store=self.store,
                               calendar=self.calendario)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    async def test_las_herramientas_de_escritura_no_se_ofrecen(self):
        nombres = [spec["name"] for spec in self.toolbox.definitions()]
        for herramienta in ("crear_cita", "mover_cita", "cancelar_cita"):
            self.assertNotIn(herramienta, nombres)

    async def test_y_si_se_llaman_lo_explican(self):
        self.assertIn("solo lectura",
                      await self.toolbox.run("mover_cita", {"cual": "x",
                                                            "nuevo_inicio": "2026-01-01"}))
        self.assertIn("solo lectura",
                      await self.toolbox.run("cancelar_cita", {"cual": "x"}))


if __name__ == "__main__":
    unittest.main()
