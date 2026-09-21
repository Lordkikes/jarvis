"""Detección de fin de frase (VAD)."""
from __future__ import annotations

import logging

from .capture import _rms

log = logging.getLogger("jarvis.audio.vad")


class _EnergyVAD:
    """Respaldo por energía cuando webrtcvad no está instalado."""

    def __init__(self, threshold: float = 0.045):
        self.threshold = threshold

    def is_speech(self, frame: bytes, sample_rate: int) -> bool:  # noqa: ARG002
        return _rms(frame) > self.threshold


def make_vad(aggressiveness: int = 2):
    try:
        import webrtcvad

        return webrtcvad.Vad(aggressiveness)
    except ImportError:
        log.warning("webrtcvad no instalado; uso detección por energía")
        return _EnergyVAD()


class Utterance:
    """Acumula frames hasta que el hablante se calla.

    Uso:
        utt = Utterance(...)
        for frame in frames:
            if utt.push(frame) == "done":
                audio = utt.audio()
    """

    def __init__(
        self,
        sample_rate: int = 16000,
        frame_ms: int = 30,
        silence_ms: int = 900,
        min_speech_ms: int = 300,
        max_seconds: int = 20,
        aggressiveness: int = 2,
    ):
        self.sample_rate = sample_rate
        self.frame_ms = frame_ms
        self.vad = make_vad(aggressiveness)
        self.silence_frames = max(1, silence_ms // frame_ms)
        self.min_speech_frames = max(1, min_speech_ms // frame_ms)
        self.max_frames = max_seconds * 1000 // frame_ms
        self.reset()

    def reset(self) -> None:
        self._frames: list[bytes] = []
        self._speech_frames = 0
        self._trailing_silence = 0
        self.started = False

    def push(self, frame: bytes) -> str:
        """Devuelve 'listening', 'speaking', 'done' o 'timeout'."""
        speech = self.vad.is_speech(frame, self.sample_rate)
        if speech:
            self._speech_frames += 1
            self._trailing_silence = 0
            self.started = self.started or self._speech_frames >= 2
        elif self.started:
            self._trailing_silence += 1

        if self.started:
            self._frames.append(frame)
        elif speech:
            self._frames.append(frame)  # conserva el arranque de la frase

        if len(self._frames) >= self.max_frames:
            return "timeout"
        if self.started and self._trailing_silence >= self.silence_frames:
            return "done" if self._speech_frames >= self.min_speech_frames else "timeout"
        return "speaking" if self.started else "listening"

    def audio(self) -> bytes:
        return b"".join(self._frames)
