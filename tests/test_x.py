"""Lector de X: parseo y cliente contra un servidor falso.

No se prueba contra la API real: no hay credenciales aquí y, sobre todo,
cada lectura se factura.
"""
from __future__ import annotations

import base64
import hashlib
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

from jarvis.bus import EventBus  # noqa: E402
from jarvis.config import Config  # noqa: E402
from jarvis.llm.tools import Toolbox  # noqa: E402
from jarvis.sources import x_twitter as x_module  # noqa: E402
from jarvis.sources.store import Store  # noqa: E402
from jarvis.sources.oauth2 import (  # noqa: E402
    PERMISOS, Tokens, cuerpo_de_canje, cuerpo_de_refresco, url_de_autorizacion,
    verificador,
)
from jarvis.sources import oauth2 as oauth2_module  # noqa: E402
from jarvis.sources.x_twitter import (  # noqa: E402
    CON_OAUTH2, CORRIENTES, POR_DEFECTO, XSource, authors_by_id,
    corrientes_validas, parse_post,
)

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
    #: Qué devolver según cómo acabe la ruta. Lo que no esté, va al genérico.
    por_ruta: dict = {}
    #: Rutas que contestan con error, para probar una corriente caída.
    rotas: tuple = ()
    #: Lo que devuelve el endpoint de tokens, y lo que se le pidió.
    tokens: dict = {"access_token": "acceso-1", "refresh_token": "refresco-2",
                    "expires_in": 7200}
    canjes: list = []
    token_status = 200

    def do_POST(self):  # noqa: N802
        largo = int(self.headers.get("Content-Length", 0))
        cuerpo = self.rfile.read(largo).decode()
        FakeX.canjes.append({k: v[0] for k, v in parse_qs(cuerpo).items()})
        datos = json.dumps(FakeX.tokens).encode()
        self.send_response(FakeX.token_status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(datos)))
        self.end_headers()
        self.wfile.write(datos)

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
            payload = next((p for final, p in FakeX.por_ruta.items()
                            if partes.path.endswith(final)), respuesta())
        estado = FakeX.status
        if any(partes.path.endswith(r) for r in FakeX.rotas):
            estado, payload = 503, {"title": "vaya"}
        cuerpo = json.dumps(payload).encode()
        self.send_response(estado)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)


class CasoConApiFalsa(unittest.TestCase):
    """El montaje: un servidor de mentira en el sitio de api.x.com."""

    def setUp(self):
        FakeX.peticiones = []
        FakeX.status = 200
        FakeX.por_ruta = {}
        FakeX.rotas = ()
        FakeX.canjes = []
        FakeX.token_status = 200
        FakeX.tokens = {"access_token": "acceso-1",
                        "refresh_token": "refresco-2", "expires_in": 7200}
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


class TestClienteContraUnaApiFalsa(CasoConApiFalsa):
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


class TestCorrientes(unittest.TestCase):
    """Qué se lee de lo tuyo. Las tres son Owned Reads; los marcadores no van."""

    def test_por_defecto_solo_el_timeline(self):
        """Quien no toque nada no empieza a gastar el triple."""
        self.assertEqual(POR_DEFECTO, ("timeline",))
        self.assertEqual(corrientes_validas(None), ("timeline",))
        self.assertEqual(corrientes_validas([]), ("timeline",))

    def test_las_que_hay(self):
        self.assertEqual(set(CORRIENTES),
                         {"timeline", "menciones", "propias", "marcadores"})

    def test_se_ordenan_por_precedencia(self):
        """De menos a más concreta, que es como se pisan luego."""
        self.assertEqual(corrientes_validas(["menciones", "timeline"]),
                         ("timeline", "menciones"))

    def test_en_una_cadena_tambien(self):
        self.assertEqual(corrientes_validas("propias, menciones"),
                         ("propias", "menciones"))

    def test_lo_que_no_existe_se_ignora(self):
        self.assertEqual(corrientes_validas(["me_gusta", "propias"]),
                         ("propias",))

    def test_si_no_queda_nada_valido_el_timeline(self):
        self.assertEqual(corrientes_validas(["me_gusta"]), ("timeline",))

    def test_cada_corriente_marca_el_tipo(self):
        for corriente, esperado in (("timeline", "publicación"),
                                    ("menciones", "mención"),
                                    ("propias", "publicación propia"),
                                    ("marcadores", "marcador")):
            with self.subTest(corriente=corriente):
                item = parse_post(tweet(), AUTORES, corriente)
                self.assertEqual(item.kind, esperado)
                self.assertEqual(item.meta["corriente"], corriente)

    def test_una_corriente_rara_no_revienta(self):
        self.assertEqual(parse_post(tweet(), AUTORES, "me_gusta").kind,
                         "publicación")


