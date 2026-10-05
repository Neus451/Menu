"""Almacenamiento local (un único archivo JSON), menú semanal y copia a carpeta.

Todo vive en `cookbook.json` dentro de la carpeta de datos de la app. La copia
a "Drive" es simplemente ese mismo archivo (más las fotos) en una carpeta que
elige el usuario; con `sync_folder` se fusionan los cambios de los dos lados.
"""
import base64
import datetime
import json
import os
import shutil
import time
import uuid

from . import recipe_parser, shopping

DATA_FILE = "cookbook.json"
MEALS = ("lunch", "dinner")
MEAL_NAMES_ES = {"lunch": "Almuerzo", "dinner": "Cena"}
DAY_NAMES_ES = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]

RECIPE_FIELDS = ("name", "description", "image_url", "image_file", "ingredients", "instructions",
                 "prep_minutes", "cook_minutes", "total_minutes", "servings", "category",
                 "keywords", "source_url", "notes", "robot")

# Tipos de plato dentro de cada comida
COURSES = ("primero", "segundo", "postre", "unico")
COURSE_NAMES_ES = {"primero": "Primero", "segundo": "Segundo", "postre": "Postre", "unico": "Plato único"}

DEFAULT_ROBOTS = ["Thermomix", "Monsieur Cuisine", "Mambo", "Cookeo", "Sin robot"]


class StoreError(Exception):
    """Error con mensaje para el usuario."""


def _now():
    return round(time.time(), 3)


def monday_of(day=None):
    """Lunes (como 'AAAA-MM-DD') de la semana que contiene `day` (str o date). Hoy si es None."""
    if day is None:
        day = datetime.date.today()
    elif isinstance(day, str):
        day = datetime.date.fromisoformat(day)
    return (day - datetime.timedelta(days=day.weekday())).isoformat()


def next_monday(today=None):
    return (datetime.date.fromisoformat(monday_of(today)) + datetime.timedelta(days=7)).isoformat()


def _empty():
    return {"version": 1, "recipes": {}, "menu": {}, "deleted": {}, "settings": {},
            "robots": list(DEFAULT_ROBOTS), "templates": {},
            # Plantillas borradas, con su fecha: si no, al sincronizar la otra
            # copia las traeria de vuelta (una lapida cada plantilla).
            "templates_deleted": {}}


def _clean_slots(slots, recipes):
    """Quita las claves vacias y las recetas que ya no existan."""
    out = {}
    for key, value in slots.items():
        courses = value if isinstance(value, dict) else {"unico": value}
        kept = {c: rid for c, rid in courses.items() if rid in recipes}
        if kept:
            out[key] = kept
    return out


def _atomic_write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=1)
        fh.flush()
        os.fsync(fh.fileno())
    if os.path.exists(path):
        shutil.copy2(path, path + ".bak")
    os.replace(tmp, path)


def _fold(text):
    return shopping.fold(text or "")


# --------------------------------------------------------------------------
# Fusión de datos de dos dispositivos (función pura, fácil de probar)
# --------------------------------------------------------------------------
def merge_data(local, remote):
    """Fusiona dos conjuntos de datos. Gana lo más reciente; los borrados se respetan."""
    out = _empty()
    out["settings"] = dict(local.get("settings", {}))
    robots = {r for r in local.get("robots", [])} | {r for r in remote.get("robots", [])}
    out["robots"] = sorted(robots, key=lambda r: _fold(r))
    deleted = dict(local.get("deleted", {}))
    for rid, ts in remote.get("deleted", {}).items():
        deleted[rid] = max(ts, deleted.get(rid, 0))

    ids = set(local.get("recipes", {})) | set(remote.get("recipes", {}))
    for rid in ids:
        candidates = [r for r in (local.get("recipes", {}).get(rid), remote.get("recipes", {}).get(rid)) if r]
        best = max(candidates, key=lambda r: r.get("updated", 0))
        if deleted.get(rid, 0) > best.get("updated", 0):
            continue                      # borrada después de su última edición
        out["recipes"][rid] = best
        deleted.pop(rid, None)            # editada después de borrarse: sigue viva
    out["deleted"] = deleted

    # Lapidas de plantillas: se fusionan por fecha maxima.
    lapidas = dict(local.get("templates_deleted") or {})
    for name, ts in (remote.get("templates_deleted") or {}).items():
        lapidas[name] = max(ts, lapidas.get(name, 0))
    out["templates_deleted"] = lapidas

    # plantillas: unión, y a igualdad de fecha gana la remota (igual que robots)
    templates = {}
    for origen in (local, remote):
        for name, tpl in (origen.get("templates") or {}).items():
            if name not in templates or (tpl.get("updated", 0) > templates[name].get("updated", 0)):
                templates[name] = tpl
    # ...pero si la otra copia la borro despues de esta fecha, se respeta.
    for name in list(templates):
        if lapidas.get(name, 0) > templates[name].get("updated", 0):
            del templates[name]
    out["templates"] = templates

    for week in set(local.get("menu", {})) | set(remote.get("menu", {})):
        cands = [m for m in (local.get("menu", {}).get(week), remote.get("menu", {}).get(week)) if m]
        out["menu"][week] = max(cands, key=lambda m: m.get("updated", 0))
    for week in out["menu"].values():
        week["slots"] = _clean_slots(week.get("slots") or {}, out["recipes"])
    for tpl in out["templates"].values():
        tpl["slots"] = _clean_slots(tpl.get("slots") or {}, out["recipes"])
    return out


