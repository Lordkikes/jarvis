"""Temporizadores: la cuenta atrás, cómo se dice y cómo se maneja hablando.

El reloj se inyecta, así que «pasan veinte minutos» cuesta lo mismo que una
suma: las pruebas del módulo no duermen ni un milisegundo. Solo las del bucle
que los hace sonar usan tiempo real, y con cuentas de centésimas.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis.bus import EventBus  # noqa: E402
from jarvis.config import Config  # noqa: E402
from jarvis.llm.tools import Toolbox, _quedan  # noqa: E402
from jarvis.temporizadores import (  # noqa: E402
    MAX_SEGUNDOS, MAX_TEMPORIZADORES, Temporizadores, al, describe,
    en_palabras, nombre,
)


class Reloj:
    """Un monótono de mentira: avanza cuando se le dice, y solo entonces."""

    def __init__(self):
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def pasa(self, segundos: float) -> None:
        self.t += segundos


class TestEnPalabras(unittest.TestCase):
    def test_como_lo_diria_una_persona(self):
        casos = {
            0: "0 segundos", 1: "1 segundo", 45: "45 segundos",
            60: "1 minuto", 61: "1 minuto y 1 segundo",
            90: "1 minuto y 30 segundos", 600: "10 minutos",
            3599: "59 minutos y 59 segundos", 3600: "1 hora",
            3660: "1 hora y 1 minuto", 7200: "2 horas",
            MAX_SEGUNDOS: "24 horas",
        }
        for segundos, dicho in casos.items():
            with self.subTest(segundos=segundos):
                self.assertEqual(en_palabras(segundos), dicho)

    def test_pasada_la_hora_no_se_dicen_los_segundos(self):
        """A quien le quedan dos horas no le importan los segundos."""
        self.assertEqual(en_palabras(7210), "2 horas")
        self.assertEqual(en_palabras(7259), "2 horas y 1 minuto",
                         "pero se redondean al minuto, no se tiran")

    def test_lo_que_aun_corre_nunca_son_cero_segundos(self):
        """Medio segundo redondea a cero, y «quedan 0» sería mentira."""
        self.assertEqual(en_palabras(0.4), "1 segundo")

    def test_el_redondeo_no_se_come_un_minuto(self):
        """Tres minutos recién puestos ya llevan unas microsegundos gastadas."""
        self.assertEqual(en_palabras(179.9998), "3 minutos")

    def test_la_contraccion_que_obliga_el_castellano(self):
        self.assertEqual(al("el arroz"), "al arroz")
        self.assertEqual(al("la colada"), "a la colada")
        self.assertEqual(al("el de 10 minutos"), "al de 10 minutos")
        self.assertEqual(al("elote"), "a elote", "«el» suelto, no «elote»")

    def test_concuerda_el_verbo(self):
        self.assertEqual(_quedan(180), "quedan 3 minutos")
        self.assertEqual(_quedan(60), "queda 1 minuto")
        self.assertEqual(_quedan(181), "quedan 3 minutos y 1 segundo")


class CasoConReloj(unittest.TestCase):
    def setUp(self):
        self.reloj = Reloj()
        self.temporizadores = Temporizadores(reloj=self.reloj)

    def pon(self, segundos: float, etiqueta: str = "") -> dict:
        resultado = self.temporizadores.pon(segundos, etiqueta)
        self.assertTrue(resultado.get("ok"), resultado)
        return resultado["temporizador"]


class TestCuentaAtras(CasoConReloj):
    def test_cuenta_hacia_abajo(self):
        self.pon(600, "el arroz")
        self.reloj.pasa(240)
        self.assertEqual(self.temporizadores.lista()[0]["restante"], 360)

    def test_vence_cuando_toca_y_no_antes(self):
        self.pon(600, "el arroz")
        self.reloj.pasa(599)
        self.assertEqual(self.temporizadores.vencidos(), [])
        self.reloj.pasa(1)
        self.assertEqual([t["etiqueta"] for t in self.temporizadores.vencidos()],
                         ["el arroz"])

    def test_se_quita_antes_de_devolverlo(self):
        """Como los recordatorios: mejor callar uno que sonar en bucle."""
        self.pon(10, "el arroz")
        self.reloj.pasa(10)
        self.assertEqual(len(self.temporizadores.vencidos()), 1)
        self.assertEqual(self.temporizadores.vencidos(), [])
        self.assertEqual(self.temporizadores.lista(), [])

    def test_varios_vencidos_salen_en_orden(self):
        self.pon(20, "la colada")
        self.pon(10, "el arroz")
        self.reloj.pasa(30)
        self.assertEqual([t["etiqueta"] for t in self.temporizadores.vencidos()],
                         ["el arroz", "la colada"])

    def test_el_proximo_es_el_mas_cercano(self):
        self.pon(600, "la colada")
        self.pon(60, "el arroz")
        self.assertEqual(self.temporizadores.proximo(), 60)

    def test_sin_ninguno_no_hay_proximo(self):
        self.assertIsNone(self.temporizadores.proximo())

    def test_la_lista_va_del_mas_cercano_al_mas_lejano(self):
        self.pon(600, "la colada")
        self.pon(60, "el arroz")
        self.assertEqual([t["etiqueta"] for t in self.temporizadores.lista()],
                         ["el arroz", "la colada"])

    def test_un_tope_de_temporizadores(self):
        for numero in range(MAX_TEMPORIZADORES):
            self.pon(600, f"el {numero}")
        fallo = self.temporizadores.pon(600, "uno más")
        self.assertFalse(fallo["ok"])
        self.assertIn(str(MAX_TEMPORIZADORES), fallo["motivo"])

    def test_no_paso_de_un_dia(self):
        fallo = self.temporizadores.pon(MAX_SEGUNDOS + 1, "eterno")
        self.assertFalse(fallo["ok"])
        self.assertIn("día", fallo["motivo"])


class TestPausa(CasoConReloj):
    def test_pausado_no_corre(self):
        arroz = self.pon(600, "el arroz")
        self.reloj.pasa(60)
        self.temporizadores.pausa(arroz["id"])
        self.reloj.pasa(3600)

        vivo = self.temporizadores.lista()[0]
        self.assertTrue(vivo["pausado"])
        self.assertEqual(vivo["restante"], 540)
        self.assertEqual(self.temporizadores.vencidos(), [],
                         "en pausa no vence aunque pase su hora")

    def test_reanudar_cuenta_desde_lo_que_quedaba(self):
        """No desde el final original: eso haría inútil la pausa."""
        arroz = self.pon(600, "el arroz")
        self.reloj.pasa(60)
        self.temporizadores.pausa(arroz["id"])
        self.reloj.pasa(3600)
        self.temporizadores.reanuda(arroz["id"])

        self.assertEqual(self.temporizadores.lista()[0]["restante"], 540)
        self.reloj.pasa(540)
        self.assertEqual(len(self.temporizadores.vencidos()), 1)

    def test_pausar_dos_veces_no_lo_alarga(self):
        arroz = self.pon(600, "el arroz")
        self.reloj.pasa(60)
        self.temporizadores.pausa(arroz["id"])
        self.temporizadores.pausa(arroz["id"])
        self.assertEqual(self.temporizadores.lista()[0]["restante"], 540)

    def test_reanudar_uno_que_corre_no_lo_toca(self):
        arroz = self.pon(600, "el arroz")
        self.reloj.pasa(60)
        self.temporizadores.reanuda(arroz["id"])
        self.assertEqual(self.temporizadores.lista()[0]["restante"], 540)

    def test_los_pausados_van_al_final_de_la_lista(self):
        corto = self.pon(10, "el corto")
        self.pon(600, "el largo")
        self.temporizadores.pausa(corto["id"])
        self.assertEqual([t["etiqueta"] for t in self.temporizadores.lista()],
                         ["el largo", "el corto"])

    def test_si_todos_estan_en_pausa_no_hay_proximo(self):
        arroz = self.pon(600, "el arroz")
        self.temporizadores.pausa(arroz["id"])
        self.assertIsNone(self.temporizadores.proximo())


class TestAjuste(CasoConReloj):
    def test_anadir_alarga(self):
        arroz = self.pon(600, "el arroz")
        self.reloj.pasa(60)
        ajustado = self.temporizadores.anade(arroz["id"], 300)
        self.assertEqual(ajustado["temporizador"]["restante"], 840)

    def test_quitar_acorta(self):
        arroz = self.pon(600, "el arroz")
        ajustado = self.temporizadores.anade(arroz["id"], -300)
        self.assertEqual(ajustado["temporizador"]["restante"], 300)

    def test_quitar_mas_de_lo_que_queda_no_lo_termina(self):
        """Eso es cancelarlo, y se pide cancelando: aquí se dice qué queda."""
        arroz = self.pon(600, "el arroz")
        self.reloj.pasa(540)
        fallo = self.temporizadores.anade(arroz["id"], -120)
        self.assertFalse(fallo["ok"])
        self.assertIn("1 minuto", fallo["motivo"])
        self.assertEqual(len(self.temporizadores.lista()), 1)

    def test_no_se_puede_estirar_mas_de_un_dia(self):
        arroz = self.pon(600, "el arroz")
        self.assertFalse(self.temporizadores.anade(arroz["id"], MAX_SEGUNDOS)["ok"])

    def test_se_puede_ajustar_uno_en_pausa(self):
        arroz = self.pon(600, "el arroz")
        self.temporizadores.pausa(arroz["id"])
        ajustado = self.temporizadores.anade(arroz["id"], 300)
        self.assertTrue(ajustado["temporizador"]["pausado"])
        self.assertEqual(ajustado["temporizador"]["restante"], 900)

    def test_ajustar_uno_que_ya_no_esta(self):
        self.assertFalse(self.temporizadores.anade("noexiste", 60)["ok"])


class TestNombres(CasoConReloj):
    def test_sin_etiqueta_se_llama_por_lo_que_dura(self):
        self.assertEqual(nombre(self.pon(600)), "el de 10 minutos")

    def test_alargarlo_cambia_como_se_llama(self):
        diez = self.pon(600)
        self.temporizadores.anade(diez["id"], 300)
        self.assertEqual(nombre(self.temporizadores.lista()[0]), "el de 15 minutos")

    def test_como_se_describe(self):
        self.pon(600, "el arroz")
        self.assertEqual(describe(self.temporizadores.lista()[0]),
                         "el arroz, 10 minutos")

    def test_como_se_describe_en_pausa(self):
        arroz = self.pon(600, "el arroz")
        self.temporizadores.pausa(arroz["id"])
        self.assertEqual(describe(self.temporizadores.lista()[0]),
                         "el arroz, en pausa con 10 minutos")


class TestBusca(CasoConReloj):
    def test_por_la_etiqueta(self):
        self.pon(600, "el arroz")
        self.assertEqual(len(self.temporizadores.busca("el arroz")), 1)

    def test_sin_tildes_ni_mayusculas(self):
        self.pon(600, "la tortilla de Doña Ángela")
        self.assertEqual(len(self.temporizadores.busca("la tortilla de dona angela")), 1)

    def test_por_una_parte(self):
        self.pon(600, "el arroz del domingo")
        self.assertEqual(self.temporizadores.busca("arroz")[0]["etiqueta"],
                         "el arroz del domingo")

    def test_exacto_gana_a_parcial(self):
        self.pon(600, "el arroz")
        self.pon(300, "el arroz del domingo")
        encontrados = self.temporizadores.busca("el arroz")
        self.assertEqual([t["etiqueta"] for t in encontrados], ["el arroz"])

    def test_varios_parciales_salen_todos(self):
        """Quien llama decide si pregunta; aquí no se adivina."""
        self.pon(600, "el arroz blanco")
        self.pon(300, "el arroz integral")
        self.assertEqual(len(self.temporizadores.busca("arroz")), 2)

    def test_lo_que_no_esta(self):
        self.pon(600, "el arroz")
        self.assertEqual(self.temporizadores.busca("la colada"), [])

    def test_tambien_por_la_duracion_del_que_no_tiene_etiqueta(self):
        self.pon(600)
        self.assertEqual(len(self.temporizadores.busca("el de 10 minutos")), 1)


class TestCancelar(CasoConReloj):
    def test_uno(self):
        arroz = self.pon(600, "el arroz")
        self.assertEqual(self.temporizadores.cancela(arroz["id"])["etiqueta"],
                         "el arroz")
        self.assertEqual(self.temporizadores.lista(), [])

    def test_uno_que_no_esta(self):
        self.assertIsNone(self.temporizadores.cancela("noexiste"))

    def test_todos(self):
        self.pon(600, "el arroz")
        self.pon(300, "la colada")
        self.assertEqual(len(self.temporizadores.cancela_todos()), 2)
        self.assertEqual(self.temporizadores.lista(), [])

    def test_cancelado_no_vence(self):
        arroz = self.pon(10, "el arroz")
        self.temporizadores.cancela(arroz["id"])
        self.reloj.pasa(60)
        self.assertEqual(self.temporizadores.vencidos(), [])


class CasoConCaja(unittest.IsolatedAsyncioTestCase):
    """Las herramientas, que es donde se decide cómo suena cada respuesta."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dicho: list[str] = []
        cfg = Config({
            "tools": {"allow_system": True,
                      "notes_file": str(Path(self.tmp.name) / "n.json"),
                      "memory_file": str(Path(self.tmp.name) / "m.json"),
                      "lists_file": str(Path(self.tmp.name) / "l.json")},
            "llm": {"web_search": False},
        })
        with mock.patch.dict(os.environ, {"JARVIS_HASS_TOKEN": ""}):
            self.caja = Toolbox(cfg, EventBus(), on_announce=self.anuncia)
        # Con el reloj de verdad, «10 minutos» se convierte en «9 y 59» en
        # cuanto la máquina va lenta. Lo que se comprueba aquí son las frases.
        self.reloj = Reloj()
        self.caja.temporizadores = Temporizadores(reloj=self.reloj)

    async def anuncia(self, mensaje: str) -> None:
        self.dicho.append(mensaje)

    def tearDown(self):
        # El bucle duerme en segundo plano; sin esto la prueba lo deja colgando.
        if self.caja._reloj is not None:
            self.caja._reloj.cancel()
        self.tmp.cleanup()

    async def pon(self, segundos: int, etiqueta: str = "") -> str:
        args = {"segundos": segundos}
        if etiqueta:
            args["etiqueta"] = etiqueta
        return await self.caja.run("poner_temporizador", args)


