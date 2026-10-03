#!/bin/bash
# Abre Recetario en el navegador.
cd "$(dirname "$0")" && exec python3 recetario_pc.py "$@"
