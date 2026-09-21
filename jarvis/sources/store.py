"""Índice local en SQLite con búsqueda de texto completo.

Es lo que consultan las herramientas del asistente. La ingesta escribe aquí y
las preguntas se responden desde aquí: sin esperas de red en mitad de una
conversación hablada.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from . import Item

log = logging.getLogger("jarvis.sources.store")

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id         TEXT PRIMARY KEY,
    source     TEXT NOT NULL,
    kind       TEXT,
    title      TEXT,
    body       TEXT,
    author     TEXT,
    url        TEXT,
    created_at TEXT,
    synced_at  TEXT,
    meta       TEXT
);
CREATE INDEX IF NOT EXISTS idx_items_source ON items(source, created_at DESC);
"""

FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5(
    id UNINDEXED, title, body,
    tokenize = "unicode61 remove_diacritics 2"
);
"""

# Para la búsqueda: nos quedamos con palabras y descartamos la puntuación, que
# en FTS5 son operadores. Lo que llega por voz trae interrogaciones y comas.
WORD = re.compile(r"[^\W_]+", re.UNICODE)

# Las preguntas habladas son frases enteras («¿qué hice con el barge-in?»).
# Exigir todas las palabras no devolvería nunca nada, así que las vacías fuera.
STOPWORDS = {
    "que", "qué", "como", "cómo", "cuando", "cuándo", "donde", "dónde", "quien",
    "quién", "cual", "cuál", "por", "para", "con", "sin", "del", "las", "los",
    "una", "unos", "unas", "the", "and", "hice", "hay", "fue", "era", "son",
    "está", "esta", "este", "esto", "eso", "algo", "sobre", "dime", "cuentame",
    "cuéntame", "busca", "buscar", "mis", "mi", "me", "te", "se", "lo", "la",
    "el", "en", "de", "al", "un", "es", "ha", "he",
}


def _terms(query: str) -> list[str]:
    words = [w.lower() for w in WORD.findall(query or "")]
    útiles = [w for w in words if len(w) >= 3 and w not in STOPWORDS]
    # Si la pregunta era toda palabras vacías, mejor buscar con ellas que no buscar.
    return útiles or [w for w in words if len(w) >= 3]


class Store:
    """Almacén de items. Seguro entre hilos: la ingesta corre aparte."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self.fts = self._try_fts()
        self._conn.commit()

    def _try_fts(self) -> bool:
        try:
            self._conn.executescript(FTS_SCHEMA)
            return True
        except sqlite3.OperationalError as exc:
            log.warning("SQLite sin FTS5 (%s); la búsqueda usará LIKE", exc)
            return False

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- escritura ---------------------------------------------------------
    def upsert(self, items: list[Item]) -> int:
        """Inserta o actualiza. Devuelve cuántos items nuevos o cambiados hay."""
        if not items:
            return 0
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        changed = 0
        with self._lock:
            for item in items:
                previous = self._conn.execute(
                    "SELECT title, body FROM items WHERE id = ?", (item.id,)
                ).fetchone()
                if previous and previous["title"] == item.title and previous["body"] == item.body:
                    continue
                self._conn.execute(
                    """INSERT INTO items
                       (id, source, kind, title, body, author, url, created_at, synced_at, meta)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(id) DO UPDATE SET
                         title=excluded.title, body=excluded.body, kind=excluded.kind,
                         author=excluded.author, url=excluded.url,
                         created_at=excluded.created_at, synced_at=excluded.synced_at,
                         meta=excluded.meta""",
                    (item.id, item.source, item.kind, item.title, item.body, item.author,
                     item.url, item.created_at, now, json.dumps(item.meta, ensure_ascii=False)),
                )
                if self.fts:
                    self._conn.execute("DELETE FROM items_fts WHERE id = ?", (item.id,))
                    self._conn.execute(
                        "INSERT INTO items_fts (id, title, body) VALUES (?, ?, ?)",
                        (item.id, item.title, item.body),
                    )
                changed += 1
            self._conn.commit()
        return changed

    # -- lectura -----------------------------------------------------------
    def search(self, query: str, source: str | None = None, limit: int = 8) -> list[dict]:
        terms = _terms(query)
        if not terms:
            return self.recent(source=source, limit=limit)

        with self._lock:
            if self.fts:
                # Primero exigiendo todas las palabras; si no sale nada, con
                # cualquiera de ellas y que bm25 ordene por relevancia.
                for operador in ("AND", "OR"):
                    match = f" {operador} ".join(f'"{term}"' for term in terms)
                    sql = """SELECT i.* FROM items_fts f JOIN items i ON i.id = f.id
                             WHERE items_fts MATCH ?"""
                    params: list = [match]
                    if source:
                        sql += " AND i.source = ?"
                        params.append(source)
                    sql += " ORDER BY bm25(items_fts), i.created_at DESC LIMIT ?"
                    params.append(limit)
                    try:
                        rows = self._conn.execute(sql, params).fetchall()
                    except sqlite3.OperationalError as exc:
                        log.debug("consulta FTS fallida (%s); uso LIKE", exc)
                        break
                    if rows or len(terms) == 1:
                        # La pasada OR encuentra cosas que solo comparten una
                        # palabra: se marcan para que Jarvis no las presente
                        # como si respondieran a la pregunta.
                        return [dict(row) | {"parcial": operador == "OR"}
                                for row in rows]

            # Respaldo sin FTS: todas las palabras deben aparecer.
            sql = "SELECT * FROM items WHERE 1=1"
            params = []
            for term in terms:
                sql += " AND (title LIKE ? OR body LIKE ?)"
                params += [f"%{term}%", f"%{term}%"]
            if source:
                sql += " AND source = ?"
                params.append(source)
            sql += " ORDER BY created_at DESC LIMIT ?"
            params.append(limit)
            return [dict(row) for row in self._conn.execute(sql, params).fetchall()]

    def recent(self, source: str | None = None, limit: int = 8) -> list[dict]:
        sql = "SELECT * FROM items"
        params: list = []
        if source:
            sql += " WHERE source = ?"
            params.append(source)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            return [dict(row) for row in self._conn.execute(sql, params).fetchall()]

    def counts(self) -> dict[str, int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT source, COUNT(*) AS n FROM items GROUP BY source").fetchall()
        return {row["source"]: row["n"] for row in rows}