class TestPonerHerramienta(CasoConCaja):
    async def test_lo_confirma_con_la_etiqueta(self):
        salida = await self.pon(600, "el arroz")
        self.assertEqual(salida, "Temporizador de 10 minutos programado para el arroz.")

    async def test_sin_etiqueta_no_se_la_inventa(self):
        self.assertEqual(await self.pon(90), "Temporizador de 1 minuto y 30 segundos "
                                             "programado.")

    async def test_varios_a_la_vez(self):
        await self.pon(600, "el arroz")
        await self.pon(1200, "la colada")
        self.assertEqual(len(self.caja.temporizadores.lista()), 2)

    async def test_pasado_el_tope_lo_dice(self):
        for numero in range(MAX_TEMPORIZADORES):
            await self.pon(600, f"el {numero}")
        self.assertIn("ya tienes", await self.pon(600, "uno más"))


class TestVerHerramienta(CasoConCaja):
    async def test_sin_ninguno(self):
        self.assertIn("ningún temporizador",
                      await self.caja.run("ver_temporizadores", {}))

    async def test_uno_solo(self):
        await self.pon(600, "el arroz")
        self.assertEqual(await self.caja.run("ver_temporizadores", {}),
                         "Solo uno: el arroz, 10 minutos.")

    async def test_varios_se_enumeran(self):
        await self.pon(600, "el arroz")
        await self.pon(1200, "la colada")
        salida = await self.caja.run("ver_temporizadores", {})
        self.assertEqual(salida, "Tienes 2: el arroz, 10 minutos y la colada, "
                                 "20 minutos.")

    async def test_preguntar_por_uno(self):
        await self.pon(600, "el arroz")
        await self.pon(1200, "la colada")
        salida = await self.caja.run("ver_temporizadores", {"cual": "arroz"})
        self.assertEqual(salida, "Al arroz le quedan 10 minutos.")

        otra = await self.caja.run("ver_temporizadores", {"cual": "colada"})
        self.assertEqual(otra, "A la colada le quedan 20 minutos.")

    async def test_preguntar_por_uno_en_pausa(self):
        await self.pon(600, "el arroz")
        await self.caja.run("ajustar_temporizador", {"accion": "pausar"})
        self.assertIn("en pausa",
                      await self.caja.run("ver_temporizadores", {"cual": "arroz"}))

    async def test_preguntar_por_uno_que_no_esta(self):
        await self.pon(600, "el arroz")
        salida = await self.caja.run("ver_temporizadores", {"cual": "la colada"})
        self.assertIn("No tengo ningún temporizador", salida)
        self.assertIn("el arroz", salida, "y se dice lo que sí hay")


