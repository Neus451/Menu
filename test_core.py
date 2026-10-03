import http.server
import json
import os
import shutil
import tempfile
import threading
import unittest

from recetario_core import recipe_parser as rp
from recetario_core import shopping
from recetario_core.store import Store, StoreError, merge_data, monday_of

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64

JSONLD_PAGE = """<html><head><title>x</title>
<script type="application/ld+json">
{"@context":"https://schema.org","@graph":[
 {"@type":"WebSite","name":"Mi blog"},
 {"@type":["Recipe"],"name":"Tortilla de patatas &amp; cebolla",
  "image":["/img/tortilla.png"],
  "description":"<p>La clásica.</p>",
  "recipeYield":["4","4 raciones"],
  "prepTime":"PT15M","cookTime":"PT0H30M0S",
  "recipeCategory":"Plato principal","keywords":"huevo, patata",
  "recipeIngredient":["500 g de patatas","4 huevos","1 cebolla","Sal"],
  "recipeInstructions":[
    {"@type":"HowToSection","name":"Base","itemListElement":[
       {"@type":"HowToStep","text":"Pela y corta las patatas."},
       {"@type":"HowToStep","text":"1. Fríelas a fuego lento."}]},
    {"@type":"HowToStep","text":"Mezcla con el huevo batido."}]}
]}
</script></head><body>hola</body></html>"""

MICRODATA_PAGE = """<html><head><meta property="og:image" content="/m.png"><title>Fallback</title></head><body>
<div itemscope itemtype="https://schema.org/Recipe">
 <h1 itemprop="name">Gazpacho</h1>
 <time itemprop="totalTime" datetime="PT20M">20 min</time>
 <ul><li itemprop="recipeIngredient">1 kg tomates</li><li itemprop="recipeIngredient">1 pepino</li></ul>
 <div itemprop="recipeInstructions"><p>Tritura todo.</p><p>Enfría.</p></div>
</div></body></html>"""

STRING_STEPS = """<script type="application/ld+json">{"@type":"Recipe","name":"Té",
"recipeIngredient":"1 bolsita de té\\n1 taza de agua","recipeInstructions":"Calienta el agua.\\nAñade el té."}</script>"""


class ParserTests(unittest.TestCase):
    def test_jsonld_graph(self):
        r = rp.extract_recipe(JSONLD_PAGE, "https://ejemplo.com/tortilla")
        self.assertEqual(r["name"], "Tortilla de patatas & cebolla")
        self.assertEqual(r["image_url"], "https://ejemplo.com/img/tortilla.png")
        self.assertEqual(r["servings"], 4)
        self.assertEqual((r["prep_minutes"], r["cook_minutes"], r["total_minutes"]), (15, 30, 45))
        self.assertEqual(r["ingredients"][0], "500 g de patatas")
        self.assertEqual(r["instructions"], ["Pela y corta las patatas.", "Fríelas a fuego lento.",
                                             "Mezcla con el huevo batido."])
        self.assertEqual(r["description"], "La clásica.")
        self.assertEqual(r["category"], "Plato principal")

    def test_microdata_fallback(self):
        r = rp.extract_recipe(MICRODATA_PAGE, "https://ejemplo.com/g")
        self.assertEqual(r["name"], "Gazpacho")
        self.assertEqual(r["ingredients"], ["1 kg tomates", "1 pepino"])
        self.assertEqual(r["instructions"], ["Tritura todo.", "Enfría."])
        self.assertEqual(r["total_minutes"], 20)

    def test_string_fields(self):
        r = rp.extract_recipe(STRING_STEPS, "")
        self.assertEqual(r["ingredients"], ["1 bolsita de té", "1 taza de agua"])
        self.assertEqual(r["instructions"], ["Calienta el agua.", "Añade el té."])

    def test_no_recipe(self):
        with self.assertRaises(rp.RecipeImportError):
            rp.extract_recipe("<html><body>Nada</body></html>", "")

    def test_duration(self):
        self.assertEqual(rp.parse_duration("PT1H30M"), 90)
        self.assertEqual(rp.parse_duration("P1DT1H"), 25 * 60)
        self.assertIsNone(rp.parse_duration("PT0S"))
        self.assertEqual(rp.parse_duration("45 min"), 45)


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/receta":
            body, ctype = JSONLD_PAGE.encode("utf-8"), "text/html; charset=utf-8"
        elif self.path == "/img/tortilla.png":
            body, ctype = PNG, "image/png"
        else:
            self.send_response(404); self.end_headers(); return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.end_headers()
        self.wfile.write(body)


class StoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.s = Store(os.path.join(self.tmp, "a"))

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_import_url_end_to_end(self):
        url = "http://127.0.0.1:%d/receta" % self.port
        r = self.s.import_from_url(url)
        self.assertEqual(r["name"], "Tortilla de patatas & cebolla")
        self.assertTrue(os.path.exists(self.s.image_path(r)))
        with self.assertRaises(rp.RecipeImportError):          # duplicado
            self.s.import_from_url(url)
        with self.assertRaises(rp.RecipeImportError):          # 404
            self.s.import_from_url("http://127.0.0.1:%d/nada" % self.port)
        self.assertEqual(Store(self.s.data_dir).list_recipes()[0]["name"], r["name"])  # persiste

    def test_manual_crud_and_search(self):
        a = self.s.add_recipe({"name": "Sopa de cebolla", "category": "Sopas", "ingredients": ["2 cebollas"]})
        self.s.add_recipe({"name": "Árbol de tomate", "ingredients": ["tomate"]})
        with self.assertRaises(StoreError):
            self.s.add_recipe({"name": "  "})
        self.assertEqual([x["name"] for x in self.s.list_recipes("CEBOLLA")], ["Sopa de cebolla"])
        self.assertEqual(len(self.s.list_recipes("arbol")), 1)            # sin tildes
        self.s.update_recipe(a["id"], {"name": "Sopa francesa"})
        self.assertEqual(self.s.get_recipe(a["id"])["name"], "Sopa francesa")
        self.assertEqual({c["name"]: c["count"] for c in self.s.categories()}, {"": 1, "Sopas": 1})

    def test_menu_and_shopping(self):
        a = self.s.add_recipe({"name": "A", "ingredients": ["200 g harina", "2 huevos"]})
        b = self.s.add_recipe({"name": "B", "ingredients": ["100 g de harina", "Sal"]})
        wk = "2026-10-07"                                  # miércoles -> semana del 5
        self.s.set_slot(wk, 0, "lunch", a["id"])                      # plato único por defecto
        self.s.set_slot(wk, 0, "dinner", b["id"], course="segundo")
        self.s.set_slot(wk, 0, "dinner", a["id"], course="primero")   # cena con dos platos
        self.s.set_slot(wk, 6, "dinner", a["id"])
        menu = self.s.get_menu("2026-10-05")
        self.assertEqual(menu["week"], "2026-10-05")
        self.assertEqual(menu["days"][0]["meals"]["lunch"]["unico"]["name"], "A")
        self.assertEqual(menu["days"][0]["meals"]["dinner"]["primero"]["name"], "A")
        self.assertEqual(menu["days"][0]["meals"]["dinner"]["segundo"]["name"], "B")
        self.assertEqual(menu["days"][6]["date"], "2026-10-11")
        texts = [i["text"] for i in self.s.shopping_list(wk)]
        self.assertIn("700 g de harina", texts)   # 3× 200 g (A×3) + 100 g (B)
        self.assertIn("6 huevos", texts)
        with self.assertRaises(StoreError):
            self.s.set_slot(wk, 9, "lunch", a["id"])
        with self.assertRaises(StoreError):
            self.s.set_slot(wk, 0, "lunch", a["id"], course="entrada")
        self.s.copy_week(wk, "2026-10-12")
        self.assertEqual(self.s.get_menu("2026-10-12")["days"][6]["meals"]["dinner"]["unico"]["name"], "A")
        self.s.delete_recipe(a["id"])                      # desaparece de todos los menús
        self.assertIsNone(self.s.get_menu(wk)["days"][0]["meals"]["lunch"])
        self.assertIsNone(self.s.get_menu("2026-10-12")["days"][6]["meals"]["dinner"])
        self.assertEqual(self.s.get_menu(wk)["days"][0]["meals"]["dinner"]["segundo"]["name"], "B")
        # clear_slot con course solo borra ese plato
        self.s.clear_slot(wk, 0, "dinner", course="segundo")
        self.assertIsNone(self.s.get_menu(wk)["days"][0]["meals"]["dinner"])

    def test_export_import_roundtrip(self):
        r = self.s.import_from_url("http://127.0.0.1:%d/receta" % self.port)
        self.s.set_slot("2026-10-05", 1, "lunch", r["id"])
        folder = os.path.join(self.tmp, "Drive", "Recetas")
        info = self.s.export_to_folder(folder, weeks=["2026-10-05"])
        self.assertIn("menu-2026-10-05.txt", info["files"])
        self.assertIn("compra-2026-10-05.txt", info["files"])
        text = open(os.path.join(folder, "menu-2026-10-05.txt"), encoding="utf-8").read()
        self.assertIn("Martes 06/10", text)
        self.assertIn("Almuerzo: Tortilla", text)
        other = Store(os.path.join(self.tmp, "b"))
        res = other.import_from_folder(folder)
        self.assertEqual(res["added"], 1)
        self.assertTrue(os.path.exists(other.image_path(other.list_recipes()[0] and other.get_recipe(r["id"]))))
        self.assertEqual(other.get_menu("2026-10-05")["days"][1]["meals"]["lunch"]["unico"]["id"], r["id"])
        with self.assertRaises(StoreError):
            Store(os.path.join(self.tmp, "c")).import_from_folder(os.path.join(self.tmp, "vacia"))

    def test_sync_two_devices(self):
        folder = os.path.join(self.tmp, "Drive")
        tablet, pc = self.s, Store(os.path.join(self.tmp, "pc"))
        r1 = tablet.add_recipe({"name": "Solo tablet"})
        tablet.sync_folder(folder)
        pc.sync_folder(folder)
        self.assertEqual([x["name"] for x in pc.list_recipes()], ["Solo tablet"])
        r2 = pc.add_recipe({"name": "Solo pc"})
        pc.update_recipe(r1["id"], {"name": "Tablet editada en pc"})
        pc.sync_folder(folder)
        tablet.sync_folder(folder)
        self.assertEqual(sorted(x["name"] for x in tablet.list_recipes()), ["Solo pc", "Tablet editada en pc"])
        tablet.delete_recipe(r2["id"])                      # el borrado también viaja
        tablet.sync_folder(folder)
        pc.sync_folder(folder)
        self.assertEqual([x["name"] for x in pc.list_recipes()], ["Tablet editada en pc"])

    def test_merge_edit_after_delete_resurrects(self):
        base = {"recipes": {"x": {"id": "x", "name": "v1", "updated": 1}}, "menu": {}, "deleted": {}}
        gone = {"recipes": {}, "menu": {}, "deleted": {"x": 5}}
        edited = {"recipes": {"x": {"id": "x", "name": "v2", "updated": 9}}, "menu": {}, "deleted": {}}
        self.assertEqual(merge_data(base, gone)["recipes"], {})
        self.assertEqual(merge_data(gone, edited)["recipes"]["x"]["name"], "v2")

    def test_robots_crud(self):
        self.assertIn("Thermomix", self.s.robots())
        self.s.add_robot("Moulinex")
        r = self.s.add_recipe({"name": "Gazpacho", "robot": "Moulinex"})
        self.s.rename_robot("Moulinex", "MyCook")
        self.assertEqual(self.s.get_recipe(r["id"])["robot"], "MyCook")
        self.s.delete_robot("MyCook")
        self.assertEqual(self.s.get_recipe(r["id"])["robot"], "")
        with self.assertRaises(StoreError):
            self.s.add_robot("thermomix")        # duplicado sin importar mayúsculas

    def test_old_slot_format_migrates(self):
        r = self.s.add_recipe({"name": "Vieja"})
        self.s.data["menu"]["2026-10-05"] = {"updated": 1, "slots": {"0_lunch": r["id"]}}
        self.s.save()
        m = Store(self.s.data_dir).get_menu("2026-10-05")
        self.assertEqual(m["days"][0]["meals"]["lunch"]["unico"]["name"], "Vieja")

    def test_corrupt_file_falls_back_to_backup(self):
        self.s.add_recipe({"name": "Uno"})
        self.s.add_recipe({"name": "Dos"})                   # crea .bak con "Uno"
        with open(self.s.path, "w") as fh:
            fh.write("{roto")
        self.assertEqual([x["name"] for x in Store(self.s.data_dir).list_recipes()], ["Uno"])


