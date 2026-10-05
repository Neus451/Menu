"""Lista de la compra: suma ingredientes de varias recetas.

"200 g harina" + "100 g de harina" -> "300 g harina".
Entiende fracciones (1/2, ½, 1 1/2), decimales con coma y unidades habituales
en español e inglés. Lo que no se entiende se deja tal cual.
"""
import re
import unicodedata

_FRACTIONS = {"¼": 0.25, "½": 0.5, "¾": 0.75, "⅓": 1 / 3, "⅔": 2 / 3, "⅛": 0.125}

# unidad escrita -> (unidad canónica, factor a la unidad base del grupo)
_UNITS = {}
# palabras que solo significan "unidades" (algunas webs escriben "1count")
_NO_UNIT = {"count", "counts", "unidad", "unidades", "ud", "uds", "unit", "units"}

# ingredientes que se ponen en las recetas pero NO van a la lista de la compra
_EXCLUDED_FROM_SHOPPING = {"agua", "sal"}


def _reg(canon, base, factor, *names):
    for n in names:
        _UNITS[n] = (canon, base, factor)


_reg("g", "g", 1, "g", "gr", "grs", "gramo", "gramos", "gram", "grams")
_reg("kg", "g", 1000, "kg", "kgs", "kilo", "kilos", "kilogramo", "kilogramos")
_reg("ml", "ml", 1, "ml", "mililitro", "mililitros")
_reg("cl", "ml", 10, "cl", "centilitro", "centilitros")
_reg("dl", "ml", 100, "dl", "decilitro", "decilitros")
_reg("l", "ml", 1000, "l", "lt", "litro", "litros", "liter", "liters", "litre", "litres")
for _names, _canon in (
        (("cucharada", "cucharadas", "cda", "cdas", "tbsp", "tablespoon", "tablespoons"), "cucharada"),
        (("cucharadita", "cucharaditas", "cdta", "cdtas", "cdita", "cditas", "tsp", "teaspoon", "teaspoons"), "cucharadita"),
        (("taza", "tazas", "cup", "cups"), "taza"),
        (("pizca", "pizcas", "pinch"), "pizca"),
        (("diente", "dientes", "clove", "cloves"), "diente"),
        (("lata", "latas", "can", "cans"), "lata"),
        (("paquete", "paquetes", "sobre", "sobres", "package", "packages"), "paquete"),
        (("rama", "ramas", "ramita", "ramitas", "sprig", "sprigs"), "rama"),
        (("hoja", "hojas", "leaf", "leaves"), "hoja"),
        (("punado", "punados", "handful"), "puñado"),
        (("oz", "onza", "onzas", "ounce", "ounces"), "oz"),
        (("lb", "lbs", "libra", "libras", "pound", "pounds"), "lb"),
):
    _reg(_canon, _canon, 1, *_names)


def fold(text):
    """Minúsculas y sin tildes, para comparar."""
    text = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in text if unicodedata.category(c) != "Mn")


def _singular(word):
    if len(word) > 4 and word.endswith("es") and word[-3] in "nlrdz":
        return word[:-2]
    if len(word) > 3 and word.endswith("s"):
        return word[:-1]
    return word


def _key(name):
    return " ".join(_singular(w) for w in fold(name).split())


_NUM = r"(?:\d+\s+\d+/\d+|\d+/\d+|\d+(?:[.,]\d+)?)"
_QTY_RE = re.compile(
    r"^\s*(?P<a>(?:" + _NUM + r"\s*[" + "".join(_FRACTIONS) + r"]?|[" + "".join(_FRACTIONS) + r"]))"
    r"(?:\s*(?:-|–|a|to)\s*(?P<b>" + _NUM + r"))?\s*(?P<rest>.*)$", re.I)


def _to_number(token):
    token = token.strip()
    total = 0.0
    for ch, val in _FRACTIONS.items():
        if ch in token:
            total += val
            token = token.replace(ch, "")
    token = token.strip()
    if token:
        m = re.match(r"^(\d+)\s+(\d+)/(\d+)$", token)
        if m:
            total += int(m.group(1)) + int(m.group(2)) / int(m.group(3))
        elif "/" in token:
            n, d = token.split("/")
            total += int(n) / int(d)
        else:
            total += float(token.replace(",", "."))
    return total


