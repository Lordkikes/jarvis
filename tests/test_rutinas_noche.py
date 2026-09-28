"""Los pasos de noche: apagar, repasar la casa y mirar lo de mañana.

La máquina de rutinas ya estaba; lo que faltaba era el vocabulario de irse a
dormir, que es casi el contrario del de levantarse. Y una regla nueva: el
repaso de la casa **mira y no toca**, ni siquiera lo que se ha quedado
abierto.
"""
from __future__ import annotations

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
from jarvis.domotica import SinConexion  # noqa: E402
from jarvis.llm.tools import Toolbox  # noqa: E402
from jarvis.rutinas import ACTUAN, CON_ARGUMENTO, PASOS, parse_pasos  # noqa: E402
from jarvis.sources import Item  # noqa: E402
from jarvis.sources.store import Store  # noqa: E402

LUNES = datetime(2026, 9, 28, 23, 0).astimezone()


def entidad(entity_id: str, state: str, nombre: str, **atributos) -> dict:
    return {"entity_id": entity_id, "state": state,
            "attributes": {"friendly_name": nombre, **atributos}}


class CasoConCaja(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "indice.db")
        cfg = Config({
            "tools": {"allow_system": True,
                      "notes_file": str(Path(self.tmp.name) / "n.json"),
                      "memory_file": str(Path(self.tmp.name) / "m.json"),
                      "lists_file": str(Path(self.tmp.name) / "l.json"),
                      "routines_file": str(Path(self.tmp.name) / "rut.json")},
            "llm": {"web_search": False},
        })
        with mock.patch.dict(os.environ, {"JARVIS_HASS_TOKEN": ""}):
            self.caja = Toolbox(cfg, EventBus(), store=self.store)

    def tearDown(self):
        if self.caja._reloj is not None:
            self.caja._reloj.cancel()
        self.store.close()
        self.tmp.cleanup()

    def con_casa(self, entidades: list[dict]) -> list:
        """Una casa de mentira, con su registro de lo que se le manda."""
        self.caja.casa.url = "http://casa"
        self.caja.casa.token = "un-token"
        self.caja.casa.estados = lambda refrescar=False: entidades
        llamadas: list = []
        self.caja.casa.llama = lambda *args, **kwargs: llamadas.append(args)
        return llamadas

    async def ejecuta(self, pasos: str) -> str:
        await self.caja.run("crear_rutina", {"nombre": "a dormir",
                                             "pasos": pasos})
        return await self.caja.run("ejecutar_rutina", {"cual": "a dormir"})


class TestVocabulario(unittest.TestCase):
    def test_los_pasos_de_noche_existen(self):
        for paso in ("manana", "repasar_casa", "apagar", "apagar_luces",
                     "parar_musica", "dormir_musica"):
            self.assertIn(paso, PASOS)

    def test_los_que_actuan_estan_marcados(self):
        """Si no, narrarían lo que hacen en mitad de la noche."""
        for paso in ("apagar", "apagar_luces", "parar_musica", "dormir_musica"):
            self.assertIn(paso, ACTUAN)

    def test_cuales_piden_argumento(self):
        self.assertIn("apagar", CON_ARGUMENTO)
        self.assertIn("dormir_musica", CON_ARGUMENTO)
        self.assertNotIn("apagar_luces", CON_ARGUMENTO)
        self.assertNotIn("parar_musica", CON_ARGUMENTO)

    def test_con_la_ñ_tambien(self):
        pasos, sobran = parse_pasos("mañana")
        self.assertEqual(pasos, [{"que": "manana", "con": ""}])
        self.assertEqual(sobran, [])


class TestManana(CasoConCaja):
    def indexa(self, titulo: str, cuando: datetime) -> None:
        self.store.upsert([Item(id=titulo, source="calendario", title=titulo,
                                created_at=cuando.astimezone(
                                    timezone.utc).isoformat(timespec="seconds"))])

    async def test_dice_lo_de_manana(self):
        self.indexa("el dentista", LUNES + timedelta(hours=10))
        with mock.patch("jarvis.llm.tools._ahora", return_value=LUNES):
            self.assertIn("el dentista", await self.ejecuta("manana"))

    async def test_no_dice_lo_que_queda_de_hoy(self):
        """A las once de la noche, lo de hoy ya no es noticia."""
        self.indexa("la cena", LUNES + timedelta(minutes=30))
        with mock.patch("jarvis.llm.tools._ahora", return_value=LUNES):
            salida = await self.ejecuta("manana")
        self.assertIn("Mañana no tienes nada", salida)

    async def test_ni_lo_de_pasado(self):
        self.indexa("el martes que viene", LUNES + timedelta(days=8))
        with mock.patch("jarvis.llm.tools._ahora", return_value=LUNES):
            self.assertIn("no tienes nada", await self.ejecuta("manana"))


