"""Sesiones de Claude Code: qué estuviste construyendo y cuándo.

Claude Code guarda cada sesión en ~/.claude/projects/<proyecto>/<id>.jsonl, una
línea JSON por evento. Lo que de verdad resume una sesión son *tus* mensajes:
lo que pediste. Eso es lo que se indexa, junto con el proyecto y la rama.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from . import Item

log = logging.getLogger("jarvis.sources.claude_code")

MAX_BODY = 12000


def _text_of(message: dict) -> str:
    """El texto de un mensaje, venga como cadena o como bloques."""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(block.get("text", "") for block in content
                         if isinstance(block, dict) and block.get("type") == "text")
    return ""


class ClaudeCodeSource:
    name = "claude_code"

    def __init__(self, path: str = "~/.claude/projects", max_sessions: int = 200):
        self.root = Path(path).expanduser()
        self.max_sessions = max_sessions

    def sync(self, store) -> int:
        if not self.root.exists():
            log.info("no hay sesiones de Claude Code en %s", self.root)
            return 0

        files = sorted(self.root.glob("*/*.jsonl"),
                       key=lambda p: p.stat().st_mtime, reverse=True)[:self.max_sessions]
        items = [item for path in files if (item := self._read_session(path))]
        changed = store.upsert(items)
        log.info("claude_code: %d sesiones leídas, %d actualizadas", len(items), changed)
        return changed

    def _read_session(self, path: Path) -> Item | None:
        prompts: list[str] = []
        replies: list[str] = []
        cwd = branch = session_id = ""
        first_ts = last_ts = ""
        tools: set[str] = set()
        models: set[str] = set()

        try:
            with path.open(encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue  # una línea a medio escribir no invalida la sesión

                    cwd = row.get("cwd") or cwd
                    branch = row.get("gitBranch") or branch
                    session_id = row.get("sessionId") or session_id
                    stamp = row.get("timestamp") or ""
                    if stamp:
                        first_ts = first_ts or stamp
                        last_ts = stamp

                    message = row.get("message")
                    if row.get("isSidechain") or not isinstance(message, dict):
                        continue  # los subagentes no cuentan como lo que pediste

                    if row.get("type") == "user" and isinstance(message.get("content"), str):
                        # content en texto plano = lo escribiste tú; una lista
                        # serían resultados de herramientas.
                        prompts.append(message["content"].strip())
                    elif row.get("type") == "assistant":
                        if model := message.get("model"):
                            models.add(model)
                        for block in message.get("content") or []:
                            if not isinstance(block, dict):
                                continue
                            if block.get("type") == "tool_use":
                                tools.add(str(block.get("name", "")))
                            elif block.get("type") == "text" and block.get("text"):
                                replies.append(block["text"].strip())
        except OSError as exc:
            log.debug("no se pudo leer %s (%s)", path, exc)
            return None

        if not prompts:
            return None

        project = Path(cwd).name if cwd else path.parent.name
        title = f"{project}: {prompts[0][:110]}"
        body = "\n\n".join(["PETICIONES:", *prompts, "", "RESPUESTAS:", *replies])[:MAX_BODY]

        return Item(
            id=f"claude_code:{session_id or path.stem}",
            source=self.name,
            kind="sesión",
            title=title,
            body=body,
            author="",
            url=str(path),
            created_at=last_ts or datetime.fromtimestamp(
                path.stat().st_mtime, timezone.utc).isoformat(timespec="seconds"),
            meta={
                "proyecto": project,
                "ruta": cwd,
                "rama": branch,
                "peticiones": len(prompts),
                "herramientas": sorted(tools)[:12],
                "modelos": sorted(models),
                "inicio": first_ts,
            },
        )
