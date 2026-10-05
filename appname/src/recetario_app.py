"""Recetario para la tablet Ubuntu Touch.

Arranca el servidor web de Recetario dentro de la app (con PyOtherSide)
y la interfaz se muestra en un WebView. Los datos se guardan en una carpeta
propia de la app, para que AppArmor no lo bloquee.
"""
import os
import socket
import threading
import traceback

from http.server import ThreadingHTTPServer

from recetario_pc import make_handler
from recetario_core.store import Store

PORT = 8765
_SERVER = None
_LOCK = threading.Lock()
_STORE = None


def _usable(ruta):
    """¿Se puede escribir Y leer en esta carpeta?

    En la tablet AppArmor deja escribir en ~/Documents pero no leer, asi
    que no basta con que el mkdir funcione: hay que comprobar que despues
    se puede volver a leer.
    """
    try:
        os.makedirs(os.path.join(ruta, "images"), exist_ok=True)
        prueba = os.path.join(ruta, ".prueba")
        with open(prueba, "w", encoding="utf-8") as fh:
            fh.write("ok")
        with open(prueba, encoding="utf-8") as fh:
            if fh.read() != "ok":
                return False
        os.remove(prueba)
        os.listdir(os.path.join(ruta, "images"))    # leer tambien el subdirectorio
        return True
    except OSError:
        return False


def _pkg_name():
    """Nombre del paquete click, deducido de la ruta de instalacion.

    AppArmor solo concede las carpetas XDG propias del paquete
    (~/.local/share/<paquete>/, ~/.cache/<paquete>/...), no cualquier
    carpeta de la casa. Este es el unico sitio donde la app puede
    escribir sin pedir permisos extra.
    """
    # En una app click los dos niveles de arriba son <paquete>/<version>.
    version_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    padre = os.path.dirname(version_dir)
    nombre = os.path.basename(padre)
    # Los paquetes click siempre son tipo reverse-DNS ("appname.neus"), con
    # punto. Asi no nos confundimos si este modulo se ejecuta desde una
    # carpeta normal (por ejemplo, probandolo en el ordenador).
    if nombre and "." in nombre and "/" not in nombre:
        return nombre
    return None


def _candidatas():
    """Carpetas de datos, de la mas segura a la menos."""
    lista = []
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        lista.append(os.path.join(xdg, "Recetario"))
    pkg = _pkg_name()
    if pkg:
        lista.append(os.path.join(os.path.expanduser("~"), ".local", "share", pkg, "Recetario"))
        lista.append(os.path.join(os.path.expanduser("~"), ".local", "share", pkg))
    lista.append(os.path.join(os.path.expanduser("~"), "Recetario"))
    lista.append(os.path.join(os.path.expanduser("~"), "Documents", "Recetario"))
    return lista


def _data_dir():
    """Carpeta de datos en la tablet.

    Se prueban varias rutas y se usa la primera que permita LEER y
    ESCRIBIR. Solo se acepta ~/Documents como ultimo recurso: segun el
    perfil de AppArmor ahi se puede escribir pero no leer.
    """
    if os.environ.get("RECETARIO_DIR"):
        return os.environ["RECETARIO_DIR"]
    candidatas = _candidatas()
    for ruta in candidatas:
        if _usable(ruta):
            return ruta
    return candidatas[0]


def _puerto_libre():
    """Primer puerto libre a partir de 8765 (por si otro proceso lo ocupa)."""
    for puerto in range(PORT, PORT + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", puerto))
            except OSError:
                continue
            return puerto
    return None


def start():
    """Arranca el servidor. Devuelve la URL, o un texto con el error.

    Se llama desde Main.qml, que usa lo devuelto para abrir el WebView o
    para enseñarle el problema a la persona.
    """
    global _SERVER, _STORE
    with _LOCK:
        if _SERVER is not None:
            return "http://127.0.0.1:%d/" % _SERVER.server_address[1]

        puerto = _puerto_libre()
        if puerto is None:
            return "No hay ningún puerto libre para el servidor."

        try:
            _STORE = Store(_data_dir())
        except OSError as exc:
            return ("No se pudo preparar la carpeta de datos (%s).\n\n"
                    "Se han probado, y AppArmor no deja escribir en ninguna:\n%s"
                    % (exc.strerror or exc,
                       "\n".join("  · " + r for r in _candidatas())))

        handler = make_handler(_STORE, lambda: _SERVER)
        try:
            _SERVER = ThreadingHTTPServer(("127.0.0.1", puerto), handler)
        except OSError as exc:
            _SERVER = None
            return ("No se pudo abrir el puerto %d (%s)."
                    % (puerto, exc.strerror or exc))

        threading.Thread(target=_SERVER.serve_forever, daemon=True).start()
        url = "http://127.0.0.1:%d/" % puerto
        print("Recetario: servidor en marcha en " + url)
        return url


def status():
    """Información de diagnóstico (para probar desde el terminal)."""
    return {
        "url": "http://127.0.0.1:%d/" % _SERVER.server_address[1] if _SERVER else None,
        "data_dir": _data_dir(),
        "python": os.sys.version.split()[0],
    }


def _diagnostico():
    """Volcado a stderr: así se ve desde journalctl en la tablet."""
    print("Recetario diagnostico: " + repr(status()), flush=True)
    try:
        traceback.print_exc()
    except Exception:
        pass


if __name__ == "__main__":
    # Permite probar el modulo en la tablet sin QML.
    print(start())
    _diagnostico()