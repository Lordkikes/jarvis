"""Cancelación de eco: remuestreo, contrato del módulo y eficacia real."""
from __future__ import annotations

import math
import random
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis.audio.aec import FRAME_MS, EchoCanceller, make_echo_canceller  # noqa: E402
from jarvis.audio.resample import resample_pcm  # noqa: E402
from jarvis.config import Config  # noqa: E402

RATE = 16000
BLOCK = RATE * FRAME_MS // 1000          # 160 muestras = 10 ms

try:
    import webrtc_audio_processing  # noqa: F401
    HAS_WEBRTC = True
except ImportError:
    HAS_WEBRTC = False


def pack(values) -> bytes:
    return struct.pack("<%dh" % len(values),
                       *[max(-32768, min(32767, int(v))) for v in values])


def rms(pcm: bytes) -> float:
    count = len(pcm) // 2
    if not count:
        return 0.0
    values = struct.unpack("<%dh" % count, pcm)
    return math.sqrt(sum(v * v for v in values) / count)


def jarvis_voice(t: float) -> float:
    """Señal con envolvente variable, parecida a voz sintetizada."""
    envelope = 0.6 + 0.4 * math.sin(2 * math.pi * 1.7 * t)
    return 9000 * envelope * (0.6 * math.sin(2 * math.pi * 180 * t)
                              + 0.3 * math.sin(2 * math.pi * 430 * t))


def user_voice(t: float) -> float:
    return 4500 * (0.7 * math.sin(2 * math.pi * 260 * t)
                   + 0.3 * math.sin(2 * math.pi * 820 * t))


