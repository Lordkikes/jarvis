"""Síntesis de voz (text-to-speech)."""
from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger("jarvis.tts")


@dataclass
class Speech:
    """Audio sintetizado. mime 'audio/pcm' = PCM int16 mono crudo."""

    data: bytes
    sample_rate: int = 22050
    mime: str = "audio/pcm"

    def __bool__(self) -> bool:
        return bool(self.data)


class NullTTS:
    name = "none"

    async def synthesize(self, text: str) -> Speech:  # noqa: ARG002
        return Speech(b"")


def make_tts(cfg):
    """Crea el motor de voz configurado, con degradación elegante."""
    provider = (cfg.get("tts.provider") or "none").lower()
    try:
        if provider == "piper":
            from .piper_tts import PiperTTS

            return PiperTTS(voice=cfg.get("tts.voice", "es_ES-davefx-medium"),
                            speed=float(cfg.get("tts.speed", 1.0)))
        if provider == "elevenlabs":
            from .elevenlabs_tts import ElevenLabsTTS

            return ElevenLabsTTS(voice_id=cfg.get("tts.elevenlabs_voice_id"))
        if provider == "system":
            from .system_tts import SystemTTS

            return SystemTTS(language=cfg.get("assistant.language", "es"))
    except Exception as exc:  # noqa: BLE001
        log.warning("TTS '%s' no disponible (%s); Jarvis responderá solo por texto",
                    provider, exc)
    return NullTTS()
