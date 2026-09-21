"""Pruebas de las piezas que no necesitan micrófono ni API.

    python -m unittest discover -s tests
"""
from __future__ import annotations

import asyncio
import math
import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis.audio.bargein import BargeInDetector, make_barge_in  # noqa: E402
from jarvis.audio.player import pcm_to_wav  # noqa: E402
from jarvis.audio.vad import Utterance  # noqa: E402
from jarvis.bus import EventBus  # noqa: E402
from jarvis.config import Config, load_config  # noqa: E402
from jarvis.llm.claude import SENTENCE_END  # noqa: E402
from jarvis.llm.tools import Toolbox  # noqa: E402

SAMPLE_RATE = 16000
FRAME_MS = 30
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000


def tone(amplitude: int = 12000) -> bytes:
    """Frame de 30 ms con una onda audible (simula voz)."""
    samples = [int(amplitude * math.sin(2 * math.pi * 220 * i / SAMPLE_RATE))
               for i in range(FRAME_SAMPLES)]
    return struct.pack("<%dh" % len(samples), *samples)


def silence() -> bytes:
    return b"\x00\x00" * FRAME_SAMPLES


class TestConfig(unittest.TestCase):
    def test_nested_access(self):
        cfg = Config({"llm": {"model": "x", "max_tokens": 10}})
        self.assertEqual(cfg.get("llm.model"), "x")
        self.assertEqual(cfg.get("llm.falta", "por_defecto"), "por_defecto")
        self.assertEqual(cfg.get("no.existe.nada", 3), 3)

    def test_env_override(self):
        os.environ["JARVIS_LLM__MAX_TOKENS"] = "512"
        try:
            cfg = load_config()
            self.assertEqual(cfg.get("llm.max_tokens"), 512)
        finally:
            del os.environ["JARVIS_LLM__MAX_TOKENS"]


class TestUtterance(unittest.TestCase):
    def test_detects_end_of_speech(self):
        utterance = Utterance(sample_rate=SAMPLE_RATE, frame_ms=FRAME_MS,
                              silence_ms=300, min_speech_ms=90, max_seconds=5)
        results = [utterance.push(tone()) for _ in range(20)]
        self.assertIn("speaking", results)

        result = "speaking"
        for _ in range(20):
            result = utterance.push(silence())
            if result == "done":
                break
        self.assertEqual(result, "done")
        self.assertGreater(len(utterance.audio()), FRAME_SAMPLES * 2)

    def test_ignores_pure_silence(self):
        utterance = Utterance(sample_rate=SAMPLE_RATE, frame_ms=FRAME_MS, silence_ms=300)
        for _ in range(30):
            self.assertEqual(utterance.push(silence()), "listening")


class TestSentenceSplit(unittest.TestCase):
    def test_splits_on_punctuation(self):
        text = "Hola, soy Jarvis. ¿Qué necesitas? Dime."
        pieces, buffer = [], text
        while (match := SENTENCE_END.search(buffer)):
            pieces.append(buffer[:match.end()].strip())
            buffer = buffer[match.end():]
        self.assertEqual(pieces[0], "Hola, soy Jarvis.")
        self.assertEqual(pieces[1], "¿Qué necesitas?")


