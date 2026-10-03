#!/usr/bin/env python3
"""Recetario para el ordenador (Linux Mint).

Arranca un pequeño servidor que SOLO escucha en tu propio ordenador y abre
Recetario en el navegador. No necesita instalar nada más que Python 3.

  python3 recetario_pc.py                    # datos en ~/Recetario
  python3 recetario_pc.py --datos ~/Drive/Recetario
"""
import argparse
import json
import mimetypes
import os
import sys
import threading
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

import datetime

from recetario_core import recipe_parser
from recetario_core.store import Store, StoreError, monday_of, next_monday

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
                    with open(path, "rb") as fh:
                        return self._send(200, fh.read(),
                                          mimetypes.guess_type(path)[0] or "application/octet-stream", cache=True)
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
                if path == "/api/shopping":
                    return {"week": monday_of(week), "items": store.shopping_list(week)}
                if path == "/api/settings":
                    return dict(store.get_settings(), data_dir=store.data_dir)
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
                if path == "/api/settings":
                    folder = os.path.expanduser(str(data.get("sync_folder", "")).strip())
                    store.set_setting("sync_folder", folder)
                    return {"ok": True}
                if path == "/api/sync":
                    folder = store.get_settings().get("sync_folder")
                    if not folder:
                        raise StoreError("Primero elige la carpeta de copia en Ajustes.")
                    try:
                        return store.sync_folder(folder)
                    except OSError as exc:
                        raise StoreError("No se pudo usar esa carpeta: %s" % exc.strerror)
                if path == "/api/quit":
                    threading.Timer(0.3, get_server().shutdown).start()
                    return {"ok": True}
            raise StoreError("Operación desconocida.")

    return Handler


def main(argv=None):
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
