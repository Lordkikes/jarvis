"""Cliente HTTP compartido.

El SDK de Anthropic 1.x se apoya en httpx2; si no está, usamos httpx clásico.
"""
from __future__ import annotations

try:  # pragma: no cover - depende del entorno
    import httpx2 as httpx
except ImportError:  # pragma: no cover
    import httpx  # type: ignore[no-redef]

AsyncClient = httpx.AsyncClient
__all__ = ["httpx", "AsyncClient"]
