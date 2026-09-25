"""Parser de iCalendar: las cuatro trampas del formato, una por una."""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis.sources.ical import (  # noqa: E402
    datetime_of, duration_of, is_all_day, parse_dt, parse_duration,
    parse_events, parse_line, unescape, unfold, value_of,
)

UTC = timezone.utc


def calendario(*eventos: str) -> str:
    cuerpo = "\r\n".join(eventos)
    return f"BEGIN:VCALENDAR\r\nVERSION:2.0\r\n{cuerpo}\r\nEND:VCALENDAR\r\n"


EVENTO = """BEGIN:VEVENT
UID:abc-123
SUMMARY:Revisión con el cliente
DTSTART;TZID=Europe/Madrid:20260925T100000
DTEND;TZID=Europe/Madrid:20260925T113000
LOCATION:Sala 2
DESCRIPTION:Repasar el presupuesto
ATTENDEE;CN=Ana:mailto:ana@ejemplo.com
ATTENDEE;CN=Luis:mailto:luis@ejemplo.com
END:VEVENT""".replace("\n", "\r\n")


class TestPlegado(unittest.TestCase):
    def test_una_continuacion_se_une_a_la_anterior(self):
        """RFC 5545 parte las líneas largas; la siguiente empieza por espacio."""
        texto = "SUMMARY:Una reunión muy larga\r\n  que sigue aquí\r\nUID:1"
        self.assertEqual(unfold(texto),
                         ["SUMMARY:Una reunión muy larga que sigue aquí", "UID:1"])

    def test_el_tabulador_tambien_continua(self):
        self.assertEqual(unfold("A:uno\r\n\tdos"), ["A:unodos"])

    def test_sirven_los_tres_finales_de_linea(self):
        for salto in ("\r\n", "\n", "\r"):
            self.assertEqual(unfold(f"A:1{salto}B:2"), ["A:1", "B:2"])

    def test_las_lineas_en_blanco_se_van(self):
        self.assertEqual(unfold("A:1\r\n\r\nB:2"), ["A:1", "B:2"])


class TestLineas(unittest.TestCase):
    def test_nombre_parametros_y_valor(self):
        nombre, params, valor = parse_line("DTSTART;TZID=Europe/Madrid:20260925T100000")
        self.assertEqual(nombre, "DTSTART")
        self.assertEqual(params, {"TZID": "Europe/Madrid"})
        self.assertEqual(valor, "20260925T100000")

    def test_un_punto_y_coma_entre_comillas_no_separa(self):
        """`CN="Pérez; Ana"` es un solo parámetro, no dos."""
        _, params, _ = parse_line('ATTENDEE;CN="Pérez; Ana";ROLE=REQ:mailto:a@b.c')
        self.assertEqual(params, {"CN": "Pérez; Ana", "ROLE": "REQ"})

    def test_el_valor_puede_llevar_dos_puntos(self):
        _, _, valor = parse_line("ATTENDEE:mailto:ana@ejemplo.com")
        self.assertEqual(valor, "mailto:ana@ejemplo.com")

    def test_sin_parametros(self):
        self.assertEqual(parse_line("UID:abc"), ("UID", {}, "abc"))


class TestEscapes(unittest.TestCase):
    def test_salto_de_linea(self):
        self.assertEqual(unescape("uno\\ndos"), "uno\ndos")
        self.assertEqual(unescape("uno\\Ndos"), "uno\ndos")

    def test_coma_y_punto_y_coma(self):
        self.assertEqual(unescape("a\\,b\;c"), "a,b;c")

    def test_la_barra_doble_es_una_barra(self):
        self.assertEqual(unescape("a\\\\b"), "a\\b")

    def test_barra_literal_seguida_de_n_no_es_un_salto(self):
        """`\\\\n` es barra + n, no un salto de línea."""
        self.assertEqual(unescape("a\\\\nb"), "a\\nb")


class TestFechas(unittest.TestCase):
    def test_utc(self):
        self.assertEqual(parse_dt("20260925T100000Z"),
                         datetime(2026, 9, 25, 10, 0, tzinfo=UTC))

    def test_con_huso_horario(self):
        fecha = parse_dt("20260925T100000", {"TZID": "Europe/Madrid"})
        self.assertEqual(fecha.utcoffset(), timedelta(hours=2))  # verano
        self.assertEqual(fecha.astimezone(UTC).hour, 8)

    def test_dia_entero(self):
        fecha = parse_dt("20260925", {"VALUE": "DATE"})
        self.assertEqual((fecha.year, fecha.month, fecha.day), (2026, 9, 25))
        self.assertEqual((fecha.hour, fecha.minute), (0, 0))

    def test_ocho_digitos_ya_son_un_dia_entero(self):
        self.assertIsNotNone(parse_dt("20260925"))

    def test_un_huso_desconocido_no_revienta(self):
        self.assertIsNotNone(parse_dt("20260925T100000", {"TZID": "Marte/Olympus"}))

    def test_lo_ilegible_da_none(self):
        for basura in ("", "ayer", "2026-09-25", None):
            self.assertIsNone(parse_dt(basura))


