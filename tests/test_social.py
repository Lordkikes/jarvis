"""Lectores de Bluesky y Mastodon. Sin red: se prueba lo que llega de la API."""
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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis.sources import html_to_text  # noqa: E402
from jarvis.sources.bluesky import BlueskySource, parse_post, post_url  # noqa: E402
from jarvis.sources.mastodon import MastodonSource, parse_status  # noqa: E402
from jarvis.sources.store import Store  # noqa: E402


def bsky_entry(text="Probando Jarvis", handle="yelko.dev", **extra) -> dict:
    entry = {
        "post": {
            "uri": f"at://did:plc:abc123/app.bsky.feed.post/3kxyz",
            "author": {"handle": handle, "displayName": "Yelko"},
            "record": {"text": text, "createdAt": "2026-09-20T10:00:00.000Z"},
            "replyCount": 2, "likeCount": 7,
        }
    }
    entry.update(extra)
    return entry


def toot(content="<p>Hola desde Mastodon</p>", **extra) -> dict:
    status = {
        "id": "109876",
        "created_at": "2026-09-20T10:00:00.000Z",
        "url": "https://mastodon.social/@yelko/109876",
        "content": content,
        "account": {"acct": "yelko", "display_name": "Yelko"},
        "replies_count": 1, "favourites_count": 3,
    }
    status.update(extra)
    return status


class TestHtmlToText(unittest.TestCase):
    def test_tags_out_entities_in(self):
        self.assertEqual(html_to_text("<p>caf&eacute; &amp; leche</p>"), "café & leche")

    def test_line_breaks_survive(self):
        self.assertEqual(html_to_text("uno<br>dos"), "uno\ndos")

    def test_paragraphs_separate(self):
        self.assertEqual(html_to_text("<p>uno</p><p>dos</p>"), "uno\n\ndos")

    def test_empty(self):
        self.assertEqual(html_to_text(""), "")


class TestBluesky(unittest.TestCase):
    def test_basic_post(self):
        item = parse_post(bsky_entry())
        self.assertEqual(item.source, "bluesky")
        self.assertEqual(item.author, "yelko.dev")
        self.assertIn("Yelko:", item.title)
        self.assertEqual(item.body, "Probando Jarvis")
        self.assertEqual(item.created_at, "2026-09-20T10:00:00.000Z")
        self.assertEqual(item.meta["me_gusta"], 7)

    def test_web_url_is_built_from_the_at_uri(self):
        self.assertEqual(
            post_url("at://did:plc:abc123/app.bsky.feed.post/3kxyz", "yelko.dev"),
            "https://bsky.app/profile/yelko.dev/post/3kxyz")

    def test_url_is_empty_without_handle(self):
        self.assertEqual(post_url("at://did/app.bsky.feed.post/3k", ""), "")

    def test_repost_records_who_boosted_it(self):
        entry = bsky_entry(reason={"$type": "app.bsky.feed.defs#reasonRepost",
                                   "by": {"handle": "otra.persona"}})
        self.assertEqual(parse_post(entry).meta["reposteado_por"], "otra.persona")

    def test_a_reply_is_not_treated_as_a_repost(self):
        entry = bsky_entry(reason={"$type": "app.bsky.feed.defs#reasonPin",
                                   "by": {"handle": "alguien"}})
        self.assertEqual(parse_post(entry).meta["reposteado_por"], "")

    def test_image_only_post_is_skipped(self):
        """Sin texto no hay nada que indexar ni que leer en voz alta."""
        self.assertIsNone(parse_post(bsky_entry(text="   ")))

    def test_missing_fields_do_not_crash(self):
        self.assertIsNone(parse_post({}))
        self.assertIsNone(parse_post({"post": {}}))

    def test_falls_back_to_indexed_at_without_created_at(self):
        entry = bsky_entry()
        del entry["post"]["record"]["createdAt"]
        entry["post"]["indexedAt"] = "2026-09-19T08:00:00.000Z"
        self.assertEqual(parse_post(entry).created_at, "2026-09-19T08:00:00.000Z")


