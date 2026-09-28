"""Recordatorios: los que vencen, los que se repiten y los que se perdieron."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis.bus import EventBus  # noqa: E402
from jarvis.config import Config  # noqa: E402
from jarvis.llm.tools import Toolbox  # noqa: E402
from jarvis.recordatorios import (  # noqa: E402
    RETRASO_MAXIMO, Recordatorios, siguiente,
)

UTC = timezone.utc


class TestSiguiente(unittest.TestCase):
    def test_diario_y_semanal(self):
        base = datetime(2026, 9, 28, 9, 0, tzinfo=UTC)
        self.assertEqual(siguiente(base, "diario"), base + timedelta(days=1))
        self.assertEqual(siguiente(base, "semanal"), base + timedelta(weeks=1))

    def test_mensual_conserva_el_dia(self):
        base = datetime(2026, 9, 15, 9, 0, tzinfo=UTC)
        self.assertEqual(siguiente(base, "mensual").date().isoformat(), "2026-10-15")

    def test_mensual_en_diciembre_salta_de_año(self):
        base = datetime(2026, 12, 10, 9, 0, tzinfo=UTC)
        self.assertEqual(siguiente(base, "mensual").date().isoformat(), "2027-01-10")

    def test_el_31_cae_en_el_ultimo_dia_del_mes_corto(self):
        """Del 31 de enero no se pasa al 3 de marzo."""
        base = datetime(2026, 1, 31, 9, 0, tzinfo=UTC)
        self.assertEqual(siguiente(base, "mensual").date().isoformat(), "2026-02-28")

    def test_sin_repeticion(self):
        base = datetime(2026, 9, 28, 9, 0, tzinfo=UTC)
        self.assertIsNone(siguiente(base, ""))
        self.assertIsNone(siguiente(base, "cuando sea"))


class CasoConFichero(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fichero = Path(self.tmp.name) / "recordatorios.json"
        self.recordatorios = Recordatorios(self.fichero)
        self.ahora = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)

    def tearDown(self):
        self.tmp.cleanup()

    def pon(self, texto, dentro_de: timedelta, repetir="") -> dict:
        return self.recordatorios.pon(texto, self.ahora + dentro_de, repetir)


class TestAlmacen(CasoConFichero):
    def test_poner_y_ver(self):
        self.pon("llamar al fontanero", timedelta(hours=2))
        pendientes = self.recordatorios.pendientes()
        self.assertEqual(len(pendientes), 1)
        self.assertEqual(pendientes[0]["texto"], "llamar al fontanero")
        self.assertFalse(pendientes[0]["hecho"])

    def test_se_ordenan_por_fecha(self):
        self.pon("tarde", timedelta(days=2))
        self.pon("pronto", timedelta(hours=1))
        self.assertEqual([r["texto"] for r in self.recordatorios.pendientes()],
                         ["pronto", "tarde"])

    def test_sobrevive_al_reinicio(self):
        """Es la diferencia con un temporizador: no muere con el proceso."""
        self.pon("llamar al fontanero", timedelta(days=3))
        self.assertEqual(len(Recordatorios(self.fichero).pendientes()), 1)

    def test_una_repeticion_invalida_se_ignora(self):
        resultado = self.pon("x", timedelta(hours=1), repetir="cada rato")
        self.assertEqual(resultado["recordatorio"]["repetir"], "")

    def test_borrar(self):
        puesto = self.pon("x", timedelta(hours=1))["recordatorio"]
        self.assertTrue(self.recordatorios.borra(puesto["id"]))
        self.assertEqual(self.recordatorios.pendientes(), [])
        self.assertFalse(self.recordatorios.borra(puesto["id"]))

    def test_buscar_sin_tildes_y_exacto_primero(self):
        self.pon("Llamar al fontanero", timedelta(hours=1))
        self.pon("Llamar al fontanero otra vez", timedelta(hours=2))
        self.assertEqual(
            [r["texto"] for r in self.recordatorios.busca("llamar al fontanero")],
            ["Llamar al fontanero"])
        self.assertEqual(len(self.recordatorios.busca("fontanero")), 2)

    def test_un_json_roto_no_revienta(self):
        self.fichero.write_text("{roto", encoding="utf-8")
        self.assertEqual(self.recordatorios.pendientes(), [])
        self.pon("x", timedelta(hours=1))
        self.assertEqual(len(self.recordatorios.pendientes()), 1)

    def test_hay_un_tope(self):
        from jarvis.recordatorios import MAX_RECORDATORIOS

        for n in range(MAX_RECORDATORIOS):
            self.pon(f"cosa {n}", timedelta(hours=1))
        self.assertFalse(self.pon("una más", timedelta(hours=1))["ok"])


class TestVencer(CasoConFichero):
    def test_el_que_aun_no_toca_no_suena(self):
        self.pon("luego", timedelta(hours=1))
        self.assertEqual(self.recordatorios.vencidos(self.ahora), [])

    def test_el_que_toca_suena_una_vez(self):
        self.pon("ahora", timedelta(minutes=-1))
        self.assertEqual(len(self.recordatorios.vencidos(self.ahora)), 1)
        self.assertEqual(self.recordatorios.vencidos(self.ahora), [],
                         "no puede sonar dos veces")
        self.assertEqual(self.recordatorios.pendientes(), [])

    def test_se_marca_antes_de_avisar(self):
        """Mejor callar uno que soltarlo en bucle cada treinta segundos."""
        self.pon("ahora", timedelta(minutes=-1))
        self.recordatorios.vencidos(self.ahora)
        datos = json.loads(self.fichero.read_text(encoding="utf-8"))
        self.assertTrue(datos[0]["hecho"])

    def test_el_retraso_se_cuenta(self):
        self.pon("hace rato", timedelta(minutes=-30))
        vencido, = self.recordatorios.vencidos(self.ahora)
        self.assertAlmostEqual(vencido["retraso"], 1800, delta=1)

    def test_uno_muy_viejo_se_marca_perdido_y_no_suena(self):
        """Soltar el de anteayer al arrancar es una avalancha inútil."""
        self.pon("de anteayer", -RETRASO_MAXIMO - timedelta(hours=1))
        self.assertEqual(self.recordatorios.vencidos(self.ahora), [])
        self.assertEqual(len(self.recordatorios.perdidos()), 1)
        self.assertEqual(self.recordatorios.pendientes(), [])

    def test_uno_de_hace_un_rato_si_suena(self):
        self.pon("de hace poco", -RETRASO_MAXIMO + timedelta(hours=1))
        self.assertEqual(len(self.recordatorios.vencidos(self.ahora)), 1)

    def test_uno_que_se_repite_no_se_cierra(self):
        self.pon("la pastilla", timedelta(minutes=-1), repetir="diario")
        self.assertEqual(len(self.recordatorios.vencidos(self.ahora)), 1)

        pendientes = self.recordatorios.pendientes()
        self.assertEqual(len(pendientes), 1, "sigue vivo para mañana")
        proxima = datetime.fromisoformat(pendientes[0]["cuando"])
        self.assertGreater(proxima, self.ahora)
        self.assertEqual(self.recordatorios.vencidos(self.ahora), [])

    def test_tras_varios_dias_apagado_solo_suena_una_vez(self):
        """No se encadenan las diez que se perdieron: se adelanta al futuro."""
        self.pon("la pastilla", timedelta(days=-10), repetir="diario")
        disparados = self.recordatorios.vencidos(self.ahora)
        self.assertEqual(len(disparados), 0, "hace diez días es demasiado viejo")

        proxima = datetime.fromisoformat(
            self.recordatorios.pendientes()[0]["cuando"])
        self.assertGreater(proxima, self.ahora)
        self.assertLess(proxima, self.ahora + timedelta(days=1, minutes=1))

    def test_una_fecha_ilegible_se_salta(self):
        self.pon("bueno", timedelta(minutes=-1))
        datos = json.loads(self.fichero.read_text(encoding="utf-8"))
        datos.append({"id": "x", "texto": "malo", "cuando": "mañana",
                      "hecho": False, "repetir": ""})
        self.fichero.write_text(json.dumps(datos), encoding="utf-8")
        self.assertEqual([v["texto"] for v in
                          self.recordatorios.vencidos(self.ahora)], ["bueno"])


class TestHerramientas(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fichero = Path(self.tmp.name) / "recordatorios.json"
        self.dicho: list[str] = []
        cfg = Config({
            "tools": {"allow_system": True,
                      "notes_file": str(Path(self.tmp.name) / "n.json"),
                      "memory_file": str(Path(self.tmp.name) / "m.json"),
                      "lists_file": str(Path(self.tmp.name) / "l.json"),
                      "reminders_file": str(self.fichero)},
            "llm": {"web_search": False},
        })
        with mock.patch.dict(os.environ, {"JARVIS_HASS_TOKEN": ""}):
            self.toolbox = Toolbox(cfg, EventBus(), on_announce=self.anuncia)

    async def anuncia(self, mensaje: str) -> None:
        self.dicho.append(mensaje)

    def tearDown(self):
        self.tmp.cleanup()

    def dentro_de(self, **kwargs) -> str:
        cuando = datetime.now().astimezone() + timedelta(**kwargs)
        return cuando.replace(microsecond=0).isoformat(timespec="minutes")

    async def test_poner_uno(self):
        salida = await self.toolbox.run("poner_recordatorio", {
            "texto": "llamar al fontanero", "cuando": self.dentro_de(days=1)})
        self.assertIn("Apuntado", salida)
        self.assertIn("llamar al fontanero", salida)

    async def test_la_fecha_se_dice_en_castellano(self):
        salida = await self.toolbox.run("poner_recordatorio", {
            "texto": "x", "cuando": self.dentro_de(days=1)})
        self.assertTrue(any(dia in salida for dia in Toolbox.DIAS), salida)

    async def test_una_hora_pasada_se_rechaza(self):
        salida = await self.toolbox.run("poner_recordatorio", {
            "texto": "x", "cuando": self.dentro_de(hours=-2)})
        self.assertIn("ya ha pasado", salida)
        self.assertEqual(Recordatorios(self.fichero).pendientes(), [])

    async def test_una_fecha_ilegible_se_rechaza(self):
        salida = await self.toolbox.run("poner_recordatorio",
                                        {"texto": "x", "cuando": "mañana"})
        self.assertIn("ISO 8601", salida)

    async def test_sin_texto(self):
        self.assertIn("¿Qué le recuerdo?",
                      await self.toolbox.run("poner_recordatorio",
                                             {"texto": " ", "cuando": "x"}))

    async def test_la_cadencia_se_dice(self):
        salida = await self.toolbox.run("poner_recordatorio", {
            "texto": "la pastilla", "cuando": self.dentro_de(hours=2),
            "repetir": "diario"})
        self.assertIn("diario", salida)

    async def test_verlos(self):
        await self.toolbox.run("poner_recordatorio", {
            "texto": "el fontanero", "cuando": self.dentro_de(days=1)})
        salida = await self.toolbox.run("ver_recordatorios", {})
        self.assertIn("el fontanero", salida)

    async def test_sin_ninguno(self):
        self.assertIn("No tienes recordatorios",
                      await self.toolbox.run("ver_recordatorios", {}))

    async def test_los_perdidos_se_cuentan(self):
        Recordatorios(self.fichero).pon(
            "viejo", datetime.now(UTC) - RETRASO_MAXIMO - timedelta(hours=1))
        await self.toolbox.dispara_recordatorios()
        salida = await self.toolbox.run("ver_recordatorios", {})
        self.assertIn("se perdieron 1", salida)

    async def test_borrar_pide_confirmacion(self):
        await self.toolbox.run("poner_recordatorio", {
            "texto": "el fontanero", "cuando": self.dentro_de(days=1)})
        salida = await self.toolbox.run("borrar_recordatorio",
                                        {"cual": "fontanero"})
        self.assertIn("Sin borrar todavía", salida)
        self.assertEqual(len(Recordatorios(self.fichero).pendientes()), 1)

    async def test_y_confirmando_se_borra(self):
        await self.toolbox.run("poner_recordatorio", {
            "texto": "el fontanero", "cuando": self.dentro_de(days=1)})
        await self.toolbox.run("borrar_recordatorio", {"cual": "fontanero"})
        salida = await self.toolbox.run("borrar_recordatorio",
                                        {"cual": "fontanero", "confirmar": True})
        self.assertIn("Borrado", salida)
        self.assertEqual(Recordatorios(self.fichero).pendientes(), [])

    async def test_el_que_no_existe(self):
        self.assertIn("No tengo ningún recordatorio",
                      await self.toolbox.run("borrar_recordatorio",
                                             {"cual": "el dentista"}))

    async def test_si_hay_varios_pregunta(self):
        for n in (1, 2):
            await self.toolbox.run("poner_recordatorio", {
                "texto": f"llamar a {n}", "cuando": self.dentro_de(days=n)})
        salida = await self.toolbox.run("borrar_recordatorio", {"cual": "llamar"})
        self.assertIn("Hay varios", salida)

    # -- el disparo --------------------------------------------------------
    async def test_disparar_lo_dice_en_voz_alta(self):
        Recordatorios(self.fichero).pon(
            "llamar al fontanero", datetime.now(UTC) - timedelta(seconds=10))
        self.assertEqual(await self.toolbox.dispara_recordatorios(), 1)
        self.assertEqual(self.dicho, ["Recordatorio: llamar al fontanero."])

    async def test_el_retraso_se_dice(self):
        Recordatorios(self.fichero).pon(
            "el fontanero", datetime.now(UTC) - timedelta(minutes=40))
        await self.toolbox.dispara_recordatorios()
        self.assertIn("40 minutos de retraso", self.dicho[0])

    async def test_no_se_dispara_dos_veces(self):
        Recordatorios(self.fichero).pon(
            "x", datetime.now(UTC) - timedelta(seconds=10))
        await self.toolbox.dispara_recordatorios()
        self.assertEqual(await self.toolbox.dispara_recordatorios(), 0)
        self.assertEqual(len(self.dicho), 1)

    async def test_lo_que_no_toca_no_se_dispara(self):
        await self.toolbox.run("poner_recordatorio", {
            "texto": "x", "cuando": self.dentro_de(days=1)})
        self.assertEqual(await self.toolbox.dispara_recordatorios(), 0)
        self.assertEqual(self.dicho, [])


if __name__ == "__main__":
    unittest.main()
