"""El bucle de audio real cortando una respuesta en curso.

Sustituimos el micrófono por una fuente de frames sintéticos y comprobamos
que hablar encima de Jarvis cancela el turno y vuelve a escuchar.
"""
from __future__ import annotations

import asyncio
import math
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis.bus import EventBus  # noqa: E402
from jarvis.config import Config  # noqa: E402
from jarvis.pipeline import LISTENING, SPEAKING, Jarvis  # noqa: E402

SAMPLE_RATE, FRAME_MS = 16000, 30
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000


def tone(amplitude: int = 12000) -> bytes:
    samples = [int(amplitude * math.sin(2 * math.pi * 220 * i / SAMPLE_RATE))
               for i in range(FRAME_SAMPLES)]
    return struct.pack("<%dh" % len(samples), *samples)


class FakeCapture:
    """Micrófono de mentira: devuelve siempre el mismo frame."""

    def __init__(self, frame: bytes):
        self.frame = frame
        self.drained = 0

    async def read(self) -> bytes:
        await asyncio.sleep(0)  # cede el control al resto del bucle
        return self.frame

    def drain(self) -> None:
        self.drained += 1

    def set_muted(self, muted: bool) -> None:
        pass

    def stop(self) -> None:
        pass


def make_jarvis(tmp: str, **barge_in) -> tuple[Jarvis, EventBus]:
    options = {"enabled": True, "mode": "voice", "threshold": 0.10,
               "echo_gain": 1.4, "frames": 3, "guard_ms": 0, "preroll_frames": 5}
    options.update(barge_in)
    cfg = Config({
        "audio": {"sample_rate": SAMPLE_RATE, "frame_ms": FRAME_MS},
        "barge_in": options,
        "wake": {"provider": "none", "followup_seconds": 0},
        "stt": {"provider": "none"},
        "tts": {"provider": "none"},
        "llm": {"provider": "claude", "web_search": False},
        "tools": {"allow_system": False,
                  "notes_file": str(Path(tmp) / "n.json"),
                  "memory_file": str(Path(tmp) / "m.json")},
    })
    bus = EventBus()
    return Jarvis(cfg, bus), bus


class TestBargeInPipeline(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def drive(self, jarvis: Jarvis, frame: bytes, cycles: int = 40) -> None:
        """Deja correr el bucle de audio unos cuantos frames."""
        jarvis.capture = FakeCapture(frame)
        loop_task = asyncio.create_task(jarvis._audio_loop())
        for _ in range(cycles):
            await asyncio.sleep(0)
            if jarvis.state == LISTENING:
                break
        loop_task.cancel()
        try:
            await loop_task
        except asyncio.CancelledError:
            pass

    async def test_speaking_over_jarvis_cancels_the_turn(self):
        jarvis, bus = make_jarvis(self.tmp.name)
        queue = bus.subscribe()

        async def long_answer() -> None:
            jarvis._turn_task = asyncio.current_task()
            await asyncio.sleep(30)

        turn = asyncio.create_task(long_answer())
        await asyncio.sleep(0)
        jarvis._set_state(SPEAKING)

        await self.drive(jarvis, tone())

        self.assertEqual(jarvis.state, LISTENING)
        self.assertTrue(turn.cancelled() or turn.done(),
                        "el turno en curso debería haberse cancelado")
        events = []
        while not queue.empty():
            events.append(queue.get_nowait()["type"])
        self.assertIn("barge_in", events)
        turn.cancel()

    async def test_its_own_echo_does_not_cancel_the_turn(self):
        jarvis, _ = make_jarvis(self.tmp.name)
        jarvis._set_state(SPEAKING)
        jarvis.player.level = 0.95      # los altavoces están a tope

        await self.drive(jarvis, tone(), cycles=60)

        self.assertEqual(jarvis.state, SPEAKING)

    async def test_disabled_barge_in_keeps_speaking(self):
        jarvis, _ = make_jarvis(self.tmp.name, enabled=False)
        jarvis._set_state(SPEAKING)

        await self.drive(jarvis, tone(), cycles=60)

        self.assertEqual(jarvis.state, SPEAKING)

    async def test_manual_interrupt_stops_the_turn(self):
        jarvis, bus = make_jarvis(self.tmp.name)
        queue = bus.subscribe()

        async def long_answer() -> None:
            jarvis._turn_task = asyncio.current_task()
            await asyncio.sleep(30)

        turn = asyncio.create_task(long_answer())
        await asyncio.sleep(0)
        jarvis._set_state(SPEAKING)
        jarvis.interrupt()
        await asyncio.sleep(0)

        self.assertTrue(turn.cancelled() or turn.done())
        events = []
        while not queue.empty():
            events.append(queue.get_nowait()["type"])
        self.assertIn("interrupted", events)
        turn.cancel()


if __name__ == "__main__":
    unittest.main()
