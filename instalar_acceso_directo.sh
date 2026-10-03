#!/bin/bash
# Crea el icono "Recetario" en el menú de aplicaciones de Linux Mint.
DIR="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$HOME/.local/share/applications"
cat > "$HOME/.local/share/applications/recetario.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Recetario
Comment=Recetas, menú semanal y lista de la compra
Exec=python3 "$DIR/recetario_pc.py"
Path=$DIR
Icon=folder-documents
Terminal=false
Categories=Utility;
DESKTOP
chmod +x "$HOME/.local/share/applications/recetario.desktop"
echo "Listo. Busca «Recetario» en el menú de aplicaciones."