class TestMastodon(unittest.TestCase):
    def test_basic_status(self):
        item = parse_status(toot(), instance="https://mastodon.social")
        self.assertEqual(item.source, "mastodon")
        self.assertEqual(item.id, "mastodon:mastodon.social:109876")
        self.assertEqual(item.body, "Hola desde Mastodon")
        self.assertEqual(item.author, "yelko")
        self.assertEqual(item.url, "https://mastodon.social/@yelko/109876")

    def test_boost_indexes_the_original_and_notes_who_boosted(self):
        original = toot(content="<p>Publicación original</p>",
                        account={"acct": "autora", "display_name": "Autora"})
        boost = toot(content="", reblog=original,
                     account={"acct": "yelko", "display_name": "Yelko"})
        item = parse_status(boost)
        self.assertEqual(item.body, "Publicación original")
        self.assertEqual(item.author, "autora")
        self.assertEqual(item.meta["impulsado_por"], "yelko")

    def test_content_warning_goes_in_front(self):
        item = parse_status(toot(spoiler_text="spoilers de la serie"))
        self.assertTrue(item.body.startswith("[spoilers de la serie]"))

    def test_empty_status_is_skipped(self):
        self.assertIsNone(parse_status(toot(content="<p></p>")))

    def test_missing_fields_do_not_crash(self):
        self.assertIsNone(parse_status({}))


class FakeAPI(BaseHTTPRequestHandler):
    """Servidor de mentira con las rutas reales de las dos APIs.

    Las pruebas de parseo no ven la petición: aquí se comprueba que la URL, la
    cabecera de autorización y el parámetro de límite salen como deben.
    """

    requests: list = []
    status = 200

    def log_message(self, *args):  # silencia el servidor en la salida de test
        pass

    def _send(self, payload):
        body = json.dumps(payload).encode()
        self.send_response(FakeAPI.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _record(self):
        FakeAPI.requests.append({
            "method": self.command,
            "path": self.path,
            "auth": self.headers.get("Authorization", ""),
        })

    def do_POST(self):  # noqa: N802
        self._record()
        self._send({"accessJwt": "token-de-prueba", "did": "did:plc:abc",
                    "handle": "yelko.dev"})

    def do_GET(self):  # noqa: N802
        self._record()
        if "getTimeline" in self.path:
            self._send({"feed": [bsky_entry()], "cursor": "x"})
        else:
            self._send([toot()])


class TestClientsAgainstAFakeAPI(unittest.TestCase):
    def setUp(self):
        FakeAPI.requests = []
        FakeAPI.status = 200
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeAPI)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "index.db")

    def tearDown(self):
        self.server.shutdown()
        self.store.close()
        self.tmp.cleanup()

    def test_bluesky_logs_in_and_reads_the_timeline(self):
        with mock.patch.dict(os.environ, {"JARVIS_BLUESKY_APP_PASSWORD": "app-pass"}):
            source = BlueskySource(handle="yelko.dev", service=self.base, limit=50)
        self.assertEqual(source.sync(self.store), 1)

        sesion, linea = FakeAPI.requests
        self.assertEqual(sesion["method"], "POST")
        self.assertIn("com.atproto.server.createSession", sesion["path"])
        self.assertIn("app.bsky.feed.getTimeline", linea["path"])
        self.assertIn("limit=50", linea["path"])
        self.assertEqual(linea["auth"], "Bearer token-de-prueba",
                         "la línea temporal va autenticada con el token de la sesión")
        self.assertEqual(self.store.counts(), {"bluesky": 1})

    def test_mastodon_sends_the_token_and_reads_home(self):
        with mock.patch.dict(os.environ, {"JARVIS_MASTODON_TOKEN": "tok"}):
            source = MastodonSource(instance=self.base, limit=40)
        self.assertEqual(source.sync(self.store), 1)

        peticion = FakeAPI.requests[0]
        self.assertIn("/api/v1/timelines/home", peticion["path"])
        self.assertIn("limit=40", peticion["path"])
        self.assertEqual(peticion["auth"], "Bearer tok")
        self.assertEqual(self.store.counts(), {"mastodon": 1})

    def test_an_api_error_does_not_raise(self):
        """Un token caducado no puede tumbar el ciclo de sincronización."""
        FakeAPI.status = 401
        with mock.patch.dict(os.environ, {"JARVIS_MASTODON_TOKEN": "caducado"}):
            source = MastodonSource(instance=self.base)
        self.assertEqual(source.sync(self.store), 0)
        self.assertEqual(self.store.counts(), {})

    def test_credentials_are_demanded_up_front(self):
        with mock.patch.dict(os.environ, {"JARVIS_BLUESKY_APP_PASSWORD": ""}):
            with self.assertRaises(RuntimeError):
                BlueskySource(handle="yelko.dev")
        with mock.patch.dict(os.environ, {"JARVIS_MASTODON_TOKEN": ""}):
            with self.assertRaises(RuntimeError):
                MastodonSource(instance="https://mastodon.social")


if __name__ == "__main__":
    unittest.main()