def parse_ingredient(line):
    """-> dict(name, qty|None, unit|None, base|None). qty ya en unidad base si hay conversión."""
    text = re.sub(r"\([^)]*\)", "", line).strip()
    text = re.split(r",(?!\d)", text)[0].strip() or line.strip()
    m = _QTY_RE.match(text)
    if not m:
        return {"name": text, "qty": None, "unit": None}
    try:
        qty = _to_number(m.group("b") or m.group("a"))   # en rangos "2-3" se compra el mayor
    except (ValueError, ZeroDivisionError):
        return {"name": text, "qty": None, "unit": None}
    rest = m.group("rest").strip()
    unit = None
    um = re.match(r"^([A-Za-zñÑáéíóúÁÉÍÓÚ]+)\.?\s*(.*)$", rest)
    if um and fold(um.group(1)) in _UNITS:
        canon, base, factor = _UNITS[fold(um.group(1))]
        unit, qty, rest = base, qty * factor, um.group(2).strip()
    elif um and fold(um.group(1)) in _NO_UNIT:
        rest = um.group(2).strip()           # "2count Dientes de ajo" -> 2 + "Dientes de ajo"
    rest = re.sub(r"^(?:de|del|of|d')\s+", "", rest, flags=re.I).strip()
    if not rest:
        return {"name": text, "qty": None, "unit": None}
    return {"name": rest, "qty": qty, "unit": unit}


def _fmt_number(x):
    x = round(x, 2)
    return str(int(x)) if x == int(x) else str(x).replace(".", ",")


_METRIC = {"g", "kg", "ml", "l", "cl", "dl", "oz", "lb"}


def format_quantity(qty, unit):
    if qty is None:
        return ""
    if unit == "g" and qty >= 1000:
        return _fmt_number(qty / 1000) + " kg"
    if unit == "ml" and qty >= 1000:
        return _fmt_number(qty / 1000) + " l"
    if unit and unit not in _METRIC and qty > 1:
        unit += "s"                      # 2 cucharadas, 3 dientes...
    return (_fmt_number(qty) + (" " + unit if unit else "")).strip()


def build_list(entries):
    """entries: lista de (nombre_receta, [líneas de ingredientes]).
    Una receta repetida se pasa varias veces y suma sus cantidades."""
    groups = {}
    for recipe_name, lines in entries:
        for line in lines:
            p = parse_ingredient(line)
            if fold(p["name"]) in _EXCLUDED_FROM_SHOPPING:
                continue                      # agua y sal: se usan, pero no se compran
            key = (_key(p["name"]), p["unit"] if p["qty"] is not None else None)
            g = groups.setdefault(key, {"name": p["name"], "qty": None, "unit": p["unit"],
                                        "recipes": [], "lines": []})
            if p["qty"] is not None:
                g["qty"] = (g["qty"] or 0) + p["qty"]
            if recipe_name not in g["recipes"]:
                g["recipes"].append(recipe_name)
            g["lines"].append(line)
    items = []
    for g in groups.values():
        qty_text = format_quantity(g["qty"], g["unit"])
        name = g["name"]
        # "2 cebolla" -> "2 cebollas" (solo palabras sueltas acabadas en vocal, sin unidad)
        if (g["qty"] or 0) > 1 and not g["unit"] and re.match(r"^[^\W\d_]+$", name) and name[-1].lower() in "aeiou":
            name += "s"
        items.append({
            "name": name,
            "quantity": qty_text,
            "text": ((qty_text + (" de " if g["unit"] else " ") + name).strip()
                     if g["qty"] is not None else g["lines"][0]),
            "recipes": g["recipes"],
        })
    items.sort(key=lambda i: fold(i["name"]))
    return items


def to_text(items, title="Lista de la compra"):
    lines = [title, "=" * len(title), ""]
    lines += ["[ ] " + i["text"] for i in items] or ["(vacía)"]
    return "\n".join(lines) + "\n"
