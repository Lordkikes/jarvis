"""Cancelación de eco acústico (AEC) con webrtc-audio-processing.

El detector de barge-in por energía funciona, pero se pelea con el eco: si
Jarvis suena fuerte por los altavoces, su propia voz entra por el micrófono y
hay que subir umbrales hasta que interrumpirle cuesta gritar.

La solución de verdad es restar ese eco antes de mirar nada. El módulo de
WebRTC recibe dos señales:

  · la *referencia* (reverse stream): lo que se está mandando a los altavoces,
    que el reproductor va entregando bloque a bloque;
  · la *señal cercana* (stream): lo que capta el micrófono, eco incluido.

Con ambas estima el camino acústico y devuelve el micrófono limpio. Todo lo que
viene después (VAD, wake word, barge-in y transcripción) trabaja ya sin eco.

Requiere frames de exactamente 10 ms: el binding lee el tamaño de la
configuración, no de la cadena que le pasas, así que enviar un trozo más corto
sería leer fuera de rango. Por eso aquí nunca se llama con otro tamaño.
"""
from __future__ import annotations

import logging
import threading

from .resample import resample_pcm

log = logging.getLogger("jarvis.audio.aec")

FRAME_MS = 10
FULL, MOBILE = "full", "mobile"
# aec_type del binding: 1 = AECM (móvil), 2 = AEC completo.
AEC_TYPE = {MOBILE: 1, FULL: 2}


class EchoCanceller:
    """Envoltorio del módulo de audio de WebRTC, seguro entre hilos.

    `reference()` la llama el hilo de reproducción y `process()` el bucle de
    eventos, así que todo acceso al módulo va bajo el mismo cerrojo.
    """

    def __init__(
        self,
        rate: int = 16000,
        mode: str = FULL,
        suppression: int = 0,
        noise_suppression: int = 1,
        delay_ms: int | None = None,
        default_delay_ms: int = 80,
    ):
        from webrtc_audio_processing import AudioProcessingModule

        self.rate = rate
        self.mode = mode if mode in AEC_TYPE else FULL
        self.frame_bytes = rate // 1000 * FRAME_MS * 2
        self.fixed_delay_ms = delay_ms
        self.default_delay_ms = default_delay_ms
        self.input_latency_ms = 0
        self.output_latency_ms = 0

        self._lock = threading.Lock()
        self._reference_buffer = bytearray()

        self.ap = AudioProcessingModule(
            aec_type=AEC_TYPE[self.mode],
            enable_ns=noise_suppression >= 0,
            agc_type=0,
            enable_vad=True,
        )
        self.ap.set_stream_format(rate, 1)
        self.ap.set_reverse_stream_format(rate, 1)
        if noise_suppression >= 0:
            self.ap.set_ns_level(max(0, min(3, noise_suppression)))
        if self.mode == FULL:
            # Nivel bajo: el filtro adaptativo ya cancela el eco y una
            # supresión alta se come tu voz cuando hablas encima.
            self.ap.set_aec_level(max(0, min(2, suppression)))
        self._apply_delay()
        log.info("cancelación de eco activa (modo %s, %d Hz)", self.mode, rate)

    # -- retardo del sistema ----------------------------------------------
    @property
    def delay_ms(self) -> int:
        if self.fixed_delay_ms is not None:
            return self.fixed_delay_ms
        measured = self.input_latency_ms + self.output_latency_ms
        return measured or self.default_delay_ms

    def _apply_delay(self) -> None:
        with self._lock:
            self.ap.set_system_delay(int(self.delay_ms))

    def set_latencies(self, input_ms: float | None = None,
                      output_ms: float | None = None) -> None:
        """Retardo real entre lo que sale por el altavoz y lo que vuelve."""
        if input_ms is not None:
            self.input_latency_ms = int(input_ms)
        if output_ms is not None:
            self.output_latency_ms = int(output_ms)
        self._apply_delay()

    # -- señales -----------------------------------------------------------
    def reference(self, pcm: bytes, rate: int | None = None) -> None:
        """Entrega lo que va a sonar por los altavoces.

        Los bloques del reproductor son de 100 ms exactos, así que el
        remuestreo cae en un número entero de frames y no acumula desfase.
        """
        if not pcm:
            return
        if rate and rate != self.rate:
            pcm = resample_pcm(pcm, rate, self.rate)
        with self._lock:
            self._reference_buffer.extend(pcm)
            while len(self._reference_buffer) >= self.frame_bytes:
                chunk = bytes(self._reference_buffer[:self.frame_bytes])
                del self._reference_buffer[:self.frame_bytes]
                self.ap.process_reverse_stream(chunk)

    def clear_reference(self) -> None:
        """Al terminar (o cortar) la reproducción, descarta el resto parcial."""
        with self._lock:
            self._reference_buffer.clear()

    def process(self, frame: bytes) -> bytes:
        """Devuelve el frame del micrófono sin eco (mismo tamaño que entró)."""
        if len(frame) % self.frame_bytes:
            # Tamaño inesperado: mejor pasar el audio tal cual que leer basura.
            log.debug("frame de %d bytes no múltiplo de 10 ms; sin procesar", len(frame))
            return frame
        with self._lock:
            return b"".join(
                self.ap.process_stream(frame[offset:offset + self.frame_bytes])
                for offset in range(0, len(frame), self.frame_bytes)
            )

    def has_voice(self) -> bool:
        """VAD de WebRTC sobre el audio ya limpio del último frame."""
        with self._lock:
            return bool(self.ap.has_voice())


def make_echo_canceller(cfg) -> EchoCanceller | None:
    """Crea el cancelador si está configurado y la librería disponible."""
    section = cfg.section("aec")
    if not section.get("enabled", True):
        return None

    frame_ms = int(cfg.get("audio.frame_ms", 30))
    if frame_ms % FRAME_MS:
        log.warning("audio.frame_ms=%d no es múltiplo de 10; sin cancelación de eco",
                    frame_ms)
        return None

    try:
        return EchoCanceller(
            rate=int(cfg.get("audio.sample_rate", 16000)),
            mode=str(section.get("mode", FULL)).lower(),
            suppression=int(section.get("suppression", 0)),
            noise_suppression=int(section.get("noise_suppression", 1)),
            delay_ms=section.get("delay_ms"),
        )
    except ImportError:
        log.info("webrtc-audio-processing no instalado; sin cancelación de eco "
                 "(pip install webrtc-audio-processing)")
    except Exception as exc:  # noqa: BLE001
        log.warning("no se pudo iniciar la cancelación de eco (%s)", exc)
    return None
