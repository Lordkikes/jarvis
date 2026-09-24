"""Firma OAuth 1.0a (HMAC-SHA1), que es lo que X sigue aceptando.

Se implementa aquí en vez de traer una dependencia porque son cuarenta líneas
de biblioteca estándar y evita arrastrar oauthlib a un proyecto que ya tiene
bastantes piezas opcionales. A cambio, la firma está verificada contra la
implementación de referencia (ver tests/test_oauth1.py).

La alternativa, OAuth 2.0 con PKCE, obliga a un paseo por el navegador y a
guardar un refresh token; con OAuth 1.0a las cuatro credenciales se generan de
una vez en el portal de desarrolladores y no caducan.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from urllib.parse import quote, urlsplit, urlunsplit

# RFC 3986: solo estos caracteres van sin escapar. `quote` deja fuera ~ por
# defecto en algunas versiones, así que se declara explícitamente.
UNRESERVED = "-._~"


def encode(value) -> str:
    return quote(str(value), safe=UNRESERVED)


def base_string(method: str, url: str, params: dict) -> str:
    """La cadena que se firma: método, URL sin query y parámetros ordenados."""
    parts = urlsplit(url)
    # La URL base va sin query ni fragmento, y el puerto por defecto se omite.
    netloc = parts.netloc.lower()
    if (parts.scheme == "https" and netloc.endswith(":443")) or \
       (parts.scheme == "http" and netloc.endswith(":80")):
        netloc = netloc.rsplit(":", 1)[0]
    clean_url = urlunsplit((parts.scheme.lower(), netloc, parts.path, "", ""))

    # Se ordena por clave codificada y, a igualdad, por valor codificado.
    pairs = sorted((encode(key), encode(value)) for key, value in params.items())
    joined = "&".join(f"{key}={value}" for key, value in pairs)
    return f"{method.upper()}&{encode(clean_url)}&{encode(joined)}"


def sign(base: str, consumer_secret: str, token_secret: str = "") -> str:
    """HMAC-SHA1 en base64 con la clave formada por los dos secretos."""
    key = f"{encode(consumer_secret)}&{encode(token_secret)}".encode()
    digest = hmac.new(key, base.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


def authorization_header(
    method: str,
    url: str,
    consumer_key: str,
    consumer_secret: str,
    token: str,
    token_secret: str,
    params: dict | None = None,
    nonce: str | None = None,
    timestamp: str | None = None,
) -> str:
    """Cabecera `Authorization: OAuth ...` lista para la petición.

    `params` son los de la query, que entran en la firma aunque viajen en la
    URL: olvidarlos es la causa clásica del 401 al añadir un filtro.
    """
    oauth = {
        "oauth_consumer_key": consumer_key,
        "oauth_nonce": nonce or secrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": timestamp or str(int(time.time())),
        "oauth_token": token,
        "oauth_version": "1.0",
    }
    signature = sign(base_string(method, url, {**(params or {}), **oauth}),
                     consumer_secret, token_secret)
    oauth["oauth_signature"] = signature

    # En la cabecera van solo los oauth_*, nunca los parámetros de la query.
    inner = ", ".join(f'{encode(key)}="{encode(value)}"'
                      for key, value in sorted(oauth.items()))
    return f"OAuth {inner}"
