"""Cerebro: Claude con streaming y uso de herramientas."""
from __future__ import annotations

import logging
import platform
import re
from datetime import datetime

import anthropic

log = logging.getLogger("jarvis.llm")

SENTENCE_END = re.compile(r"[.!?…]['\")\]]?\s|[\n]{1,2}")


class Brain:
    """Mantiene la conversación y decide cuándo usar herramientas."""

    def __init__(self, cfg, toolbox):
        self.cfg = cfg
        self.toolbox = toolbox
        self.client = anthropic.AsyncAnthropic()
        self.model = cfg.get("llm.model", "claude-opus-5")
        self.effort = cfg.get("llm.effort", "low")
        self.max_tokens = int(cfg.get("llm.max_tokens", 2048))
        self.memory_turns = int(cfg.get("llm.memory_turns", 12))
        self.messages: list[dict] = []

    # -- contexto ----------------------------------------------------------
    def _system_prompt(self) -> list[dict]:
        persona = self.cfg.get("assistant.persona", "Eres un asistente de voz.")
        name = self.cfg.get("assistant.name", "Jarvis")
        blocks = [{
            "type": "text",
            # Bloque estable: se cachea entre peticiones para ahorrar tokens.
            "text": f"{persona}\n\nTe llamas {name}.",
            "cache_control": {"type": "ephemeral"},
        }]
        context = [f"Fecha y hora al iniciar el turno: "
                   f"{datetime.now().astimezone().strftime('%Y-%m-%d %H:%M %Z')}",
                   f"Sistema operativo del usuario: {platform.system()}"]
        memories = self.toolbox.memories()
        if memories:
            context.append("Datos que recuerdas del usuario: " + "; ".join(memories))
        blocks.append({"type": "text", "text": "\n".join(context)})
        return blocks

    def reset(self) -> None:
        self.messages.clear()

    def _trim(self) -> None:
        """Conserva los últimos turnos sin romper pares tool_use/tool_result."""
        limit = self.memory_turns * 2
        if len(self.messages) <= limit:
            return
        cut = len(self.messages) - limit
        while cut < len(self.messages):
            message = self.messages[cut]
            content = message.get("content")
            is_tool_result = isinstance(content, list) and any(
                getattr(block, "type", None) == "tool_result"
                or (isinstance(block, dict) and block.get("type") == "tool_result")
                for block in content
            )
            if message["role"] == "user" and not is_tool_result:
                break
            cut += 1
        self.messages = self.messages[cut:]

    # -- turno de conversación --------------------------------------------
    async def respond(self, user_text: str, on_delta=None, on_sentence=None,
                      on_tool=None) -> str:
        """Procesa un turno completo. Devuelve el texto final ya dicho."""
        self.messages.append({"role": "user", "content": user_text})
        self._trim()

        tools = self.toolbox.definitions()
        spoken: list[str] = []
        buffer = ""

        async def flush(force: bool = False) -> None:
            """Envía frases completas al TTS para que empiece a hablar antes."""
            nonlocal buffer
            if on_sentence is None:
                return
            while True:
                match = SENTENCE_END.search(buffer)
                if not match:
                    break
                sentence, buffer = buffer[:match.end()].strip(), buffer[match.end():]
                if sentence:
                    await on_sentence(sentence)
            if force and buffer.strip():
                await on_sentence(buffer.strip())
                buffer = ""

        for _ in range(8):  # tope de vueltas del bucle de herramientas
            try:
                async with self.client.messages.stream(
                    model=self.model,
                    max_tokens=self.max_tokens,
                    system=self._system_prompt(),
                    messages=self.messages,
                    tools=tools,
                    output_config={"effort": self.effort},
                ) as stream:
                    async for event in stream:
                        if event.type == "text":
                            buffer += event.text
                            if on_delta is not None:
                                await on_delta(event.text)
                            await flush()
                    response = await stream.get_final_message()
            except anthropic.APIStatusError as exc:
                log.error("error de la API (%s): %s", exc.status_code, exc)
                return "Tengo un problema para conectarme con el modelo."
            except anthropic.APIConnectionError as exc:
                log.error("sin conexión con la API: %s", exc)
                return "No tengo conexión ahora mismo."

            text = "".join(block.text for block in response.content
                           if block.type == "text")
            spoken.append(text)
            self.messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "refusal":
                await flush(force=True)
                return "Prefiero no responder a eso."

            if response.stop_reason == "pause_turn":
                continue  # la búsqueda web se reanuda enviando el turno de vuelta

            tool_uses = [block for block in response.content if block.type == "tool_use"]
            if not tool_uses:
                await flush(force=True)
                return "\n".join(part for part in spoken if part).strip()

            results = []
            for block in tool_uses:
                if on_tool is not None:
                    await on_tool(block.name, block.input)
                output = await self.toolbox.run(block.name, dict(block.input or {}))
                log.info("herramienta %s -> %s", block.name, str(output)[:120])
                results.append({"type": "tool_result", "tool_use_id": block.id,
                                "content": str(output)})
            self.messages.append({"role": "user", "content": results})

        await flush(force=True)
        return "\n".join(part for part in spoken if part).strip() or \
            "No he podido completar la petición."