class TestVariasCorrientes(CasoConApiFalsa):
    def rutas(self) -> list[str]:
        return [p["ruta"] for p in FakeX.peticiones]

    def test_pide_una_por_corriente(self):
        self.fuente(user_id="7",
                    lee=["timeline", "menciones", "propias"]).sync(self.store)
        rutas = self.rutas()
        self.assertEqual(len(rutas), 3)
        for final in ("timelines/reverse_chronological", "mentions", "tweets"):
            self.assertTrue(any(r.endswith(f"/users/7/{final}") for r in rutas),
                            rutas)

    def test_lo_propio_deja_fuera_respuestas_y_reenvios(self):
        """«Lo que he publicado» no son las respuestas ni lo que reenvío."""
        self.fuente(user_id="7", lee=["propias"]).sync(self.store)
        peticion, = FakeX.peticiones
        self.assertEqual(peticion["params"]["exclude"], "retweets,replies")

    def test_el_timeline_no_lleva_exclude(self):
        self.fuente(user_id="7").sync(self.store)
        self.assertNotIn("exclude", FakeX.peticiones[0]["params"])

    def test_el_id_se_resuelve_una_vez_para_las_tres(self):
        """Resolverlo por corriente serían dos lecturas pagadas de más."""
        self.fuente(lee=["timeline", "menciones", "propias"]).sync(self.store)
        self.assertEqual(sum(r.endswith("/users/me") for r in self.rutas()), 1)

    def test_lo_mismo_en_dos_corrientes_se_indexa_una_vez(self):
        FakeX.por_ruta = {"mentions": respuesta(tweet()),
                          "timelines/reverse_chronological": respuesta(tweet())}
        self.assertEqual(
            self.fuente(user_id="7", lee=["timeline", "menciones"]).sync(self.store),
            1)
        self.assertEqual(self.store.counts(), {"x": 1})

    def test_y_manda_la_corriente_mas_concreta(self):
        """Si además de pasar por tu timeline te nombra, es una mención."""
        FakeX.por_ruta = {"mentions": respuesta(tweet()),
                          "timelines/reverse_chronological": respuesta(tweet())}
        self.fuente(user_id="7", lee=["menciones", "timeline"]).sync(self.store)
        self.assertEqual(self.store.recent(source="x")[0]["kind"], "mención")

    def test_una_corriente_caida_no_se_lleva_a_las_otras(self):
        FakeX.rotas = ("mentions",)
        FakeX.por_ruta = {"timelines/reverse_chronological":
                          respuesta(tweet(id="2"))}
        leidas = self.fuente(user_id="7",
                             lee=["timeline", "menciones"]).sync(self.store)
        self.assertEqual(leidas, 1)
        self.assertEqual(self.store.counts(), {"x": 1})


