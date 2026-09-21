"""Captura de micrófono en frames PCM 16 kHz mono."""
from __future__ import annotations

import asyncio
import logging
import math
from typing import Any

from . import AudioUnavailable

log = logging.getLogger("jarvis.audio.capture")


def _rms(frame: bytes) -> float:
    """Nivel 0..1 de un frame PCM int16 (para animar la interfaz)."""
    if not frame:
        return 0.0
    total = 0
    count = len(frame) // 2
    for i in range(0, count * 2, 2):
        sample = int.from_bytes(frame[i:i + 2], "little", signed=True)
        total += sample * sample
    return min(1.0, math.sqrt(total / count) / 8000.0)


class AudioCapture:
    """Empuja frames del micrófono a una cola asyncio desde el hilo de audio."""

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        sample_rate: int = 16000,
        frame_ms: int = 30,
        device: Any = None,
        on_level=None,
    ):
        self.loop = loop
        self.sample_rate = sample_rate
        self.frame_size = int(sample_rate * frame_ms / 1000)
        self.device = device
        self.on_level = on_level
        self.queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=100)
        self._stream = None
        self._muted = False

    # -- ciclo de vida -----------------------------------------------------
    def start(self) -> None:
        try:
            import sounddevice as sd
        except Exception as exc:  # noqa: BLE001 - la causa real va en el mensaje
            raise AudioUnavailable(f"sounddevice no disponible: {exc}") from exc

        try:
            self._stream = sd.RawInputStream(
                samplerate=self.sample_rate,
                blocksize=self.frame_size,
                device=self.device,
                dtype="int16",
                channels=1,
                callback=self._callback,
            )
            self._stream.start()
        except Exception as exc:  # noqa: BLE001
            raise AudioUnavailable(f"no se pudo abrir el micrófono: {exc}") from exc
        log.info("micrófono abierto (%d Hz, frames de %d muestras)",
                 self.sample_rate, self.frame_size)

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def set_muted(self, muted: bool) -> None:
        self._muted = muted

    # -- interno -----------------------------------------------------------
    def _callback(self, indata, frames, time_info, status) -> None:  # noqa: ANN001
        if status:
            log.debug("estado del stream: %s", status)
        if self._muted:
            return
        frame = bytes(indata)
        self.loop.call_soon_threadsafe(self._dispatch, frame)

    def _dispatch(self, frame: bytes) -> None:
        if self.on_level is not None:
            self.on_level(_rms(frame))
        try:
            self.queue.put_nowait(frame)
        except asyncio.QueueFull:
            # El consumidor va lento: tiramos el frame más viejo.
            try:
                self.queue.get_nowait()
                self.queue.put_nowait(frame)
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                pass

    async def read(self) -> bytes:
        return await self.queue.get()

    def drain(self) -> None:
        while not self.queue.empty():
            try:
                self.queue.get_nowait()
            except asyncio.QueueEmpty:
                break
