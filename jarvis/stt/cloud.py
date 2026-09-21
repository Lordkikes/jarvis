"""Transcripción por API (Groq o OpenAI): más rápida que local en equipos modestos."""
from __future__ import annotations

import logging
import os

from ..audio.player import pcm_to_wav
from ..http import AsyncClient

log = logging.getLogger("jarvis.stt.cloud")

ENDPOINTS = {
    "groq": ("https://api.groq.com/openai/v1/audio/transcriptions",
             "GROQ_API_KEY", "whisper-large-v3-turbo"),
    "openai": ("https://api.openai.com/v1/audio/transcriptions",
               "OPENAI_API_KEY", "gpt-4o-mini-transcribe"),
}


class CloudSTT:
    def __init__(self, provider: str = "groq", language: str = "es",
                 model: str | None = None):
        url, env_var, default_model = ENDPOINTS[provider]
        api_key = os.getenv(env_var)
        if not api_key:
            raise RuntimeError(f"falta {env_var}")
        self.name = provider
        self.url = url
        self.api_key = api_key
        self.language = language
        # El nombre de modelo local (small, medium...) no aplica a la nube.
        self.model = model if model and "-" in str(model) else default_model

    async def transcribe(self, pcm: bytes, sample_rate: int) -> str:
        wav = pcm_to_wav(pcm, sample_rate)
        async with AsyncClient(timeout=60.0) as client:
            response = await client.post(
                self.url,
                headers={"Authorization": f"Bearer {self.api_key}"},
                files={"file": ("audio.wav", wav, "audio/wav")},
                data={"model": self.model, "language": self.language,
                      "response_format": "json"},
            )
        if response.status_code >= 400:
            log.error("error de transcripción %s: %s", response.status_code, response.text[:200])
            return ""
        return (response.json().get("text") or "").strip()
