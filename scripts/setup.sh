#!/usr/bin/env bash
# Instalación completa de Jarvis en Linux/macOS.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "==> Comprobando Python"
python3 -c 'import sys; assert sys.version_info >= (3, 10), "se requiere Python 3.10+"'

echo "==> Dependencias del sistema"
if [[ "$OSTYPE" == "darwin"* ]]; then
  command -v brew >/dev/null && brew install portaudio || echo "   (instala portaudio manualmente)"
elif command -v apt-get >/dev/null; then
  sudo apt-get update -qq
  sudo apt-get install -y portaudio19-dev python3-dev libsndfile1 espeak-ng
fi

echo "==> Entorno virtual"
[[ -d .venv ]] || python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip --quiet

echo "==> Paquetes de Python"
pip install -r requirements.txt
read -r -p "¿Instalar también la parte de voz (micrófono, Whisper, Piper)? [S/n] " answer
[[ "${answer:-S}" =~ ^[SsYy]?$ ]] && pip install -r requirements-voice.txt

[[ -f .env ]] || { cp .env.example .env; echo "==> Creado .env: añade tu ANTHROPIC_API_KEY"; }

echo "==> Diagnóstico"
python scripts/doctor.py || true

echo
echo "Listo. Arranca con:  source .venv/bin/activate && python run.py"
