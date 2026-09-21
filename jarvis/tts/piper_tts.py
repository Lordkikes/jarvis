"""Piper: voces neuronales locales, rápidas y gratuitas."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from . import Speech
from ..config import data_path

log = logging.getLogger("jarvis.tts.piper")

HF_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main"


def _voice_url(voice: str) -> str:
    """'es_ES-davefx-medium' -> .../es/es_ES/davefx/medium/es_ES-davefx-medium.onnx"""
    locale, speaker, quality = voice.split("-", 2)
    language = locale.split("_")[0]
    return f"{HF_BASE}/{language}/{locale}/{speaker}/{quality}/{voice}.onnx"


def ensure_voice(voice: str) -> Path:
    """Descarga el modelo de voz la primera vez que se usa."""
    import urllib.request

    model = data_path(f"models/piper/{voice}.onnx")
    config = Path(str(model) + ".json")
    if model.exists() and config.exists():
        return model

    url = _voice_url(voice)
    log.info("descargando voz Piper '%s' (una sola vez)...", voice)
    urllib.request.urlretrieve(url, model)              # noqa: S310 - URL fija de HF
    urllib.request.urlretrieve(url + ".json", config)   # noqa: S310
    log.info("voz descargada en %s", model)
    return model


class PiperTTS:
    name = "piper"

    def __init__(self, voice: str = "es_ES-davefx-medium", speed: float = 1.0):
        from piper.voice import PiperVoice

        model = ensure_voice(voice)
        self.voice = PiperVoice.load(str(model))
        self.sample_rate = getattr(self.voice.config, "sample_rate", 22050)
        # Piper interpreta length_scale al revés que "velocidad".
        self.length_scale = 1.0 / max(0.5, min(2.0, speed))

    async def synthesize(self, text: str) -> Speech:
        if not text.strip():
            return Speech(b"", self.sample_rate)
        pcm = await asyncio.to_thread(self._synthesize_blocking, text)
        return Speech(pcm, self.sample_rate, "audio/pcm")

    def _synthesize_blocking(self, text: str) -> bytes:
        # La API de Piper cambió entre versiones: soportamos ambas.
        if hasattr(self.voice, "synthesize_stream_raw"):
            chunks = self.voice.synthesize_stream_raw(
                text, length_scale=self.length_scale)
            return b"".join(chunks)
        chunks = []
        for chunk in self.voice.synthesize(text):
            chunks.append(getattr(chunk, "audio_int16_bytes", chunk))
        return b"".join(chunks)
