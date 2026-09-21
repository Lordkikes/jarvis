"""Detección de interrupción (barge-in): hablar encima de Jarvis para cortarle.

El problema no es detectar voz, es detectar *tu* voz mientras el micrófono
está oyendo a los altavoces. Sin cancelación de eco por hardware, el truco es
comparar el nivel del micrófono con el nivel del audio que se está
reproduciendo en ese instante: tu voz tiene que superar al eco con margen.
"""
from __future__ import annotations

import logging
import time
from collections import deque

from .capture import _rms
from .vad import make_vad

log = logging.getLogger("jarvis.audio.bargein")

OFF, VOICE, WAKEWORD = "off", "voice", "wakeword"


class BargeInDetector:
    """Decide si el usuario ha empezado a hablar mientras Jarvis respondía.

    `feed()` devuelve los frames previos (pre-roll) cuando confirma la
    interrupción, para que la frase del usuario no pierda las primeras sílabas.
    """

    def __init__(
        self,
        mode: str = VOICE,
        sample_rate: int = 16000,
        frame_ms: int = 30,
        threshold: float = 0.10,
        echo_gain: float = 1.4,
        frames: int = 5,
        guard_ms: int = 350,
        preroll_frames: int = 10,
        aggressiveness: int = 2,
    ):
        self.mode = mode if mode in (OFF, VOICE, WAKEWORD) else OFF
        self.sample_rate = sample_rate
        self.frame_ms = frame_ms
        self.threshold = threshold
        self.echo_gain = echo_gain
        self.frames_needed = max(1, frames)
        self.guard_seconds = guard_ms / 1000.0
        self.vad = make_vad(aggressiveness)
        self.preroll: deque[bytes] = deque(maxlen=max(1, preroll_frames))
        self.armed_at = 0.0
        self.hits = 0
        self.last_level = 0.0
        self.last_gate = 0.0

    @property
    def enabled(self) -> bool:
        return self.mode != OFF

    def arm(self) -> None:
        """Jarvis empieza a hablar (o a pensar): a partir de ahora vigilamos."""
        self.armed_at = time.monotonic()
        self.hits = 0
        self.preroll.clear()

    def reset(self) -> None:
        self.armed_at = 0.0
        self.hits = 0
        self.preroll.clear()

    def feed(self, frame: bytes, playback_level: float = 0.0,
             wake_hit: bool = False) -> list[bytes] | None:
        """Devuelve el pre-roll si hay interrupción, o None si no la hay."""
        if not self.enabled or not self.armed_at:
            return None

        self.preroll.append(frame)

        if self.mode == WAKEWORD:
            return list(self.preroll) if wake_hit else None

        # Margen inicial: ignora la cola de la frase anterior y el chasquido
        # del altavoz al arrancar.
        if time.monotonic() - self.armed_at < self.guard_seconds:
            return None

        level = _rms(frame)
        # El umbral sube con el volumen de lo que suena por los altavoces.
        gate = max(self.threshold, self.echo_gain * playback_level)
        self.last_level, self.last_gate = level, gate

        if level > gate and self.vad.is_speech(frame, self.sample_rate):
            self.hits += 1
        elif self.hits:
            self.hits -= 1  # un pico suelto no interrumpe

        if self.hits >= self.frames_needed:
            log.info("barge-in: nivel %.3f sobre umbral %.3f", level, gate)
            prefix = list(self.preroll)
            self.reset()
            return prefix
        return None


def make_barge_in(cfg) -> BargeInDetector:
    section = cfg.section("barge_in")
    mode = str(section.get("mode", VOICE)).lower()
    if not section.get("enabled", True):
        mode = OFF
    return BargeInDetector(
        mode=mode,
        sample_rate=int(cfg.get("audio.sample_rate", 16000)),
        frame_ms=int(cfg.get("audio.frame_ms", 30)),
        threshold=float(section.get("threshold", 0.10)),
        echo_gain=float(section.get("echo_gain", 1.4)),
        frames=int(section.get("frames", 5)),
        guard_ms=int(section.get("guard_ms", 350)),
        preroll_frames=int(section.get("preroll_frames", 10)),
        aggressiveness=int(cfg.get("audio.vad_aggressiveness", 2)),
    )