class TestToolbox(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = Config({
            "tools": {
                "allow_system": False,
                "notes_file": str(Path(self.tmp.name) / "notes.json"),
                "memory_file": str(Path(self.tmp.name) / "memory.json"),
            },
            "llm": {"web_search": False},
        })
        self.toolbox = Toolbox(self.cfg, EventBus())

    def tearDown(self):
        self.tmp.cleanup()

    def test_system_tools_hidden_when_disabled(self):
        names = [spec["name"] for spec in self.toolbox.definitions()]
        self.assertNotIn("abrir", names)
        self.assertIn("obtener_fecha_hora", names)

    def test_notes_roundtrip(self):
        asyncio.run(self.toolbox.run("guardar_nota", {"texto": "comprar café"}))
        salida = asyncio.run(self.toolbox.run("leer_notas", {}))
        self.assertIn("comprar café", salida)

    def test_memory_is_persisted(self):
        asyncio.run(self.toolbox.run("recordar_dato", {"dato": "vive en Bogotá"}))
        self.assertIn("vive en Bogotá", self.toolbox.memories())

    def test_unknown_tool_is_reported(self):
        self.assertIn("desconocida", asyncio.run(self.toolbox.run("inventada", {})))


class TestBargeIn(unittest.TestCase):
    def detector(self, **kwargs):
        options = dict(sample_rate=SAMPLE_RATE, frame_ms=FRAME_MS, threshold=0.10,
                       echo_gain=1.4, frames=3, guard_ms=0, preroll_frames=4)
        options.update(kwargs)
        return BargeInDetector(**options)

    def test_disabled_never_interrupts(self):
        detector = self.detector(mode="off")
        detector.arm()
        for _ in range(20):
            self.assertIsNone(detector.feed(tone(), playback_level=0.0))

    def test_voice_over_silence_interrupts(self):
        detector = self.detector()
        detector.arm()
        result = None
        for _ in range(6):
            result = detector.feed(tone(), playback_level=0.0)
            if result is not None:
                break
        self.assertIsNotNone(result)
        self.assertTrue(result, "el pre-roll no puede venir vacío")

    def test_preroll_keeps_recent_audio(self):
        detector = self.detector(frames=6, preroll_frames=4)
        detector.arm()
        for _ in range(10):
            result = detector.feed(tone(), playback_level=0.0)
            if result is not None:
                self.assertEqual(len(result), 4)  # se guardan los 4 últimos frames
                return
        self.fail("no se detectó la interrupción")

    def test_own_echo_does_not_interrupt(self):
        """Lo que sale por los altavoces entra por el micro: no debe cortarse solo."""
        detector = self.detector()
        detector.arm()
        for _ in range(30):
            # El micro oye el mismo nivel que se está reproduciendo.
            self.assertIsNone(detector.feed(tone(), playback_level=0.95))

    def test_quiet_noise_does_not_interrupt(self):
        detector = self.detector()
        detector.arm()
        for _ in range(30):
            self.assertIsNone(detector.feed(tone(600), playback_level=0.0))

    def test_isolated_peak_does_not_interrupt(self):
        """Un golpe suelto entre silencios no cuenta como voz."""
        detector = self.detector(frames=4)
        detector.arm()
        for _ in range(10):
            self.assertIsNone(detector.feed(tone(), playback_level=0.0))
            self.assertIsNone(detector.feed(silence(), playback_level=0.0))

    def test_guard_window_ignores_the_tail_of_your_own_phrase(self):
        detector = self.detector(guard_ms=10_000)
        detector.arm()
        for _ in range(20):
            self.assertIsNone(detector.feed(tone(), playback_level=0.0))

    def test_wakeword_mode_only_reacts_to_the_wake_word(self):
        detector = self.detector(mode="wakeword")
        detector.arm()
        for _ in range(10):
            self.assertIsNone(detector.feed(tone(), playback_level=0.0))
        self.assertIsNotNone(detector.feed(silence(), wake_hit=True))

    def test_not_armed_means_not_listening(self):
        detector = self.detector()
        for _ in range(20):
            self.assertIsNone(detector.feed(tone(), playback_level=0.0))

    def test_factory_reads_config(self):
        detector = make_barge_in(Config({"barge_in": {"enabled": False, "mode": "voice"}}))
        self.assertFalse(detector.enabled)
        detector = make_barge_in(Config({"barge_in": {"mode": "wakeword"}}))
        self.assertEqual(detector.mode, "wakeword")


class TestWav(unittest.TestCase):
    def test_header(self):
        wav = pcm_to_wav(tone(), SAMPLE_RATE)
        self.assertTrue(wav.startswith(b"RIFF"))
        self.assertIn(b"WAVE", wav[:16])


class TestBus(unittest.TestCase):
    def test_subscribers_receive_events(self):
        async def run():
            bus = EventBus()
            queue = bus.subscribe()
            bus.emit("state", state="listening")
            return await queue.get()

        event = asyncio.run(run())
        self.assertEqual(event["state"], "listening")


if __name__ == "__main__":
    unittest.main()
