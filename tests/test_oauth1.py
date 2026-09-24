"""Firma OAuth 1.0a contrastada con vectores de la implementación de referencia.

Las firmas de `VECTORES` se generaron con oauthlib 3.3.1 y coinciden con las
nuestras. No se importa oauthlib aquí a propósito: la gracia de fijar los
valores es que el test corre en un entorno limpio y detecta si alguien rompe
el codificado o el orden de los parámetros.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis.sources.oauth1 import (  # noqa: E402
    authorization_header, base_string, encode, sign,
)

CK, CS = "clave-app", "secreto&app"
TK, TS = "token-usuario", "secreto/token"
NONCE, SELLO = "abc123nonce", "1789000000"

TIMELINE = {"max_results": "40", "tweet.fields": "created_at,public_metrics",
            "expansions": "author_id", "user.fields": "username,name"}

VECTORES = [
    ("GET", "https://api.x.com/2/users/me", {},
     "kt1eB1XwZ5Oy35BYvwRGMO15Tdo="),
    ("GET", "https://api.x.com/2/users/123/timelines/reverse_chronological",
     TIMELINE, "zao70bMYu54CWk4VeJ7oH3LZAQ8="),
    # Con caracteres que se escapan distinto según la implementación.
    ("GET", "https://api.x.com/2/tweets/search/recent",
     {"query": 'de ñandúes "a/b" c+d e=f', "max_results": "10"},
     "ZJ06qUjz3CjcG6E0BC/v1FC4mfE="),
    ("POST", "https://api.x.com/2/users/9/likes", {},
     "pr1Yuhubi3hyipg0uoyss/FpSYg="),
]


def firma_de(cabecera: str) -> str:
    """Saca oauth_signature de la cabecera y la desescapa."""
    from urllib.parse import unquote

    for trozo in cabecera.removeprefix("OAuth ").split(", "):
        clave, _, valor = trozo.partition("=")
        if clave.strip() == "oauth_signature":
            return unquote(valor.strip('"'))
    raise AssertionError("la cabecera no lleva firma")


class TestCodificado(unittest.TestCase):
    def test_los_reservados_se_escapan(self):
        self.assertEqual(encode("a b&c=d"), "a%20b%26c%3Dd")

    def test_la_virgulilla_no_se_escapa(self):
        """RFC 3986 la deja sin escapar; algunas versiones de quote no."""
        self.assertEqual(encode("~-._"), "~-._")

    def test_utf8_antes_de_escapar(self):
        self.assertEqual(encode("ñ"), "%C3%B1")


class TestCadenaBase(unittest.TestCase):
    def test_query_y_fragmento_fuera_de_la_url(self):
        base = base_string("GET", "https://api.x.com/2/users/me?a=1#x", {"a": "1"})
        self.assertIn(encode("https://api.x.com/2/users/me"), base)
        self.assertNotIn("%23", base, "el fragmento no entra en la firma")

    def test_el_puerto_por_defecto_se_omite(self):
        con = base_string("GET", "https://api.x.com:443/2/users/me", {})
        sin = base_string("GET", "https://api.x.com/2/users/me", {})
        self.assertEqual(con, sin)

    def test_los_parametros_van_ordenados(self):
        base = base_string("GET", "https://ejemplo.com/", {"b": "2", "a": "1"})
        self.assertTrue(base.endswith(encode("a=1&b=2")), base)

    def test_el_metodo_va_en_mayusculas(self):
        self.assertTrue(base_string("post", "https://ejemplo.com/", {}).startswith("POST&"))


class TestVectoresDeReferencia(unittest.TestCase):
    """Cada firma se comprobó contra oauthlib antes de fijarla aquí."""

    def test_coinciden(self):
        for metodo, url, params, esperada in VECTORES:
            with self.subTest(url=url):
                cabecera = authorization_header(
                    metodo, url, CK, CS, TK, TS,
                    params=params, nonce=NONCE, timestamp=SELLO)
                self.assertEqual(firma_de(cabecera), esperada)

    def test_los_parametros_de_query_entran_en_la_firma(self):
        """Olvidarlos es la causa clásica del 401 al añadir un filtro."""
        con = authorization_header("GET", VECTORES[1][1], CK, CS, TK, TS,
                                   params=TIMELINE, nonce=NONCE, timestamp=SELLO)
        sin = authorization_header("GET", VECTORES[1][1], CK, CS, TK, TS,
                                   nonce=NONCE, timestamp=SELLO)
        self.assertNotEqual(firma_de(con), firma_de(sin))

    def test_el_secreto_del_token_cuenta(self):
        una = sign(base_string("GET", "https://ejemplo.com/", {}), CS, TS)
        otra = sign(base_string("GET", "https://ejemplo.com/", {}), CS, "")
        self.assertNotEqual(una, otra)


class TestCabecera(unittest.TestCase):
    def cabecera(self, **kwargs):
        return authorization_header("GET", "https://api.x.com/2/users/me",
                                    CK, CS, TK, TS, nonce=NONCE, timestamp=SELLO,
                                    **kwargs)

    def test_lleva_lo_que_exige_el_protocolo(self):
        cabecera = self.cabecera()
        self.assertTrue(cabecera.startswith("OAuth "))
        for campo in ("oauth_consumer_key", "oauth_nonce", "oauth_signature",
                      "oauth_signature_method", "oauth_timestamp",
                      "oauth_token", "oauth_version"):
            self.assertIn(f'{campo}="', cabecera)
        self.assertIn('oauth_signature_method="HMAC-SHA1"', cabecera)
        self.assertIn('oauth_version="1.0"', cabecera)

    def test_los_parametros_de_query_no_viajan_en_la_cabecera(self):
        cabecera = authorization_header(
            "GET", VECTORES[1][1], CK, CS, TK, TS, params=TIMELINE,
            nonce=NONCE, timestamp=SELLO)
        self.assertNotIn("max_results", cabecera)
        self.assertNotIn("expansions", cabecera)

    def test_los_valores_van_escapados(self):
        cabecera = authorization_header("GET", "https://api.x.com/2/users/me",
                                        "clave con espacio", CS, TK, TS,
                                        nonce=NONCE, timestamp=SELLO)
        self.assertIn('oauth_consumer_key="clave%20con%20espacio"', cabecera)

    def test_sin_nonce_fijo_cada_firma_es_distinta(self):
        una = authorization_header("GET", "https://api.x.com/2/users/me",
                                   CK, CS, TK, TS)
        otra = authorization_header("GET", "https://api.x.com/2/users/me",
                                    CK, CS, TK, TS)
        self.assertNotEqual(firma_de(una), firma_de(otra))


if __name__ == "__main__":
    unittest.main()