class TestFiltroPorTipo(unittest.TestCase):
    """Lo que hace posible preguntar «¿me han mencionado?» y no otra cosa."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "index.db")
        self.store.upsert([
            parse_post(tweet(id="1"), AUTORES, "menciones"),
            parse_post(tweet(id="2", text="otra"), AUTORES, "timeline"),
        ])

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_solo_las_menciones(self):
        filas = self.store.recent(source="x", kind="mención")
        self.assertEqual([f["id"] for f in filas], ["x:1"])

    def test_sin_filtro_salen_las_dos(self):
        self.assertEqual(len(self.store.recent(source="x")), 2)

    def test_un_tipo_que_no_hay(self):
        self.assertEqual(self.store.recent(source="x", kind="marcador"), [])


class TestPKCE(unittest.TestCase):
    """La parte de OAuth 2.0 que es pura cuenta: se comprueba sin red."""

    def test_el_reto_es_el_sha256_del_verificador(self):
        verifier, challenge = verificador()
        esperado = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode("ascii")).digest()).decode().rstrip("=")
        self.assertEqual(challenge, esperado)

    def test_sin_relleno_y_seguro_para_urls(self):
        verifier, challenge = verificador()
        for trozo in (verifier, challenge):
            self.assertNotIn("=", trozo)
            self.assertNotIn("+", trozo)
            self.assertNotIn("/", trozo)

    def test_cada_vez_uno_distinto(self):
        self.assertNotEqual(verificador()[0], verificador()[0])

    def test_el_verificador_tiene_longitud_de_sobra(self):
        """RFC 7636: entre 43 y 128 caracteres."""
        verifier, _ = verificador()
        self.assertTrue(43 <= len(verifier) <= 128, len(verifier))

    def test_la_url_lleva_lo_que_x_pide(self):
        url = url_de_autorizacion("mi-id", "http://127.0.0.1:8723/x", "reto",
                                  "estado")
        params = parse_qs(urlsplit(url).query)
        self.assertEqual(params["response_type"], ["code"])
        self.assertEqual(params["code_challenge_method"], ["S256"])
        self.assertEqual(params["code_challenge"], ["reto"])
        self.assertEqual(params["client_id"], ["mi-id"])
        self.assertEqual(params["state"], ["estado"])

    def test_pide_permiso_para_marcadores_y_para_refrescar(self):
        """Sin `offline.access` no hay refresh token, y a las dos horas fuera."""
        self.assertIn("bookmark.read", PERMISOS)
        self.assertIn("offline.access", PERMISOS)
        params = parse_qs(urlsplit(url_de_autorizacion(
            "id", "uri", "reto", "estado")).query)
        self.assertEqual(params["scope"][0].split(), list(PERMISOS))

    def test_los_cuerpos_del_canje(self):
        canje = cuerpo_de_canje("codigo", "id", "uri", "verificador")
        self.assertEqual(canje["grant_type"], "authorization_code")
        self.assertEqual(canje["code_verifier"], "verificador")

        refresco = cuerpo_de_refresco("refresco", "id")
        self.assertEqual(refresco["grant_type"], "refresh_token")
        self.assertNotIn("code_verifier", refresco)


class TestGuardaTokens(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fichero = Path(self.tmp.name) / "x_oauth2.json"
        self.tokens = Tokens(self.fichero)

    def tearDown(self):
        self.tmp.cleanup()

    def test_ida_y_vuelta(self):
        self.tokens.guarda("refresco-1")
        self.assertEqual(Tokens(self.fichero).lee(), "refresco-1")

    def test_sin_fichero_no_hay_nada(self):
        self.assertEqual(self.tokens.lee(), "")

    def test_un_fichero_roto_no_revienta(self):
        self.fichero.write_text("{ no es json", encoding="utf-8")
        self.assertEqual(self.tokens.lee(), "")

    def test_solo_lo_lee_su_dueño(self):
        """Es una credencial: no tiene por qué verla el resto del sistema."""
        self.tokens.guarda("refresco-1")
        self.assertEqual(self.fichero.stat().st_mode & 0o077, 0)

    def test_no_guarda_el_de_acceso(self):
        """Dura dos horas: tenerlo en disco solo sería una credencial más."""
        self.tokens.guarda("refresco-1")
        self.assertNotIn("access", self.fichero.read_text(encoding="utf-8"))


class TestMarcadores(CasoConApiFalsa):
    def setUp(self):
        super().setUp()
        self.fichero = Path(self.tmp.name) / "x_oauth2.json"
        Tokens(self.fichero).guarda("refresco-1")
        self.patch_token = mock.patch.object(
            oauth2_module, "TOKEN",
            f"http://127.0.0.1:{self.server.server_address[1]}/2/oauth2/token")
        self.patch_token.start()

    def tearDown(self):
        self.patch_token.stop()
        super().tearDown()

    def fuente(self, **kwargs):
        kwargs.setdefault("tokens", self.fichero)
        with mock.patch.dict(os.environ,
                             {**CREDENCIALES, "JARVIS_X_CLIENT_ID": "mi-id"}):
            return XSource(**kwargs)

    def test_los_marcadores_van_por_oauth2(self):
        self.assertEqual(CON_OAUTH2, ("marcadores",))
        self.fuente(user_id="7", lee=["marcadores"]).sync(self.store)

        peticion = next(p for p in FakeX.peticiones
                        if p["ruta"].endswith("/bookmarks"))
        self.assertEqual(peticion["auth"], "Bearer acceso-1")

    def test_el_resto_sigue_firmado_con_oauth1(self):
        """Dos autenticaciones a la vez, cada una donde le toca."""
        self.fuente(user_id="7", lee=["timeline", "marcadores"]).sync(self.store)
        por_ruta = {p["ruta"].rsplit("/", 1)[-1]: p["auth"]
                    for p in FakeX.peticiones}
        self.assertTrue(por_ruta["reverse_chronological"].startswith("OAuth "))
        self.assertEqual(por_ruta["bookmarks"], "Bearer acceso-1")

    def test_el_refresh_token_nuevo_se_guarda(self):
        """X rota: el que acaba de llegar mata al anterior."""
        self.fuente(user_id="7", lee=["marcadores"]).sync(self.store)
        self.assertEqual(Tokens(self.fichero).lee(), "refresco-2")

    def test_y_se_guarda_antes_de_usarlo(self):
        """Si se pierde, hay que volver al navegador; la lectura no."""
        FakeX.rotas = ("bookmarks",)
        self.fuente(user_id="7", lee=["marcadores"]).sync(self.store)
        self.assertEqual(Tokens(self.fichero).lee(), "refresco-2",
                         "la lectura falló, pero el token está a salvo")

    def test_el_canje_manda_lo_que_x_pide(self):
        self.fuente(user_id="7", lee=["marcadores"]).sync(self.store)
        canje, = FakeX.canjes
        self.assertEqual(canje["grant_type"], "refresh_token")
        self.assertEqual(canje["refresh_token"], "refresco-1")
        self.assertEqual(canje["client_id"], "mi-id")

    def test_el_token_se_reutiliza_mientras_dure(self):
        """Dos horas dura: renovarlo en cada vuelta es una llamada de más."""
        fuente = self.fuente(user_id="7", lee=["marcadores"])
        fuente.sync(self.store)
        fuente.sync(self.store)
        self.assertEqual(len(FakeX.canjes), 1)

    def test_si_el_canje_falla_no_se_lee_ni_se_pierde_el_token(self):
        FakeX.token_status = 400
        FakeX.tokens = {"error": "invalid_grant"}
        with self.assertLogs("jarvis.sources", level="ERROR"):
            self.fuente(user_id="7", lee=["marcadores"]).sync(self.store)

        self.assertFalse(any(p["ruta"].endswith("/bookmarks")
                             for p in FakeX.peticiones))
        self.assertEqual(Tokens(self.fichero).lee(), "refresco-1")

    def test_sin_autorizar_se_salta_y_las_otras_siguen(self):
        """Quien no haya pasado por el navegador no se queda sin timeline."""
        vacio = Path(self.tmp.name) / "no-hay.json"
        with mock.patch.dict(os.environ, {**CREDENCIALES,
                                          "JARVIS_X_CLIENT_ID": "mi-id"}):
            fuente = XSource(user_id="7", lee=["timeline", "marcadores"],
                             tokens=vacio)
        self.assertEqual(fuente.sync(self.store), 1)
        self.assertFalse(any(p["ruta"].endswith("/bookmarks")
                             for p in FakeX.peticiones))

    def test_sin_client_id_tampoco(self):
        with mock.patch.dict(os.environ, {**CREDENCIALES,
                                          "JARVIS_X_CLIENT_ID": ""}):
            fuente = XSource(user_id="7", lee=["marcadores"],
                             tokens=self.fichero)
        self.assertFalse(fuente.oauth2_listo)
        self.assertEqual(fuente.sync(self.store), 0)
        self.assertEqual(FakeX.canjes, [])

    def test_un_marcador_se_marca_como_tal(self):
        self.fuente(user_id="7", lee=["marcadores"]).sync(self.store)
        self.assertEqual(self.store.recent(source="x")[0]["kind"], "marcador")


class TestHerramientaDeMenciones(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "index.db")
        cfg = Config({
            "tools": {"notes_file": str(Path(self.tmp.name) / "n.json"),
                      "memory_file": str(Path(self.tmp.name) / "m.json"),
                      "lists_file": str(Path(self.tmp.name) / "l.json")},
            "llm": {"web_search": False},
        })
        with mock.patch.dict(os.environ, {"JARVIS_HASS_TOKEN": ""}):
            self.caja = Toolbox(cfg, EventBus(), store=self.store)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    async def test_sin_menciones_dice_donde_se_activan(self):
        salida = await self.caja.run("menciones_recientes", {})
        self.assertIn("sources.x.lee", salida)

    async def test_solo_las_menciones(self):
        self.store.upsert([
            parse_post(tweet(id="1", text="te nombro"), AUTORES, "menciones"),
            parse_post(tweet(id="2", text="esto es del timeline"), AUTORES,
                       "timeline"),
        ])
        salida = await self.caja.run("menciones_recientes", {})
        self.assertIn("te nombro", salida)
        self.assertNotIn("esto es del timeline", salida)

    async def test_vienen_valladas_como_todo_lo_de_fuera(self):
        """Una mención es texto que escribe cualquiera: datos, no órdenes."""
        self.store.upsert([parse_post(tweet(id="1"), AUTORES, "menciones")])
        salida = await self.caja.run("menciones_recientes", {})
        self.assertIn("DATOS EXTERNOS", salida)
        self.assertTrue(self.caja.external_content_seen)

    async def test_sin_indice_no_se_ofrece(self):
        cfg = Config({"tools": {}, "llm": {"web_search": False}})
        with mock.patch.dict(os.environ, {"JARVIS_HASS_TOKEN": ""}):
            caja = Toolbox(cfg, EventBus())
        self.assertNotIn("menciones_recientes",
                         [e["name"] for e in caja.definitions()])


if __name__ == "__main__":
    unittest.main()
