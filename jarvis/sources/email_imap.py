"""Correo por IMAP, en solo lectura.

IMAP en vez de la API de Gmail a propósito: `imaplib` viene con Python, no hay
OAuth que montar y funciona igual con Gmail, Fastmail o el correo del trabajo.
Con Gmail necesitas una contraseña de aplicación (requiere verificación en dos
pasos); el buzón se abre en modo lectura y se usa PEEK, así que nada se marca
como leído por culpa de Jarvis.
"""
from __future__ import annotations

import email
import imaplib
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from email import policy
from email.utils import parsedate_to_datetime

from . import Item

log = logging.getLogger("jarvis.sources.email")

MAX_BODY = 4000
BATCH = 25
TAG = re.compile(r"<[^>]+>")
BLANKS = re.compile(r"\n{3,}")


def _plain_text(message) -> str:
    """Texto legible del correo, prefiriendo la parte de texto plano."""
    try:
        part = message.get_body(preferencelist=("plain", "html"))
    except Exception:  # noqa: BLE001 - correos malformados los hay a diario
        part = None
    if part is None:
        return ""

    try:
        content = part.get_content()
    except Exception:  # noqa: BLE001
        payload = part.get_payload(decode=True) or b""
        content = payload.decode(part.get_content_charset() or "utf-8", errors="replace")

    if part.get_content_type() == "text/html":
        import html

        content = html.unescape(TAG.sub(" ", content))
    return BLANKS.sub("\n\n", content).strip()


def parse_message(raw: bytes, folder: str = "INBOX", uid: str = "") -> Item | None:
    """Convierte un correo crudo en un item indexable. Sin red: es testeable."""
    try:
        message = email.message_from_bytes(raw, policy=policy.default)
    except Exception as exc:  # noqa: BLE001
        log.debug("correo ilegible (%s)", exc)
        return None

    subject = str(message.get("Subject", "") or "(sin asunto)").strip()
    sender = str(message.get("From", "") or "").strip()
    message_id = str(message.get("Message-ID", "") or "").strip()

    when = ""
    if raw_date := message.get("Date"):
        try:
            when = parsedate_to_datetime(str(raw_date)).astimezone(
                timezone.utc).isoformat(timespec="seconds")
        except (TypeError, ValueError):
            when = ""

    return Item(
        id=f"email:{message_id or f'{folder}:{uid}'}",
        source="correo",
        kind="correo",
        title=subject[:200],
        body=_plain_text(message)[:MAX_BODY],
        author=sender,
        created_at=when or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        meta={
            "carpeta": folder,
            "para": str(message.get("To", "") or "")[:200],
            "responder_a": str(message.get("Reply-To", "") or "")[:200],
        },
    )


class EmailSource:
    name = "correo"

    def __init__(self, host: str, port: int = 993, user: str = "",
                 folder: str = "INBOX", days: int = 7, limit: int = 200):
        password = os.getenv("JARVIS_EMAIL_PASSWORD", "")
        if not (host and user and password):
            raise RuntimeError(
                "faltan datos del correo: sources.email.host/user en config.yaml "
                "y JARVIS_EMAIL_PASSWORD en .env")
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.folder = folder
        self.days = days
        self.limit = limit

    def sync(self, store) -> int:
        since = (datetime.now(timezone.utc) - timedelta(days=self.days)).strftime("%d-%b-%Y")
        items: list[Item] = []

        with imaplib.IMAP4_SSL(self.host, self.port) as imap:
            imap.login(self.user, self.password)
            # readonly: el buzón no se toca, ni siquiera las marcas de leído.
            imap.select(self.folder, readonly=True)
            status, data = imap.search(None, f'(SINCE {since})')
            if status != "OK":
                log.warning("búsqueda IMAP fallida: %s", status)
                return 0

            ids = data[0].split()[-self.limit:]
            for start in range(0, len(ids), BATCH):
                lote = b",".join(ids[start:start + BATCH])
                # PEEK para no marcar como leído.
                status, chunks = imap.fetch(lote.decode(), "(BODY.PEEK[])")
                if status != "OK":
                    continue
                for chunk in chunks:
                    if not (isinstance(chunk, tuple) and len(chunk) > 1):
                        continue
                    uid = chunk[0].split()[0].decode(errors="replace")
                    if item := parse_message(chunk[1], self.folder, uid):
                        items.append(item)

        changed = store.upsert(items)
        log.info("correo: %d mensajes leídos, %d nuevos", len(items), changed)
        return changed