class TestCancelarHerramienta(CasoConCaja):
    async def test_uno_solo_no_hace_falta_nombrarlo(self):
        await self.pon(600, "el arroz")
        salida = await self.caja.run("cancelar_temporizador", {})
        self.assertIn("Cancelo el arroz", salida)
        self.assertIn("10 minutos", salida)
        self.assertEqual(self.caja.temporizadores.lista(), [])

    async def test_con_varios_pregunta_cual(self):
        await self.pon(600, "el arroz")
        await self.pon(1200, "la colada")
        salida = await self.caja.run("cancelar_temporizador", {})
        self.assertIn("pregúntale a cuál", salida)
        self.assertEqual(len(self.caja.temporizadores.lista()), 2,
                         "y no cancela nada mientras tanto")

    async def test_por_la_etiqueta(self):
        await self.pon(600, "el arroz")
        await self.pon(1200, "la colada")
        await self.caja.run("cancelar_temporizador", {"cual": "colada"})
        self.assertEqual([t["etiqueta"] for t in self.caja.temporizadores.lista()],
                         ["el arroz"])

    async def test_todos_pide_confirmacion(self):
        """Uno se repite con la misma frase que lo pidió; todos, no."""
        await self.pon(600, "el arroz")
        await self.pon(1200, "la colada")
        salida = await self.caja.run("cancelar_temporizador", {"todos": True})
        self.assertIn("Sin cancelar todavía", salida)
        self.assertEqual(len(self.caja.temporizadores.lista()), 2)

        hecho = await self.caja.run("cancelar_temporizador", {"todos": True,
                                                              "confirmar": True})
        self.assertEqual(hecho, "Cancelados los 2.")
        self.assertEqual(self.caja.temporizadores.lista(), [])

    async def test_todos_con_uno_solo_no_molesta(self):
        await self.pon(600, "el arroz")
        salida = await self.caja.run("cancelar_temporizador", {"todos": True})
        self.assertIn("Cancelo el arroz", salida)

    async def test_confirmar_sin_haberlo_propuesto_no_vale(self):
        """El segundo paso lo exige el código, no la buena fe del modelo."""
        await self.pon(600, "el arroz")
        await self.pon(1200, "la colada")
        salida = await self.caja.run("cancelar_temporizador", {"todos": True,
                                                               "confirmar": True})
        self.assertIn("Sin cancelar todavía", salida)
        self.assertEqual(len(self.caja.temporizadores.lista()), 2)

    async def test_sin_ninguno(self):
        self.assertIn("ningún temporizador",
                      await self.caja.run("cancelar_temporizador", {}))