class TestResample(unittest.TestCase):
    def test_same_rate_is_untouched(self):
        pcm = pack([100, -100, 200])
        self.assertIs(resample_pcm(pcm, RATE, RATE), pcm)

    def test_block_of_100ms_keeps_exact_length(self):
        """Clave para el AEC: 100 ms a 22 050 Hz son 100 ms a 16 000 Hz."""
        pcm = pack([int(3000 * math.sin(i / 20)) for i in range(2205)])
        self.assertEqual(len(resample_pcm(pcm, 22050, 16000)) // 2, 1600)

    def test_every_block_converts_the_same_way(self):
        """Dos bloques idénticos deben dar el mismo resultado, sin arrastres."""
        block = pack([int(3000 * math.sin(i / 7)) for i in range(2205)])
        first = resample_pcm(block, 22050, 16000)
        second = resample_pcm(block, 22050, 16000)
        self.assertEqual(first, second)
        self.assertEqual(len(first) // 2, 1600)

    def test_upsampling_also_works(self):
        pcm = pack([0] * 800)
        self.assertEqual(len(resample_pcm(pcm, 8000, 16000)) // 2, 1600)

    def test_empty_input(self):
        self.assertEqual(resample_pcm(b"", 22050, 16000), b"")


class TestFactory(unittest.TestCase):
    def test_disabled_by_config(self):
        cfg = Config({"aec": {"enabled": False}, "audio": {"frame_ms": 30}})
        self.assertIsNone(make_echo_canceller(cfg))

    def test_frame_size_must_be_a_multiple_of_10ms(self):
        """El binding lee 10 ms fijos: con otro tamaño leería fuera de rango."""
        cfg = Config({"aec": {"enabled": True}, "audio": {"frame_ms": 25}})
        self.assertIsNone(make_echo_canceller(cfg))


@unittest.skipUnless(HAS_WEBRTC, "webrtc-audio-processing no está instalado")
class TestEchoCanceller(unittest.TestCase):
    def canceller(self, **kwargs):
        options = dict(rate=RATE, mode="full", suppression=0, noise_suppression=1)
        options.update(kwargs)
        return EchoCanceller(**options)

    def test_output_keeps_the_frame_size(self):
        aec = self.canceller()
        frame = pack([100] * (BLOCK * 3))      # 30 ms
        self.assertEqual(len(aec.process(frame)), len(frame))

    def test_odd_sized_frame_passes_through(self):
        aec = self.canceller()
        frame = pack([100] * 137)
        self.assertEqual(aec.process(frame), frame)

    def test_delay_comes_from_the_devices(self):
        aec = self.canceller()
        aec.set_latencies(input_ms=30, output_ms=90)
        self.assertEqual(aec.delay_ms, 120)

    def test_fixed_delay_wins_over_measurements(self):
        aec = self.canceller(delay_ms=200)
        aec.set_latencies(input_ms=30, output_ms=90)
        self.assertEqual(aec.delay_ms, 200)

    def test_default_delay_when_nothing_is_known(self):
        self.assertEqual(self.canceller().delay_ms, 80)

    def _run_echo(self, aec, frames=400, delay_frames=10, attenuation=0.45,
                  talk_from=None):
        """Simula altavoz -> sala -> micrófono y devuelve (eco, voz cercana)."""
        random.seed(11)
        far = [[jarvis_voice((f * BLOCK + i) / RATE) for i in range(BLOCK)]
               for f in range(frames)]
        aec.set_latencies(input_ms=0, output_ms=delay_frames * FRAME_MS)
        echo_only, double_talk = [], []
        for f in range(frames):
            aec.reference(pack(far[f]))
            source = far[f - delay_frames] if f >= delay_frames else [0.0] * BLOCK
            speaking = talk_from is not None and f >= talk_from
            near = [user_voice((f * BLOCK + i) / RATE) if speaking else 0.0
                    for i in range(BLOCK)]
            mic = pack([attenuation * s + n + random.gauss(0, 30)
                        for s, n in zip(source, near)])
            clean = aec.process(mic)
            if 200 < f < (talk_from or frames):
                echo_only.append(rms(clean))
            elif speaking and f > talk_from + 10:
                double_talk.append(rms(clean))
        mean = lambda values: sum(values) / len(values) if values else 0.0  # noqa: E731
        return mean(echo_only), mean(double_talk)

    def test_echo_is_actually_cancelled(self):
        residual, _ = self._run_echo(self.canceller())
        # El eco que entra ronda los 1300 de RMS; sin cancelar seguiría ahí.
        self.assertLess(residual, 130, "menos de 20 dB de cancelación")

    def test_your_voice_survives_while_it_is_talking(self):
        """Double-talk: interrumpir tiene que destacar sobre el eco residual."""
        residual, talking = self._run_echo(self.canceller(), talk_from=300)
        self.assertGreater(talking, residual * 8)

    def test_cancels_with_a_reference_at_the_piper_rate(self):
        """Caso real: Piper suena a 22 050 Hz y el micrófono capta a 16 000 Hz.

        Además reproduce el ritmo real: el reproductor entrega la referencia en
        bloques de 100 ms que van por delante de lo que se oye (el búfer de
        salida), mientras el micrófono llega frame a frame.
        """
        latency_ms, block_ms, ref_rate = 100, 100, 22050
        samples_per_block = ref_rate * block_ms // 1000
        aec = self.canceller()
        aec.set_latencies(input_ms=0, output_ms=latency_ms)
        random.seed(11)

        steps, fed, incoming, residual = 600, 0, [], []
        for f in range(steps):
            now_ms = f * FRAME_MS
            while fed * block_ms - latency_ms <= now_ms:
                start = fed * block_ms / 1000.0
                aec.reference(
                    pack([jarvis_voice(start + i / ref_rate)
                          for i in range(samples_per_block)]), ref_rate)
                fed += 1
            source = [jarvis_voice(now_ms / 1000.0 + i / RATE) for i in range(BLOCK)]
            mic = pack([0.45 * s + random.gauss(0, 30) for s in source])
            clean = aec.process(mic)
            if f > steps // 2:
                incoming.append(rms(mic))
                residual.append(rms(clean))

        entering = sum(incoming) / len(incoming)
        left = sum(residual) / len(residual)
        self.assertLess(left, entering / 10, "menos de 20 dB de cancelación")

    def test_mobile_mode_also_cancels(self):
        residual, _ = self._run_echo(self.canceller(mode="mobile"))
        self.assertLess(residual, 400)


if __name__ == "__main__":
    unittest.main()
