#!/bin/sh
# Instala o actualiza Recetario en la tablet Ubuntu Touch.
# Uso:  sudo sh /tmp/instalar-recetario.sh
#
# Solo toca lo de Recetario. No borra ninguna otra app.
#
# Importante: hay que retirar el perfil de AppArmor anterior antes de
# instalar. Si se deja, click reutiliza el perfil viejo (generado cuando la
# app declaraba policy_groups vacio) y el WebView no arranca: la app peta
# con SEGV nada mas abrir.
set -e

PKG=/tmp/recetario.click
PAQUETE=appname.neus
PERFIL="click_${PAQUETE}_appname"
VER=1.0.0

[ -f "$PKG" ] || { echo "No esta $PKG. Subelo antes con:  adb push <paquete> $PKG" >&2; exit 1; }

echo "== Retirando la version anterior de Recetario =="
click unregister "$PAQUETE" 2>/dev/null || true
rm -rf "/opt/click.ubuntu.com/$PAQUETE" 2>/dev/null || true

echo "== Retirando el perfil de AppArmor anterior =="
# El perfil es un fichero en sysfs: al borrarlo, easyprof lo regenera al
# instalar y esta vez con los permisos de red y de WebView.
PROF="/sys/kernel/security/apparmor/profiles/${PERFIL}_${VER}"
[ -e "$PROF" ] && { echo 0 > "$PROF" 2>/dev/null && echo "   perfil eliminado" \
                   || echo "   AVISO: no se pudo borrar el perfil"; }
rm -f  "/var/lib/apparmor/profiles/${PERFIL}_${VER}"    2>/dev/null || true
rm -rf "/var/lib/apparmor/clicks/${PAQUETE}_appname_${VER}" 2>/dev/null || true

echo "== Instalando =="
click install --allow-unauthenticated --user=phablet "$PKG"

echo "== Comprobando los permisos del WebView =="
# easyprof nombra el perfil en sysfs con un sufijo numerico, asi que se
# busca el que empiece por el nombre de la app.
ENCONTRADO=""
for f in /sys/kernel/security/apparmor/profiles/*"${PAQUETE}"*; do
    [ -e "$f" ] && ENCONTRADO="$f" && break
done
if [ -z "$ENCONTRADO" ]; then
    echo "   AVISO: no veo ningun perfil cargado para $PAQUETE."
elif cat "/sys/kernel/security/apparmor/policy/profiles/$(basename "$ENCONTRADO")" 2>/dev/null \
        | grep -q "QtWebEngineProcess" \
   || grep -q "QtWebEngineProcess" \
        "/var/lib/apparmor/profiles/${PERFIL}_${VER}" 2>/dev/null; then
    echo "   OK: el perfil concede QtWebEngineProcess (el WebView deberia funcionar)"
else
    echo "   AVISO: el perfil no concede QtWebEngineProcess."
    echo "   Prueba:  sudo aa-easyprof -f ${PAQUETE}.appname"
fi

echo
echo "== Estado =="
click list | grep "$PAQUETE" || true
echo
echo "Abre Recetario desde el menu de aplicaciones."