class TestAjustarHerramienta(CasoConCaja):
    async def test_pausar_y_reanudar(self):
        await self.pon(600, "el arroz")
        parado = await self.caja.run("ajustar_temporizador", {"accion": "pausar"})
        self.assertIn("En pausa el arroz", parado)
        self.assertTrue(self.caja.temporizadores.lista()[0]["pausado"])

        seguido = await self.caja.run("ajustar_temporizador", {"accion": "reanudar"})
        self.assertIn("Sigue el arroz", seguido)
        self.assertFalse(self.caja.temporizadores.lista()[0]["pausado"])

    async def test_pausar_uno_ya_pausado(self):
        await self.pon(600, "el arroz")
        await self.caja.run("ajustar_temporizador", {"accion": "pausar"})
        self.assertIn("ya estaba en pausa",
                      await self.caja.run("ajustar_temporizador",
                                          {"accion": "pausar"}))

    async def test_reanudar_uno_que_no_estaba_parado(self):
        await self.pon(600, "el arroz")
        self.assertIn("no estaba parado",
                      await self.caja.run("ajustar_temporizador",
                                          {"accion": "reanudar"}))

    async def test_anadir_tiempo(self):
        await self.pon(600, "el arroz")
        salida = await self.caja.run("ajustar_temporizador",
                                     {"accion": "anadir", "segundos": 300})
        self.assertIn("Añado 5 minutos al arroz", salida)
        self.assertIn("quedan 15 minutos", salida)

    async def test_quitar_tiempo(self):
        await self.pon(600, "el arroz")
        salida = await self.caja.run("ajustar_temporizador",
                                     {"accion": "quitar", "segundos": 300})
        self.assertIn("Quito 5 minutos al arroz", salida)
        self.assertIn("quedan 5 minutos", salida)

    async def test_quitar_mas_de_lo_que_queda_ofrece_cancelarlo(self):
        await self.pon(600, "el arroz")
        salida = await self.caja.run("ajustar_temporizador",
                                     {"accion": "quitar", "segundos": 1200})
        self.assertIn("¿Lo cancelo?", salida)
        self.assertEqual(len(self.caja.temporizadores.lista()), 1)

    async def test_sin_decir_cuanto(self):
        await self.pon(600, "el arroz")
        self.assertIn("¿Cuánto",
                      await self.caja.run("ajustar_temporizador",
                                          {"accion": "anadir"}))

    async def test_una_accion_que_no_existe(self):
        await self.pon(600, "el arroz")
        self.assertIn("No sé hacer eso",
                      await self.caja.run("ajustar_temporizador",
                                          {"accion": "bailar"}))

    async def test_la_accion_se_entiende_con_tilde(self):
        """El modelo escribe «añadir» tantas veces como «anadir»."""
        await self.pon(600, "el arroz")
        self.assertIn("Añado",
                      await self.caja.run("ajustar_temporizador",
                                          {"accion": "añadir", "segundos": 60}))


