"""Orquestador: wake word -> escucha -> transcripción -> Claude -> voz."""
from __future__ import annotations

import asyncio
import base64
import logging
import time

from .audio import AudioUnavailable
from .audio.aec import make_echo_canceller
from .audio.bargein import make_barge_in
from .audio.capture import AudioCapture
from .audio.player import AudioPlayer, pcm_to_wav
from .audio.vad import Utterance
from .audio.wakeword import make_wakeword
from .bus import EventBus
from .llm.claude import Brain
from .llm.tools import Toolbox
from .stt import make_stt
from .tts import make_tts

log = logging.getLogger("jarvis.pipeline")

IDLE, LISTENING, THINKING, SPEAKING = "idle", "listening", "thinking", "speaking"


class Jarvis:
    def __init__(self, cfg, bus: EventBus):
        self.cfg = cfg
        self.bus = bus
        self.state = IDLE
        self.voice_mode = False
        self.muted = False
        self._tasks: set[asyncio.Task] = set()
        self._speech_queue: asyncio.Queue[str | None] = asyncio.Queue()
        self._activate_event = asyncio.Event()
        self._speech_epoch = 0
        self._followup_until = 0.0
        self._busy = asyncio.Lock()

        self.toolbox = Toolbox(cfg, bus, on_announce=self._announce)
        self.brain = Brain(cfg, self.toolbox)
        self.stt = make_stt(cfg)
        self.tts = make_tts(cfg)
        self.aec = make_echo_canceller(cfg)
        self.player = AudioPlayer(device=cfg.get("audio.output_device"), aec=self.aec)
        self.wakeword = make_wakeword(cfg)
        self.barge_in = make_barge_in(cfg)
        if self.aec is not None:
            # Sin eco en el micrófono, interrumpir ya no exige levantar la voz.
            self.barge_in.echo_gain = float(cfg.get("barge_in.echo_gain_aec", 0.6))
        self._turn_task: asyncio.Task | None = None
        self.capture: AudioCapture | None = None

    # -- ciclo de vida -----------------------------------------------------
    async def start(self) -> None:
        self._spawn(self._speech_worker())
        try:
            self.capture = AudioCapture(
                loop=asyncio.get_running_loop(),
                sample_rate=int(self.cfg.get("audio.sample_rate", 16000)),
                frame_ms=int(self.cfg.get("audio.frame_ms", 30)),
                device=self.cfg.get("audio.input_device"),
                on_level=self._emit_level,
                aec=self.aec,
            )
            self.capture.start()
            self.voice_mode = True
            self._spawn(self._audio_loop())
        except AudioUnavailable as exc:
            log.warning("modo solo texto: %s", exc)
            self.bus.emit("log", level="warning",
                          message="Sin micrófono: usa el cuadro de texto o el botón.")
        if (self.barge_in.mode == "wakeword"
                and getattr(self.wakeword, "name", "none") == "none"):
            log.warning("barge_in.mode='wakeword' pero no hay detector de wake word; "
                        "usa mode='voice' o configura wake.provider")
        self._set_state(IDLE)
        self.bus.emit("ready", voice=self.voice_mode,
                      stt=type(self.stt).__name__, tts=type(self.tts).__name__,
                      wake=getattr(self.wakeword, "name", "none"),
                      barge_in=self.barge_in.mode, aec=self.aec_mode,
                      model=self.brain.model)

    def _emit_level(self, level: float) -> None:
        """Envía el nivel a la interfaz ~11 veces por segundo, no en cada frame."""
        self._level_tick = getattr(self, "_level_tick", 0) + 1
        self._level_peak = max(getattr(self, "_level_peak", 0.0), level)
        if self._level_tick % 3 == 0:
            self.bus.emit("level", value=round(self._level_peak, 3))
            self._level_peak = 0.0

    async def stop(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        if self.capture is not None:
            self.capture.stop()

    def _spawn(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    @property
    def aec_mode(self) -> str:
        return self.aec.mode if self.aec is not None else "off"

    # -- estado ------------------------------------------------------------
    def _set_state(self, state: str, **extra) -> None:
        previous, self.state = self.state, state
        if state in (THINKING, SPEAKING):
            if previous not in (THINKING, SPEAKING):
                self.barge_in.arm()
        elif previous in (THINKING, SPEAKING):
            self.barge_in.reset()
        self.bus.emit("state", state=state, **extra)

    # -- entrada de audio --------------------------------------------------
    async def _audio_loop(self) -> None:
        """Bucle principal: escucha el micrófono y decide qué hacer."""
        assert self.capture is not None
        audio_cfg = self.cfg.section("audio")
        utterance = Utterance(
            sample_rate=int(audio_cfg.get("sample_rate", 16000)),
            frame_ms=int(audio_cfg.get("frame_ms", 30)),
            silence_ms=int(audio_cfg.get("silence_ms", 900)),
            min_speech_ms=int(audio_cfg.get("min_speech_ms", 300)),
            max_seconds=int(audio_cfg.get("max_utterance_s", 20)),
            aggressiveness=int(audio_cfg.get("vad_aggressiveness", 2)),
        )
        followup_seconds = float(self.cfg.get("wake.followup_seconds", 8))
        capturing = False

        while True:
            frame = await self.capture.read()

            if self.muted:
                continue

            if self.state in (THINKING, SPEAKING):
                # Jarvis está respondiendo: solo nos interesa saber si el
                # usuario ha empezado a hablar por encima.
                if not self.barge_in.enabled:
                    continue
                wake_hit = (self.barge_in.mode == "wakeword"
                            and self.wakeword.detect(frame))
                prefix = self.barge_in.feed(
                    frame, playback_level=self.player.level, wake_hit=wake_hit)
                if prefix is None:
                    continue
                self._cut_off()
                utterance.reset()
                for buffered in prefix:
                    utterance.push(buffered)
                capturing = True
                self._followup_until = 0.0
                self._set_state(LISTENING)
                continue

            if not capturing:
                manual = self._activate_event.is_set()
                followup = time.monotonic() < self._followup_until
                if manual or followup or self.wakeword.detect(frame):
                    self._activate_event.clear()
                    self.wakeword.reset()
                    utterance.reset()
                    capturing = True
                    if not followup:
                        self.bus.emit("wake")
                    self._set_state(LISTENING)
                continue

            result = utterance.push(frame)
            if result == "timeout":
                capturing = False
                self._followup_until = 0.0
                utterance.reset()
                self._set_state(IDLE)
            elif result == "done":
                capturing = False
                audio = utterance.audio()
                utterance.reset()
                self.capture.drain()
                self._followup_until = time.monotonic() + followup_seconds
                self._set_state(THINKING)   # evita re-activarse en el frame siguiente
                self._spawn(self._process_utterance(audio))

    async def _process_utterance(self, pcm: bytes) -> None:
        self._turn_task = asyncio.current_task()
        self._set_state(THINKING)
        sample_rate = int(self.cfg.get("audio.sample_rate", 16000))
        text = await self.stt.transcribe(pcm, sample_rate)
        if not text:
            self.bus.emit("log", level="info", message="No he entendido nada.")
            self._set_state(IDLE)
            return
        self.bus.emit("user", text=text)
        await self.handle_text(text, announce=False)

    # -- turno de conversación --------------------------------------------
    async def handle_text(self, text: str, announce: bool = True) -> None:
        """Procesa una petición (venga de la voz o del cuadro de texto)."""
        if self._busy.locked():
            self.bus.emit("log", level="info", message="Espera, estoy terminando...")
            return
        self._turn_task = asyncio.current_task()
        async with self._busy:
            if announce:
                self.bus.emit("user", text=text)
            self._set_state(THINKING)
            self.bus.emit("assistant_start")

            async def on_delta(chunk: str) -> None:
                self.bus.emit("assistant_delta", text=chunk)

            async def on_sentence(sentence: str) -> None:
                await self._speech_queue.put(sentence)
                if self.state != SPEAKING:
                    self._set_state(SPEAKING)

            async def on_tool(name: str, args) -> None:
                self.bus.emit("tool", name=name, args=args)

            try:
                reply = await self.brain.respond(text, on_delta=on_delta,
                                                 on_sentence=on_sentence,
                                                 on_tool=on_tool)
            except asyncio.CancelledError:
                log.info("turno cancelado: el usuario ha interrumpido")
                raise
            self.bus.emit("assistant_done", text=reply)
            await self._speech_queue.join()
            if self.state in (THINKING, SPEAKING):
                self._set_state(IDLE)

    def submit(self, text: str) -> None:
        """Lanza un turno desde la interfaz, recordando la tarea para poder cortarla."""
        self._turn_task = self._spawn(self.handle_text(text))

    async def _announce(self, message: str) -> None:
        """Habla por iniciativa propia (por ejemplo, al vencer un temporizador)."""
        self.bus.emit("assistant_done", text=message)
        await self._speech_queue.put(message)

    # -- salida de voz -----------------------------------------------------
    async def _speech_worker(self) -> None:
        """Sintetiza y reproduce frase a frase, en orden."""
        while True:
            sentence = await self._speech_queue.get()
            epoch = self._speech_epoch
            try:
                if sentence:
                    await self._speak(sentence, epoch)
            except Exception:  # noqa: BLE001 - un fallo de voz no debe tumbar el bucle
                log.exception("fallo al sintetizar")
            finally:
                self._speech_queue.task_done()

    async def _speak(self, text: str, epoch: int | None = None) -> None:
        speech = await self.tts.synthesize(text)
        if not speech:
            return
        if epoch is not None and epoch != self._speech_epoch:
            return  # nos interrumpieron mientras se sintetizaba esta frase
        if self.state != SPEAKING:
            self._set_state(SPEAKING)
        if speech.mime == "audio/pcm":
            played = await self.player.play(speech.data, speech.sample_rate)
            if played:
                return
            payload = pcm_to_wav(speech.data, speech.sample_rate)
            mime = "audio/wav"
        else:
            payload, mime = speech.data, speech.mime
        # Sin altavoces locales: que lo reproduzca el navegador.
        self.bus.emit("audio", mime=mime,
                      data=base64.b64encode(payload).decode("ascii"))

    # -- corte de la respuesta ---------------------------------------------
    def _cut_off(self, reason: str = "barge_in") -> None:
        """Calla a Jarvis y cancela el turno que estaba en marcha."""
        self.player.stop()
        self._speech_epoch += 1   # invalida lo que ya se estaba sintetizando
        self._drain_speech()
        task = self._turn_task
        if task is not None and not task.done() and task is not asyncio.current_task():
            task.cancel()
        self._turn_task = None
        self.bus.emit(reason)

    def _drain_speech(self) -> None:
        while not self._speech_queue.empty():
            try:
                self._speech_queue.get_nowait()
                self._speech_queue.task_done()
            except asyncio.QueueEmpty:
                break

    # -- controles desde la interfaz ---------------------------------------
    def activate(self) -> None:
        """Equivalente a decir la palabra de activación."""
        self._activate_event.set()
        if not self.voice_mode:
            self.bus.emit("log", level="warning", message="No hay micrófono disponible.")

    def interrupt(self) -> None:
        self._cut_off(reason="interrupted")
        self._followup_until = 0.0
        self._set_state(IDLE)

    def set_muted(self, muted: bool) -> None:
        self.muted = muted
        if self.capture is not None:
            self.capture.set_muted(muted)
        self.bus.emit("muted", value=muted)

    def reset_conversation(self) -> None:
        self.brain.reset()
        self.bus.emit("cleared")
