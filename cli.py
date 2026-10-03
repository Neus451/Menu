#!/usr/bin/env python3
"""Prueba del motor de Recetario desde la terminal.

  python3 cli.py preview URL        # lee la receta de la web SIN guardarla
  python3 cli.py import URL         # la guarda
  python3 cli.py list [texto]       # lista / busca
  python3 cli.py show ID
  python3 cli.py menu [AAAA-MM-DD]  # semana (por defecto la próxima)
  python3 cli.py set DIA COMIDA ID [AAAA-MM-DD]   # DIA 1-7 (lunes=1), COMIDA almuerzo|cena, CURSO primero|segundo|postre|unico
  python3 cli.py shopping [AAAA-MM-DD]
  python3 cli.py sync CARPETA       # copia/fusiona con una carpeta (Drive, etc.)

Los datos de prueba van a ~/.local/share/recetario-dev (o a $RECETARIO_DIR).
"""
import os
import sys

from recetario_core import recipe_parser, shopping
from recetario_core.store import MEAL_NAMES_ES, Store, StoreError, next_monday


def show(r):
    print("\n%s" % r["name"])
    meta = [m for m in (r.get("category"), r.get("robot"),
                        "%s min" % r["total_minutes"] if r.get("total_minutes") else "",
                        "%s raciones" % r["servings"] if r.get("servings") else "") if m]
    if meta:
        print("  " + " · ".join(meta))
    print("\nIngredientes:")
    for i in r["ingredients"]:
        print("  - " + i)
    print("\nPasos:")
    for n, s in enumerate(r["instructions"], 1):
        print("  %d. %s" % (n, s))
    print("\nImagen: %s" % (r.get("image_url") or "(ninguna)"))


def main(argv):
    if len(argv) < 2 or argv[1] in ("-h", "--help"):
        print(__doc__)
        return 0
    store = Store(os.environ.get("RECETARIO_DIR") or os.path.expanduser("~/.local/share/recetario-dev"))
    cmd, args = argv[1], argv[2:]
    try:
        if cmd == "preview":
            show(recipe_parser.import_recipe(args[0]))
        elif cmd == "import":
            r = store.import_from_url(args[0])
            print("Guardada: %s (id %s)%s" % (r["name"], r["id"], "" if r.get("image_file") else " — sin foto"))
        elif cmd == "list":
            for r in store.list_recipes(" ".join(args)):
                print("%s  %s" % (r["id"], r["name"]))
        elif cmd == "show":
            show(store.get_recipe(args[0]))
        elif cmd == "menu":
            print(store.week_text(args[0] if args else next_monday()))
        elif cmd == "set":
            day = int(args[0]) - 1
            meal = {"almuerzo": "lunch", "cena": "dinner"}.get(args[1].lower(), args[1])
            store.set_slot(args[3] if len(args) > 3 else next_monday(), day, meal, args[2],
                           args[4] if len(args) > 4 else "unico")
            print("Hecho.")
        elif cmd == "shopping":
            print(shopping.to_text(store.shopping_list(args[0] if args else next_monday())))
        elif cmd == "sync":
            print(store.sync_folder(os.path.expanduser(args[0])))
        else:
            print("Comando desconocido. Usa --help.")
            return 2
    except (recipe_parser.RecipeImportError, StoreError) as exc:
        print("Error: %s" % exc)
        return 1
    except IndexError:
        print("Faltan datos. Usa --help.")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