class TestEventos(CasoConCaja):
    """Los cinco temas pintan `e.message`; sin él sale «undefined»."""

    def setUp(self):
        super().setUp()
        self.cola = self.caja.bus.subscribe()

    def eventos(self) -> list[dict]:
        salidos = []
        while not self.cola.empty():
            salidos.append(self.cola.get_nowait())
        return salidos

    async def test_poner_uno_avisa_con_texto(self):
        await self.pon(600, "el arroz")
        evento = self.eventos()[-1]
        self.assertEqual(evento["type"], "timer")
        self.assertIn("el arroz", evento["message"])

    async def test_cancelar_avisa_con_texto(self):
        await self.pon(600, "el arroz")
        await self.caja.run("cancelar_temporizador", {})
        self.assertIn("Cancelo el arroz", self.eventos()[-1]["message"])

    async def test_todo_evento_de_temporizador_trae_mensaje(self):
        for etiqueta in ("el arroz", "la colada", "el horno"):
            await self.pon(600, etiqueta)
        await self.caja.run("cancelar_temporizador", {"cual": "arroz"})
        await self.caja.run("cancelar_temporizador", {"todos": True})
        await self.caja.run("cancelar_temporizador", {"todos": True,
                                                      "confirmar": True})

        timers = [e for e in self.eventos() if e["type"] == "timer"]
        self.assertEqual(len(timers), 5, "tres puestos, uno y los dos últimos")
        for evento in timers:
            self.assertTrue(evento.get("message"), evento)

    async def test_proponer_sin_confirmar_no_avisa(self):
        """Lo que no se hace no se pinta: la propuesta no es un evento."""
        await self.pon(600, "el arroz")
        await self.pon(1200, "la colada")
        self.eventos()
        await self.caja.run("cancelar_temporizador", {"todos": True})
        self.assertEqual(self.eventos(), [])


