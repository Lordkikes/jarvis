"""Reconocimiento de voz (speech-to-text)."""
from __future__ import annotations

import logging

log = logging.getLogger("jarvis.stt")


class NullSTT:
    name = "none"

    async def transcribe(self, pcm: bytes, sample_rate: int) -> str:  # noqa: ARG002
        return ""


def make_stt(cfg):
    """Crea el motor de transcripción configurado, con degradación elegante."""
    provider = (cfg.get("stt.provider") or "none").lower()
    try:
        if provider in ("faster-whisper", "whisper", "local"):
            from .whisper_local import FasterWhisperSTT

            return FasterWhisperSTT(
                model=cfg.get("stt.model", "small"),
                device=cfg.get("stt.device", "cpu"),
                compute_type=cfg.get("stt.compute_type", "int8"),
                language=cfg.get("stt.language", "es"),
            )
        if provider in ("groq", "openai"):
            from .cloud import CloudSTT

            return CloudSTT(provider=provider, language=cfg.get("stt.language", "es"),
                            model=cfg.get("stt.model"))
    except Exception as exc:  # noqa: BLE001
        log.warning("STT '%s' no disponible (%s); modo texto", provider, exc)
    return NullSTT()
