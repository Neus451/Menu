"""Almacenamiento local (un único archivo JSON), menú semanal y copia a carpeta.

Todo vive en `cookbook.json` dentro de la carpeta de datos de la app. La copia
a "Drive" es simplemente ese mismo archivo (más las fotos) en una carpeta que
elige el usuario; con `sync_folder` se fusionan los cambios de los dos lados.
"""
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
    return {"version": 1, "recipes": {}, "menu": {}, "deleted": {}, "settings": {}, "robots": list(DEFAULT_ROBOTS)}


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

    for week in set(local.get("menu", {})) | set(remote.get("menu", {})):
        cands = [m for m in (local.get("menu", {}).get(week), remote.get("menu", {}).get(week)) if m]
        out["menu"][week] = max(cands, key=lambda m: m.get("updated", 0))
    for week in out["menu"].values():
        slots = {}
        for key, value in week.get("slots", {}).items():
            if isinstance(value, str):          # formato antiguo: una receta por comida
                value = {"unico": value}
            kept = {c: rid for c, rid in value.items() if rid in out["recipes"]}
            if kept:
                slots[key] = kept
        week["slots"] = slots
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
        return {k: self.data[k] for k in ("version", "recipes", "menu", "deleted", "robots")}

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

    def import_from_folder(self, folder, replace=False):
        path = os.path.join(folder, DATA_FILE)
        if not os.path.exists(path):
            raise StoreError("No hay ninguna copia (cookbook.json) en esa carpeta.")
        try:
            with open(path, encoding="utf-8") as fh:
                remote = json.load(fh)
        except (ValueError, OSError):
            raise StoreError("El archivo de la copia está dañado o no se puede leer.")
        before = set(self.data["recipes"])
        if replace:
            merged = _empty()
            merged.update({k: remote.get(k, merged[k]) for k in ("recipes", "menu", "deleted", "robots")})
            merged["settings"] = self.data["settings"]
        else:
            merged = merge_data(self.data, remote)
        self.data = merged
        self._copy_images(os.path.join(folder, "images"), self.images_dir)
        self.save()
        after = set(self.data["recipes"])
        return {"added": len(after - before), "removed": len(before - after), "recipes": len(after)}

    def sync_folder(self, folder):
        """Trae los cambios de la carpeta y deja allí la versión fusionada."""
        result = {"added": 0, "removed": 0}
        if os.path.exists(os.path.join(folder, DATA_FILE)):
            result = self.import_from_folder(folder)
        result.update(self.export_to_folder(folder))
        return result
