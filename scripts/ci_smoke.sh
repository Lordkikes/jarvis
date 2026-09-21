#!/usr/bin/env bash
# Arranca el servidor y comprueba que sirve las cinco interfaces.
# Es la prueba de humo que las unitarias no cubren: importaciones, rutas y
# arranque del pipeline en modo degradado (sin micrófono ni altavoces).
set -euo pipefail
cd "$(dirname "$0")/.."

PORT="${PORT:-8765}"
LOG="$(mktemp)"

python run.py --no-browser --port "$PORT" > "$LOG" 2>&1 &
SERVER=$!
trap 'kill "$SERVER" 2>/dev/null || true' EXIT

for _ in $(seq 1 60); do
  if curl -sf "http://127.0.0.1:$PORT/api/themes" > /dev/null 2>&1; then
    break
  fi
  if ! kill -0 "$SERVER" 2>/dev/null; then
    echo "el servidor murió al arrancar:"; cat "$LOG"; exit 1
  fi
  sleep 0.5
done

THEMES=$(curl -sf "http://127.0.0.1:$PORT/api/themes")
echo "temas disponibles: $THEMES"

for theme in hud orb paper terminal wave; do
  body=$(curl -sf "http://127.0.0.1:$PORT/?theme=$theme")
  if ! grep -q "data-theme=\"$theme\"" <<< "$body"; then
    echo "::error::/?theme=$theme no sirvió el tema $theme"; exit 1
  fi
  echo "  ok  /?theme=$theme"
done

# Sin parámetro debe salir el tema de config.yaml.
default=$(curl -sf "http://127.0.0.1:$PORT/" | grep -o 'data-theme="[a-z]*"' | head -1)
echo "  ok  / sirve $default"

if grep -iq "traceback" "$LOG"; then
  echo "::error::el servidor registró una excepción:"; cat "$LOG"; exit 1
fi

echo "prueba de humo superada"
