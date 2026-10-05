#!/usr/bin/env python3
"""Recetario para el ordenador (Linux Mint).

Arranca un pequeño servidor que SOLO escucha en tu propio ordenador y abre
Recetario en el navegador. No necesita instalar nada más que Python 3.

  python3 recetario_pc.py                    # datos en ~/Recetario
  python3 recetario_pc.py --datos ~/Drive/Recetario
"""
import json
import os
import sys
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

import datetime

from recetario_core import recipe_parser
from recetario_core.store import Store, StoreError, monday_of, next_monday

# argparse, mimetypes y webbrowser solo hacen falta cuando este fichero se
# ejecuta como programa (el PC). En la tablet se importa desde QML y no
# están, por eso se cargan más abajo, solo si hacen falta.

HERE = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(HERE, "web")
LOCK = threading.RLock()


def default_week():
    """De viernes a domingo se muestra la semana que viene; el resto, la actual."""
    today = datetime.date.today()
    return next_monday(today) if today.weekday() >= 4 else monday_of(today)


def _int_or_none(value):
    try:
        return int(value) if str(value).strip() else None
    except (TypeError, ValueError):
        return None


def recipe_fields(data):
    """Toma del formulario solo lo permitido y con el tipo correcto."""
    out = {}
    for key in ("name", "description", "category", "notes", "source_url", "robot"):
        if key in data:
            out[key] = str(data[key] or "").strip()
    for key in ("servings", "total_minutes", "prep_minutes", "cook_minutes"):
        if key in data:
            out[key] = _int_or_none(data[key])
    for key in ("ingredients", "instructions", "keywords"):
        if key in data:
            value = data[key]
            if isinstance(value, str):
                value = value.splitlines()
            out[key] = [str(x).strip() for x in value if str(x).strip()]
    return out


def _content_type(name):
    """Tipo de contenido por extensión.

    No se usa mimetypes porque dentro de la tablet AppArmor no deja leer
    /etc/mime.types y guess_type() revienta. Las fotos que guarda la app
    son siempre de estos cuatro formatos.
    """
    ext = os.path.splitext(name)[1].lower()
    return {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
            "webp": "image/webp", "gif": "image/gif"}.get(ext.lstrip("."),
                                                             "application/octet-stream")


def es_tablet():
    """¿Estamos corriendo dentro de la app de la tablet (click)?"""
    return os.path.isdir("/opt/click.ubuntu.com")


def sync_todo(store):
    """Sincroniza con la carpeta de copia y con la tablet, la que haya.

    Devuelve {"carpeta": ..., "tablet": ..., "avisos": [...]}. Si no se
    puede hacer ninguna de las dos, avisa con un error claro.
    """
    avisos = []
    resultado = {"carpeta": None, "tablet": None, "avisos": avisos}

    if es_tablet():
        # En la tablet no hay adb: el cable se maneja desde el ordenador.
        folder = store.get_settings().get("sync_folder")
        if folder and not (folder.startswith("/run/user/") or "gvfs" in folder):
            try:
                resultado["carpeta"] = store.sync_folder(folder)
            except OSError as exc:
                avisos.append("No se pudo usar la carpeta: %s" % (exc.strerror or exc))
        else:
            avisos.append("Aquí no hay carpeta de copia. Para pasar recetas entre el "
                          "ordenador y la tablet, con el cable conectado, ejecuta en el "
                          "ordenador: bash ~/bin/sincronizar_tablet.sh")
        return resultado

    folder = store.get_settings().get("sync_folder")
    if not folder:
        avisos.append("No hay carpeta de copia elegida.")
    elif folder.startswith("/run/user/") or "gvfs" in folder:
        avisos.append("La carpeta elegida es del explorador de archivos (gvfs/MTP) "
                      "y no se puede usar desde la app. Elige una ruta normal.")
    else:
        try:
            resultado["carpeta"] = store.sync_folder(folder)
        except OSError as exc:
            avisos.append("No se pudo usar la carpeta: %s" % (exc.strerror or exc))

    # La tablet solo si este ordenador tiene adb (allí no existe).
    # RECETARIO_SIN_TABLET=1 lo desactiva, para pruebas y para depurar.
    tablet_sync = None
    if os.environ.get("RECETARIO_SIN_TABLET"):
        avisos.append("Sincronización con la tablet desactivada (RECETARIO_SIN_TABLET).")
    else:
        try:
            import tablet_sync
        except ImportError:
            avisos.append("Este ordenador no puede sincronizar con la tablet "
                          "(falta tablet_sync.py).")
    if tablet_sync is not None:
        try:
            resultado["tablet"] = tablet_sync.sincronizar(store, traer=True)
        except tablet_sync.TabletError as exc:
            avisos.append(str(exc))

    if resultado["carpeta"] is None and resultado["tablet"] is None:
        raise StoreError("No se pudo sincronizar con nada.\n\n"
                         + "\n".join("· " + a for a in avisos))
    return resultado


