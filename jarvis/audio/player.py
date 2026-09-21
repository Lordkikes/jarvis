"""Reproducción de audio PCM con interrupción (barge-in)."""
from __future__ import annotations

import asyncio
import io
import logging
import threading
import wave
from typing import Any

from .capture import _rms

log = logging.getLogger("jarvis.audio.player")


def pcm_to_wav(pcm: bytes, sample_rate: int, channels: int = 1) -> bytes:
    """Empaqueta PCM int16 en un WAV (para enviarlo al navegador)."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)
    return buffer.getvalue()


class AudioPlayer:
    """Reproduce por los altavoces del equipo; si no hay salida, avisa al llamador."""

    def __init__(self, device: Any = None):
        self.device = device
        self._stop = threading.Event()
        self.available = True
        # Nivel del trozo que suena ahora mismo: lo usa el detector de barge-in
        # para saber cuánto eco puede estar entrando por el micrófono.
        self.level = 0.0

    def stop(self) -> None:
        """Corta la reproducción en curso (el usuario volvió a hablar)."""
        self._stop.set()
        self.level = 0.0

    async def play(self, pcm: bytes, sample_rate: int) -> bool:
        """Devuelve True si sonó localmente, False si no hay salida de audio."""
        if not pcm or not self.available:
            return False
        self._stop.clear()
        try:
            return await asyncio.to_thread(self._play_blocking, pcm, sample_rate)
        finally:
            self.level = 0.0

    def _play_blocking(self, pcm: bytes, sample_rate: int) -> bool:
        try:
            import sounddevice as sd
        except Exception as exc:  # noqa: BLE001
            log.info("sin salida de audio local (%s); se reproducirá en el navegador", exc)
            self.available = False
            return False

        chunk = sample_rate // 10 * 2  # bloques de 100 ms
        try:
            with sd.RawOutputStream(
                samplerate=sample_rate, channels=1, dtype="int16", device=self.device
            ) as stream:
                for offset in range(0, len(pcm), chunk):
                    if self._stop.is_set():
                        log.info("reproducción interrumpida")
                        break
                    block = pcm[offset:offset + chunk]
                    self.level = _rms(block)
                    stream.write(block)
        except Exception as exc:  # noqa: BLE001
            log.warning("fallo al reproducir (%s); se usará el navegador", exc)
            self.available = False
            return False
        finally:
            self.level = 0.0
        return True
