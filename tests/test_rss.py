"""Lector de feeds: parseo de los tres formatos y cliente contra un servidor falso."""
from __future__ import annotations

import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis.sources import rss as rss_module  # noqa: E402
from jarvis.sources.rss import (  # noqa: E402
    RssSource, has_doctype, parse_date, parse_feed,
)
from jarvis.sources.store import Store  # noqa: E402

RSS2 = """<?xml version="1.0"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
<channel><title>Blog de Yelko</title><link>https://yelko.dev</link>
<item>
  <title>Montar un asistente de voz</title>
  <link>https://yelko.dev/asistente</link>
  <guid isPermaLink="false">yelko-1</guid>
  <pubDate>Thu, 24 Sep 2026 10:00:00 +0200</pubDate>
  <description>&lt;p&gt;Llevo un mes con &lt;b&gt;esto&lt;/b&gt;.&lt;/p&gt;</description>
  <category>python</category>
</item></channel></rss>""".encode()

ATOM = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>Noticias</title>
<entry><title>Algo pasa</title>
  <link rel="edit" href="https://n.com/edit"/>
  <link href="https://n.com/algo"/>
  <id>tag:n.com,2026:1</id>
  <updated>2026-09-25T08:30:00Z</updated>
  <author><name>Redacción</name></author>
  <summary>Un resumen.</summary>
  <category term="mundo"/>
</entry></feed>""".encode()

RDF = """<?xml version="1.0"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
 xmlns="http://purl.org/rss/1.0/" xmlns:dc="http://purl.org/dc/elements/1.1/">