class TestCortafuegos(CasoConCaja):
    """Un correo no cancela el temporizador del horno."""

    async def test_no_cancela_tras_leer_algo_de_fuera(self):
        await self.pon(600, "el arroz")
        self.caja._external("un correo cualquiera")
        salida = await self.caja.run("cancelar_temporizador", {})
        self.assertIn("contenido de fuera", salida)
        self.assertEqual(len(self.caja.temporizadores.lista()), 1)

    async def test_no_ajusta_tras_leer_algo_de_fuera(self):
        await self.pon(600, "el arroz")
        self.caja._external("un correo cualquiera")
        salida = await self.caja.run("ajustar_temporizador", {"accion": "pausar"})
        self.assertIn("contenido de fuera", salida)
        self.assertFalse(self.caja.temporizadores.lista()[0]["pausado"])

    async def test_poner_uno_si_sigue_valiendo(self):
        """Uno de más es ruido que se cancela; uno de menos es el soufflé."""
        self.caja._external("un correo cualquiera")
        self.assertIn("programado", await self.pon(600, "el arroz"))

    async def test_el_turno_limpio_vuelve_a_permitirlo(self):
        await self.pon(600, "el arroz")
        self.caja._external("un correo cualquiera")
        self.caja.begin_turn()
        self.assertIn("Cancelo",
                      await self.caja.run("cancelar_temporizador", {}))


