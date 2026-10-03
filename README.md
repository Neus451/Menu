# Recetario

## Versión para el ordenador (Linux Mint)

    python3 recetario_pc.py          # abre Recetario en el navegador
    bash instalar_acceso_directo.sh  # (opcional) icono en el menú de aplicaciones

Los datos se guardan en `~/Recetario` (cookbook.json + carpeta images).
Para usar otra carpeta, por ejemplo una sincronizada con Drive:

    python3 recetario_pc.py --datos ~/Drive/Recetario

Cada comida (almuerzo/cena) admite varios platos: primero, segundo, postre
y plato único. En las recetas se puede indicar el robot de cocina, y la lista
de robots se gestiona en Ajustes.

Probar solo el lector de webs, sin guardar nada:

    python3 cli.py preview https://la-web-de-una-receta

## Pruebas

    python3 -m unittest test_core test_server -v
