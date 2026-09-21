"""Remuestreo lineal de PCM int16 mono.

Solo hace falta para alinear el audio que sale por los altavoces (22 050 Hz en
Piper) con la frecuencia a la que trabaja el cancelador de eco (16 000 Hz).
"""
from __future__ import annotations

import struct

try:  # pragma: no cover - ruta rápida cuando numpy está disponible
    import numpy as _np
except ImportError:  # pragma: no cover
    _np = None


def resample_pcm(pcm: bytes, src_rate: int, dst_rate: int) -> bytes:
    """Convierte PCM int16 mono de una frecuencia a otra."""
    if src_rate == dst_rate or not pcm:
        return pcm

    source_len = len(pcm) // 2
    target_len = max(1, round(source_len * dst_rate / src_rate))
    # El paso es la razón exacta entre frecuencias, no (n-1)/(m-1): así cada
    # bloque se convierte igual que el anterior y no se estira al final, que
    # descolocaría la referencia del cancelador de eco.
    step = src_rate / dst_rate
    last = source_len - 1

    if _np is not None:
        source = _np.frombuffer(pcm, dtype=_np.int16)
        positions = _np.minimum(_np.arange(target_len) * step, last)
        values = _np.interp(positions, _np.arange(source_len), source)
        return values.astype(_np.int16).tobytes()

    source = struct.unpack("<%dh" % source_len, pcm)
    out = []
    for i in range(target_len):
        position = min(i * step, last)
        left = int(position)
        right = min(left + 1, last)
        weight = position - left
        out.append(int(source[left] * (1 - weight) + source[right] * weight))
    return struct.pack("<%dh" % target_len, *out)