class ShoppingTests(unittest.TestCase):
    def test_count_style_from_real_site(self):
        # formato real de directoalpaladar.com
        items = shopping.build_list([("Gazpacho", ["1kg Tomate pera", "1count Pimiento verde italiano",
                                                   "2count Dientes de ajo", "50ml Aceite de oliva virgen extra", "5g Sal"])])
        d = {i["name"]: i["text"] for i in items}
        self.assertEqual(d["Tomate pera"], "1 kg de Tomate pera")
        self.assertEqual(d["Pimiento verde italiano"], "1 Pimiento verde italiano")
        self.assertEqual(d["Dientes de ajo"], "2 Dientes de ajo")
        self.assertEqual(d["Aceite de oliva virgen extra"], "50 ml de Aceite de oliva virgen extra")

    def test_units_and_merge(self):
        items = shopping.build_list([("A", ["1,5 kg patatas", "½ cebolla", "2 cucharadas de aceite"]),
                                     ("B", ["500 g de patatas", "1 cebolla", "1 cucharada aceite"])])
        d = {i["name"]: i["text"] for i in items}
        self.assertEqual(d["patatas"], "2 kg de patatas")
        self.assertEqual(d["cebollas"], "1,5 cebollas")
        self.assertEqual(d["aceite"], "3 cucharadas de aceite")


if __name__ == "__main__":
    unittest.main(verbosity=2)
