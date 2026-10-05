"""Lee una receta desde una página web.

La mayoría de webs de cocina incluyen los datos de la receta en un bloque
JSON-LD (schema.org/Recipe). Si no lo tienen se prueba con microdatos y,
como último recurso, con las etiquetas og: de la página.
"""
import html as htmllib
import json
import re
from html.parser import HTMLParser
from urllib.parse import urljoin
from urllib.request import Request, urlopen

USER_AGENT = ("Mozilla/5.0 (X11; Linux x86_64; rv:120.0) "
              "Gecko/20100101 Firefox/120.0 Recetario/0.1")
MAX_HTML_BYTES = 5 * 1024 * 1024
MAX_IMAGE_BYTES = 8 * 1024 * 1024


class RecipeImportError(Exception):
    """Error con un mensaje pensado para mostrarse al usuario."""


# --------------------------------------------------------------------------
# Descarga
# --------------------------------------------------------------------------
def normalize_url(url):
    url = (url or "").strip()
    if not url:
        raise RecipeImportError("Escribe la dirección de una web.")
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    return url


def _request(url, timeout, accept):
    return Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": accept,
        "Accept-Language": "es,en;q=0.8",
    })


def fetch_html(url, timeout=20):
    """Devuelve (html, url_final)."""
    url = normalize_url(url)
    try:
        with urlopen(_request(url, timeout, "text/html,application/xhtml+xml,*/*;q=0.8"),
                     timeout=timeout) as resp:
            raw = resp.read(MAX_HTML_BYTES)
            charset = resp.headers.get_content_charset()
            final_url = resp.geturl()
    except Exception as exc:  # urllib lanza muchos tipos distintos
        code = getattr(exc, "code", None)
        if code:
            raise RecipeImportError(
                "La web respondió con el error %s. Algunas webs bloquean "
                "las descargas automáticas." % code)
        raise RecipeImportError(
            "No se pudo abrir la web (%s). ¿Hay conexión?" % (getattr(exc, "reason", None) or exc))
    if not charset:
        m = re.search(rb'<meta[^>]+charset=["\']?([A-Za-z0-9_\-]+)', raw[:4096], re.I)
        charset = m.group(1).decode("ascii") if m else "utf-8"
    try:
        text = raw.decode(charset, errors="replace")
    except LookupError:
        text = raw.decode("utf-8", errors="replace")
    return text, final_url


def download_image(url, dest_no_ext, timeout=15):
    """Descarga una imagen. Devuelve la ruta guardada (con extensión) o None."""
    try:
        with urlopen(_request(url, timeout, "image/*,*/*;q=0.5"), timeout=timeout) as resp:
            data = resp.read(MAX_IMAGE_BYTES + 1)
    except Exception:
        return None
    if not data or len(data) > MAX_IMAGE_BYTES:
        return None
    if data[:3] == b"\xff\xd8\xff":
        ext = ".jpg"
    elif data[:8] == b"\x89PNG\r\n\x1a\n":
        ext = ".png"
    elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        ext = ".webp"
    elif data[:4] == b"GIF8":
        ext = ".gif"
    else:
        return None
    path = dest_no_ext + ext
    with open(path, "wb") as fh:
        fh.write(data)
    return path