class TestBucle(CasoConCaja):
    """El único sitio con tiempo real, y en centésimas de segundo."""

    def setUp(self):
        super().setUp()
        self.caja.temporizadores = Temporizadores()  # aquí sí, el de verdad

    async def test_suena_cuando_toca(self):
        await self.pon(0, "el arroz")
        await _espera(lambda: self.dicho)
        self.assertEqual(self.dicho, ["Ha terminado el arroz."])

    async def test_sin_etiqueta_se_llama_el_temporizador(self):
        await self.pon(0)
        await _espera(lambda: self.dicho)
        self.assertEqual(self.dicho, ["Ha terminado el temporizador."])

    async def test_el_bucle_se_apaga_cuando_no_queda_ninguno(self):
        await self.pon(0, "el arroz")
        await _espera(lambda: self.caja._reloj.done())
        self.assertEqual(self.caja.temporizadores.lista(), [])

    async def test_y_vuelve_a_arrancar_con_el_siguiente(self):
        await self.pon(0, "el arroz")
        await _espera(lambda: self.caja._reloj.done())

        self.caja.temporizadores.pon(0.02, "la colada")
        self.caja._arranca_reloj()
        await _espera(lambda: len(self.dicho) == 2)
        self.assertEqual(self.dicho[-1], "Ha terminado la colada.")

    async def test_espera_de_verdad_a_que_pase_el_tiempo(self):
        self.caja.temporizadores.pon(0.05, "el arroz")
        self.caja._arranca_reloj()
        await asyncio.sleep(0.01)
        self.assertEqual(self.dicho, [], "todavía no")
        await _espera(lambda: self.dicho)

    async def test_uno_cancelado_no_suena(self):
        self.caja.temporizadores.pon(0.02, "el arroz")
        self.caja._arranca_reloj()
        self.caja.temporizadores.cancela_todos()
        await asyncio.sleep(0.1)
        self.assertEqual(self.dicho, [])

    async def test_reanudar_vuelve_a_arrancar_el_bucle(self):
        """Con el único en pausa el bucle se apaga; al seguir, tiene que volver."""
        self.caja.temporizadores.pon(0.05, "el arroz")
        arroz = self.caja.temporizadores.lista()[0]
        self.caja.temporizadores.pausa(arroz["id"])
        self.caja._arranca_reloj()
        await _espera(lambda: self.caja._reloj.done())

        await self.caja.run("ajustar_temporizador", {"accion": "reanudar"})
        await _espera(lambda: self.dicho)
        self.assertEqual(self.dicho, ["Ha terminado el arroz."])


async def _espera(condicion, limite: float = 3.0) -> None:
    """Espera a que el bucle dé la vuelta, sin dormir a ciegas."""
    for _ in range(int(limite / 0.01)):
        if condicion():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("el temporizador no llegó a tiempo")


if __name__ == "__main__":
    unittest.main()