def make_handler(store, get_server):
    class Handler(BaseHTTPRequestHandler):
        server_version = "Recetario"

        def log_message(self, fmt, *args):      # sin ruido en la terminal
            pass

        # ---- respuestas ----
        def _send(self, code, body, ctype, cache=False):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "max-age=3600" if cache else "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code=200):
            self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")

        # ---- GET ----
        def do_GET(self):
            url = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            try:
                if url.path in ("/", "/index.html"):
                    with open(os.path.join(WEB_DIR, "index.html"), "rb") as fh:
                        return self._send(200, fh.read(), "text/html; charset=utf-8")
                if url.path.startswith("/images/"):
                    name = os.path.basename(unquote(url.path[len("/images/"):]))
                    path = os.path.join(store.images_dir, name)
                    if not os.path.isfile(path):
                        return self._json({"error": "No existe esa imagen."}, 404)
                    try:
                        with open(path, "rb") as fh:
                            body = fh.read()
                    except OSError:
                        # AppArmor puede impedir leerla: mejor un 404 que
                        # un error 500 que rompe la pagina entera.
                        return self._json({"error": "No se puede leer esa imagen."}, 404)
                    return self._send(200, body, _content_type(name), cache=True)
                if url.path.startswith("/api/"):
                    return self._json(self.api_get(url.path, q))
                return self._json({"error": "No encontrado."}, 404)
            except (StoreError, recipe_parser.RecipeImportError, ValueError) as exc:
                self._json({"error": str(exc)}, 400)
            except Exception:
                traceback.print_exc()
                self._json({"error": "Error inesperado (mira la terminal)."}, 500)

        def api_get(self, path, q):
            week = q.get("week") or default_week()
            with LOCK:
                if path == "/api/recipes":
                    return store.list_recipes(q.get("q", ""), q.get("category", ""), q.get("robot", ""))
                if path == "/api/categories":
                    return store.categories()
                if path == "/api/robots":
                    return store.robots()
                if path.startswith("/api/recipe/"):
                    return store.get_recipe(path.rsplit("/", 1)[1])
                if path == "/api/menu":
                    return store.get_menu(week)
                if path == "/api/menu_text":
                    return {"text": store.week_text(week)}
                if path == "/api/templates":
                    return {"week": monday_of(week), "items": store.list_templates()}
                if path == "/api/data":
                    # Intercambio con el ordenador por el cable (adb reverse).
                    return store.export_data(with_images=q.get("imagenes", "1") != "0")
                if path == "/api/shopping":
                    return {"week": monday_of(week), "items": store.shopping_list(week)}
                if path == "/api/settings":
                    return dict(store.get_settings(), data_dir=store.data_dir,
                                es_tablet=es_tablet())
                if path == "/api/ingredients":
                    return store.list_ingredients()
            raise StoreError("Operación desconocida.")

        # ---- POST ----
        def do_POST(self):
            # Cabecera propia: impide que otras páginas web abiertas en el navegador
            # manden órdenes a Recetario sin que lo sepas.
            if self.headers.get("X-Recetario") != "1":
                return self._json({"error": "Petición no permitida."}, 403)
            try:
                size = int(self.headers.get("Content-Length") or 0)
                data = json.loads(self.rfile.read(size) or b"{}")
                self._json(self.api_post(urlparse(self.path).path, data))
            except (StoreError, recipe_parser.RecipeImportError, ValueError) as exc:
                self._json({"error": str(exc)}, 400)
            except Exception:
                traceback.print_exc()
                self._json({"error": "Error inesperado (mira la terminal)."}, 500)

        def api_post(self, path, data):
            with LOCK:
                if path == "/api/import":
                    return store.import_from_url(data.get("url", ""))
                if path == "/api/recipe":
                    return store.add_recipe(recipe_fields(data))
                if path.startswith("/api/recipe/") and path.endswith("/delete"):
                    store.delete_recipe(path.split("/")[3])
                    return {"ok": True}
                if path.startswith("/api/recipe/"):
                    return store.update_recipe(path.rsplit("/", 1)[1], recipe_fields(data))
                if path == "/api/menu/set":
                    store.set_slot(data.get("week") or default_week(), int(data["day"]),
                                   data["meal"], data["recipe_id"], data.get("course") or "unico")
                    return {"ok": True}
                if path == "/api/menu/clear":
                    store.clear_slot(data.get("week") or default_week(), int(data["day"]),
                                     data["meal"], data.get("course") or None)
                    return {"ok": True}
                if path == "/api/robot/add":
                    return {"name": store.add_robot(data.get("name", ""))}
                if path == "/api/robot/rename":
                    store.rename_robot(data.get("old", ""), data.get("new", ""))
                    return {"ok": True}
                if path == "/api/robot/delete":
                    store.delete_robot(data.get("name", ""))
                    return {"ok": True}
                if path == "/api/menu/clear_week":
                    store.clear_week(data.get("week") or default_week())
                    return {"ok": True}
                if path == "/api/menu/copy":
                    store.copy_week(data["src"], data["dst"])
                    return {"ok": True}
                if path == "/api/template/save":
                    return store.save_template(data.get("name", ""), data.get("week"))
                if path == "/api/template/apply":
                    return store.apply_template(data.get("name", ""), data.get("week"),
                                                replace=bool(data.get("replace", True)))
                if path == "/api/template/delete":
                    store.delete_template(data.get("name", ""))
                    return {"ok": True}
                if path == "/api/data":
                    # Llega lo que el ordenador manda por el cable.
                    payload = data.get("data")
                    if not isinstance(payload, dict):
                        raise StoreError("No han llegado datos.")
                    return store.import_data(payload, replace=bool(data.get("replace")))
                if path == "/api/settings":
                    folder = os.path.expanduser(str(data.get("sync_folder", "")).strip())
                    store.set_setting("sync_folder", folder)
                    return {"ok": True}
                if path == "/api/sync":
                    # Boton "Sincronizar ahora": hace las dos cosas, la
                    # carpeta de copia si hay alguna y la tablet si esta
                    # conectada por cable. Solo falla si ninguna de las dos
                    # se puede hacer.
                    return sync_todo(store)
                if path == "/api/quit":
                    threading.Timer(0.3, get_server().shutdown).start()
                    return {"ok": True}
                if path == "/api/ingredients/rename":
                    return {"changed": store.rename_ingredient(
                        data.get("old", ""), data.get("new", ""))}
                if path == "/api/ingredients/delete":
                    return {"changed": store.delete_ingredient(data.get("name", ""))}
            raise StoreError("Operación desconocida.")

    return Handler


def main(argv=None):
    import argparse
    import webbrowser

    ap = argparse.ArgumentParser(description="Recetario para el ordenador")
    ap.add_argument("--datos", default=os.environ.get("RECETARIO_DIR") or os.path.expanduser("~/Recetario"),
                    help="carpeta donde se guardan las recetas (por defecto ~/Recetario)")
    ap.add_argument("--puerto", type=int, default=8765)
    ap.add_argument("--no-abrir", action="store_true", help="no abrir el navegador")
    args = ap.parse_args(argv)

    url = "http://127.0.0.1:%d/" % args.puerto
    store = Store(os.path.expanduser(args.datos))
    holder = {}
    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.puerto), make_handler(store, lambda: holder["s"]))
    except OSError:
        print("Recetario ya está en marcha: abro el navegador.")
        webbrowser.open(url)
        return 0
    holder["s"] = server
    print("Recetario en %s\nDatos en: %s\n(Ctrl+C para cerrar)" % (url, store.data_dir))
    if not args.no_abrir:
        threading.Timer(0.5, webbrowser.open, [url]).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
