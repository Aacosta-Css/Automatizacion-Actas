#!/bin/bash
cd "$(dirname "$0")"

if [ -f ".venv/bin/activate" ]; then
    echo "[INFO] Activando entorno virtual .venv..."
    source .venv/bin/activate
fi

echo "[INFO] Iniciando servidor..."
python3 main.py
