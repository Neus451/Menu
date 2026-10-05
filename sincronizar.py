"""Ayudante de sincronizar_tablet.sh: mueve datos entre devices.

    python3 sincronizar.py aplicar  <descarga.json> <carpeta> --fusionar|--reemplazar
    python3 sincronizar.py exportar <carpeta> <salida.json>

Usa el mismo Store que la app, asi que la fusion es la misma que se hace
al copiar una carpeta: gana lo mas reciente y se respetan los borrados.
"""
import json
import os
import sys

from recetario_core.store import Store


def aplicar(ruta_json, carpeta, replace):
    with open(ruta_json, encoding="utf-8") as fh:
        remoto = json.load(fh)
    store = Store(carpeta)
    res = store.import_data(remoto, replace=replace)
    print("   recetas: %d (nuevas %d, borradas %d, fotos %d)"
          % (res["recipes"], res["added"], res["removed"], res.get("images", 0)))


def exportar(carpeta, ruta_json):
    store = Store(carpeta)
    datos = store.export_data(with_images=True)
    with open(ruta_json, "w", encoding="utf-8") as fh:
        json.dump({"data": datos}, fh, ensure_ascii=False)
    print("   %d recetas, %d semanas de menú, %d fotos"
          % (len(datos["recipes"]), len(datos["menu"]), len(datos.get("images") or {})))


def main(argv):
    if len(argv) < 4:
        print(__doc__)
        return 2
    accion, origen, destino = argv[1], argv[2], argv[3]
    flags = argv[4:]
    if accion == "aplicar":
        aplicar(origen, destino, "--reemplazar" in flags)
    elif accion == "exportar":
        exportar(origen, destino)
    else:
        print("Acción desconocida: %s" % accion, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))