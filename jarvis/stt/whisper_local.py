"""Transcripción local con faster-whisper (sin conexión, sin coste)."""
from __future__ import annotations

import asyncio
import logging

log = logging.getLogger("jarvis.stt.local")


class FasterWhisperSTT:
    name = "faster-whisper"

    def __init__(self, model: str = "small", device: str = "cpu",
                 compute_type: str = "int8", language: str = "es"):
        import numpy as np
        from faster_whisper import WhisperModel

        self._np = np
        self.language = language
        log.info("cargando modelo Whisper '%s' (%s/%s)...", model, device, compute_type)
        self.model = WhisperModel(model, device=device, compute_type=compute_type)
        log.info("modelo Whisper listo")

    async def transcribe(self, pcm: bytes, sample_rate: int) -> str:  # noqa: ARG002
        return await asyncio.to_thread(self._transcribe_blocking, pcm)

    def _transcribe_blocking(self, pcm: bytes) -> str:
        audio = self._np.frombuffer(pcm, dtype=self._np.int16)
        audio = audio.astype(self._np.float32) / 32768.0
        segments, _ = self.model.transcribe(
            audio,
            language=self.language,
            vad_filter=True,
            beam_size=1,           # prioriza latencia sobre exactitud
            condition_on_previous_text=False,
        )
        return " ".join(segment.text.strip() for segment in segments).strip()
