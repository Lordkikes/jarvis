"""Bus de eventos en memoria: el pipeline publica, la interfaz se suscribe."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

log = logging.getLogger("jarvis.bus")


class EventBus:
    def __init__(self, queue_size: int = 256):
        self._subscribers: set[asyncio.Queue] = set()
        self._queue_size = queue_size
        self.last_state: dict[str, Any] = {"type": "state", "state": "idle"}

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=self._queue_size)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    def emit(self, type_: str, **payload: Any) -> None:
        """Publica sin bloquear. Si un cliente va lento, descarta su evento."""
        event = {"type": type_, **payload}
        if type_ == "state":
            self.last_state = event
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                log.debug("cola llena, evento descartado: %s", type_)