class Store:
    def __init__(self, data_dir):
        self.data_dir = data_dir
        self.images_dir = os.path.join(data_dir, "images")
        os.makedirs(self.images_dir, exist_ok=True)
        self.path = os.path.join(data_dir, DATA_FILE)
        self.data = self._load()

    # ---- persistencia ----
    def _load(self):
        for candidate in (self.path, self.path + ".bak"):
            if os.path.exists(candidate):
                try:
                    with open(candidate, encoding="utf-8") as fh:
                        data = json.load(fh)
                    base = _empty()
                    base.update(data)
                    if "robots" not in data:
                        base["robots"] = list(DEFAULT_ROBOTS)
                    for week in base.get("menu", {}).values():
                        for key, value in list(week.get("slots", {}).items()):
                            if isinstance(value, str):      # formato antiguo
                                week["slots"][key] = {"unico": value}
                    return base
                except (ValueError, OSError):
                    continue
        return _empty()

    def save(self):
        _atomic_write_json(self.path, self.data)

    # ---- recetas ----
    def _summary(self, r):
        return {k: r.get(k) for k in ("id", "name", "category", "robot", "total_minutes", "servings", "image_file", "keywords")}

    def image_path(self, recipe):
        f = recipe.get("image_file")
        return os.path.join(self.images_dir, f) if f else ""

    def add_recipe(self, fields):
        name = (fields.get("name") or "").strip()
        if not name:
            raise StoreError("La receta necesita un nombre.")
        rid = uuid.uuid4().hex[:12]
        r = {k: fields.get(k) for k in RECIPE_FIELDS}
        r.update(id=rid, name=name, created=_now(), updated=_now())
        for k in ("ingredients", "instructions", "keywords"):
            r[k] = [str(x).strip() for x in (r[k] or []) if str(x).strip()]
        self.data["recipes"][rid] = r
        self.data["deleted"].pop(rid, None)
        self.save()
        return r

    def update_recipe(self, rid, fields):
        r = self.get_recipe(rid)
        for k in RECIPE_FIELDS:
            if k in fields:
                r[k] = fields[k]
        if not (r.get("name") or "").strip():
            raise StoreError("La receta necesita un nombre.")
        for k in ("ingredients", "instructions", "keywords"):
            r[k] = [str(x).strip() for x in (r.get(k) or []) if str(x).strip()]
        r["updated"] = _now()
        self.save()
        return r

    def get_recipe(self, rid):
        r = self.data["recipes"].get(rid)
        if not r:
            raise StoreError("Esa receta ya no existe.")
        return r

    def delete_recipe(self, rid):
        self.get_recipe(rid)
        r = self.data["recipes"].pop(rid)
        if r.get("image_file"):
            try:
                os.remove(os.path.join(self.images_dir, r["image_file"]))
            except OSError:
                pass
        ts = _now()
        self.data["deleted"][rid] = ts
        for week in self.data["menu"].values():
            slots = week["slots"]
            changed = False
            for k in list(slots):
                v = slots[k]
                if isinstance(v, str):
                    if v == rid:
                        del slots[k]
                        changed = True
                else:
                    rest = {c: r for c, r in v.items() if r != rid}
                    if rest != v:
                        changed = True
                        if rest:
                            slots[k] = rest
                        else:
                            del slots[k]
            if changed:
                week["updated"] = ts
        self.save()

    def list_recipes(self, query="", category="", robot=""):
        q = _fold(query).split()
        out = []
        for r in self.data["recipes"].values():
            if category and r.get("category") != category:
                continue
            if robot and (r.get("robot") or "") != robot:
                continue
            hay = _fold(" ".join([r["name"], r.get("category") or "", r.get("robot") or "", " ".join(r.get("keywords") or []),
                                  " ".join(r.get("ingredients") or [])]))
            if all(w in hay for w in q):
                out.append(self._summary(r))
        out.sort(key=lambda s: _fold(s["name"]))
        return out

    def categories(self):
        counts = {}
        for r in self.data["recipes"].values():
            c = r.get("category") or ""
            counts[c] = counts.get(c, 0) + 1
        return [{"name": c, "count": n} for c, n in sorted(counts.items(), key=lambda kv: _fold(kv[0]))]

    # ---- ingredientes ----
    def list_ingredients(self):
        """Ingredientes usados en las recetas: nombre, nº de recetas y ejemplos."""
        counts = {}
        for r in self.data["recipes"].values():
            seen = set()
            for line in r.get("ingredients") or []:
                name = shopping.parse_ingredient(line)["name"]
                if not name:
                    continue
                key = _fold(name)
                if key in seen:
                    continue
                seen.add(key)
                g = counts.setdefault(key, {"name": name, "count": 0, "lines": []})
                g["count"] += 1
                if len(g["lines"]) < 3:
                    g["lines"].append(line)
        out = list(counts.values())
        out.sort(key=lambda g: _fold(g["name"]))
        return out

    def rename_ingredient(self, old, new):
        """Cambia el nombre de un ingrediente en todas las recetas."""
        old = (old or "").strip()
        new = (new or "").strip()
        if not old or not new:
            raise StoreError("Falta el nombre del ingrediente.")
        if _fold(old) == _fold(new):
            raise StoreError("El nombre nuevo es igual al anterior.")
        key = _fold(old)
        changed = 0
        for r in self.data["recipes"].values():
            lines = r.get("ingredients")
            if not lines:
                continue
            touched = False
            for i, line in enumerate(lines):
                p = shopping.parse_ingredient(line)
                if _fold(p["name"]) == key:
                    qty_text = shopping.format_quantity(p["qty"], p["unit"])
                    if qty_text:
                        lines[i] = (qty_text + (" de " if p["unit"] else " ") + new).strip()
                    else:
                        lines[i] = new
                    touched = True
                    changed += 1
            if touched:
                r["updated"] = _now()
        if not changed:
            raise StoreError("No se encontró «%s» en ninguna receta." % old)
        self.save()
        return changed

    def delete_ingredient(self, name):
        """Quita un ingrediente de todas las recetas."""
        name = (name or "").strip()
        if not name:
            raise StoreError("Falta el nombre del ingrediente.")
        key = _fold(name)
        changed = 0
        for r in self.data["recipes"].values():
            lines = r.get("ingredients")
            if not lines:
                continue
            kept = [ln for ln in lines
                    if _fold(shopping.parse_ingredient(ln)["name"]) != key]
            if len(kept) != len(lines):
                r["ingredients"] = kept
                r["updated"] = _now()
                changed += len(lines) - len(kept)
        if not changed:
            raise StoreError("No se encontró «%s» en ninguna receta." % name)
        self.save()
        return changed

    # ---- robots de cocina ----
    def robots(self):
        return sorted(self.data.get("robots", []), key=lambda r: _fold(r))

    def add_robot(self, name):
        name = (name or "").strip()
        if not name:
            raise StoreError("Escribe un nombre para el robot.")
        if name.lower() in [r.lower() for r in self.data.get("robots", [])]:
            raise StoreError("Ese robot ya está en la lista.")
        self.data.setdefault("robots", []).append(name)
        self.save()
        return name

    def rename_robot(self, old, new):
        new = (new or "").strip()
        if not new:
            raise StoreError("Escribe un nombre nuevo.")
        robots = self.data.setdefault("robots", [])
        real = next((r for r in robots if r.lower() == (old or "").lower()), None)
        if real is None:
            raise StoreError("Ese robot no existe.")
        robots[robots.index(real)] = new
        for r in self.data["recipes"].values():
            if (r.get("robot") or "").lower() == real.lower():
                r["robot"] = new
                r["updated"] = _now()
        self.save()

    def delete_robot(self, name):
        robots = self.data.setdefault("robots", [])
        real = next((r for r in robots if r.lower() == (name or "").lower()), None)
        if real is None:
            raise StoreError("Ese robot no existe.")
        robots.remove(real)
        for r in self.data["recipes"].values():
            if (r.get("robot") or "").lower() == real.lower():
                r["robot"] = ""
                r["updated"] = _now()
        self.save()

    def import_from_url(self, url, allow_duplicate=False):
        """Descarga y guarda una receta desde una web. Lanza RecipeImportError."""
        url = recipe_parser.normalize_url(url)
        if not allow_duplicate:
            for r in self.data["recipes"].values():
                if r.get("source_url") == url:
                    raise recipe_parser.RecipeImportError("Ya tienes esta receta: «%s»." % r["name"])
        parsed = recipe_parser.import_recipe(url)
        r = self.add_recipe(parsed)
        if parsed.get("image_url"):
            saved = recipe_parser.download_image(parsed["image_url"], os.path.join(self.images_dir, r["id"]))
            if saved:
                r = self.update_recipe(r["id"], {"image_file": os.path.basename(saved)})
        return r

    # ---- menú semanal ----
    def _week(self, week, create=False):
        week = monday_of(week)
        m = self.data["menu"].get(week)
        if m is None and create:
            m = self.data["menu"][week] = {"updated": _now(), "slots": {}}
        return week, m

    def get_menu(self, week=None):
        week, m = self._week(week)
        slots = m["slots"] if m else {}
        start = datetime.date.fromisoformat(week)
        days = []
        for d in range(7):
            meals = {}
            for meal in MEALS:
                slot = slots.get("%d_%s" % (d, meal)) or {}
                if isinstance(slot, str):       # formato antiguo
                    slot = {"unico": slot}
                dishes = {}
                for course, rid in slot.items():
                    r = self.data["recipes"].get(rid)
                    if r:
                        dishes[course] = self._summary(r)
                meals[meal] = dishes or None
            days.append({"index": d, "name": DAY_NAMES_ES[d],
                         "date": (start + datetime.timedelta(days=d)).isoformat(), "meals": meals})
        return {"week": week, "days": days}

    def set_slot(self, week, day, meal, rid, course="unico"):
        if day not in range(7) or meal not in MEALS:
            raise StoreError("Día o comida no válidos.")
        if course not in COURSES:
            raise StoreError("Tipo de plato no válido.")
        self.get_recipe(rid)
        week, m = self._week(week, create=True)
        slot = m["slots"].setdefault("%d_%s" % (day, meal), {})
        if isinstance(slot, str):
            slot = {"unico": slot}
        slot[course] = rid
        m["slots"]["%d_%s" % (day, meal)] = slot
        m["updated"] = _now()
        self.save()

    def clear_slot(self, week, day, meal, course=None):
        week, m = self._week(week)
        if not m:
            return
        key = "%d_%s" % (day, meal)
        slot = m["slots"].get(key)
        if isinstance(slot, str) or course is None:
            if m["slots"].pop(key, None) is not None:
                m["updated"] = _now()
                self.save()
            return
        if course in slot:
            del slot[course]
            if slot:
                m["slots"][key] = slot
            else:
                del m["slots"][key]
            m["updated"] = _now()
            self.save()

    def clear_week(self, week):
        week, m = self._week(week)
        if m and m["slots"]:
            m["slots"] = {}
            m["updated"] = _now()
            self.save()

    def copy_week(self, src, dst):
        _, s = self._week(src)
        if not s or not s["slots"]:
            raise StoreError("La semana de origen está vacía.")
        _, d = self._week(dst, create=True)
        d["slots"] = {k: dict(v) if isinstance(v, dict) else {"unico": v}
                      for k, v in s["slots"].items()}
        d["updated"] = _now()
        self.save()

    # ---- plantillas de menú (patrones que se repiten cada semana) ----
    def list_templates(self):
        out = []
        for name, tpl in (self.data.get("templates") or {}).items():
            slots = tpl.get("slots") or {}
            out.append({"name": name, "updated": tpl.get("updated", 0),
                        "dishes": sum(len(v) for v in slots.values())})
        out.sort(key=lambda t: _fold(t["name"]))
        return out

    def save_template(self, name, week=None):
        name = (name or "").strip()
        if not name:
            raise StoreError("Ponle un nombre a la plantilla.")
        _, m = self._week(week or monday_of())
        slots = _clean_slots((m or {}).get("slots") or {}, self.data["recipes"])
        if not slots:
            raise StoreError("Esa semana está vacía: no hay nada que guardar.")
        self.data.setdefault("templates", {})[name] = {"updated": _now(), "slots": slots}
        # Si estaba borrada antes, la lapida ya no vale.
        self.data.setdefault("templates_deleted", {}).pop(name, None)
        self.save()
        return {"name": name, "dishes": sum(len(v) for v in slots.values())}

    def delete_template(self, name):
        if name not in (self.data.get("templates") or {}):
            raise StoreError("No existe esa plantilla.")
        del self.data["templates"][name]
        # Lapida: al sincronizar, la otra copia no la debe resucitar.
        self.data.setdefault("templates_deleted", {})[name] = _now()
        self.save()

    def apply_template(self, name, week=None, replace=True):
        tpl = (self.data.get("templates") or {}).get(name)
        if not tpl:
            raise StoreError("No existe esa plantilla.")
        week, m = self._week(week or monday_of(), create=True)
        if replace:
            m["slots"] = {}
        skipped = 0
        for key, courses in (tpl.get("slots") or {}).items():
            for course, rid in (courses if isinstance(courses, dict) else {"unico": courses}).items():
                if rid not in self.data["recipes"]:
                    skipped += 1            # receta borrada: se avisa al aplicar
                    continue
                slot = m["slots"].setdefault(key, {})
                if isinstance(slot, str):
                    slot = m["slots"][key] = {"unico": slot}
                slot[course] = rid
        m["updated"] = _now()
        self.save()
        return {"week": week, "dishes": sum(len(v) for v in m["slots"].values()),
                "skipped": skipped}

    def shopping_list(self, week=None):
        _, m = self._week(week)
        entries = []
        for key in sorted((m or {"slots": {}})["slots"]):
            slot = m["slots"][key]
            if isinstance(slot, str):
                slot = {"unico": slot}
            for rid in slot.values():
                r = self.data["recipes"].get(rid)
                if r:
                    entries.append((r["name"], r.get("ingredients") or []))
        return shopping.build_list(entries)

    def week_text(self, week=None):
        menu = self.get_menu(week)
        d0 = datetime.date.fromisoformat(menu["week"])
        title = "Menú de la semana del %s" % d0.strftime("%d/%m/%Y")
        lines = [title, "=" * len(title), ""]
        for day in menu["days"]:
            lines.append("%s %s" % (day["name"], datetime.date.fromisoformat(day["date"]).strftime("%d/%m")))
            for meal in MEALS:
                dishes = day["meals"][meal] or {}
                if not dishes:
                    lines.append("  %s: —" % MEAL_NAMES_ES[meal])
                    continue
                parts = ["%s" % dishes[c]["name"] if len(dishes) == 1 and c == "unico"
                         else "%s: %s" % (COURSE_NAMES_ES.get(c, c), dishes[c]["name"])
                         for c in COURSES if c in dishes]
                lines.append("  %s: %s" % (MEAL_NAMES_ES[meal], " · ".join(parts)))
            lines.append("")
        return "\n".join(lines)

    # ---- ajustes (solo locales, no se copian) ----
    def get_settings(self):
        return dict(self.data["settings"])

    def set_setting(self, key, value):
        self.data["settings"][key] = value
        self.save()

    # ---- copia / sincronización con carpeta ----
    def _shared(self):
        return {k: self.data[k] for k in ("version", "recipes", "menu", "deleted", "robots",
                                          "templates", "templates_deleted")}

    def _copy_images(self, src_dir, dst_dir):
        n = 0
        if not os.path.isdir(src_dir):
            return 0
        os.makedirs(dst_dir, exist_ok=True)
        for f in os.listdir(src_dir):
            s, d = os.path.join(src_dir, f), os.path.join(dst_dir, f)
            if os.path.isfile(s) and (not os.path.exists(d) or os.path.getsize(d) != os.path.getsize(s)):
                shutil.copy2(s, d)
                n += 1
        return n

    def export_to_folder(self, folder, weeks=None):
        """Escribe cookbook.json, fotos y el menú / compra de la semana en texto legible."""
        os.makedirs(folder, exist_ok=True)
        _atomic_write_json(os.path.join(folder, DATA_FILE), self._shared())
        images = self._copy_images(self.images_dir, os.path.join(folder, "images"))
        files = [DATA_FILE]
        for week in (weeks or [monday_of(), next_monday()]):
            _, m = self._week(week)
            if not m or not m["slots"]:
                continue
            for name, text in (("menu-%s.txt" % monday_of(week), self.week_text(week)),
                               ("compra-%s.txt" % monday_of(week),
                                shopping.to_text(self.shopping_list(week)))):
                with open(os.path.join(folder, name), "w", encoding="utf-8") as fh:
                    fh.write(text)
                files.append(name)
        return {"recipes": len(self.data["recipes"]), "images": images, "files": files}

    def export_data(self, with_images=True):
        """Todo lo compartible, para llevarlo a otro dispositivo.

        Con with_images las fotos van en base64, para poder cruzar el cable
        sin copiar carpetas a mano.
        """
        data = self._shared()
        if with_images:
            data["images"] = {}
            # Una foto que no se puede leer no debe tumbar la exportacion
            # entera: en la tablet AppArmor puede impedir leer ~/Documents.
            try:
                nombres = sorted(os.listdir(self.images_dir))
            except OSError:
                nombres = []
            for name in nombres:
                path = os.path.join(self.images_dir, name)
                try:
                    if not os.path.isfile(path) or os.path.getsize(path) > 12 * 1024 * 1024:
                        continue
                    with open(path, "rb") as fh:
                        data["images"][name] = base64.b64encode(fh.read()).decode("ascii")
                except OSError:
                    continue
        return data

    def import_data(self, remote, replace=False):
        """Carga datos traidos de otro dispositivo.

        Por defecto fusiona (gana lo mas reciente y se respetan los
        borrados); con replace=True se queda solo con lo recibido.
        """
        before = set(self.data["recipes"])
        if replace:
            merged = _empty()
            merged.update({k: remote.get(k, merged[k]) for k in
                           ("recipes", "menu", "deleted", "robots", "templates",
                            "templates_deleted")})
            merged["settings"] = self.data["settings"]
        else:
            merged = merge_data(self.data, remote)
        self.data = merged
        n = self._write_images(remote.get("images") or {})
        self.save()
        after = set(self.data["recipes"])
        return {"added": len(after - before), "removed": len(before - after),
                "recipes": len(after), "images": n}

    def _write_images(self, images):
        """Guarda fotos recibidas en base64. Ignora lo que no cuadre."""
        n = 0
        if not isinstance(images, dict):
            return 0
        os.makedirs(self.images_dir, exist_ok=True)
        for name, b64 in images.items():
            name = os.path.basename(str(name))
            if not name:
                continue
            try:
                raw = base64.b64decode(b64, validate=True)
            except (ValueError, TypeError):
                continue
            path = os.path.join(self.images_dir, name)
            try:
                if os.path.exists(path) and os.path.getsize(path) == len(raw):
                    continue                      # ya esta, no se toca
                with open(path, "wb") as fh:
                    fh.write(raw)
                n += 1
            except OSError:
                continue
        return n

    def import_from_folder(self, folder, replace=False):
        path = os.path.join(folder, DATA_FILE)
        if not os.path.exists(path):
            raise StoreError("No hay ninguna copia (cookbook.json) en esa carpeta.")
        try:
            with open(path, encoding="utf-8") as fh:
                remote = json.load(fh)
        except (ValueError, OSError):
            raise StoreError("El archivo de la copia está dañado o no se puede leer.")
        result = self.import_data(remote, replace)
        self._copy_images(os.path.join(folder, "images"), self.images_dir)
        return result

    def sync_folder(self, folder):
        """Trae los cambios de la carpeta y deja allí la versión fusionada."""
        result = {"added": 0, "removed": 0}
        if os.path.exists(os.path.join(folder, DATA_FILE)):
            result = self.import_from_folder(folder)
        result.update(self.export_to_folder(folder))
        return result
