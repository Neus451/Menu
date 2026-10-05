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

## Versión para la tablet (Ubuntu Touch)

La app está en `appname/`. La interfaz es la misma de siempre (HTML) servida
por un servidor Python que corre dentro de la app y se muestra en un WebView,
así que las recetas y el menú se guardan igual que en el ordenador.

### Por qué no arrancaba (y ya está arreglado)

La app se quedaba en blanco sin dar error. Las causas, en orden de importancia:

1. **AppArmor sin permisos.** `appname.apparmor` tenía `"policy_groups": []`.
   Sin `networking` y `webview` el proceso no puede ni abrir el puerto 8765
   ni embeber el WebView, así que moría antes de dibujar nada.
   Los nombres válidos son `networking`, `webview` y `connectivity`
   (no existen `network`, `network-bind` ni `home`).
2. **La compilación fallaba.** El target que genera el `.pot` usaba
   `xgettext --c++ --qt --language=javascript` a la vez. Son lenguajes
   excluyentes mutuos, así que `xgettext` no generaba nada y el `copy`
   posterior abortaba la construcción entera. Recetario no usa `tr()`, así
   que ese target ahora es un no-op.
3. **Bytecode ajeno en el paquete.** Se empaquetaban `__pycache__/*.pyc`
   compilados con Python 3.12 del PC. En la tablet se ignoran, pero estorban.
4. **Imports que el sandbox puede bloquear.** `recetario_pc.py` importaba
   `argparse`, `mimetypes` y `webbrowser` al cargarse. Ahora solo se importan
   dentro de `main()`, que es el camino del PC.
5. **Puerto fijo.** Si el 8765 estaba ocupado, la app se quedaba en blanco
   para siempre. Ahora busca el primer puerto libre desde 8765.
6. **Errores invisibles.** `start()` no devolvía nada y `Main.qml` solo
   reintentaba una vez, sin mostrar el motivo.

### Qué hace ahora

- `recetario_app.start()` devuelve la URL, o un texto explicando el fallo, y
  `Main.qml` lo muestra con un botón de reintentar.
- Prueba `~/Documents/Recetario` y, si AppArmor no deja escribir, cae a
  `~/.local/share/Recetario` en vez de reventar.
- Al arrancar con `--help`-like: `python3 src/recetario_app.py` imprime la URL
  y el diagnóstico, útil desde el terminal de la tablet.

### Compilar

Paquete click (revisado por `click-review`, pasa):

    cd appname
    clickable build
    # -> build/all/app/appname.neus_1.0.0_all.click

Snap (para instalar en UT 24.04 sin root):

    cd appname
    snapcraft
    # -> recetario_1.0.0_<arch>.snap

### Instalar

Por USB, con la tablet conectada (Depuración USB activada en Ajustes →
Desarrollador):

    bash instalar_tablet.sh

A mano, con el snap:

    adb push recetario_1.0.0_arm64.snap /tmp/
    adb shell snap install --dangerous /tmp/recetario_1.0.0_arm64.snap

### Permisos

En la tablet: **Ajustes → Permisos de las aplicaciones → Recetario**, y
comprobar que Red e Internet está activado.

Los datos quedan en la carpeta de la propia app. Para pasar recetas del
ordenador a la tablet, copia `cookbook.json` (y `images/`) a
`~/.local/share/Recetario/` desde el explorador de archivos de la tablet.

## Pruebas

    python3 -m unittest test_core test_server -v