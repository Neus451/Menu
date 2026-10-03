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
        self.tmp = tempfile.mkdtemp()
        self.store = Store(os.path.join(self.tmp, "datos"))
        holder = {}
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), recetario_pc.make_handler(self.store, lambda: holder["s"]))
        holder["s"] = self.server
        self.base = "http://127.0.0.1:%d" % self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
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
        self.post("/api/recipe", {"name": "Paella", "ingredients": "arroz"})
        code, data = self.post("/api/sync", {})
        self.assertEqual(code, 400)
        self.assertIn("carpeta", data["error"])
        folder = os.path.join(self.tmp, "Drive")
        self.assertEqual(self.post("/api/settings", {"sync_folder": folder})[0], 200)
        code, data = self.post("/api/sync", {})
        self.assertEqual(code, 200)
        self.assertTrue(os.path.exists(os.path.join(folder, "cookbook.json")))

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


if __name__ == "__main__":
    unittest.main()
