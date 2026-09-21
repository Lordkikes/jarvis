#!/usr/bin/env python3
"""Diagnóstico: comprueba dependencias, claves y dispositivos de audio.

    python scripts/doctor.py
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

OK, WARN, BAD = "\033[92m✓\033[0m", "\033[93m!\033[0m", "\033[91m✗\033[0m"

CORE = ["fastapi", "uvicorn", "yaml", "anthropic"]
VOICE = ["sounddevice", "numpy", "webrtcvad", "openwakeword", "faster_whisper", "piper"]
AEC = "webrtc_audio_processing"
KEYS = [("ANTHROPIC_API_KEY", True), ("ELEVENLABS_API_KEY", False),
        ("GROQ_API_KEY", False), ("PICOVOICE_ACCESS_KEY", False)]


def check_module(name: str) -> bool:
    try:
        importlib.import_module(name)
        return True
    except Exception:  # noqa: BLE001
        return False


def main() -> int:
    try:
        from dotenv import load_dotenv

        load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    except ImportError:
        pass

    problems = 0
    print(f"\nPython {sys.version.split()[0]}  ({sys.executable})\n")

    print("Núcleo")
    for module in CORE:
        ok = check_module(module)
        problems += not ok
        print(f"  {OK if ok else BAD} {module}")

    print("\nVoz (opcional, pero necesario para hablar)")
    for module in VOICE:
        ok = check_module(module)
        print(f"  {OK if ok else WARN} {module}")

    print("\nCancelación de eco (opcional)")
    if check_module(AEC):
        print(f"  {OK} {AEC}")
    else:
        import shutil

        print(f"  {WARN} {AEC} no instalado: se podrá interrumpir a Jarvis, pero "
              "habrá que subir barge_in.echo_gain")
        tool = "swig" if shutil.which("swig") else None
        print(f"  {OK if tool else WARN} swig "
              f"{'disponible' if tool else 'no encontrado (hace falta para compilarlo)'}")
        print("    instálalo con:  pip install -r requirements-aec.txt")

    print("\nClaves de API")
    for key, required in KEYS:
        value = os.getenv(key)
        if value:
            print(f"  {OK} {key} = {value[:8]}…")
        elif required:
            problems += 1
            print(f"  {BAD} {key} no definida (obligatoria)")
        else:
            print(f"  {WARN} {key} no definida (opcional)")

    print("\nDispositivos de audio")
    try:
        import sounddevice as sd

        default_in, default_out = sd.default.device
        for index, device in enumerate(sd.query_devices()):
            marks = []
            if index == default_in and device["max_input_channels"]:
                marks.append("entrada por defecto")
            if index == default_out and device["max_output_channels"]:
                marks.append("salida por defecto")
            flag = OK if marks else " "
            suffix = f"  <- {', '.join(marks)}" if marks else ""
            print(f"  {flag} [{index}] {device['name']}{suffix}")
    except Exception as exc:  # noqa: BLE001
        print(f"  {WARN} no se pueden listar dispositivos ({exc})")

    print("\nConfiguración")
    try:
        from jarvis.config import load_config

        cfg = load_config()
        for path in ("llm.model", "stt.provider", "tts.provider", "wake.provider",
                     "barge_in.mode", "aec.enabled"):
            print(f"  {OK} {path} = {cfg.get(path)}")
    except Exception as exc:  # noqa: BLE001
        problems += 1
        print(f"  {BAD} error leyendo config.yaml: {exc}")

    print("\n" + ("Todo listo: ejecuta  python run.py" if not problems
                  else f"{problems} problema(s) que resolver antes de arrancar"))
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
