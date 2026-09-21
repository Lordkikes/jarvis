# Jarvis

Asistente de voz personal que se activa cuando le hablas, entiende, razona con
Claude, ejecuta acciones en tu equipo y responde en voz alta — con una interfaz
web moderna que reacciona a tu voz en tiempo real.

Todo el procesamiento de audio (wake word, transcripción y síntesis) puede correr
**en local y gratis**; solo el modelo de lenguaje sale a internet.

---

## 1. Cómo funciona

```
   🎤 micrófono
        │  frames PCM de 30 ms
        ▼
 ┌──────────────┐   "Hey Jarvis"   ┌──────────────┐   fin de frase   ┌──────────────┐
 │  WAKE WORD   │ ───────────────► │     VAD      │ ───────────────► │     STT      │
 │ openWakeWord │                  │  webrtcvad   │                  │   Whisper    │
 └──────────────┘                  └──────────────┘                  └──────┬───────┘
        ▲                                                                   │ texto
        │ vuelve a reposo                                                   ▼
 ┌──────┴───────┐   audio    ┌──────────────┐   frase a frase   ┌──────────────────┐
 │  ALTAVOCES   │ ◄───────── │     TTS      │ ◄──────────────── │   CLAUDE + HERR. │
 └──────────────┘            │    Piper     │                   │  clima, notas,   │
                             └──────────────┘                   │  temporizadores… │
                                                                └──────────────────┘
        └──────────────► eventos por WebSocket ──────────────► 🖥️  INTERFAZ WEB
```

Cinco piezas independientes. Cada una es intercambiable desde `config.yaml` sin
tocar código, y si una falta, Jarvis sigue funcionando en modo degradado
(por ejemplo: sin micrófono arranca en modo texto).

---

## 2. Qué necesitas

