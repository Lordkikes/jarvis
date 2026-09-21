#!/usr/bin/env python3
"""Comprueba que config.yaml es coherente con lo que hay en el repositorio.

Se ejecuta en CI y también sirve en local:  python scripts/check_config.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    problems: list[str] = []

    config = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))

    themes = {path.name for path in (ROOT / "jarvis/server/static/themes").iterdir()
              if (path / "index.html").exists()}
    theme = config.get("server", {}).get("theme")
    if theme not in themes:
        problems.append(f"server.theme = {theme!r} no existe; hay {sorted(themes)}")

    # El cancelador de eco trabaja en bloques de 10 ms y webrtcvad solo acepta
    # 10, 20 o 30: un frame_ms distinto desactiva medio sistema en silencio.
    frame_ms = config.get("audio", {}).get("frame_ms")
    if frame_ms not in (10, 20, 30):
        problems.append(f"audio.frame_ms = {frame_ms!r}; debe ser 10, 20 o 30")

    mode = config.get("barge_in", {}).get("mode")
    if mode not in ("voice", "wakeword", "off"):
        problems.append(f"barge_in.mode = {mode!r}; debe ser voice, wakeword u off")

    suppression = config.get("aec", {}).get("suppression")
    if not isinstance(suppression, int) or not 0 <= suppression <= 2:
        problems.append(f"aec.suppression = {suppression!r}; debe ser 0, 1 o 2")

    for problem in problems:
        print(f"config.yaml: {problem}", file=sys.stderr)
    if not problems:
        print(f"config.yaml correcto (tema '{theme}', frames de {frame_ms} ms)")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
