"""Lector de Reddit: parseo y cliente contra un servidor falso."""
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

from jarvis.sources import reddit as reddit_module  # noqa: E402
from jarvis.sources.reddit import RedditSource, parse_post  # noqa: E402
from jarvis.sources.store import Store  # noqa: E402

CREDENCIALES = {
    "JARVIS_REDDIT_CLIENT_ID": "id",
    "JARVIS_REDDIT_CLIENT_SECRET": "secreto",
    "JARVIS_REDDIT_PASSWORD": "clave",
}


def child(**data) -> dict:
    base = {
        "id": "abc123", "name": "t3_abc123",
        "title": "Cómo monté un asistente de voz en local",
        "selftext": "Llevo un mes con esto y por fin funciona.",
        "author": "yelko", "subreddit": "selfhosted",
        "permalink": "/r/selfhosted/comments/abc123/como_monte/",
        "url": "https://www.reddit.com/r/selfhosted/comments/abc123/como_monte/",
        "created_utc": 1789000000.0, "score": 128, "num_comments": 17,
    }
    base.update(data)
    return {"kind": "t3", "data": base}


class TestParse(unittest.TestCase):
    def test_basic_post(self):
        item = parse_post(child())
        self.assertEqual(item.source, "reddit")
        self.assertEqual(item.id, "reddit:t3_abc123")
        self.assertTrue(item.title.startswith("r/selfhosted:"))
        self.assertIn("por fin funciona", item.body)
        self.assertEqual(item.author, "u/yelko")
        self.assertTrue(item.url.startswith("https://www.reddit.com/r/selfhosted/"))
        self.assertEqual(item.meta["votos"], 128)

    def test_epoch_becomes_an_iso_date(self):
        item = parse_post(child(created_utc=1789000000.0))
        self.assertTrue(item.created_at.startswith("2026-"), item.created_at)
        self.assertTrue(item.created_at.endswith("+00:00"), "debe venir en UTC")

    def test_link_post_records_where_it_points(self):
        """Una publicación de enlace no trae texto; el enlace es el contenido."""
        item = parse_post(child(selftext="", url="https://ejemplo.com/articulo"))
        self.assertIn("https://ejemplo.com/articulo", item.body)

    def test_post_without_title_is_skipped(self):
        self.assertIsNone(parse_post(child(title="   ")))

    def test_missing_fields_do_not_crash(self):
        self.assertIsNone(parse_post({}))
        self.assertIsNone(parse_post({"data": {}}))

    def test_broken_date_falls_back_to_now(self):
        item = parse_post(child(created_utc="no es una fecha"))
        self.assertTrue(item.created_at)

    def test_nsfw_and_flair_are_kept(self):
        item = parse_post(child(over_18=True, link_flair_text="Guía"))
        self.assertTrue(item.meta["nsfw"])
        self.assertEqual(item.meta["etiqueta"], "Guía")


class TestFeedPath(unittest.TestCase):
    def source(self, **kwargs):
        with mock.patch.dict(os.environ, CREDENCIALES):
            return RedditSource(username="yelko", **kwargs)

    def test_without_subreddits_reads_your_front_page(self):
        self.assertEqual(self.source().path, "/best")

    def test_with_subreddits_joins_them(self):
        source = self.source(subreddits=["python", "selfhosted"], feed="new")
        self.assertEqual(source.path, "/r/python+selfhosted/new")

    def test_an_invalid_feed_falls_back_to_best(self):
        self.assertEqual(self.source(feed="inventado").feed, "best")

    def test_user_agent_follows_reddits_format(self):
        """Un User-Agent genérico acaba estrangulado por Reddit."""
        self.assertEqual(self.source().user_agent,
                         "python:jarvis-asistente:0.1 (by /u/yelko)")

    def test_credentials_are_demanded_up_front(self):
        with mock.patch.dict(os.environ, {**CREDENCIALES, "JARVIS_REDDIT_PASSWORD": ""}):
            with self.assertRaises(RuntimeError):
                RedditSource(username="yelko")


class FakeReddit(BaseHTTPRequestHandler):
    requests: list = []
    status = 200

    def log_message(self, *args):
        pass

    def _send(self, payload):
        body = json.dumps(payload).encode()
        self.send_response(FakeReddit.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _record(self):
        FakeReddit.requests.append({
            "method": self.command,
            "path": self.path,
            "auth": self.headers.get("Authorization", ""),
            "agent": self.headers.get("User-Agent", ""),
        })

    def do_POST(self):  # noqa: N802
        self._record()
        self._send({"access_token": "token-de-prueba", "expires_in": 3600})

    def do_GET(self):  # noqa: N802
        self._record()
        self._send({"kind": "Listing", "data": {"children": [child()], "after": None}})


class TestClientAgainstAFakeAPI(unittest.TestCase):
    def setUp(self):
        FakeReddit.requests = []
        FakeReddit.status = 200
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeReddit)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "index.db")
        # Las URLs de Reddit son constantes del módulo: se apuntan al servidor falso.
        self.patches = [
            mock.patch.object(reddit_module, "TOKEN_URL", f"{self.base}/api/v1/access_token"),
            mock.patch.object(reddit_module, "API", self.base),
        ]
        for patch in self.patches:
            patch.start()

    def tearDown(self):
        for patch in self.patches:
            patch.stop()
        self.server.shutdown()
        self.store.close()
        self.tmp.cleanup()

    def source(self, **kwargs):
        with mock.patch.dict(os.environ, CREDENCIALES):
            return RedditSource(username="yelko", **kwargs)

    def test_authenticates_and_reads_the_listing(self):
        self.assertEqual(self.source().sync(self.store), 1)

        token, listado = FakeReddit.requests
        self.assertEqual(token["method"], "POST")
        self.assertIn("/api/v1/access_token", token["path"])
        self.assertTrue(token["auth"].startswith("Basic "),
                        "el token se pide con autenticación básica de la app")
        self.assertIn("/best", listado["path"])
        self.assertIn("limit=40", listado["path"])
        self.assertEqual(listado["auth"], "bearer token-de-prueba")
        self.assertEqual(self.store.counts(), {"reddit": 1})

    def test_the_user_agent_travels_in_both_requests(self):
        self.source().sync(self.store)
        for peticion in FakeReddit.requests:
            self.assertIn("by /u/yelko", peticion["agent"])

    def test_chosen_subreddits_end_up_in_the_url(self):
        self.source(subreddits=["python", "selfhosted"], feed="new").sync(self.store)
        self.assertIn("/r/python+selfhosted/new", FakeReddit.requests[1]["path"])

    def test_an_api_error_does_not_raise(self):
        FakeReddit.status = 401
        self.assertEqual(self.source().sync(self.store), 0)
        self.assertEqual(self.store.counts(), {})


if __name__ == "__main__":
    unittest.main()