class TestDuraciones(unittest.TestCase):
    def test_formas_habituales(self):
        casos = {"PT1H": timedelta(hours=1), "PT30M": timedelta(minutes=30),
                 "PT1H30M": timedelta(hours=1, minutes=30),
                 "P2D": timedelta(days=2), "P1W": timedelta(weeks=1),
                 "-PT15M": timedelta(minutes=-15)}
        for texto, esperado in casos.items():
            with self.subTest(texto=texto):
                self.assertEqual(parse_duration(texto), esperado)

    def test_lo_que_no_se_entiende_es_cero(self):
        self.assertEqual(parse_duration("mañana"), timedelta(0))

    def test_dtend_manda_sobre_duration(self):
        evento, = parse_events(calendario(EVENTO))
        self.assertEqual(duration_of(evento), timedelta(hours=1, minutes=30))

    def test_sin_dtend_vale_duration(self):
        crudo = EVENTO.replace("DTEND;TZID=Europe/Madrid:20260925T113000",
                               "DURATION:PT45M")
        evento, = parse_events(calendario(crudo))
        self.assertEqual(duration_of(evento), timedelta(minutes=45))

    def test_un_dia_entero_sin_dtend_dura_un_dia(self):
        crudo = ("BEGIN:VEVENT\r\nUID:d\r\nSUMMARY:Festivo\r\n"
                 "DTSTART;VALUE=DATE:20260925\r\nEND:VEVENT")
        evento, = parse_events(calendario(crudo))
        self.assertEqual(duration_of(evento), timedelta(days=1))
        self.assertTrue(is_all_day(evento))


class TestEventos(unittest.TestCase):
    def test_lectura_completa(self):
        evento, = parse_events(calendario(EVENTO))
        self.assertEqual(value_of(evento, "UID"), "abc-123")
        self.assertEqual(value_of(evento, "SUMMARY"), "Revisión con el cliente")
        self.assertEqual(value_of(evento, "LOCATION"), "Sala 2")
        self.assertEqual(datetime_of(evento, "DTSTART").astimezone(UTC).hour, 8)
        self.assertFalse(is_all_day(evento))

    def test_los_invitados_llegan_todos(self):
        """ATTENDEE se repite una vez por persona: no puede pisarse."""
        evento, = parse_events(calendario(EVENTO))
        self.assertEqual(len(evento["ATTENDEE"]), 2)

    def test_una_alarma_dentro_no_es_un_evento(self):
        con_alarma = EVENTO.replace(
            "END:VEVENT",
            "BEGIN:VALARM\r\nACTION:DISPLAY\r\nTRIGGER:-PT15M\r\n"
            "DESCRIPTION:Recuerda\r\nEND:VALARM\r\nEND:VEVENT")
        evento, = parse_events(calendario(con_alarma))
        self.assertEqual(value_of(evento, "DESCRIPTION"), "Repasar el presupuesto",
                         "la descripción de la alarma no debe pisar la del evento")
        self.assertNotIn("TRIGGER", evento)

    def test_los_vtimezone_no_son_eventos(self):
        zona = ("BEGIN:VTIMEZONE\r\nTZID:Europe/Madrid\r\n"
                "BEGIN:DAYLIGHT\r\nDTSTART:19700329T020000\r\nEND:DAYLIGHT\r\n"
                "END:VTIMEZONE")
        self.assertEqual(len(parse_events(calendario(zona, EVENTO))), 1)

    def test_varios_eventos(self):
        self.assertEqual(len(parse_events(calendario(EVENTO, EVENTO))), 2)

    def test_un_calendario_vacio_o_roto_da_lista_vacia(self):
        self.assertEqual(parse_events(""), [])
        self.assertEqual(parse_events("no soy un calendario"), [])
        self.assertEqual(parse_events(calendario()), [])

    def test_el_texto_llega_sin_escapes(self):
        crudo = EVENTO.replace("DESCRIPTION:Repasar el presupuesto",
                               "DESCRIPTION:Primero\\nSegundo\\, y ya")
        evento, = parse_events(calendario(crudo))
        self.assertEqual(value_of(evento, "DESCRIPTION"), "Primero\nSegundo, y ya")


if __name__ == "__main__":
    unittest.main()
