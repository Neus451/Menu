#!/bin/bash
# Sincroniza con la tablet Ubuntu Touch por el cable USB.
#
#   bash ~/bin/sincronizar_tablet.sh            # fusiona los dos lados
#   bash ~/bin/sincronizar_tablet.sh enviar     # solo PC -> tablet
#   bash ~/bin/sincronizar_tablet.sh recibir    # solo tablet -> PC
#
# La app de la tablet tiene que estar ABIERTA (su servidor es el que
# contesta). El script busca en que puerto esta, asi que da igual si la app
# ha tenido que arrancar en el 8766 en vez del 8765.
set -e

AQUI="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
DATOS="${RECETARIO_DIR:-$HOME/Recetario}"
MODO="$1"

case "$MODO" in
    enviar)  ARG="--solo enviar" ;;
    recibir) ARG="--solo recibir" ;;
    "")      ARG="" ;;
    *)       echo "Uso: $0 [enviar|recibir]" >&2; exit 2 ;;
esac

command -v adb >/dev/null || {
    echo "Falta adb: sudo apt install android-tools-adb" >&2; exit 1; }

# El módulo vive junto a este script y necesita recetario_core.
[ -d "$AQUI/recetario_core" ] || {
    echo "Falta $AQUI/recetario_core" >&2; exit 1; }

python3 "$AQUI/tablet_sync.py" --datos "$DATOS" $ARG