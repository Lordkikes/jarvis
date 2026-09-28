"""OAuth 2.0 con PKCE, que es lo único que abre los marcadores de X.

El resto de X se lee firmando con OAuth 1.0a: cuatro credenciales que se
generan una vez en el portal y no caducan. Los marcadores no admiten eso —
piden un token de usuario con el permiso `bookmark.read`—, así que hay que
pasar por el aro de OAuth 2.0: un paseo por el navegador la primera vez y un
`refresh token` que hay que guardar.

Lo que de verdad importa aquí no es la firma, que es simple, sino **la
rotación**: cada vez que se canjea el refresh token, X devuelve uno nuevo y
mata el anterior. Si se pierde el nuevo, hay que volver al navegador. Por eso
se guarda **antes** de usar el token de acceso que vino con él, igual que los
recordatorios se marcan antes de decirse: más vale una vuelta en balde que
quedarse fuera.

El paseo inicial no lo hace Jarvis, lo hace `scripts/x_autoriza.py`: un
asistente que no arranca no debería abrirte un navegador.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import secrets
import time
from pathlib import Path
from urllib.parse import urlencode

log = logging.getLogger("jarvis.sources.oauth2")

AUTORIZA = "https://x.com/i/oauth2/authorize"
TOKEN = "https://api.x.com/2/oauth2/token"
# `offline.access` es lo que hace que venga un refresh token; sin él, a las
# dos horas habría que volver al navegador.
PERMISOS = ("tweet.read", "users.read", "bookmark.read", "offline.access")
# Se renueva un poco antes de que caduque: si expira a mitad de la petición,
# la lectura se pierde y encima se cobra.
MARGEN = 120


def verificador() -> tuple[str, str]:
    """El par de PKCE: lo que se guarda y lo que se enseña (S256)."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).decode().rstrip("=")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return verifier, challenge


def url_de_autorizacion(client_id: str, redirect_uri: str, challenge: str,
                        state: str, permisos=PERMISOS) -> str:
    return AUTORIZA + "?" + urlencode({
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": " ".join(permisos),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })


def cuerpo_de_canje(code: str, client_id: str, redirect_uri: str,
                    verifier: str) -> dict:
    return {"code": code, "grant_type": "authorization_code",
            "client_id": client_id, "redirect_uri": redirect_uri,
            "code_verifier": verifier}


def cuerpo_de_refresco(refresh_token: str, client_id: str) -> dict:
    return {"refresh_token": refresh_token, "grant_type": "refresh_token",
            "client_id": client_id}


def canjea(client, cuerpo: dict) -> dict:
    """Pide tokens al endpoint de X. Devuelve {} si algo sale mal.

    El cliente HTTP lo pone quien llama: así esto se prueba contra un servidor
    de mentira sin tener que parchear nada por dentro.
    """
    try:
        respuesta = client.post(
            TOKEN, data=cuerpo,
            headers={"Content-Type": "application/x-www-form-urlencoded"})
    except Exception as exc:  # noqa: BLE001 - la red de casa también falla
        log.error("no se ha podido hablar con el endpoint de tokens: %s", exc)
        return {}

    if respuesta.status_code >= 400:
        # El cuerpo lleva el motivo, pero también puede llevar el token: no
        # se registra entero, solo el código y el `error` si viene.
        motivo = ""
        try:
            motivo = str((respuesta.json() or {}).get("error", ""))
        except Exception:  # noqa: BLE001
            pass
        log.error("X rechazó el canje de tokens (%s) %s",
                  respuesta.status_code, motivo)
        return {}

    datos = respuesta.json() or {}
    return datos if datos.get("access_token") else {}


class Tokens:
    """El refresh token, en un fichero que solo puede leer su dueño.

    El de acceso no se guarda: dura dos horas y se pide cuando hace falta, así
    que tenerlo en disco solo añadiría una credencial más que perder.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def lee(self) -> str:
        try:
            datos = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return ""
        return str(datos.get("refresh_token", "")) if isinstance(datos, dict) else ""

    def guarda(self, refresh_token: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"refresh_token": refresh_token,
                        "guardado": int(time.time())}, indent=2),
            encoding="utf-8")
        try:
            os.chmod(self.path, 0o600)
        except OSError:  # pragma: no cover - sistemas sin permisos POSIX
            log.debug("no se han podido ajustar los permisos de %s", self.path)
