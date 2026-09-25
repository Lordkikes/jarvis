"""Expansión de repeticiones, contrastada con vectores de referencia.

Las cuentas y las primeras fechas de `VECTORES` se generaron con
python-dateutil 2.9 y coinciden con las nuestras. Aquí no se importa dateutil
a propósito: fijar los valores hace que la prueba corra en un entorno limpio y
delate cualquier cambio de comportamiento.
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis.sources.rrule import expand, is_supported, parse_rrule  # noqa: E402

UTC = timezone.utc
INICIO = datetime(2026, 1, 6, 9, 0, tzinfo=UTC)          # un martes
DESDE = datetime(2026, 1, 1, tzinfo=UTC)
HASTA = datetime(2026, 7, 1, tzinfo=UTC)

VECTORES = {
    "FREQ=DAILY": (176, ["2026-01-06", "2026-01-07", "2026-01-08"]),
    "FREQ=DAILY;INTERVAL=3": (59, ["2026-01-06", "2026-01-09", "2026-01-12"]),
    "FREQ=DAILY;COUNT=5": (5, ["2026-01-06", "2026-01-07", "2026-01-08"]),
    "FREQ=DAILY;UNTIL=20260120T090000Z": (15, ["2026-01-06", "2026-01-07", "2026-01-08"]),
    "FREQ=WEEKLY": (26, ["2026-01-06", "2026-01-13", "2026-01-20"]),
    "FREQ=WEEKLY;BYDAY=MO,WE,FR": (75, ["2026-01-07", "2026-01-09", "2026-01-12"]),
    "FREQ=WEEKLY;INTERVAL=2;BYDAY=TU": (13, ["2026-01-06", "2026-01-20", "2026-02-03"]),
    "FREQ=WEEKLY;BYDAY=MO;COUNT=4": (4, ["2026-01-12", "2026-01-19", "2026-01-26"]),
    "FREQ=MONTHLY": (6, ["2026-01-06", "2026-02-06", "2026-03-06"]),
    "FREQ=MONTHLY;BYDAY=3TU": (6, ["2026-01-20", "2026-02-17", "2026-03-17"]),
    "FREQ=MONTHLY;BYDAY=-1FR": (6, ["2026-01-30", "2026-02-27", "2026-03-27"]),
    "FREQ=MONTHLY;BYMONTHDAY=31": (3, ["2026-01-31", "2026-03-31", "2026-05-31"]),
    "FREQ=MONTHLY;INTERVAL=2": (3, ["2026-01-06", "2026-03-06", "2026-05-06"]),
    "FREQ=YEARLY": (1, ["2026-01-06"]),
    "FREQ=YEARLY;BYMONTH=1": (1, ["2026-01-06"]),
}


def dias(fechas) -> list[str]:
    return [f.strftime("%Y-%m-%d") for f in fechas]


class TestParseo(unittest.TestCase):
    def test_las_listas_se_parten(self):
        self.assertEqual(parse_rrule("FREQ=WEEKLY;BYDAY=MO,WE"),
                         {"FREQ": "WEEKLY", "BYDAY": ["MO", "WE"]})

    def test_una_regla_vacia_no_revienta(self):
        self.assertEqual(parse_rrule(""), {})


class TestVectoresDeReferencia(unittest.TestCase):
    """Cada cuenta se comprobó contra dateutil antes de fijarla aquí."""

    def test_coinciden(self):
        for texto, (cuantas, primeras) in VECTORES.items():
            with self.subTest(regla=texto):
                fechas = expand(INICIO, parse_rrule(texto), DESDE, HASTA)
                self.assertEqual(len(fechas), cuantas)
                self.assertEqual(dias(fechas)[:3], primeras)

    def test_siempre_salen_ordenadas(self):
        for texto in VECTORES:
            fechas = expand(INICIO, parse_rrule(texto), DESDE, HASTA)
            self.assertEqual(fechas, sorted(fechas), texto)

    def test_la_hora_del_inicio_se_conserva(self):
        for texto in VECTORES:
            for fecha in expand(INICIO, parse_rrule(texto), DESDE, HASTA):
                self.assertEqual((fecha.hour, fecha.minute), (9, 0), texto)


class TestVentana(unittest.TestCase):
    def test_solo_lo_que_cae_dentro(self):
        fechas = expand(INICIO, parse_rrule("FREQ=DAILY"),
                        datetime(2026, 2, 1, tzinfo=UTC),
                        datetime(2026, 2, 5, tzinfo=UTC))
        self.assertEqual(dias(fechas), ["2026-02-01", "2026-02-02",
                                        "2026-02-03", "2026-02-04"])

    def test_el_final_no_entra(self):
        fechas = expand(INICIO, parse_rrule("FREQ=DAILY"), DESDE,
                        datetime(2026, 1, 8, tzinfo=UTC))
        self.assertEqual(dias(fechas), ["2026-01-06", "2026-01-07"])

    def test_sin_regla_solo_esta_el_evento(self):
        self.assertEqual(expand(INICIO, {}, DESDE, HASTA), [INICIO])

    def test_sin_regla_y_fuera_de_la_ventana_no_hay_nada(self):
        self.assertEqual(expand(INICIO, {}, HASTA, datetime(2027, 1, 1, tzinfo=UTC)), [])

    def test_un_evento_viejo_sigue_llegando_a_la_ventana(self):
        """Una regla diaria de hace años debe alcanzar hoy, no agotarse antes."""
        viejo = datetime(2016, 1, 6, 9, 0, tzinfo=UTC)
        fechas = expand(viejo, parse_rrule("FREQ=DAILY"),
                        datetime(2026, 6, 1, tzinfo=UTC),
                        datetime(2026, 6, 5, tzinfo=UTC))
        self.assertEqual(len(fechas), 4)


class TestExclusionesYExtras(unittest.TestCase):
    def test_exdate_quita_una_ocurrencia(self):
        quitada = datetime(2026, 1, 8, 9, 0, tzinfo=UTC)
        fechas = expand(INICIO, parse_rrule("FREQ=DAILY;COUNT=5"), DESDE, HASTA,
                        exdate={quitada})
        self.assertNotIn(quitada, fechas)
        self.assertEqual(len(fechas), 4)

    def test_rdate_añade_una_suelta(self):
        extra = datetime(2026, 3, 15, 9, 0, tzinfo=UTC)
        fechas = expand(INICIO, {}, DESDE, HASTA, rdate=[extra])
        self.assertEqual(fechas, [INICIO, extra])

    def test_un_rdate_fuera_de_la_ventana_no_entra(self):
        fechas = expand(INICIO, {}, DESDE, HASTA,
                        rdate=[datetime(2027, 1, 1, tzinfo=UTC)])
        self.assertEqual(fechas, [INICIO])


class TestReglasQueNoSabemosExpandir(unittest.TestCase):
    """Es mejor quedarse corto que inventarse unas fechas que no son."""

    def test_bysetpos_no_esta_soportada(self):
        regla = parse_rrule("FREQ=MONTHLY;BYDAY=MO;BYSETPOS=-1")
        self.assertFalse(is_supported(regla))
        self.assertEqual(expand(INICIO, regla, DESDE, HASTA), [INICIO])

    def test_una_frecuencia_rara_tampoco(self):
        self.assertFalse(is_supported(parse_rrule("FREQ=SECONDLY")))

    def test_wkst_distinto_con_intervalo_mayor_que_uno(self):
        """Las semanas se cuentan desde el lunes; con otro WKST cambiaría."""
        self.assertFalse(is_supported(parse_rrule("FREQ=WEEKLY;INTERVAL=2;WKST=SU")))
        self.assertTrue(is_supported(parse_rrule("FREQ=WEEKLY;WKST=SU")))

    def test_las_habituales_si_lo_estan(self):
        for texto in VECTORES:
            self.assertTrue(is_supported(parse_rrule(texto)), texto)


class TestMesesRaros(unittest.TestCase):
    def test_el_31_se_salta_los_meses_que_no_lo_tienen(self):
        inicio = datetime(2026, 1, 31, 9, 0, tzinfo=UTC)
        fechas = expand(inicio, parse_rrule("FREQ=MONTHLY"),
                        datetime(2026, 1, 1, tzinfo=UTC),
                        datetime(2026, 5, 1, tzinfo=UTC))
        self.assertEqual(dias(fechas), ["2026-01-31", "2026-03-31"])

    def test_el_29_de_febrero_solo_en_bisiestos(self):
        inicio = datetime(2024, 2, 29, 9, 0, tzinfo=UTC)
        fechas = expand(inicio, parse_rrule("FREQ=YEARLY"),
                        datetime(2024, 1, 1, tzinfo=UTC),
                        datetime(2033, 1, 1, tzinfo=UTC))
        self.assertEqual(dias(fechas), ["2024-02-29", "2028-02-29", "2032-02-29"])

    def test_un_quinto_martes_que_no_existe_se_salta(self):
        fechas = expand(INICIO, parse_rrule("FREQ=MONTHLY;BYDAY=5TU"),
                        DESDE, datetime(2026, 5, 1, tzinfo=UTC))
        # Solo marzo de 2026 tiene cinco martes en ese tramo.
        self.assertEqual(dias(fechas), ["2026-03-31"])


if __name__ == "__main__":
    unittest.main()
