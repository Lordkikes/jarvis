"""Detectores de palabra de activación ('Hey Jarvis')."""
from __future__ import annotations

import logging

log = logging.getLogger("jarvis.audio.wakeword")


class NullWakeWord:
    """Sin wake word: la activación es manual (botón o atajo)."""

    name = "none"

    def detect(self, frame: bytes) -> bool:  # noqa: ARG002
        return False

    def reset(self) -> None:
        pass


class OpenWakeWord:
    """openWakeWord: offline, gratuito, incluye el modelo 'hey_jarvis'."""

    name = "openwakeword"

    def __init__(self, model: str = "hey_jarvis", threshold: float = 0.5):
        import numpy as np
        from openwakeword.model import Model

        try:
            import openwakeword

            openwakeword.utils.download_models([model])
        except Exception as exc:  # noqa: BLE001 - modelos ya descargados
            log.debug("descarga de modelos omitida: %s", exc)

        self._np = np
        self.model = Model(wakeword_models=[model], inference_framework="onnx")
        self.key = model
        self.threshold = threshold
        # El modelo trabaja con bloques de 80 ms; los frames del micro son menores.
        self.block = 1280
        self._buffer = bytearray()

    def detect(self, frame: bytes) -> bool:
        self._buffer.extend(frame)
        detected = False
        while len(self._buffer) >= self.block * 2:
            chunk = bytes(self._buffer[:self.block * 2])
            del self._buffer[:self.block * 2]
            samples = self._np.frombuffer(chunk, dtype=self._np.int16)
            for key, score in self.model.predict(samples).items():
                if score >= self.threshold:
                    log.info("wake word '%s' detectada (%.2f)", key, score)
                    detected = True
        return detected

    def reset(self) -> None:
        self._buffer.clear()
        self.model.reset()


class Porcupine:
    """Picovoice Porcupine: muy preciso, requiere PICOVOICE_ACCESS_KEY."""

    name = "porcupine"

    def __init__(self, keyword: str = "jarvis", access_key: str | None = None,
                 sensitivity: float = 0.5):
        import os
        import struct

        import pvporcupine

        self._struct = struct
        self.engine = pvporcupine.create(
            access_key=access_key or os.environ["PICOVOICE_ACCESS_KEY"],
            keywords=[keyword],
            sensitivities=[sensitivity],
        )
        self.frame_length = self.engine.frame_length
        self._buffer: list[int] = []

    def detect(self, frame: bytes) -> bool:
        count = len(frame) // 2
        self._buffer.extend(self._struct.unpack_from("<%dh" % count, frame))
        while len(self._buffer) >= self.frame_length:
            chunk = self._buffer[:self.frame_length]
            self._buffer = self._buffer[self.frame_length:]
            if self.engine.process(chunk) >= 0:
                log.info("wake word detectada (porcupine)")
                return True
        return False

    def reset(self) -> None:
        self._buffer.clear()


def make_wakeword(cfg) -> object:
    """Crea el detector configurado; cae a NullWakeWord si falla."""
    provider = (cfg.get("wake.provider") or "none").lower()
    model = cfg.get("wake.model", "hey_jarvis")
    threshold = float(cfg.get("wake.threshold", 0.5))
    try:
        if provider == "openwakeword":
            return OpenWakeWord(model=model, threshold=threshold)
        if provider == "porcupine":
            return Porcupine(keyword=model, sensitivity=threshold)
    except Exception as exc:  # noqa: BLE001
        log.warning("wake word '%s' no disponible (%s); activación manual", provider, exc)
    return NullWakeWord()