# --------------------------------------------------------------------------
# Utilidades de texto
# --------------------------------------------------------------------------
def clean_text(value):
    """Quita etiquetas HTML y entidades; conserva saltos de línea."""
    if value is None:
        return ""
    s = str(value)
    s = re.sub(r"<\s*br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"</\s*(p|li|div)\s*>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    s = htmllib.unescape(s).replace("\xa0", " ")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\s*\n\s*", "\n", s)
    return s.strip()


def parse_duration(value):
    """'PT1H30M' -> 90 (minutos). Devuelve None si no se entiende."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value) if value > 0 else None
    m = re.match(r"^\s*P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:\d+S)?)?\s*$", str(value), re.I)
    if m and any(m.groups()):
        minutes = (int(m.group(1) or 0) * 24 + int(m.group(2) or 0)) * 60 + int(m.group(3) or 0)
        return minutes or None
    h = re.search(r"(\d+)\s*(?:h|hora)", str(value), re.I)
    mi = re.search(r"(\d+)\s*(?:m|min)", str(value), re.I)
    if h or mi:
        return (int(h.group(1)) * 60 if h else 0) + (int(mi.group(1)) if mi else 0) or None
    return None


def _first_int(value):
    if isinstance(value, list):
        for item in value:
            n = _first_int(item)
            if n:
                return n
        return None
    if isinstance(value, (int, float)):
        return int(value) or None
    m = re.search(r"\d+", str(value or ""))
    return int(m.group(0)) if m else None


_STEP_NUMBER = re.compile(r"^\s*(?:paso\s*)?\d+\s*[\.\)\-:]\s+(?=\S)", re.I)


def _instructions(value):
    steps = []

    def add_text(text):
        for line in clean_text(text).split("\n"):
            line = _STEP_NUMBER.sub("", line).strip()
            if line:
                steps.append(line)

    def visit(v):
        if v is None:
            return
        if isinstance(v, str):
            add_text(v)
        elif isinstance(v, list):
            for item in v:
                visit(item)
        elif isinstance(v, dict):
            if "itemListElement" in v:
                visit(v["itemListElement"])
            elif v.get("text"):
                visit(v["text"])
            elif v.get("name"):
                visit(v["name"])

    visit(value)
    return steps


def _ingredients(value):
    out = []
    items = value if isinstance(value, list) else [value]
    for item in items:
        if item is None:
            continue
        if isinstance(item, dict):
            item = item.get("text") or item.get("name") or ""
        for line in clean_text(item).split("\n"):
            if line.strip():
                out.append(line.strip())
    return out


def _image(value, base_url):
    if isinstance(value, list):
        value = value[0] if value else None
    if isinstance(value, dict):
        value = value.get("url") or value.get("contentUrl")
    if isinstance(value, str) and value.strip():
        return urljoin(base_url, value.strip())
    return ""


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return [clean_text(v) for v in value if clean_text(v)]
    return [p.strip() for p in clean_text(value).split(",") if p.strip()]


# --------------------------------------------------------------------------
# JSON-LD
# --------------------------------------------------------------------------
class _ScriptCollector(HTMLParser):
    def __init__(self):
        HTMLParser.__init__(self)
        self.scripts = []
        self._inside = False
        self._buf = []

    def handle_starttag(self, tag, attrs):
        if tag == "script" and "ld+json" in (dict(attrs).get("type") or "").lower():
            self._inside, self._buf = True, []

    def handle_endtag(self, tag):
        if tag == "script" and self._inside:
            self.scripts.append("".join(self._buf))
            self._inside = False

    def handle_data(self, data):
        if self._inside:
            self._buf.append(data)


def _is_recipe_node(node):
    t = node.get("@type")
    types = t if isinstance(t, list) else [t]
    return any(isinstance(x, str) and x.lower().endswith("recipe") for x in types)


def _find_recipes(node, out):
    if isinstance(node, list):
        for item in node:
            _find_recipes(item, out)
    elif isinstance(node, dict):
        if _is_recipe_node(node):
            out.append(node)
        for value in node.values():
            if isinstance(value, (list, dict)):
                _find_recipes(value, out)


def _from_jsonld(page_html, base_url):
    collector = _ScriptCollector()
    try:
        collector.feed(page_html)
    except Exception:
        pass
    found = []
    for script in collector.scripts:
        text = script.strip()
        text = re.sub(r"^<!--|-->$", "", text).strip()
        try:
            _find_recipes(json.loads(text, strict=False), found)
        except ValueError:
            continue
    if not found:
        return None
    node = max(found, key=lambda n: len(_ingredients(n.get("recipeIngredient") or n.get("ingredients")))
               + len(_instructions(n.get("recipeInstructions"))))
    prep = parse_duration(node.get("prepTime"))
    cook = parse_duration(node.get("cookTime"))
    total = parse_duration(node.get("totalTime")) or ((prep or 0) + (cook or 0) or None)
    cats = _as_list(node.get("recipeCategory"))
    keywords = _as_list(node.get("keywords")) + _as_list(node.get("recipeCuisine"))
    return {
        "name": clean_text(node.get("name")),
        "description": clean_text(node.get("description")),
        "image_url": _image(node.get("image"), base_url),
        "ingredients": _ingredients(node.get("recipeIngredient") or node.get("ingredients")),
        "instructions": _instructions(node.get("recipeInstructions")),
        "prep_minutes": prep, "cook_minutes": cook, "total_minutes": total,
        "servings": _first_int(node.get("recipeYield")),
        "category": cats[0] if cats else "",
        "keywords": keywords,
    }


# --------------------------------------------------------------------------
# Microdatos / og: (plan B)
# --------------------------------------------------------------------------
class _MicrodataCollector(HTMLParser):
    TARGETS = {"name", "recipeingredient", "ingredients", "recipeinstructions", "image",
               "recipeyield", "preptime", "cooktime", "totaltime", "recipecategory",
               "description", "keywords"}
    VOID = {"meta", "img", "link", "source"}
    BLOCK = {"li", "p", "div", "br", "tr"}

    def __init__(self):
        HTMLParser.__init__(self)
        self.props = {}
        self.og = {}
        self.title = ""
        self._open = []        # [prop, tag, depth, [texto]]
        self._in_title = False
        self.has_recipe_type = False

    def _store(self, prop, text):
        text = text.strip()
        if text:
            self.props.setdefault(prop, []).append(text)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if "recipe" in (a.get("itemtype") or "").lower():
            self.has_recipe_type = True
        if tag == "title":
            self._in_title = True
        if tag == "meta" and (a.get("property") or "").startswith("og:"):
            self.og[a["property"][3:]] = a.get("content") or ""
        for cap in self._open:
            if tag in self.BLOCK:
                cap[3].append("\n")
            if tag == cap[1]:
                cap[2] += 1
        prop = (a.get("itemprop") or "").lower().split(" ")[0]
        if prop in self.TARGETS:
            if tag in self.VOID:
                self._store(prop, a.get("content") or a.get("src") or "")
            elif not any(c[0] == prop for c in self._open):
                if a.get("datetime") or a.get("content"):
                    self._store(prop, a.get("datetime") or a.get("content"))
                else:
                    self._open.append([prop, tag, 0, []])

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        for cap in list(self._open):
            if tag in self.BLOCK:
                cap[3].append("\n")
            if tag == cap[1]:
                if cap[2] == 0:
                    self._store(cap[0], "".join(cap[3]))
                    self._open.remove(cap)
                else:
                    cap[2] -= 1

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        for cap in self._open:
            cap[3].append(data)


def _from_microdata(page_html, base_url):
    c = _MicrodataCollector()
    try:
        c.feed(page_html)
    except Exception:
        pass
    p = c.props
    ingredients = []
    for key in ("recipeingredient", "ingredients"):
        for text in p.get(key, []):
            ingredients += _ingredients(text)
    instructions = []
    for text in p.get("recipeinstructions", []):
        instructions += _instructions(text)
    name = (p.get("name") or [c.og.get("title") or c.title])[0]
    if not (ingredients or instructions):
        return None
    prep = parse_duration((p.get("preptime") or [None])[0])
    cook = parse_duration((p.get("cooktime") or [None])[0])
    total = parse_duration((p.get("totaltime") or [None])[0]) or ((prep or 0) + (cook or 0) or None)
    img = (p.get("image") or [c.og.get("image", "")])[0]
    return {
        "name": clean_text(name),
        "description": clean_text((p.get("description") or [c.og.get("description", "")])[0]),
        "image_url": urljoin(base_url, img) if img else "",
        "ingredients": ingredients, "instructions": instructions,
        "prep_minutes": prep, "cook_minutes": cook, "total_minutes": total,
        "servings": _first_int(p.get("recipeyield")),
        "category": clean_text((p.get("recipecategory") or [""])[0]),
        "keywords": _as_list(", ".join(p.get("keywords", []))),
    }


# --------------------------------------------------------------------------
# API pública
# --------------------------------------------------------------------------
def extract_recipe(page_html, url=""):
    """Extrae la receta de un HTML ya descargado. Lanza RecipeImportError."""
    data = _from_jsonld(page_html, url) or _from_microdata(page_html, url)
    if not data or not data["name"] or not (data["ingredients"] or data["instructions"]):
        raise RecipeImportError(
            "No he encontrado una receta en esa página. Algunas webs no incluyen "
            "los datos de forma que se puedan leer; puedes crearla a mano.")
    data["source_url"] = url
    return data


def import_recipe(url, timeout=20):
    page_html, final_url = fetch_html(url, timeout)
    return extract_recipe(page_html, final_url)
