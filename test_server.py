import json
import os
import shutil
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import recetario_pc
from recetario_core.store import Store


class ServerTests(unittest.TestCase):
    def setUp(self):
        # Que no dependa de si hay una tablet conectada de verdad.
        os.environ["RECETARIO_SIN_TABLET"] = "1"
        self.tmp = tempfile.mkdtemp()
        self.store = Store(os.path.join(self.tmp, "datos"))
        holder = {}
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), recetario_pc.make_handler(self.store, lambda: holder["s"]))
        holder["s"] = self.server
        self.base = "http://127.0.0.1:%d" % self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        os.environ.pop("RECETARIO_SIN_TABLET", None)
        self.server.shutdown()
        self.server.server_close()
        shutil.rmtree(self.tmp)

    def post(self, path, body, header=True):
        req = urllib.request.Request(self.base + path, json.dumps(body).encode(), method="POST",
                                     headers={"X-Recetario": "1"} if header else {})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            return e.code, json.load(e)

    def get(self, path):
        try:
            with urllib.request.urlopen(self.base + path) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            return e.code, json.load(e)

    def test_sync_needs_folder_then_works(self):
        # Sin carpeta ni tablet conectada responde 400 explicando las dos.
        self.post("/api/recipe", {"name": "Paella", "ingredients": "arroz"})
        code, data = self.post("/api/sync", {})
        self.assertEqual(code, 400)
        self.assertIn("carpeta", data["error"].lower())

        folder = os.path.join(self.tmp, "Drive")
        self.assertEqual(self.post("/api/settings", {"sync_folder": folder})[0], 200)
        code, data = self.post("/api/sync", {})
        self.assertEqual(code, 200)
        self.assertTrue(os.path.exists(os.path.join(folder, "cookbook.json")))
        # Sin tablet, lo dice en los avisos pero no es un error.
        self.assertIsNone(data["tablet"])
        self.assertTrue(any("tablet" in a.lower() or "adb" in a.lower()
                            for a in data["avisos"]), data["avisos"])

    def test_sync_rejects_gvfs_folder(self):
        # Una carpeta del explorador de archivos no vale, pero como la
        # tablet tampoco esta, avisa de las dos cosas.
        self.post("/api/settings", {"sync_folder": "/run/user/1000/gvfs/mtp:x/y"})
        code, data = self.post("/api/sync", {})
        self.assertEqual(code, 400)
        self.assertIn("gvfs", data["error"])

    def test_errors_are_json_and_csrf_blocked(self):
        self.assertEqual(self.post("/api/recipe", {"name": ""})[0], 400)
        self.assertEqual(self.post("/api/recipe", {"name": "x"}, header=False)[0], 403)
        self.assertEqual(self.get("/api/recipe/nope")[0], 400)
        self.assertEqual(self.get("/images/../../etc/passwd")[0], 404)

    def test_default_week_is_a_monday(self):
        code, menu = self.get("/api/menu")
        self.assertEqual(code, 200)
        import datetime
        self.assertEqual(datetime.date.fromisoformat(menu["week"]).weekday(), 0)

    def test_data_crosses_from_pc_to_tablet(self):
        # El PC prepara recetas + menu y las manda a la tablet.
        self.post("/api/recipe", {"name": "Paella", "ingredients": ["400 g arroz", "1 l caldo"]})
        week = "2026-10-05"
        rid = self.get("/api/recipes")[1][0]["id"]
        self.post("/api/menu/set", {"week": week, "day": 0, "meal": "lunch",
                                    "recipe_id": rid, "course": "unico"})
        code, tpl = self.post("/api/template/save", {"name": "Verano", "week": week})
        self.assertEqual(code, 200)

        code, paquete = self.get("/api/data")
        self.assertEqual(code, 200)
        self.assertEqual(len(paquete["recipes"]), 1)
        self.assertIn(week, paquete["menu"])
        self.assertIn("Verano", paquete["templates"])

        # La tablet (otra Store) lo recibe todo.
        otra = Store(os.path.join(self.tmp, "tablet"))
        r = otra.import_data(paquete)
        self.assertEqual(r["recipes"], 1)
        self.assertEqual(len(otra.list_recipes()), 1)
        self.assertEqual(otra.get_menu(week)["days"][0]["meals"]["lunch"]["unico"]["name"], "Paella")
        self.assertEqual([t["name"] for t in otra.list_templates()], ["Verano"])

    def test_data_can_ignore_images_to_go_light(self):
        self.post("/api/recipe", {"name": "Tarta", "ingredients": "harina"})
        self.assertNotIn("images", self.get("/api/data?imagenes=0")[1])
        self.assertIn("images", self.get("/api/data")[1])

    def test_import_data_rejects_junk_and_merges(self):
        code, _ = self.post("/api/data", {"data": "no soy datos"})
        self.assertEqual(code, 400)
        # Al fusionar, lo mas reciente gana y no se pisa lo propio.
        self.post("/api/recipe", {"name": "Propia"})
        remota = {"recipes": {"x": {"id": "x", "name": "Remota", "updated": 1}}}
        self.assertEqual(self.store.import_data(remota)["recipes"], 2)


    def test_template_round_trip(self):
        # Una semana con dos platos en la misma comida se guarda y se
        # aplica igual en otra semana vacia.
        a = self.post("/api/recipe", {"name": "Lentejas", "ingredients": ["200 g de lentejas"]})[1]["id"]
        b = self.post("/api/recipe", {"name": "Natillas", "ingredients": ["500 ml de leche"]})[1]["id"]
        w1, w2 = "2026-10-05", "2026-10-12"
        self.post("/api/menu/set", {"week": w1, "day": 0, "meal": "lunch",
                                    "recipe_id": a, "course": "primero"})
        self.post("/api/menu/set", {"week": w1, "day": 0, "meal": "lunch",
                                    "recipe_id": b, "course": "postre"})

        code, r = self.post("/api/template/save", {"name": "Normal", "week": w1})
        self.assertEqual(code, 200)
        self.assertEqual(r["dishes"], 2)
        self.assertEqual([t["name"] for t in self.get("/api/templates")[1]["items"]], ["Normal"])

        code, r = self.post("/api/template/apply", {"name": "Normal", "week": w2})
        self.assertEqual(code, 200)
        self.assertEqual(r["skipped"], 0)
        comidas = self.get("/api/menu?week=" + w2)[1]["days"][0]["meals"]["lunch"]
        self.assertEqual(sorted(comidas), ["postre", "primero"])

        # La compra de la semana nueva trae los dos ingredientes.
        items = self.get("/api/shopping?week=" + w2)[1]["items"]
        self.assertEqual(len(items), 2)

    def test_template_guards(self):
        w = "2026-10-05"
        code, r = self.post("/api/template/save", {"name": "Vacia", "week": w})
        self.assertEqual(code, 400)
        self.assertIn("vacía", r["error"])
        self.assertEqual(self.post("/api/template/save", {"name": "", "week": w})[0], 400)
        self.assertEqual(self.post("/api/template/apply", {"name": "NoExiste"})[0], 400)
        self.assertEqual(self.post("/api/template/delete", {"name": "NoExiste"})[0], 400)

    def test_template_skips_deleted_recipes(self):
        # Si borras una receta, la plantilla sigue existiendo pero al
        # aplicarla avisa de que se salto ese plato.
        a = self.post("/api/recipe", {"name": "SeVa"})[1]["id"]
        w1, w2 = "2026-10-05", "2026-10-12"
        self.post("/api/menu/set", {"week": w1, "day": 0, "meal": "lunch",
                                    "recipe_id": a, "course": "unico"})
        self.post("/api/template/save", {"name": "Normal", "week": w1})
        self.post("/api/recipe/%s/delete" % a, {})
        code, r = self.post("/api/template/apply", {"name": "Normal", "week": w2})
        self.assertEqual(code, 200)
        self.assertEqual(r["skipped"], 1)


    def test_menu_deletion_survives_a_sync(self):
        # Vaciar una semana en un dispositivo debe seguir vacia despues de
        # sincronizar en los dos sentidos: si no, los menus "resucitan".
        rid = self.post("/api/recipe", {"name": "Paella"})[1]["id"]
        w1, w2 = "2026-10-05", "2026-10-12"
        for d in range(3):
            self.post("/api/menu/set", {"week": w1, "day": d, "meal": "lunch",
                                        "recipe_id": rid, "course": "unico"})
        otra = Store(os.path.join(self.tmp, "otra"))
        otra.import_data(self.store.export_data())
        self.assertEqual(len(otra.get_menu(w1)["days"][0]["meals"]), 2)

        self.post("/api/menu/clear_week", {"week": w1})
        self.assertEqual(sum(1 for d in self.get("/api/menu?week=" + w1)[1]["days"]
                             for m in d["meals"].values() if m), 0)

        # Sync completo: una se trae, la otra se manda.
        otra.import_data(self.store.export_data())
        self.store.import_data(otra.export_data())
        for store in (self.store, otra):
            self.assertEqual(sum(1 for d in store.get_menu(w1)["days"]
                                 for m in d["meals"].values() if m), 0)
        # La otra semana no se toca.
        self.post("/api/menu/set", {"week": w2, "day": 0, "meal": "dinner",
                                    "recipe_id": rid, "course": "unico"})
        otra.import_data(self.store.export_data())
        self.assertEqual(otra.get_menu(w2)["days"][0]["meals"]["dinner"]["unico"]["name"],
                         "Paella")

    def test_templates_travel_in_both_directions(self):
        # Plantillas distintas en cada lado: al sincronizar se unen.
        rid = self.post("/api/recipe", {"name": "Paella"})[1]["id"]
        w = "2026-10-05"
        self.post("/api/menu/set", {"week": w, "day": 0, "meal": "lunch",
                                    "recipe_id": rid, "course": "unico"})
        self.post("/api/template/save", {"name": "Del PC", "week": w})

        otra = Store(os.path.join(self.tmp, "otra"))
        otra.import_data(self.store.export_data())
        otra.set_slot(w, 1, "lunch", rid)
        otra.save_template("De la tablet", w)
        self.assertEqual([t["name"] for t in otra.list_templates()], ["De la tablet", "Del PC"])

        self.store.import_data(otra.export_data())
        self.assertEqual([t["name"] for t in self.store.list_templates()],
                         ["De la tablet", "Del PC"])


    def test_deleted_template_does_not_come_back(self):
        # Si borro una plantilla en un lado, sincronizar no debe devolvarla
        # desde el otro: por eso hay lapidas.
        rid = self.post("/api/recipe", {"name": "Paella"})[1]["id"]
        w = "2026-10-05"
        self.post("/api/menu/set", {"week": w, "day": 0, "meal": "lunch",
                                    "recipe_id": rid, "course": "unico"})
        self.post("/api/template/save", {"name": "Se va", "week": w})

        otra = Store(os.path.join(self.tmp, "otra"))
        otra.import_data(self.store.export_data())
        self.assertEqual([t["name"] for t in otra.list_templates()], ["Se va"])

        self.post("/api/template/delete", {"name": "Se va"})
        self.assertEqual(self.get("/api/templates")[1]["items"], [])

        # La tablet, que aun la tiene, no debe poder resucitarla.
        self.store.import_data(otra.export_data())
        self.assertEqual([t["name"] for t in self.store.list_templates()], [])
        otra.import_data(self.store.export_data())
        self.assertEqual([t["name"] for t in otra.list_templates()], [])

        # Pero si la vuelvo a crear con el mismo nombre, esta vez si vale.
        self.post("/api/menu/set", {"week": w, "day": 1, "meal": "lunch",
                                    "recipe_id": rid, "course": "unico"})
        self.post("/api/template/save", {"name": "Se va", "week": w})
        otra.import_data(self.store.export_data())
        self.assertEqual([t["name"] for t in otra.list_templates()], ["Se va"])


if __name__ == "__main__":
    unittest.main()
