"""Lector de X: parseo y cliente contra un servidor falso.

No se prueba contra la API real: no hay credenciales aquí y, sobre todo,
cada lectura se factura.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis.sources import x_twitter as x_module  # noqa: E402
from jarvis.sources.store import Store  # noqa: E402
from jarvis.sources.x_twitter import XSource, authors_by_id, parse_post  # noqa: E402

CREDENCIALES = {
    "JARVIS_X_API_KEY": "clave",
    "JARVIS_X_API_SECRET": "secreto",
    "JARVIS_X_ACCESS_TOKEN": "token",
    "JARVIS_X_ACCESS_SECRET": "secreto-token",
}

AUTORES = {"7": {"id": "7", "username": "yelko", "name": "Yelko"}}


def tweet(**campos) -> dict:
    base = {
        "id": "1900000000000000001",
        "author_id": "7",
        "text": "Por fin le he puesto cancelación de eco al asistente.",
        "created_at": "2026-09-20T18:30:00.000Z",
        "public_metrics": {"reply_count": 3, "like_count": 41, "retweet_count": 5},
    }
    base.update(campos)
    return base


def respuesta(*tweets) -> dict:
    return {
        "data": list(tweets) or [tweet()],
        "includes": {"users": list(AUTORES.values())},
        "meta": {"result_count": len(tweets) or 1},
    }


class TestParse(unittest.TestCase):
    def test_publicacion_completa(self):
        item = parse_post(tweet(), AUTORES)
        self.assertEqual(item.source, "x")
        self.assertEqual(item.id, "x:1900000000000000001")
        self.assertTrue(item.title.startswith("Yelko:"))
        self.assertIn("cancelación de eco", item.body)
        self.assertEqual(item.author, "@yelko")
        self.assertEqual(item.url,
                         "https://x.com/yelko/status/1900000000000000001")
        self.assertEqual(item.created_at, "2026-09-20T18:30:00.000Z")
        self.assertEqual(item.meta["me_gusta"], 41)

    def test_sin_autor_en_includes_no_revienta(self):
        """Si falta el usuario expandido queda el id, no un hueco."""
        item = parse_post(tweet(), {})
        self.assertIn("7", item.title)
        self.assertEqual(item.author, "")
        self.assertEqual(item.url, "", "sin handle no se puede formar el enlace")

    def test_publicacion_sin_texto_se_descarta(self):
        self.assertIsNone(parse_post(tweet(text="   "), AUTORES))
        self.assertIsNone(parse_post({}, AUTORES))

    def test_sin_fecha_se_usa_la_de_ahora(self):
        item = parse_post(tweet(created_at=None), AUTORES)
        self.assertTrue(item.created_at)

    def test_texto_largo_se_recorta(self):
        item = parse_post(tweet(text="a" * 5000), AUTORES)
        self.assertEqual(len(item.body), x_module.MAX_BODY)

    def test_autores_por_id(self):
        self.assertEqual(authors_by_id(respuesta())["7"]["username"], "yelko")
        self.assertEqual(authors_by_id({}), {})


class TestConfiguracion(unittest.TestCase):
    def fuente(self, **kwargs):
        with mock.patch.dict(os.environ, CREDENCIALES):
            return XSource(**kwargs)

    def test_faltando_una_credencial_avisa(self):
        for falta in CREDENCIALES:
            with self.subTest(falta=falta):
                with mock.patch.dict(os.environ, {**CREDENCIALES, falta: ""}):
                    with self.assertRaises(RuntimeError):
                        XSource()

    def test_el_limite_se_ajusta_a_lo_que_acepta_la_api(self):
        self.assertEqual(self.fuente(limit=500).limit, 100)
        self.assertEqual(self.fuente(limit=1).limit, 5)

    def test_intervalo_propio(self):
        """X cuesta dinero, así que puede ir a su ritmo y no al general."""
        self.assertEqual(self.fuente(interval_minutes=60).interval_minutes, 60)


class FakeX(BaseHTTPRequestHandler):
    peticiones: list = []
    status = 200

    def log_message(self, *args):
        pass

    def do_GET(self):  # noqa: N802
        partes = urlsplit(self.path)
        FakeX.peticiones.append({
            "ruta": partes.path,
            "params": {k: v[0] for k, v in parse_qs(partes.query).items()},
            "auth": self.headers.get("Authorization", ""),
        })
        if partes.path.endswith("/users/me"):
            payload = {"data": {"id": "7", "username": "yelko"}}
        else:
            payload = respuesta()
        cuerpo = json.dumps(payload).encode()
        self.send_response(FakeX.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)


class TestClienteContraUnaApiFalsa(unittest.TestCase):
    def setUp(self):
        FakeX.peticiones = []
        FakeX.status = 200
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeX)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{self.server.server_address[1]}/2"
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "index.db")
        self.patch = mock.patch.object(x_module, "API", base)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.server.shutdown()
        self.store.close()
        self.tmp.cleanup()

    def fuente(self, **kwargs):
        with mock.patch.dict(os.environ, CREDENCIALES):
            return XSource(**kwargs)

    def test_lee_la_linea_temporal_y_la_indexa(self):
        self.assertEqual(self.fuente(user_id="7").sync(self.store), 1)
        self.assertEqual(self.store.counts(), {"x": 1})

        peticion, = FakeX.peticiones
        self.assertTrue(peticion["ruta"].endswith(
            "/users/7/timelines/reverse_chronological"))

    def test_la_peticion_va_firmada(self):
        self.fuente(user_id="7").sync(self.store)
        cabecera = FakeX.peticiones[0]["auth"]
        self.assertTrue(cabecera.startswith("OAuth "), cabecera)
        self.assertIn('oauth_signature="', cabecera)
        self.assertIn('oauth_consumer_key="clave"', cabecera)

    def test_pide_el_autor_expandido(self):
        """Sin la expansión los mensajes llegarían firmados por un número."""
        self.fuente(user_id="7", limit=25).sync(self.store)
        params = FakeX.peticiones[0]["params"]
        self.assertEqual(params["expansions"], "author_id")
        self.assertEqual(params["user.fields"], "username,name")
        self.assertIn("created_at", params["tweet.fields"])
        self.assertEqual(params["max_results"], "25")

    def test_sin_user_id_lo_resuelve_una_vez(self):
        fuente = self.fuente()
        fuente.sync(self.store)
        fuente.sync(self.store)

        rutas = [p["ruta"] for p in FakeX.peticiones]
        self.assertEqual(sum(r.endswith("/users/me") for r in rutas), 1,
                         "resolver el id dos veces es una lectura pagada de más")
        self.assertEqual(fuente.user_id, "7")

    def test_con_user_id_no_pregunta_quien_eres(self):
        self.fuente(user_id="7").sync(self.store)
        self.assertFalse(any(p["ruta"].endswith("/users/me")
                             for p in FakeX.peticiones))

    def test_un_error_de_la_api_no_revienta(self):
        FakeX.status = 429
        self.assertEqual(self.fuente(user_id="7").sync(self.store), 0)
        self.assertEqual(self.store.counts(), {})

    def test_releer_lo_mismo_no_duplica(self):
        fuente = self.fuente(user_id="7")
        self.assertEqual(fuente.sync(self.store), 1)
        self.assertEqual(fuente.sync(self.store), 0)
        self.assertEqual(self.store.counts(), {"x": 1})


if __name__ == "__main__":
    unittest.main()
