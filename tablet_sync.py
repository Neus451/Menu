"""Sincroniza con la tablet Ubuntu Touch por el cable USB.

La app de la tablet sirve sus datos en 127.0.0.1:8765. Con "adb forward"
se abre en este ordenador un puerto que lleva hasta alli, y asi se pueden
mandar y traer recetas, menus y plantillas sin tocar la tablet a mano.

Esto solo se usa en el ordenador (en la tablet no hay adb).
"""
import json
import subprocess
import sys
import urllib.error
import urllib.request

LOCAL_PORT = 8766           # puerto que se abre aqui; distinto del 8765 del PC
DEVICE_PORT = 8765          # el que sirve la app en la tablet... si esta libre
TIMEOUT = 8
# La app busca el primer puerto libre desde 8765, asi que si 8765 esta
# ocupado (por ejemplo, tras instalar sin cerrar la anterior) usa 8766, 8767...
# Por eso hay que probar en vez de asumir el 8765.
PUERTOS_A_PROBAR = range(8765, 8785)


class TabletError(Exception):
    """Problema con la tablet, con un texto para mostrar a la persona."""


def _adb(*args, timeout=TIMEOUT):
    try:
        return subprocess.run(["adb"] + list(args), capture_output=True, text=True,
                              timeout=timeout)
    except FileNotFoundError:
        raise TabletError("No está instalado adb (sudo apt install android-tools-adb).")
    except subprocess.TimeoutExpired:
        raise TabletError("adb no contesta. ¿La tablet está conectada por cable?")


def device():
    """(estado, nombre) de la tablet conectada, o None si no hay ninguna."""
    r = _adb("devices")
    for linea in r.stdout.splitlines()[1:]:
        partes = linea.split()
        if len(partes) < 2:
            continue
        nombre, estado = partes[0], partes[1]
        if estado == "unauthorized":
            raise TabletError("La tablet no autoriza este ordenador. Acepta el aviso en la pantalla.")
        if estado == "offline":
            raise TabletError("La tablet aparece apagada o sin conexión USB. "
                             "Desbloquea la pantalla y revisa el cable.")
        if estado == "device":
            return nombre
    return None


def _url(path, timeout=30):
    return "http://127.0.0.1:%d%s" % (LOCAL_PORT, path)


def _abrir_puerto():
    """Abre el puerto local hacia el puerto que este usando la app.

    Devuelve el puerto de la tablet, o None si no contesta en ninguno.
    """
    for p in PUERTOS_A_PROBAR:
        _adb("forward", "--remove", "tcp:%d" % LOCAL_PORT, timeout=4)
        _adb("forward", "tcp:%d" % LOCAL_PORT, "tcp:%d" % p)
        try:
            _get("/api/settings", timeout=3)
            return p
        except urllib.error.HTTPError:
            return p          # contesto con error: es la app, vale igual
        except Exception:
            continue           # este puerto no lo usa nadie
    return None


def _cerrar_puerto():
    try:
        _adb("forward", "--remove", "tcp:%d" % LOCAL_PORT, timeout=4)
    except Exception:
        pass


def _get(path, timeout=60):
    with urllib.request.urlopen(_url(path), timeout=timeout) as r:
        return json.load(r)


def _post(path, cuerpo):
    req = urllib.request.Request(
        _url(path), json.dumps(cuerpo).encode("utf-8"),
        {"Content-Type": "application/json", "X-Recetario": "1"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)


def _comprobar_app(puerto):
    if puerto is None:
        raise TabletError("La app no responde en la tablet. Ábrela allí y vuelve a intentar.")


def sincronizar(store, traer=True, merger=True):
    """Pasa los datos entre el ordenador y la tablet. Devuelve un resumen.

    trae la tablet: primero trae lo que haya (para no perder cambios)
    merger: si no, sustituye lo de la tablet por lo del ordenador
    """
    nombre = device()
    if not nombre:
        raise TabletError("No hay ninguna tablet conectada por USB.")
    puerto = _abrir_puerto()
    try:
        _comprobar_app(puerto)
        resumen = {"tablet": nombre, "puerto": puerto}
        if traer:
            datos = _get("/api/data")
            r = store.import_data(datos, replace=False)
            resumen["traidas"] = {"recetas": r["recipes"], "nuevas": r["added"],
                                  "quitadas": r["removed"], "fotos": r.get("images", 0)}
        else:
            resumen["traidas"] = None
        propios = store.export_data(with_images=True)
        r = _post("/api/data", {"data": propios, "replace": not merger})
        resumen["enviadas"] = {"recetas": r["recipes"], "fotos": r.get("images", 0)}
        return resumen
    finally:
        _cerrar_puerto()


def main(argv=None):
    """Uso: python3 tablet_sync.py [--datos CARPETA] [--solo enviar|recibir| fusionar]"""
    import argparse
    import os
    from recetario_core.store import Store

    ap = argparse.ArgumentParser(description="Sincroniza con la tablet por el cable")
    ap.add_argument("--datos", default=os.environ.get("RECETARIO_DIR") or
                    os.path.expanduser("~/Recetario"))
    ap.add_argument("--solo", choices=("enviar", "recibir"), default=None,
                    help="enviar = solo PC -> tablet; recibir = solo tablet -> PC")
    args = ap.parse_args(argv)

    store = Store(args.datos)
    try:
        if args.solo == "enviar":
            r = sincronizar(store, traer=False, merger=False)
        elif args.solo == "recibir":
            r = sincronizar(store, traer=True)
        else:
            r = sincronizar(store, traer=True)
    except TabletError as e:
        print("ERROR: %s" % e, file=sys.stderr)
        return 1
    t = r["traidas"]
    print("Tablet %s (puerto %d)." % (r["tablet"], r["puerto"]))
    if t:
        print("  Traidas: %d recetas (%d nuevas, %d quitadas, %d fotos)."
              % (t["recetas"], t["nuevas"], t["quitadas"], t["fotos"]))
    print("  Enviadas: %d recetas, %d fotos." % (r["enviadas"]["recetas"], r["enviadas"]["fotos"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())