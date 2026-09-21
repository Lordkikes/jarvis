"""Voz del sistema operativo: sin dependencias, calidad robótica.

Útil como red de seguridad cuando Piper o ElevenLabs no están disponibles.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

from . import Speech

log = logging.getLogger("jarvis.tts.system")


class SystemTTS:
    name = "system"

    def __init__(self, language: str = "es"):
        self.language = language
        if sys.platform == "darwin":
            self.engine = "say"
        elif shutil.which("espeak-ng") or shutil.which("espeak"):
            self.engine = shutil.which("espeak-ng") or "espeak"
        else:
            raise RuntimeError("no hay 'say' ni 'espeak-ng' en el sistema")

    async def synthesize(self, text: str) -> Speech:
        if not text.strip():
            return Speech(b"")
        return await asyncio.to_thread(self._synthesize_blocking, text)

    def _synthesize_blocking(self, text: str) -> Speech:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.wav"
            if self.engine == "say":
                cmd = ["say", "-o", str(path), "--data-format=LEI16@22050", text]
            else:
                cmd = [self.engine, "-v", self.language, "-w", str(path), text]
            subprocess.run(cmd, check=True, capture_output=True)  # noqa: S603
            with wave.open(str(path), "rb") as wav:
                return Speech(wav.readframes(wav.getnframes()),
                              wav.getframerate(), "audio/pcm")
