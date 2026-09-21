"""ElevenLabs: la voz más natural (de pago, requiere conexión)."""
from __future__ import annotations

import logging
import os

from . import Speech
from ..http import AsyncClient

log = logging.getLogger("jarvis.tts.elevenlabs")

API = "https://api.elevenlabs.io/v1/text-to-speech"


class ElevenLabsTTS:
    name = "elevenlabs"

    def __init__(self, voice_id: str | None = None,
                 model: str = "eleven_flash_v2_5"):
        self.api_key = os.getenv("ELEVENLABS_API_KEY")
        if not self.api_key:
            raise RuntimeError("falta ELEVENLABS_API_KEY")
        self.voice_id = voice_id or "21m00Tcm4TlvDq8ikWAM"
        self.model = model

    async def synthesize(self, text: str) -> Speech:
        if not text.strip():
            return Speech(b"")
        payload = {"text": text, "model_id": self.model,
                   "voice_settings": {"stability": 0.4, "similarity_boost": 0.8}}
        headers = {"xi-api-key": self.api_key}
        async with AsyncClient(timeout=60.0) as client:
            # PCM evita depender de un decodificador de mp3 en el equipo.
            response = await client.post(
                f"{API}/{self.voice_id}",
                headers=headers, json=payload,
                params={"output_format": "pcm_22050"},
            )
            if response.status_code == 200:
                return Speech(response.content, 22050, "audio/pcm")

            log.info("PCM no disponible en este plan; uso mp3 (lo decodifica el navegador)")
            response = await client.post(
                f"{API}/{self.voice_id}",
                headers=headers, json=payload,
                params={"output_format": "mp3_44100_128"},
            )
        if response.status_code >= 400:
            log.error("error de ElevenLabs %s: %s", response.status_code, response.text[:200])
            return Speech(b"")
        return Speech(response.content, 44100, "audio/mpeg")