| Pieza | Opción recomendada | Alternativas | Coste |
|---|---|---|---|
| **Wake word** | [openWakeWord](https://github.com/dscripka/openWakeWord) — trae el modelo `hey_jarvis` | Picovoice Porcupine (más preciso, clave gratuita) | 0 € |
| **Detección de fin de frase** | [webrtcvad](https://github.com/wiseman/py-webrtcvad) | detección por energía (incluida) | 0 € |
| **Transcripción (STT)** | [faster-whisper](https://github.com/SYSTRAN/faster-whisper) local | Groq Whisper, OpenAI | 0 € local |
| **Cerebro (LLM)** | **Claude Opus 5** vía API de Anthropic | Sonnet 5 (más barato y rápido) | ~5 $/M tokens entrada |
| **Voz (TTS)** | [Piper](https://github.com/rhasspy/piper) local, voces en español | ElevenLabs (más natural, de pago), voz del sistema | 0 € local |
| **Interfaz** | FastAPI + WebSocket + canvas | — | 0 € |

**Requisitos previos**

- Python 3.10 o superior.
- Micrófono y altavoces (sin ellos funciona igual, pero por texto).
- Una clave de API de Anthropic: <https://console.anthropic.com> → *API Keys*.
- ~2 GB de disco si usas Whisper `small` + una voz de Piper.

---

## 3. Instalación paso a paso

### Paso 1 — Clona el proyecto

```bash
git clone https://github.com/Lordkikes/jarvis.git
cd jarvis
```

### Paso 2 — Instala las librerías del sistema

Son las que necesita el micrófono (PortAudio) y la voz de respaldo.

```bash
# Ubuntu / Debian / Raspberry Pi OS
sudo apt install portaudio19-dev python3-dev libsndfile1 espeak-ng

# macOS
brew install portaudio

# Windows: no hace falta nada, sounddevice trae PortAudio incluido
```

### Paso 3 — Entorno virtual y dependencias

```bash
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt      # núcleo: servidor + Claude
pip install -r requirements-voice.txt # voz: micrófono, Whisper, Piper
```

> En Linux/macOS puedes hacer los pasos 2 y 3 de una vez con `./scripts/setup.sh`.

### Paso 4 — Configura tu clave

```bash
cp .env.example .env
# edita .env y pega tu clave:  ANTHROPIC_API_KEY=sk-ant-...
```

### Paso 5 — Comprueba que todo está en su sitio

```bash
python scripts/doctor.py
```

Te dirá qué falta, qué claves ha encontrado y qué micrófonos ve el sistema
(anota el número del que quieras usar).

### Paso 6 — Arranca

```bash
python run.py
```

Se abre `http://127.0.0.1:8765` en tu navegador. La primera vez tarda un poco:
descarga el modelo de wake word, el de Whisper y la voz de Piper (una sola vez).

Di **«Hey Jarvis»**, espera a que el orbe se ponga verde y habla.

---

## 4. Uso

| Acción | Cómo |
|---|---|
| Activar por voz | Di «Hey Jarvis» |
| Activar a mano | Botón del micrófono o **barra espaciadora** |
| Escribir en vez de hablar | Cuadro de texto inferior (o pulsa `/`) |
| Interrumpir a Jarvis | Botón de stop o **Esc** |
| Silenciar el micrófono | Botón del micro arriba o tecla **M** |
| Mostrar/ocultar la conversación | Botón de las tres rayas |

El orbe cambia de color según el estado: **azul** en reposo, **verde**
escuchándote, **ámbar** pensando, **cian** hablando.

Tras responder, Jarvis sigue escuchando 8 segundos sin necesidad de repetir la
palabra de activación (`wake.followup_seconds`), para poder encadenar frases.

### Lo que ya sabe hacer

- Decir la fecha y la hora.
- Consultar el tiempo de cualquier ciudad (Open-Meteo, sin clave).
- Poner temporizadores y avisarte en voz alta cuando vencen.
- Guardar y leer notas.
- Recordar datos tuyos entre sesiones (`data/memory.json`).
- Informar del estado del equipo (CPU, memoria, disco).
- Abrir webs y aplicaciones.
- Buscar en internet (búsqueda web del lado del servidor de Anthropic).

---

## 5. Estructura del código

```
jarvis/
├── run.py                  # punto de entrada
├── config.yaml             # toda la configuración
├── jarvis/
│   ├── pipeline.py         # ★ orquestador: une las cinco piezas
│   ├── config.py           # config.yaml + variables de entorno
│   ├── bus.py              # bus de eventos hacia la interfaz
│   ├── audio/
│   │   ├── capture.py      # micrófono -> frames
│   │   ├── vad.py          # ¿ha terminado de hablar?
│   │   ├── wakeword.py     # "Hey Jarvis"
│   │   └── player.py       # altavoces (con interrupción)
│   ├── stt/                # whisper_local.py | cloud.py
│   ├── llm/
│   │   ├── claude.py       # ★ streaming + bucle de herramientas
│   │   └── tools.py        # ★ lo que Jarvis sabe hacer
│   ├── tts/                # piper_tts.py | elevenlabs_tts.py | system_tts.py
│   └── server/
│       ├── app.py          # FastAPI + WebSocket
│       └── static/         # index.html, styles.css, app.js, orb.js
├── scripts/                # setup.sh, doctor.py
└── tests/                  # python -m unittest discover -s tests
```

Los tres ficheros marcados con ★ son el 80 % de la lógica.

### Detalles que hacen que se sienta rápido

- **Respuesta por streaming**: Claude emite texto token a token; en cuanto se
  completa una frase se manda al TTS, así Jarvis empieza a hablar antes de haber
  terminado de pensar (`Brain.respond` → `on_sentence`).
- **Caché de prompt**: el bloque de personalidad se marca con `cache_control`,
  de modo que no se vuelve a pagar en cada turno.
- **`effort: low`** en la llamada al modelo: prioriza latencia, que es lo que
  importa en una conversación hablada. Súbelo a `high` para tareas complejas.
- **El micrófono se ignora mientras Jarvis habla**, para que no se escuche a
  sí mismo.

---

## 6. Personalización

### Cambiar la personalidad

`config.yaml` → `assistant.persona`. Es el *system prompt*. Mantén la instrucción
de respuestas cortas: lo que se lee bien en pantalla se hace eterno en voz alta.

### Cambiar la voz

```yaml
tts:
  provider: piper
  voice: es_ES-davefx-medium    # otras: es_MX-claude-high, es_ES-sharvard-medium
```

El catálogo está en <https://huggingface.co/rhasspy/piper-voices>. Se descarga sola.

Para la voz más natural (de pago), pon `provider: elevenlabs` y añade
`ELEVENLABS_API_KEY` en `.env`.

### Cambiar el modelo

```yaml
llm:
  model: claude-sonnet-5   # más barato y rápido que Opus 5
  effort: low              # low | medium | high | xhigh | max
```

### Equipo modesto (portátil viejo, Raspberry Pi)

```yaml
stt:
  provider: groq      # transcribe en la nube, ~1 s, requiere GROQ_API_KEY
llm:
  model: claude-sonnet-5
```

O baja el modelo de Whisper a `tiny`/`base`.

### Añadir una herramienta nueva

Dos pasos en `jarvis/llm/tools.py`:

```python
# 1) Declara el esquema dentro de SPECS
{
    "name": "encender_luz",
    "description": "Enciende o apaga una luz de casa.",
    "input_schema": {
        "type": "object",
        "properties": {
            "habitacion": {"type": "string"},
            "encendida": {"type": "boolean"},
        },
        "required": ["habitacion", "encendida"],
    },
}

# 2) Implementa el método (puede ser async)
def _tool_encender_luz(self, args: dict) -> str:
    requests.post("http://homeassistant.local/api/...", json=args)
    return f"Luz de {args['habitacion']} {'encendida' if args['encendida'] else 'apagada'}."
```

No hay que registrar nada más: el modelo la ve en el siguiente turno.

### Usar otro micrófono

```yaml
audio:
  input_device: 3     # el número que te dio scripts/doctor.py
```

---

## 7. Problemas frecuentes

| Síntoma | Causa y solución |
|---|---|
| `sounddevice no disponible` | Falta PortAudio (paso 2) o `pip install sounddevice` |
| No reacciona a «Hey Jarvis» | Baja `wake.threshold` a 0.35; comprueba el micro con `doctor.py`; en Windows revisa los permisos de micrófono |
| Se activa solo | Sube `wake.threshold` a 0.6-0.7 |
| Corta mientras hablas | Sube `audio.silence_ms` a 1200 y baja `audio.vad_aggressiveness` a 1 |
| Transcribe mal | Usa un modelo mayor (`stt.model: medium`) o pásate a `provider: groq` |
| Tarda mucho en responder | `stt.model: base`, `llm.model: claude-sonnet-5`, `llm.effort: low` |
| `invalid x-api-key` | La clave de `.env` no es válida o no se cargó: revísala con `doctor.py` |
| Se oye a sí mismo | Usa auriculares, o baja el volumen: el eco puede disparar el wake word |
| La voz suena en el navegador, no en los altavoces | No hay salida de audio local; es el respaldo automático. Revisa `audio.output_device` |

---

## 8. Costes

Con Whisper y Piper en local solo pagas el modelo de lenguaje. Un turno típico
de conversación (system prompt cacheado + 10 turnos de contexto + respuesta
corta) ronda los **1.500 tokens de entrada y 100 de salida**:

| Modelo | Coste aproximado por turno | 100 turnos al día |
|---|---|---|
| Claude Opus 5 | ~0,010 $ | ~1 $/día |
| Claude Sonnet 5 | ~0,004 $ | ~0,4 $/día |

La caché del prompt reduce bastante la entrada en conversaciones largas.

---

## 9. Siguientes pasos

Ideas para seguir construyendo, más o menos por dificultad:

1. **Barge-in real**: interrumpirle hablando encima (requiere cancelación de eco).
2. **Más herramientas**: domótica, calendario, correo, control de música.
3. **Memoria semántica**: sustituir `memory.json` por una base vectorial.
4. **Ejecutable de escritorio**: empaquetar la interfaz con Tauri o pywebview.
5. **Acceso desde el móvil**: exponer el servidor en la red local (`server.host: 0.0.0.0`) — hazlo solo en redes de confianza, no hay autenticación.

---

## 10. Nota sobre privacidad

- El audio nunca sale del equipo si usas `faster-whisper` + `piper`.
- Lo que sí viaja a la API de Anthropic es el **texto** de la conversación.
- Las notas y la memoria se guardan en texto plano en `data/`.
- `tools.allow_system: false` desactiva abrir aplicaciones y leer el estado del equipo.

## Licencia

MIT.