<channel><title>Viejo feed</title></channel>
<item><title>Entrada RDF</title><link>https://v.com/1</link>
<description>Texto.</description><dc:date>2026-09-20T12:00:00+02:00</dc:date>
<dc:creator>Alguien</dc:creator></item></rdf:RDF>""".encode()

BOMBA = """<?xml version="1.0"?>
<!DOCTYPE lolz [
 <!ENTITY lol "lol">
 <!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
]>
<rss><channel><item><title>&lol2;</title></item></channel></rss>""".encode()


class TestRss2(unittest.TestCase):
    def setUp(self):
        self.item, = parse_feed(RSS2, "https://yelko.dev/feed.xml")

    def test_campos(self):
        self.assertEqual(self.item.source, "rss")
        self.assertEqual(self.item.kind, "artículo")
        self.assertEqual(self.item.id, "rss:yelko.dev:yelko-1")
        self.assertEqual(self.item.title, "Blog de Yelko: Montar un asistente de voz")
        self.assertEqual(self.item.url, "https://yelko.dev/asistente")
        self.assertEqual(self.item.meta["etiquetas"], ["python"])

    def test_el_html_de_la_descripcion_se_convierte_en_texto(self):
        self.assertEqual(self.item.body, "Llevo un mes con esto.")

    def test_la_fecha_rfc822_pasa_a_utc(self):
        self.assertEqual(self.item.created_at, "2026-09-24T08:00:00+00:00")

    def test_sin_autor_firma_el_feed(self):
        self.assertEqual(self.item.author, "Blog de Yelko")

    def test_content_encoded_gana_a_description(self):
        feed = RSS2.replace(
            b"<category>python</category>",
            b"<content:encoded>El texto completo.</content:encoded>")
        self.assertEqual(parse_feed(feed)[0].body, "El texto completo.")


class TestAtom(unittest.TestCase):
    def setUp(self):
        self.item, = parse_feed(ATOM, "https://n.com/atom")

    def test_campos(self):
        self.assertEqual(self.item.id, "rss:n.com:tag:n.com,2026:1")
        self.assertEqual(self.item.title, "Noticias: Algo pasa")
        self.assertEqual(self.item.author, "Redacción")
        self.assertEqual(self.item.body, "Un resumen.")
        self.assertEqual(self.item.created_at, "2026-09-25T08:30:00+00:00")
        self.assertEqual(self.item.meta["etiquetas"], ["mundo"])

    def test_se_elige_el_enlace_alternativo_no_el_de_edicion(self):
        """En Atom hay varios <link>; el del artículo es el rel=alternate."""
        self.assertEqual(self.item.url, "https://n.com/algo")

    def test_sin_alternate_vale_cualquier_enlace(self):
        feed = ATOM.replace(b'<link href="https://n.com/algo"/>', b"")
        self.assertEqual(parse_feed(feed)[0].url, "https://n.com/edit")


class TestRdf(unittest.TestCase):
    def test_rss1_tambien_se_entiende(self):
        item, = parse_feed(RDF, "https://v.com/rdf")
        self.assertEqual(item.title, "Viejo feed: Entrada RDF")
        self.assertEqual(item.author, "Alguien")
        self.assertEqual(item.created_at, "2026-09-20T10:00:00+00:00")


class TestEntradasRaras(unittest.TestCase):
    def envuelve(self, entrada: str) -> bytes:
        return f"<rss><channel><title>F</title>{entrada}</channel></rss>".encode()

    def test_una_entrada_vacia_se_descarta(self):
        self.assertEqual(parse_feed(self.envuelve("<item></item>")), [])

    def test_sin_titulo_pero_con_texto_se_conserva(self):
        item, = parse_feed(self.envuelve("<item><description>Algo</description></item>"))
        self.assertEqual(item.title, "F: Algo")

    def test_sin_guid_ni_enlace_el_titulo_da_la_identidad(self):
        item, = parse_feed(self.envuelve("<item><title>T</title></item>"), "https://a.com/f")
        self.assertEqual(item.id, "rss:a.com:T")

    def test_sin_fecha_se_usa_la_de_ahora(self):
        item, = parse_feed(self.envuelve("<item><title>T</title></item>"))
        self.assertTrue(item.created_at)

    def test_texto_largo_se_recorta(self):
        largo = "a" * 5000
        item, = parse_feed(self.envuelve(f"<item><description>{largo}</description></item>"))
        self.assertEqual(len(item.body), rss_module.MAX_BODY)

    def test_un_feed_ilegible_no_revienta(self):
        self.assertEqual(parse_feed(b"esto no es xml"), [])
        self.assertEqual(parse_feed(b""), [])


class TestDefensaXml(unittest.TestCase):
    """ElementTree expande las entidades internas, así que un DTD se rechaza."""

    def test_el_feed_con_dtd_se_descarta_entero(self):
        self.assertTrue(has_doctype(BOMBA))
        self.assertEqual(parse_feed(BOMBA, "https://malo/feed"), [])

    def test_un_feed_normal_no_lo_parece(self):
        for feed in (RSS2, ATOM, RDF):
            self.assertFalse(has_doctype(feed))

    def test_doctype_dentro_de_un_comentario_del_prologo(self):
        self.assertFalse(has_doctype(b"<!-- <!DOCTYPE x> --><rss/>"))

    def test_doctype_en_el_contenido_no_cuenta(self):
        """Solo el prólogo puede declarar un DTD; el texto de un artículo, no."""
        feed = b"<rss><channel><item><title>que es <!DOCTYPE></title></item></channel></rss>"
        self.assertFalse(has_doctype(feed))


class TestFechas(unittest.TestCase):
    def test_formatos_que_circulan(self):
        casos = {
            "Thu, 24 Sep 2026 10:00:00 +0200": "2026-09-24T08:00:00+00:00",
            "2026-09-25T08:30:00Z": "2026-09-25T08:30:00+00:00",
            "2026-09-25T08:30:00+0200": "2026-09-25T06:30:00+00:00",
            # Nanosegundos: fromisoformat solo admite hasta seis dígitos.
            "2026-09-25T08:30:00.123456789Z": "2026-09-25T08:30:00+00:00",
            "2026-09-25": "2026-09-25T00:00:00+00:00",
        }
        for entrada, esperado in casos.items():
            with self.subTest(entrada=entrada):
                self.assertEqual(parse_date(entrada), esperado)

    def test_sin_zona_horaria_se_asume_utc(self):
        self.assertTrue(parse_date("2026-09-25T08:30:00").endswith("+00:00"))

    def test_lo_que_no_es_fecha_da_cadena_vacia(self):
        for basura in ("no es fecha", "", None):
            self.assertEqual(parse_date(basura), "")


class FakeFeeds(BaseHTTPRequestHandler):
    peticiones: list = []
    status = 200
    cuerpo = RSS2
    etag = '"v1"'

    def log_message(self, *args):
        pass

    def do_GET(self):  # noqa: N802
        FakeFeeds.peticiones.append({
            "ruta": self.path,
            "agente": self.headers.get("User-Agent", ""),
            "if_none_match": self.headers.get("If-None-Match", ""),
        })
        if self.path == "/roto":
            cuerpo, status = b"esto no es xml", 200
        elif self.path == "/error":
            cuerpo, status = b"nope", 500
        elif FakeFeeds.etag and self.headers.get("If-None-Match") == FakeFeeds.etag:
            self.send_response(304)
            self.end_headers()
            return
        else:
            cuerpo, status = FakeFeeds.cuerpo, FakeFeeds.status

        self.send_response(status)
        self.send_header("Content-Type", "application/rss+xml")
        self.send_header("Content-Length", str(len(cuerpo)))
        if FakeFeeds.etag:
            self.send_header("ETag", FakeFeeds.etag)
        self.end_headers()
        self.wfile.write(cuerpo)


class TestClienteContraUnServidorFalso(unittest.TestCase):
    def setUp(self):
        FakeFeeds.peticiones = []
        FakeFeeds.status = 200
        FakeFeeds.cuerpo = RSS2
        FakeFeeds.etag = '"v1"'
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeFeeds)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "index.db")

    def tearDown(self):
        self.server.shutdown()
        self.store.close()
        self.tmp.cleanup()

    def test_lee_e_indexa(self):
        fuente = RssSource(feeds=[f"{self.base}/feed.xml"])
        self.assertEqual(fuente.sync(self.store), 1)
        self.assertEqual(self.store.counts(), {"rss": 1})
        self.assertIn("jarvis", FakeFeeds.peticiones[0]["agente"])

    def test_varios_feeds_en_una_sincronizacion(self):
        FakeFeeds.etag = ""
        fuente = RssSource(feeds=[f"{self.base}/uno", f"{self.base}/dos"])
        fuente.sync(self.store)
        self.assertEqual(len(FakeFeeds.peticiones), 2)

    def test_la_segunda_vuelta_pregunta_con_etag_y_recibe_304(self):
        """Si el feed no cambió, el servidor no reenvía nada y no se reparsea."""
        fuente = RssSource(feeds=[f"{self.base}/feed.xml"])
        self.assertEqual(fuente.sync(self.store), 1)
        self.assertEqual(fuente.sync(self.store), 0)

        self.assertEqual(FakeFeeds.peticiones[0]["if_none_match"], "")
        self.assertEqual(FakeFeeds.peticiones[1]["if_none_match"], '"v1"')
        self.assertEqual(self.store.counts(), {"rss": 1})

    def test_un_feed_roto_no_impide_leer_los_demas(self):
        FakeFeeds.etag = ""
        fuente = RssSource(feeds=[f"{self.base}/roto", f"{self.base}/bueno"])
        self.assertEqual(fuente.sync(self.store), 1)

    def test_un_error_del_servidor_no_revienta(self):
        FakeFeeds.etag = ""
        self.assertEqual(RssSource(feeds=[f"{self.base}/error"]).sync(self.store), 0)
        self.assertEqual(self.store.counts(), {})

    def test_un_feed_gigante_se_descarta(self):
        FakeFeeds.etag = ""
        FakeFeeds.cuerpo = RSS2 + b"<!-- " + b"a" * rss_module.MAX_BYTES + b" -->"
        self.assertEqual(RssSource(feeds=[f"{self.base}/gordo"]).sync(self.store), 0)

    def test_el_limite_recorta_por_feed(self):
        FakeFeeds.etag = ""
        entradas = "".join(f"<item><title>T{n}</title></item>" for n in range(10))
        FakeFeeds.cuerpo = f"<rss><channel><title>F</title>{entradas}</channel></rss>".encode()
        self.assertEqual(RssSource(feeds=[f"{self.base}/muchos"], limit=3)
                         .sync(self.store), 3)


class TestConfiguracion(unittest.TestCase):
    def test_sin_feeds_avisa(self):
        for vacio in ([], None, ["", None]):
            with self.assertRaises(RuntimeError):
                RssSource(feeds=vacio)

    def test_intervalo_propio(self):
        self.assertEqual(RssSource(feeds=["https://a/f"],
                                   interval_minutes=30).interval_minutes, 30)


if __name__ == "__main__":
    unittest.main()
