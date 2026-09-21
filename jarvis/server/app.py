"""API web de Jarvis (FastAPI + WebSocket)."""
from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ..bus import EventBus
from ..config import load_config
from ..pipeline import Jarvis

log = logging.getLogger("jarvis.server")
STATIC = Path(__file__).parent / "static"


def create_app(config_path: str | None = None) -> FastAPI:
    cfg = load_config(config_path)
    bus = EventBus()
    jarvis = Jarvis(cfg, bus)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):  # noqa: ANN001, ARG001
        await jarvis.start()
        yield
        await jarvis.stop()

    app = FastAPI(title="Jarvis", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    @app.get("/api/config")
    async def get_config() -> dict:
        return {
            "assistant": cfg.section("assistant") | {"persona": None},
            "state": jarvis.state,
            "voice": jarvis.voice_mode,
            "model": jarvis.brain.model,
            "stt": getattr(jarvis.stt, "name", "none"),
            "tts": getattr(jarvis.tts, "name", "none"),
            "wake": getattr(jarvis.wakeword, "name", "none"),
        }

    @app.websocket("/ws")
    async def websocket_endpoint(socket: WebSocket) -> None:
        await socket.accept()
        queue = bus.subscribe()
        await socket.send_json({
            "type": "hello",
            "name": cfg.get("assistant.name", "Jarvis"),
            "greeting": cfg.get("assistant.greeting", ""),
            "voice": jarvis.voice_mode,
            "wake": getattr(jarvis.wakeword, "name", "none"),
            "model": jarvis.brain.model,
            "stt": getattr(jarvis.stt, "name", "none"),
            "tts": getattr(jarvis.tts, "name", "none"),
        })
        await socket.send_json(bus.last_state)

        async def pump() -> None:
            while True:
                event = await queue.get()
                await socket.send_json(event)

        pump_task = asyncio.create_task(pump())
        try:
            while True:
                command = await socket.receive_json()
                await handle_command(jarvis, command)
        except WebSocketDisconnect:
            pass
        except Exception:  # noqa: BLE001
            log.exception("error en el WebSocket")
        finally:
            pump_task.cancel()
            bus.unsubscribe(queue)

    app.state.jarvis = jarvis
    app.state.bus = bus
    app.state.config = cfg
    return app


async def handle_command(jarvis: Jarvis, command: dict) -> None:
    action = command.get("type")
    if action == "text":
        text = (command.get("text") or "").strip()
        if text:
            asyncio.create_task(jarvis.handle_text(text))
    elif action == "activate":
        jarvis.activate()
    elif action == "interrupt":
        jarvis.interrupt()
    elif action == "mute":
        jarvis.set_muted(bool(command.get("value")))
    elif action == "clear":
        jarvis.reset_conversation()
    elif action == "ping":
        pass
    else:
        log.debug("comando desconocido: %s", action)