class TestRepasoDeCasa(CasoConCaja):
    def casa_con(self, *entidades: dict) -> list:
        return self.con_casa(list(entidades))

    async def test_dice_lo_que_esta_abierto(self):
        self.casa_con(
            entidad("lock.puerta", "unlocked", "la puerta"),
            entidad("cover.persiana", "open", "la persiana"),
            entidad("cover.garaje", "closed", "el garaje"))
        salida = await self.ejecuta("repasar_casa")
        self.assertIn("la puerta", salida)
        self.assertIn("la persiana", salida)
        self.assertNotIn("el garaje", salida)

    async def test_una_ventana_abierta_cuenta(self):
        self.casa_con(entidad("binary_sensor.ventana", "on", "la ventana",
                              device_class="window"))
        self.assertIn("la ventana", await self.ejecuta("repasar_casa"))

    async def test_un_sensor_de_movimiento_no(self):
        """«on» en un detector de presencia no es una ventana abierta."""
        self.casa_con(
            entidad("binary_sensor.pasillo", "on", "el pasillo",
                    device_class="motion"),
            entidad("cover.persiana", "closed", "la persiana"))
        self.assertEqual(await self.ejecuta("repasar_casa"), "Todo cerrado.")

    async def test_con_todo_cerrado(self):
        self.casa_con(entidad("lock.puerta", "locked", "la puerta"))
        self.assertEqual(await self.ejecuta("repasar_casa"), "Todo cerrado.")

    async def test_sin_nada_que_vigilar_lo_explica(self):
        """Decir «todo cerrado» sin ver ninguna puerta sería mentir."""
        self.casa_con(entidad("light.salon", "on", "la luz"))
        salida = await self.ejecuta("repasar_casa")
        self.assertIn("No tengo puertas ni persianas", salida)
        self.assertIn("dominios", salida)

    async def test_mira_pero_no_toca(self):
        """Cerrarlo por su cuenta sería justo lo que una rutina no hace."""
        llamadas = self.casa_con(entidad("lock.puerta", "unlocked", "la puerta"))
        await self.ejecuta("repasar_casa")
        self.assertEqual(llamadas, [])

    async def test_sin_home_assistant_se_calla(self):
        self.assertEqual(await self.ejecuta("repasar_casa"),
                         "Hecha la rutina «a dormir».")

    async def test_si_la_casa_no_contesta_lo_dice(self):
        self.con_casa([])
        def revienta(refrescar=False):
            raise SinConexion("no contesta")
        self.caja.casa.estados = revienta
        self.assertIn("No consigo repasar la casa",
                      await self.ejecuta("repasar_casa"))


class TestApagar(CasoConCaja):
    async def test_apaga_un_dispositivo(self):
        llamadas = []
        self.caja._dispositivo = lambda que: entidad("light.salon", "on",
                                                     "la luz del salón")
        self.caja._tool_controlar_dispositivo = lambda args: llamadas.append(args)
        await self.ejecuta("apagar: la luz del salón")
        self.assertEqual(llamadas, [{"que": "la luz del salón",
                                     "accion": "apagar"}])

    async def test_no_cierra_una_cerradura(self):
        """`apagar` una cerradura es `lock`, y de noche hasta suena razonable.

        Por eso mismo tiene que decidirlo una voz y no una lista guardada.
        """
        llamadas = []
        self.caja._dispositivo = lambda que: entidad("lock.puerta", "unlocked",
                                                     "la puerta")
        self.caja._tool_controlar_dispositivo = lambda args: llamadas.append(args)
        await self.ejecuta("apagar: la puerta")
        self.assertEqual(llamadas, [])
        self.assertIsNone(self.caja._pendiente, "ni deja armada la propuesta")

    async def test_ni_baja_una_persiana(self):
        llamadas = []
        self.caja._dispositivo = lambda que: entidad("cover.persiana", "open",
                                                     "la persiana")
        self.caja._tool_controlar_dispositivo = lambda args: llamadas.append(args)
        await self.ejecuta("apagar: la persiana")
        self.assertEqual(llamadas, [])

    async def test_no_narra_lo_que_apaga(self):
        self.caja._dispositivo = lambda que: entidad("light.salon", "on", "la luz")
        self.caja._tool_controlar_dispositivo = lambda args: "Hecho."
        salida = await self.ejecuta("apagar: la luz, decir: buenas noches")
        self.assertEqual(salida, "buenas noches")


