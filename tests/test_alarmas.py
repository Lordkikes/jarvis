"""Alarmas: la hora, los días, la insistencia y lo que se perdió apagado.

El «ahora» se inyecta en todas las operaciones que miran el reloj, así que
«son las siete y dos minutos» o «han pasado once vueltas» cuestan una suma.
Ninguna prueba duerme.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, time, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis.alarmas import (  # noqa: E402
    CADA, FIN_DE_SEMANA, LABORABLES, MAX_ALARMAS, REPETICIONES,
    RETRASO_MAXIMO, TODOS, Alarmas, dias_en_palabras, parse_dias, parse_hora,
    siguiente_ocurrencia,
)
from jarvis.bus import EventBus  # noqa: E402
from jarvis.config import Config  # noqa: E402
from jarvis.llm.tools import Toolbox  # noqa: E402
from jarvis.pipeline import Jarvis  # noqa: E402

# Un lunes a las ocho de la mañana, para que «mañana» y «el sábado» se vean.
LUNES = datetime(2026, 9, 28, 8, 0).astimezone()


class TestParseo(unittest.TestCase):
    def test_horas_que_valen(self):
        self.assertEqual(parse_hora("07:00"), time(7, 0))
        self.assertEqual(parse_hora("7:05"), time(7, 5))
        self.assertEqual(parse_hora("7.30"), time(7, 30), "el punto también")
        self.assertEqual(parse_hora("22"), time(22, 0))

    def test_horas_que_no(self):
        for malo in ("", None, "las siete", "25:00", "07:61", "7:0:0"):
            with self.subTest(valor=malo):
                self.assertIsNone(parse_hora(malo))

    def test_dias_sueltos(self):
        self.assertEqual(parse_dias(["lunes", "jueves"]), (0, 3))

    def test_dias_con_tilde_y_en_plural(self):
        self.assertEqual(parse_dias(["miércoles", "sábados"]), (2, 5))

    def test_dias_en_una_cadena(self):
        self.assertEqual(parse_dias("lunes, martes"), (0, 1))

    def test_grupos(self):
        self.assertEqual(parse_dias("laborables"), LABORABLES)
        self.assertEqual(parse_dias("entre semana"), LABORABLES)
        self.assertEqual(parse_dias("fin de semana"), FIN_DE_SEMANA)
        self.assertEqual(parse_dias("diario"), TODOS)

    def test_sin_dias_es_una_sola_vez(self):
        self.assertEqual(parse_dias(None), ())
        self.assertEqual(parse_dias(""), ())

    def test_lo_que_no_entiende_lo_ignora(self):
        self.assertEqual(parse_dias(["lunes", "cuandosea"]), (0,))

    def test_no_se_repiten(self):
        self.assertEqual(parse_dias("laborables, lunes"), LABORABLES)

    def test_como_se_dicen(self):
        self.assertEqual(dias_en_palabras(LABORABLES), "de lunes a viernes")
        self.assertEqual(dias_en_palabras(FIN_DE_SEMANA), "los fines de semana")
        self.assertEqual(dias_en_palabras(TODOS), "todos los días")
        self.assertEqual(dias_en_palabras((0,)), "los lunes")
        self.assertEqual(dias_en_palabras((0, 2, 4)),
                         "los lunes, miércoles y viernes")
        self.assertEqual(dias_en_palabras(()), "una vez")


class TestProximaVez(unittest.TestCase):
    def test_hoy_si_todavia_no_ha_pasado(self):
        cuando = siguiente_ocurrencia(time(9, 0), (), LUNES)
        self.assertEqual(cuando.date(), LUNES.date())

    def test_manana_si_ya_paso(self):
        cuando = siguiente_ocurrencia(time(7, 0), (), LUNES)
        self.assertEqual((cuando.date() - LUNES.date()).days, 1)

    def test_salta_al_dia_que_toca(self):
        """Lunes a las ocho, alarma de los sábados: el sábado."""
        cuando = siguiente_ocurrencia(time(9, 0), (5,), LUNES)
        self.assertEqual(cuando.weekday(), 5)
        self.assertEqual((cuando.date() - LUNES.date()).days, 5)

    def test_laborables_pasada_la_hora_es_el_dia_siguiente(self):
        cuando = siguiente_ocurrencia(time(7, 0), LABORABLES, LUNES)
        self.assertEqual(cuando.weekday(), 1)

    def test_el_viernes_por_la_tarde_salta_al_lunes(self):
        viernes = datetime(2026, 10, 2, 20, 0).astimezone()
        cuando = siguiente_ocurrencia(time(7, 0), LABORABLES, viernes)
        self.assertEqual(cuando.weekday(), 0)
        self.assertEqual((cuando.date() - viernes.date()).days, 3)

    def test_no_arrastra_el_desfase(self):
        """Se calcula desde el reloj, no sumándole un día a la anterior."""
        cuando = siguiente_ocurrencia(time(7, 0), TODOS,
                                      LUNES + timedelta(days=30))
        self.assertEqual(cuando.time(), time(7, 0))


class CasoConFichero(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fichero = Path(self.tmp.name) / "alarmas.json"
        self.alarmas = Alarmas(self.fichero)

    def tearDown(self):
        self.tmp.cleanup()

    def pon(self, hora="07:00", dias=LABORABLES, etiqueta="",
            ahora=LUNES) -> dict:
        resultado = self.alarmas.pon(parse_hora(hora), dias, etiqueta, ahora)
        self.assertTrue(resultado.get("ok"), resultado)
        return resultado["alarma"]

    def guardadas(self) -> list:
        return json.loads(self.fichero.read_text(encoding="utf-8"))


class TestAlmacen(CasoConFichero):
    def test_sin_fichero_no_hay_alarmas(self):
        self.assertEqual(self.alarmas.lista(), [])

    def test_sobrevive_al_proceso(self):
        """Es lo que la separa de un temporizador: una alarma está en disco."""
        self.pon()
        self.assertEqual(len(Alarmas(self.fichero).lista()), 1)

    def test_un_fichero_roto_no_tira_nada(self):
        self.fichero.write_text("{ esto no es json", encoding="utf-8")
        self.assertEqual(self.alarmas.lista(), [])

    def test_guarda_la_proxima_vez(self):
        alarma = self.pon(hora="07:00", dias=LABORABLES)
        self.assertEqual(alarma["hora"], "07:00")
        self.assertTrue(alarma["proxima"].startswith("2026-09-29T07:00"))

    def test_un_tope_de_alarmas(self):
        for minuto in range(MAX_ALARMAS):
            self.pon(hora=f"07:{minuto:02d}")
        fallo = self.alarmas.pon(time(9, 0), (), "", LUNES)
        self.assertFalse(fallo["ok"])

    def test_quitar(self):
        alarma = self.pon()
        self.assertTrue(self.alarmas.quita(alarma["id"]))
        self.assertEqual(self.alarmas.lista(), [])
        self.assertFalse(self.alarmas.quita(alarma["id"]))

    def test_apagar_y_encender(self):
        alarma = self.pon()
        self.alarmas.activa(alarma["id"], False)
        self.assertFalse(self.alarmas.lista()[0]["activa"])
        self.alarmas.activa(alarma["id"], True, LUNES)
        self.assertTrue(self.alarmas.lista()[0]["activa"])

    def test_al_encenderla_su_proxima_vez_se_recalcula(self):
        """Si llevaba una semana apagada, no vuelve arrastrando la de entonces."""
        alarma = self.pon(hora="07:00", dias=LABORABLES)
        self.alarmas.activa(alarma["id"], False)
        despues = LUNES + timedelta(days=7)
        vuelta = self.alarmas.activa(alarma["id"], True, despues)
        self.assertTrue(vuelta["proxima"].startswith("2026-10-06T07:00"))

    def test_las_apagadas_van_al_final(self):
        pronto = self.pon(hora="06:00")
        self.pon(hora="23:00")
        self.alarmas.activa(pronto["id"], False)
        self.assertEqual(self.alarmas.lista()[0]["hora"], "23:00")


class TestBusca(CasoConFichero):
    def test_por_la_hora(self):
        self.pon(hora="07:00")
        self.pon(hora="09:30")
        self.assertEqual(self.alarmas.busca("09:30")[0]["hora"], "09:30")

    def test_por_la_hora_dicha_con_las_delante(self):
        self.pon(hora="07:00")
        self.pon(hora="09:30")
        self.assertEqual(self.alarmas.busca("la de las 7:00")[0]["hora"], "07:00")

    def test_por_la_etiqueta(self):
        self.pon(hora="07:00", etiqueta="el gimnasio")
        self.assertEqual(len(self.alarmas.busca("gimnasio")), 1)

    def test_por_el_dia(self):
        self.pon(hora="07:00", dias=LABORABLES)
        self.pon(hora="10:00", dias=FIN_DE_SEMANA)
        encontradas = self.alarmas.busca("fines de semana")
        self.assertEqual([a["hora"] for a in encontradas], ["10:00"])

    def test_lo_que_no_esta(self):
        self.pon(hora="07:00")
        self.assertEqual(self.alarmas.busca("la de las 23:00"), [])


class TestSonar(CasoConFichero):
    def test_no_suena_antes_de_hora(self):
        self.pon(hora="09:00", dias=())
        self.assertEqual(self.alarmas.revisa(LUNES), [])

    def test_suena_a_su_hora(self):
        self.pon(hora="09:00", dias=(), etiqueta="el dentista")
        avisos = self.alarmas.revisa(LUNES.replace(hour=9))
        self.assertEqual(len(avisos), 1)
        self.assertEqual(avisos[0]["vez"], 1)
        self.assertEqual(avisos[0]["etiqueta"], "el dentista")

    def test_insiste_cada_media_vuelta(self):
        self.pon(hora="09:00", dias=())
        nueve = LUNES.replace(hour=9)
        self.alarmas.revisa(nueve)
        self.assertEqual(self.alarmas.revisa(nueve + timedelta(seconds=5)), [],
                         "todavía no le toca repetir")
        segunda = self.alarmas.revisa(nueve + CADA)
        self.assertEqual(segunda[0]["vez"], 2)

    def test_se_rinde_despues_de_varias(self):
        self.pon(hora="09:00", dias=())
        ahora = LUNES.replace(hour=9)
        veces = []
        for _ in range(REPETICIONES + 3):
            if avisos := self.alarmas.revisa(ahora):
                veces.append(avisos[0]["vez"])
            ahora += CADA
        self.assertEqual(veces, list(range(1, REPETICIONES + 1)))
        self.assertIsNone(self.alarmas.lista()[0]["sonando"])

    def test_la_que_sonaba_al_apagarse_no_retoma_horas_despues(self):
        """Volver a sonar a las doce porque a las nueve se cortó la luz, no."""
        self.pon(hora="09:00", dias=TODOS)
        nueve = LUNES.replace(hour=9)
        self.alarmas.revisa(nueve)  # empieza a sonar y queda así en el fichero

        vuelta = Alarmas(self.fichero)
        self.assertEqual(vuelta.revisa(nueve + timedelta(hours=3)), [])
        self.assertEqual(vuelta.sonando(), [])
        self.assertTrue(vuelta.lista()[0]["perdida"])

    def test_pero_un_corte_de_un_minuto_sigue_sonando(self):
        self.pon(hora="09:00", dias=TODOS)
        nueve = LUNES.replace(hour=9)
        self.alarmas.revisa(nueve)

        vuelta = Alarmas(self.fichero).revisa(nueve + timedelta(minutes=1))
        self.assertEqual(vuelta[0]["vez"], 2)

    def test_el_estado_se_guarda_antes_de_avisar(self):
        """Reiniciar a las siete y cinco no la calla, y un fallo no la repite."""
        self.pon(hora="09:00", dias=())
        self.alarmas.revisa(LUNES.replace(hour=9))
        self.assertTrue(self.guardadas()[0]["sonando"])
        self.assertEqual(Alarmas(self.fichero).sonando()[0]["hora"], "09:00")

    def test_una_apagada_no_suena(self):
        alarma = self.pon(hora="09:00", dias=())
        self.alarmas.activa(alarma["id"], False)
        self.assertEqual(self.alarmas.revisa(LUNES.replace(hour=9)), [])

    def test_la_que_se_perdio_apagado_no_despierta_a_nadie(self):
        """Un recordatorio de hace tres horas aún sirve; una alarma, no."""
        self.pon(hora="09:00", dias=())
        tarde = LUNES.replace(hour=9) + RETRASO_MAXIMO + timedelta(minutes=1)
        self.assertEqual(self.alarmas.revisa(tarde), [])
        self.assertTrue(self.alarmas.lista()[0]["perdida"])

    def test_un_retraso_pequeño_si_suena(self):
        self.pon(hora="09:00", dias=())
        poco = LUNES.replace(hour=9) + timedelta(minutes=2)
        avisos = self.alarmas.revisa(poco)
        self.assertEqual(len(avisos), 1)
        self.assertEqual(avisos[0]["retraso"], 120)

    def test_la_perdida_se_recoloca_en_la_siguiente(self):
        self.pon(hora="09:00", dias=TODOS)
        tarde = LUNES.replace(hour=12)
        self.alarmas.revisa(tarde)
        self.assertTrue(self.alarmas.lista()[0]["proxima"].startswith(
            "2026-09-29T09:00"))

    def test_sonar_borra_la_marca_de_perdida(self):
        self.pon(hora="09:00", dias=TODOS)
        self.alarmas.revisa(LUNES.replace(hour=12))
        self.alarmas.revisa(LUNES.replace(hour=9) + timedelta(days=1))
        self.assertNotIn("perdida", self.alarmas.lista()[0])

    def test_dos_a_la_vez_suenan_las_dos(self):
        self.pon(hora="09:00", dias=())
        self.pon(hora="09:00", dias=(), etiqueta="la otra")
        self.assertEqual(len(self.alarmas.revisa(LUNES.replace(hour=9))), 2)


class TestPararYPosponer(CasoConFichero):
    def suena(self, **kwargs) -> datetime:
        self.pon(hora="09:00", **kwargs)
        nueve = LUNES.replace(hour=9)
        self.alarmas.revisa(nueve)
        return nueve

    def test_parar_la_calla(self):
        nueve = self.suena(dias=TODOS)
        self.assertEqual(len(self.alarmas.para(ahora=nueve)), 1)
        self.assertEqual(self.alarmas.sonando(), [])
        self.assertEqual(self.alarmas.revisa(nueve + CADA), [])

    def test_parar_la_deja_lista_para_manana(self):
        nueve = self.suena(dias=TODOS)
        self.alarmas.para(ahora=nueve)
        self.assertTrue(self.alarmas.lista()[0]["proxima"].startswith(
            "2026-09-29T09:00"))

    def test_posponer_la_trae_de_vuelta(self):
        nueve = self.suena(dias=TODOS)
        pospuestas = self.alarmas.pospon(ahora=nueve)
        self.assertEqual(pospuestas[0]["espera"], timedelta(minutes=9))
        self.assertEqual(self.alarmas.revisa(nueve + timedelta(minutes=8)), [])
        vuelta = self.alarmas.revisa(nueve + timedelta(minutes=9))
        self.assertEqual(vuelta[0]["vez"], 1)

    def test_posponer_no_mueve_la_de_manana(self):
        """Lo que se pospone es esta vez, no la alarma de las nueve."""
        nueve = self.suena(dias=TODOS)
        self.alarmas.pospon(ahora=nueve)
        despues = nueve + timedelta(minutes=9)
        self.alarmas.revisa(despues)
        self.alarmas.para(ahora=despues)
        self.assertTrue(self.alarmas.lista()[0]["proxima"].startswith(
            "2026-09-29T09:00"))

    def test_posponer_los_minutos_que_diga(self):
        nueve = self.suena(dias=TODOS)
        self.alarmas.pospon(minutos=3, ahora=nueve)
        self.assertEqual(len(self.alarmas.revisa(nueve + timedelta(minutes=3))), 1)

    def test_parar_sin_nada_sonando_no_hace_nada(self):
        self.pon(hora="09:00", dias=TODOS)
        self.assertEqual(self.alarmas.para(ahora=LUNES), [])

    def test_parar_solo_una_de_las_dos(self):
        nueve = self.suena(dias=TODOS)
        otra = self.pon(hora="09:00", dias=TODOS, etiqueta="la otra",
                        ahora=LUNES.replace(hour=8, minute=59))
        self.alarmas.revisa(nueve)
        self.alarmas.para(otra["id"], ahora=nueve)
        self.assertEqual(len(self.alarmas.sonando()), 1)

    def test_una_de_una_sola_vez_se_para_y_se_queda(self):
        """No se borra sola: ya se verá si la quitas o la reaprovechas."""
        nueve = self.suena(dias=())
        self.alarmas.para(ahora=nueve)
        self.assertEqual(len(self.alarmas.lista()), 1)
        self.assertTrue(self.alarmas.lista()[0]["proxima"].startswith(
            "2026-09-29T09:00"))


class CasoConCaja(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fichero = Path(self.tmp.name) / "alarmas.json"
        self.dicho: list[str] = []
        cfg = Config({
            "tools": {"allow_system": True,
                      "notes_file": str(Path(self.tmp.name) / "n.json"),
                      "memory_file": str(Path(self.tmp.name) / "m.json"),
                      "lists_file": str(Path(self.tmp.name) / "l.json"),
                      "alarms_file": str(self.fichero)},
            "llm": {"web_search": False},
        })
        with mock.patch.dict(os.environ, {"JARVIS_HASS_TOKEN": ""}):
            self.caja = Toolbox(cfg, EventBus(), on_announce=self.anuncia)

    async def anuncia(self, mensaje: str) -> None:
        self.dicho.append(mensaje)

    def tearDown(self):
        self.tmp.cleanup()

    async def pon(self, hora="07:00", dias="laborables", etiqueta="") -> str:
        args = {"hora": hora, "dias": dias}
        if etiqueta:
            args["etiqueta"] = etiqueta
        return await self.caja.run("poner_alarma", args)


class TestHerramientas(CasoConCaja):
    async def test_poner_una(self):
        salida = await self.pon("07:00", "laborables")
        self.assertEqual(salida, "Alarma puesta: las 07:00, de lunes a viernes.")

    async def test_poner_una_de_una_sola_vez_dice_el_dia(self):
        salida = await self.pon("07:00", "")
        self.assertTrue(salida.endswith("mañana.") or salida.endswith("hoy."),
                        salida)

    async def test_la_etiqueta_se_dice(self):
        salida = await self.pon("07:00", "laborables", "el gimnasio")
        self.assertIn("(el gimnasio)", salida)

    async def test_una_hora_que_no_se_entiende(self):
        self.assertIn("No entiendo esa hora",
                      await self.caja.run("poner_alarma", {"hora": "las siete"}))

    async def test_ver_sin_ninguna(self):
        self.assertEqual(await self.caja.run("ver_alarmas", {}),
                         "No tienes alarmas puestas.")

    async def test_ver_las_que_hay(self):
        await self.pon("07:00", "laborables")
        await self.pon("10:00", "fin de semana")
        salida = await self.caja.run("ver_alarmas", {})
        self.assertIn("las 07:00, de lunes a viernes", salida)
        self.assertIn("las 10:00, los fines de semana", salida)

    async def test_apagar_y_encender(self):
        await self.pon("07:00", "laborables")
        apagada = await self.caja.run("encender_alarma", {"encendida": False})
        self.assertIn("Apagada", apagada)
        self.assertIn("(apagada)", await self.caja.run("ver_alarmas", {}))

        encendida = await self.caja.run("encender_alarma", {"encendida": True})
        self.assertIn("Encendida", encendida)
        self.assertNotIn("(apagada)", await self.caja.run("ver_alarmas", {}))

    async def test_apagar_una_que_ya_estaba_apagada(self):
        await self.pon("07:00", "laborables")
        await self.caja.run("encender_alarma", {"encendida": False})
        self.assertIn("ya estaba apagada",
                      await self.caja.run("encender_alarma", {"encendida": False}))

    async def test_con_varias_pregunta_cual(self):
        await self.pon("07:00", "laborables")
        await self.pon("10:00", "fin de semana")
        salida = await self.caja.run("encender_alarma", {"encendida": False})
        self.assertIn("pregúntale a cuál", salida)
        self.assertTrue(all(a["activa"] for a in self.caja.alarmas.lista()))

    async def test_se_elige_por_la_hora(self):
        await self.pon("07:00", "laborables")
        await self.pon("10:00", "fin de semana")
        await self.caja.run("encender_alarma", {"cual": "la de las 10:00",
                                                "encendida": False})
        apagadas = [a["hora"] for a in self.caja.alarmas.lista()
                    if not a["activa"]]
        self.assertEqual(apagadas, ["10:00"])

    async def test_quitar_pide_confirmacion(self):
        await self.pon("07:00", "laborables")
        salida = await self.caja.run("quitar_alarma", {})
        self.assertIn("Sin borrar todavía", salida)
        self.assertEqual(len(self.caja.alarmas.lista()), 1)

        hecho = await self.caja.run("quitar_alarma", {"confirmar": True})
        self.assertIn("Borrada", hecho)
        self.assertEqual(self.caja.alarmas.lista(), [])

    async def test_confirmar_sin_haberlo_propuesto_no_vale(self):
        await self.pon("07:00", "laborables")
        self.assertIn("Sin borrar todavía",
                      await self.caja.run("quitar_alarma", {"confirmar": True}))
        self.assertEqual(len(self.caja.alarmas.lista()), 1)


class TestSonarConHerramientas(CasoConCaja):
    async def suena(self, dias="diario") -> None:
        """Pone una y le adelanta la hora en el fichero, para no esperar."""
        await self.pon("07:00", dias)
        self.adelanta("proxima")
        await self.caja.dispara_alarmas()

    def adelanta(self, campo: str) -> None:
        pasado = (datetime.now().astimezone()
                  - timedelta(seconds=1)).isoformat(timespec="seconds")
        datos = json.loads(self.fichero.read_text(encoding="utf-8"))
        if campo == "proxima":
            datos[0]["proxima"] = pasado
        else:
            datos[0]["sonando"]["siguiente"] = pasado
        self.fichero.write_text(json.dumps(datos), encoding="utf-8")

    async def test_lo_dice_en_voz_alta(self):
        await self.suena()
        self.assertEqual(len(self.dicho), 1)
        self.assertIn("Son las 07:00", self.dicho[0])
        self.assertIn("«para»", self.dicho[0])

    async def test_insiste_con_otra_frase(self):
        await self.suena()
        self.adelanta("sonando")
        await self.caja.dispara_alarmas()
        self.assertEqual(len(self.dicho), 2)
        self.assertIn("Sigue sonando", self.dicho[1])

    async def test_pararla_la_calla(self):
        await self.suena()
        salida = await self.caja.run("parar_alarma", {})
        self.assertIn("Apagada", salida)
        self.assertIn("La siguiente,", salida, "y se dice cuándo vuelve")
        self.assertEqual(self.caja.alarmas.sonando(), [])

    async def test_una_de_una_sola_vez_no_dice_cuando_vuelve(self):
        await self.suena(dias="")
        self.assertEqual(await self.caja.run("parar_alarma", {}), "Apagada.")

    async def test_pospuesta_se_ve_al_preguntar(self):
        await self.suena()
        await self.caja.run("parar_alarma", {"posponer": True})
        self.assertIn("(pospuesta)", await self.caja.run("ver_alarmas", {}))

    async def test_posponerla(self):
        await self.suena()
        salida = await self.caja.run("parar_alarma", {"posponer": True,
                                                      "minutos": 5})
        self.assertIn("5 minutos", salida)
        self.assertEqual(self.caja.alarmas.sonando(), [])

    async def test_parar_sin_nada_sonando(self):
        await self.pon("07:00", "laborables")
        self.assertIn("No está sonando",
                      await self.caja.run("parar_alarma", {}))

    async def test_el_evento_lleva_texto(self):
        """Los temas pintan `e.message`; sin él saldría «undefined»."""
        cola = self.caja.bus.subscribe()
        await self.suena()
        eventos = []
        while not cola.empty():
            eventos.append(cola.get_nowait())
        alarmas = [e for e in eventos if e["type"] == "alarma"]
        self.assertTrue(alarmas)
        for evento in alarmas:
            self.assertTrue(evento.get("message"), evento)

    async def test_mientras_suena_se_ve_al_preguntar(self):
        await self.suena()
        self.assertIn("(sonando ahora)", await self.caja.run("ver_alarmas", {}))


class TestCortafuegos(CasoConCaja):
    """Un correo no pone una alarma a las tres ni calla la de las siete."""

    async def test_no_pone_tras_leer_algo_de_fuera(self):
        self.caja._external("un correo cualquiera")
        salida = await self.pon("03:00", "diario")
        self.assertIn("contenido de fuera", salida)
        self.assertEqual(self.caja.alarmas.lista(), [])

    async def test_no_para_tras_leer_algo_de_fuera(self):
        await self.pon("07:00", "diario")
        self.caja._external("un correo cualquiera")
        self.assertIn("contenido de fuera",
                      await self.caja.run("parar_alarma", {}))

    async def test_no_apaga_tras_leer_algo_de_fuera(self):
        await self.pon("07:00", "diario")
        self.caja._external("un correo cualquiera")
        salida = await self.caja.run("encender_alarma", {"encendida": False})
        self.assertIn("contenido de fuera", salida)
        self.assertTrue(self.caja.alarmas.lista()[0]["activa"])

    async def test_no_borra_tras_leer_algo_de_fuera(self):
        await self.pon("07:00", "diario")
        await self.caja.run("quitar_alarma", {})
        self.caja._external("un correo cualquiera")
        salida = await self.caja.run("quitar_alarma", {"confirmar": True})
        self.assertIn("contenido de fuera", salida)
        self.assertEqual(len(self.caja.alarmas.lista()), 1)

    async def test_ver_las_alarmas_si_se_puede(self):
        """Preguntar no actúa, y el modelo necesita poder mirar."""
        await self.pon("07:00", "diario")
        self.caja._external("un correo cualquiera")
        self.assertIn("las 07:00", await self.caja.run("ver_alarmas", {}))


class TestBucleDeLaTuberia(unittest.IsolatedAsyncioTestCase):
    """El bucle se prueba suelto: construir el Jarvis entero pide micrófono."""

    async def da_una_vuelta(self, toolbox) -> asyncio.Task:
        """Arranca el bucle y lo deja justo después de la primera vuelta."""
        tarea = asyncio.create_task(Jarvis._alarm_worker(
            SimpleNamespace(toolbox=toolbox)))
        self.addCleanup(tarea.cancel)
        for _ in range(20):
            await asyncio.sleep(0)
            if toolbox.veces:
                return tarea
        raise AssertionError("el bucle no llegó a mirar las alarmas")

    async def test_llama_a_disparar_las_alarmas(self):
        toolbox = FalsaCaja()
        await self.da_una_vuelta(toolbox)
        self.assertEqual(toolbox.veces, 1)

    async def test_un_fallo_no_para_el_bucle(self):
        """Si un JSON a medias revienta una vuelta, el bucle sigue vivo."""
        toolbox = FalsaCaja(revienta=True)
        with self.assertLogs("jarvis.pipeline", level="ERROR"):
            tarea = await self.da_una_vuelta(toolbox)
            await asyncio.sleep(0)
        self.assertFalse(tarea.done(), "sigue esperando a la siguiente vuelta")


class FalsaCaja:
    def __init__(self, revienta: bool = False):
        self.veces = 0
        self.revienta = revienta

    async def dispara_alarmas(self) -> int:
        self.veces += 1
        if self.revienta:
            raise RuntimeError("el fichero estaba a medias")
        return 0


if __name__ == "__main__":
    unittest.main()
