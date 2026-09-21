"""Captura, detección de voz, wake word y reproducción de audio."""


class AudioUnavailable(RuntimeError):
    """No hay micrófono/altavoz utilizable (falta sounddevice o dispositivo)."""