class TestApagarLuces(CasoConCaja):
    async def test_apaga_las_encendidas(self):
        llamadas = self.con_casa([
            entidad("light.salon", "on", "el salón"),
            entidad("light.cocina", "off", "la cocina"),
            entidad("light.pasillo", "on", "el pasillo")])
        await self.ejecuta("apagar_luces")
        self.assertEqual(llamadas, [("light", "turn_off", "light.salon"),
                                    ("light", "turn_off", "light.pasillo")])

    async def test_no_toca_los_enchufes(self):
        """Un enchufe apagado de madrugada puede ser la nevera."""
        llamadas = self.con_casa([entidad("switch.nevera", "on", "la nevera"),
                                  entidad("light.salon", "on", "el salón")])
        await self.ejecuta("apagar_luces")
        self.assertEqual(llamadas, [("light", "turn_off", "light.salon")])

    async def test_sin_luces_encendidas_no_llama_a_nadie(self):
        llamadas = self.con_casa([entidad("light.salon", "off", "el salón")])
        await self.ejecuta("apagar_luces")
        self.assertEqual(llamadas, [])

    async def test_lo_que_hizo_se_ve_en_la_interfaz(self):
        cola = self.caja.bus.subscribe()
        self.con_casa([entidad("light.salon", "on", "el salón")])
        await self.ejecuta("apagar_luces")

        mensajes = []
        while not cola.empty():
            evento = cola.get_nowait()
            if evento["type"] == "rutina":
                mensajes.append(evento.get("message", ""))
        self.assertTrue(any("apagadas 1" in m for m in mensajes), mensajes)


class TestMusicaDeNoche(CasoConCaja):
    async def test_parar(self):
        llamadas = []
        self.caja._tool_controlar_musica = lambda args: llamadas.append(args)
        await self.ejecuta("parar_musica")
        self.assertEqual(llamadas, [{"accion": "pausa"}])

    async def test_dormir_la_musica_pone_un_temporizador(self):
        await self.ejecuta("dormir_musica: 30")
        temporizadores = self.caja.temporizadores.lista()
        self.assertEqual(len(temporizadores), 1)
        self.assertEqual(temporizadores[0]["etiqueta"], "la música")
        self.assertEqual(temporizadores[0]["accion"], "parar_musica")
        self.assertAlmostEqual(temporizadores[0]["restante"], 30 * 60, places=1)

    async def test_no_lo_cuenta_en_voz_alta(self):
        salida = await self.ejecuta("dormir_musica: 30, decir: buenas noches")
        self.assertEqual(salida, "buenas noches")

    async def test_unos_minutos_que_no_son_minutos(self):
        await self.ejecuta("dormir_musica: un rato")
        self.assertEqual(self.caja.temporizadores.lista(), [])


class TestUnaNocheEntera(CasoConCaja):
    async def test_el_orden_y_el_silencio(self):
        self.con_casa([entidad("light.salon", "on", "el salón"),
                       entidad("cover.persiana", "open", "la persiana")])
        self.caja._tool_controlar_musica = lambda args: "pausada"
        salida = await self.ejecuta(
            "decir: buenas noches, manana, repasar_casa, apagar_luces, "
            "dormir_musica: 20")

        self.assertTrue(salida.startswith("buenas noches"), salida)
        self.assertIn("Mañana no tienes nada", salida)
        self.assertIn("la persiana", salida)
        self.assertNotIn("apagadas", salida, "lo que actúa no narra")
        self.assertEqual(len(self.caja.temporizadores.lista()), 1)


if __name__ == "__main__":
    unittest.main()
