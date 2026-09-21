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
| **Cancelación de eco** | [webrtc-audio-processing](https://github.com/xiongyihui/python-webrtc-audio-processing) | ninguna (solo umbral por energía) | 0 € |
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
sudo apt install portaudio19-dev python3-dev libsndfile1 espeak-ng \
                 swig build-essential

# macOS
brew install portaudio swig

# Windows: sounddevice trae PortAudio incluido. Para la cancelación de eco
# hacen falta swig y las Build Tools de Visual Studio.
```

`swig` y el compilador solo son necesarios para la cancelación de eco, que se
compila al instalarse. Sin ella Jarvis funciona igual (ver *barge-in*).

### Paso 3 — Entorno virtual y dependencias

```bash
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt       # núcleo: servidor + Claude
pip install -r requirements-voice.txt # voz: micrófono, Whisper, Piper
pip install -r requirements-aec.txt   # cancelación de eco (se compila)
```

El AEC va en su propio fichero a propósito: si la compilación falla en tu
sistema, el resto de Jarvis se instala igual.

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
| Interrumpir a Jarvis | **Háblale encima** (barge-in), botón de stop o **Esc** |
| Silenciar el micrófono | Botón del micro arriba o tecla **M** |
| Mostrar/ocultar la conversación | Botón de las tres rayas |

El orbe cambia de color según el estado: **azul** en reposo, **verde**
escuchándote, **ámbar** pensando, **cian** hablando.

Tras responder, Jarvis sigue escuchando 8 segundos sin necesidad de repetir la
palabra de activación (`wake.followup_seconds`), para poder encadenar frases.

### Interrumpirle hablando (barge-in)

No hace falta esperar a que termine: empieza a hablar y se calla a media frase,
descarta lo que le quedaba por decir y se pone a escucharte. Lo que ya había
dicho queda en el contexto marcado como interrumpido, así que «no, mejor en
inglés» se entiende sin repetir nada.

El problema difícil aquí es el **eco**: el micrófono también oye a Jarvis. Se
ataca en dos capas.

**Capa 1 — cancelación de eco (AEC).** El módulo de audio de WebRTC recibe las
dos señales: la *referencia* (lo que el reproductor está mandando a los
altavoces) y la del micrófono. Estima el camino acústico de la sala y resta el
eco. Todo lo que viene después —VAD, wake word, barge-in y la propia
transcripción— trabaja ya sobre audio limpio. En la simulación de las pruebas
(`tests/test_aec.py`) el eco residual baja del orden de **30 dB** y la
diferencia entre tu voz y lo que queda del eco se multiplica por **siete**. Por
eso, cuando el AEC está activo, el umbral de interrupción baja solo
(`barge_in.echo_gain_aec`, 0.6 en vez de 1.4): puedes cortarle hablando normal.

**Capa 2 — el umbral por energía**, que sigue ahí como red de seguridad y es lo
único que queda si no instalas el AEC: tu voz tiene que superar al nivel del
audio que suena en ese instante (`AudioPlayer.level`) por un margen
(`echo_gain`) durante varios frames seguidos (`frames`), y el VAD tiene que
confirmar que es voz y no un portazo. Los frames inmediatamente anteriores se
conservan (`preroll_frames`) y se usan como principio de tu nueva frase, para
que no se pierdan las primeras sílabas.

```yaml
aec:
  enabled: true
  mode: full           # full (recomendado) | mobile (más ligero, menos eficaz)
  suppression: 0       # 0..2; subirlo se come tu voz cuando hablas encima
  noise_suppression: 1 # 0..3, o -1 para desactivarlo
  delay_ms: null       # null = medido de los propios dispositivos

barge_in:
  enabled: true
  mode: voice          # voice | wakeword | off
  threshold: 0.10      # nivel mínimo de voz para cortarle
  echo_gain: 1.4       # margen sobre el eco cuando NO hay AEC
  echo_gain_aec: 0.6   # margen cuando el AEC está activo
  frames: 5            # frames seguidos que lo confirman (~150 ms)
  guard_ms: 350        # margen tras empezar a responder
  preroll_frames: 10   # audio previo que se conserva
```

Sobre `aec.suppression`: es la supresión *adicional* posterior al filtro. El
que cancela de verdad es el filtro adaptativo; subir este número apenas mejora
el eco residual (15 frente a 18 de RMS en la simulación) y en cambio atenúa tu
voz durante el double-talk a menos de la mitad (1169 → 533). Súbelo solo si de
verdad se cuela eco.

El retardo importa: el AEC necesita saber cuánto tarda el sonido en salir por
el altavoz y volver por el micro. Se mide de la latencia que declaran los
propios dispositivos; si tu equipo miente (típico en Bluetooth), fíjalo a mano
con `aec.delay_ms`.

| Si te pasa esto | Ajusta |
|---|---|
| Se corta solo al oírse a sí mismo | Comprueba primero que el AEC está activo (`scripts/doctor.py`); luego sube `echo_gain`/`echo_gain_aec` y `threshold` a 0.15 |
| Se cuela eco aunque el AEC esté activo | Prueba `aec.delay_ms: 120` (o mide el tuyo) y sube `aec.suppression` a 1 |
| Cuesta interrumpirle, hay que gritar | Baja `echo_gain_aec` a 0.4 y `threshold` a 0.07 |
| Le corta el ruido de fondo | Sube `frames` a 8 y `audio.vad_aggressiveness` a 3 |
| Pierde tus primeras palabras | Sube `preroll_frames` a 15 |
| Con altavoces es imposible afinarlo | `mode: wakeword`: solo le corta oír «Hey Jarvis» |

Un caso en el que ninguna de las dos capas puede ayudar: cuando el servidor no
tiene altavoces y la voz se reproduce **en el navegador**. Ahí no hay señal de
referencia que restar ni nivel que comparar, porque el audio suena en otro
proceso. Usa auriculares o `mode: wakeword`.

**Con auriculares funciona sin tocar nada.** Con altavoces, el AEC es lo que
hace la diferencia; si no lo tienes instalado y el volumen es alto, `mode:
wakeword` es el plan B infalible: solo le corta oír «Hey Jarvis».

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
│   │   ├── bargein.py      # ★ ¿me está interrumpiendo?
│   │   ├── aec.py          # ★ cancelación de eco (WebRTC)
│   │   ├── resample.py     # alinea la referencia con el micrófono
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
- **Barge-in**: mientras Jarvis habla, el micrófono sigue analizándose, pero
  solo para decidir si le estás interrumpiendo (`jarvis/audio/bargein.py`).
- **Audio limpio en un solo punto**: la cancelación de eco se aplica en
  `AudioCapture._dispatch`, así que el resto del sistema no sabe que existe y
  todo —wake word, VAD, barge-in y Whisper— se beneficia.
- **Cancelación limpia del turno**: al interrumpir se corta el audio, se
  descartan las frases pendientes y se cancela la petición al modelo; el
  cerebro cierra el turno abierto para que el historial siga siendo válido.

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
| Se interrumpe solo constantemente | Activa el AEC, sube `barge_in.echo_gain_aec`, o `barge_in.mode: wakeword` |
| `swig: command not found` al instalar | Es el AEC: instala `swig` y `build-essential`, o sáltate `requirements-aec.txt` |
| El AEC no cancela nada | El retardo declarado por tus dispositivos es falso: fija `aec.delay_ms` (empieza por 120) |
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

1. **Más herramientas**: domótica, calendario, correo, control de música.
2. **Memoria semántica**: sustituir `memory.json` por una base vectorial.
3. **Ejecutable de escritorio**: empaquetar la interfaz con Tauri o pywebview.
4. **Acceso desde el móvil**: exponer el servidor en la red local (`server.host: 0.0.0.0`) — hazlo solo en redes de confianza, no hay autenticación.

---

## 10. Nota sobre privacidad

- El audio nunca sale del equipo si usas `faster-whisper` + `piper`. La
  cancelación de eco también es local: es una librería de C++, no un servicio.
- Lo que sí viaja a la API de Anthropic es el **texto** de la conversación.
- Las notas y la memoria se guardan en texto plano en `data/`.
- `tools.allow_system: false` desactiva abrir aplicaciones y leer el estado del equipo.

## Licencia

MIT.
