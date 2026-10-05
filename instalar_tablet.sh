#!/bin/bash
# Instala Recetario en la tablet Ubuntu Touch por USB.
#
#   bash instalar_tablet.sh
#
# La tablet debe estar conectada por cable y con Depuracion USB activada
# (Ajustes -> Desarrollador -> Depuracion USB).
#
# Ubuntu Touch 24.04 no deja instalar paquetes click sin privilegios, asi
# que hace falta root en la tablet. Este script deja el paquete preparado
# y te dice el comando exacto que hay que ejecutar.
set -e

AQUI="$(cd "$(dirname "$0")" && pwd)"
CLICK="$AQUI/appname/build/all/app/appname.neus_1.0.0_all.click"
SNAP="$(ls "$AQUI"/appname/recetario_*.snap 2>/dev/null | head -1)"
PAQUETE="appname.neus"

command -v adb >/dev/null || {
    echo "Falta adb. En Ubuntu: sudo apt install android-tools-adb" >&2; exit 1; }

adb wait-for-device
echo "== Dispositivo: $(adb shell getprop ro.product.model 2>/dev/null | tr -d '\r')"
echo "== Recetario instalado: $(adb shell "click list | grep $PAQUETE" 2>/dev/null | tr -d '\r' || echo 'no')"

# ---- Elige el metodo: snap (sin root) o click (necesita root) ----
if [ -n "$SNAP" ]; then
    echo
    echo "== Metodo snap (no necesita root) =="
    adb push "$SNAP" /tmp/recetario.snap >/dev/null
    echo "Ejecuta en la tablet:"
    echo "    snap install --dangerous /tmp/recetario.snap"
    exit 0
fi

if [ ! -f "$CLICK" ]; then
    echo "Falta el paquete. Generarlo con:  cd appname && clickable build" >&2
    exit 1
fi

echo
echo "== Subiendo el paquete =="
adb push "$CLICK" /tmp/recetario.click >/dev/null

echo
echo "== La tablet pide root para registrar la app =="
echo
echo "Abre un terminal en la tablet (o usa 'adb shell' y pega esto):"
echo
echo "    sudo click install --allow-unauthenticated \\"
echo "        --user=phablet /tmp/recetario.click"
echo
echo "Si pide contrasena, tecleala a continuacion."
echo
echo "Despues, comprueba que aparece en la lista:"
echo "    click list | grep $PAQUETE"
echo
echo "Y en Ajustes -> Permisos de las aplicaciones -> Recetario,"
echo "activa Red e Internet."