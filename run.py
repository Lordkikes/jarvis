#!/usr/bin/env python3
"""Punto de entrada: python run.py"""
from __future__ import annotations

import argparse
import logging
import threading
import webbrowser

import uvicorn

from jarvis.config import load_config
from jarvis.server.app import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description="Jarvis, asistente de voz local")
    parser.add_argument("--config", help="ruta a config.yaml")
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

    cfg = load_config(args.config)
    host = args.host or cfg.get("server.host", "127.0.0.1")
    port = args.port or int(cfg.get("server.port", 8765))
    url = f"http://{host}:{port}"

    if not args.no_browser and cfg.get("server.open_browser", True):
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()

    print(f"\n  Jarvis escuchando en {url}\n")
    uvicorn.run(create_app(args.config), host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
