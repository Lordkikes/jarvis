#!/usr/bin/env python3
"""Saca el permiso de X para leer tus marcadores. Se hace una vez.

Los marcadores son lo único de X que no admite OAuth 1.0a: piden un token de
usuario con el permiso `bookmark.read`, y eso obliga a pasar por el navegador
una vez. De ahí sale un `refresh token` que Jarvis guarda y renueva solo.

    python scripts/x_autoriza.py

Hace falta, en el portal de desarrolladores de X, tener la app con OAuth 2.0
activado, de tipo **cliente público** (native/SPA), y con esta URL entre las
de redirección:

    http://127.0.0.1:8723/x

El identificador de cliente va en `.env` como `JARVIS_X_CLIENT_ID`. No es el
mismo que las cuatro credenciales de OAuth 1.0a: aquellas siguen haciendo
falta para el resto de las corrientes.
"""
from __future__ import annotations

import http.server
import os
import secrets
import sys
import webbrowser
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis.config import data_path, load_config  # noqa: E402
from jarvis.http import Client  # noqa: E402
from jarvis.sources.oauth2 import (  # noqa: E402
    PERMISOS, Tokens, canjea, cuerpo_de_canje, url_de_autorizacion,
    verificador,
)

PUERTO = 8723
REDIRECCION = f"http://127.0.0.1:{PUERTO}/x"


class Recoge(http.server.BaseHTTPRequestHandler):
    """Se queda con el código que X devuelve y cierra el chiringuito."""

    codigo = ""
    estado = ""
    error = ""

    def log_message(self, *args):  # silencio: esto lo lee una persona
        pass

    def do_GET(self):  # noqa: N802
        query = parse_qs(urlsplit(self.path).query)
        Recoge.codigo = (query.get("code") or [""])[0]
        Recoge.estado = (query.get("state") or [""])[0]
        Recoge.error = (query.get("error") or [""])[0]

        if Recoge.error:
            cuerpo = f"X ha dicho que no: {Recoge.error}. Puedes cerrar esto."
        elif Recoge.codigo:
            cuerpo = "Listo. Ya puedes cerrar esta pestaña y volver a la consola."
        else:
            cuerpo = "No ha venido ningún código. Puedes cerrar esto."

        datos = f"<html><body><p>{cuerpo}</p></body></html>".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(datos)))
        self.end_headers()
        self.wfile.write(datos)


def main() -> int:
    client_id = os.getenv("JARVIS_X_CLIENT_ID", "")
    if not client_id:
        print("Falta JARVIS_X_CLIENT_ID en .env (el de OAuth 2.0 de tu app).")
        return 1

    cfg = load_config()
    destino = data_path(cfg.get("sources.x.tokens_file", "data/x_oauth2.json"))

    verifier, challenge = verificador()
    estado = secrets.token_urlsafe(16)
    url = url_de_autorizacion(client_id, REDIRECCION, challenge, estado)

    servidor = http.server.HTTPServer(("127.0.0.1", PUERTO), Recoge)
    print("Abriendo el navegador para que le des permiso a tu propia app.")
    print(f"Si no se abre solo, entra aquí:\n\n  {url}\n")
    webbrowser.open(url)

    # Una petición cada vuelta, hasta que llegue la buena: el navegador suele
    # pedir el favicon por el camino y eso no es la respuesta.
    while not (Recoge.codigo or Recoge.error):
        servidor.handle_request()
    servidor.server_close()

    if Recoge.error:
        print(f"X ha dicho que no: {Recoge.error}")
        return 1
    if Recoge.estado != estado:
        # Si el estado no vuelve igual, la respuesta no es la nuestra.
        print("El estado no coincide; no canjeo nada.")
        return 1

    with Client(timeout=30.0) as client:
        datos = canjea(client, cuerpo_de_canje(Recoge.codigo, client_id,
                                               REDIRECCION, verifier))
    if not datos.get("refresh_token"):
        print("X no ha devuelto refresh token. ¿Está 'offline.access' entre "
              f"los permisos de la app? Pido estos: {', '.join(PERMISOS)}")
        return 1

    Tokens(destino).guarda(datos["refresh_token"])
    print(f"Guardado en {destino}. Ya puedes poner «marcadores» en "
          "sources.x.lee.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
