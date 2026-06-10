"""
INGEST — turn whatever JSON files are in /data into canonical product rows.

Nothing here knows about trends or scoring. It only:
  1. matches files in DATA_DIR against the adapter config globs,
  2. maps raw keys -> canonical fields,
  3. recomputes true discount from price/mrp (never trusts scraper discount fields),
  4. extracts a date proxy from image URLs (freshness approximation, labelled as such),
  5. AUTO-DETECTS dead demand signals: a platform declared "india_demand" whose
     rating_count is absent or all-zero is demoted — it contributes no demand —
     and the demotion is recorded as a disclosed limitation,
  6. derives a canonical CATEGORY (women/men/kids) and SUB_CATEGORY (tops/shirts/
     jeans/...) for every product so the UI can scope the analysis to any segment
     actually present in the data. Resolution order, per field:
        raw field from the adapter's field_map
        → adapter default (a property of how the scrape was taken)
        → generic keyword inference from sub-category text, URL, then name
        → "unknown" (never silently discarded; counted and disclosed).
"""

import csv
import fnmatch
import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from adapter_config import PLATFORM_ADAPTERS

# Repo data dir (read-only on serverless hosts like Vercel — the JSONs are
# bundled with the deployment). Override with TBW_DATA_DIR.
DATA_DIR = Path(os.environ.get(
    "TBW_DATA_DIR", Path(__file__).resolve().parent.parent / "data"))


def _writable(p: Path) -> bool:
    try:
        p.mkdir(parents=True, exist_ok=True)
        probe = p / ".write_probe"
        probe.write_text("")
        probe.unlink()
        return True
    except OSError:
        return False


def upload_dir() -> Path:
    """Where POST /api/upload saves files. Locally this is /data itself; on a
    read-only deployment (Vercel) it falls back to a tmp dir — which is
    EPHEMERAL per serverless instance. The durable path on such hosts is to
    commit the file to /data and redeploy (disclosed by /api/upload)."""
    env = os.environ.get("TBW_UPLOAD_DIR")
    if env:
        p = Path(env)
        p.mkdir(parents=True, exist_ok=True)
        return p
    if _writable(DATA_DIR):
        return DATA_DIR
    p = Path(tempfile.gettempdir()) / "tbw-data"
    p.mkdir(parents=True, exist_ok=True)
    return p

_MONTHS = {m.lower(): i + 1 for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"])}

# ---------------------------------------------------------------------------
# Generic segment inference — keyword tables, never per-dataset hardcoding.
# Order matters: first match wins. "women" is checked before "men" and the
# men-pattern uses a word boundary so it cannot match inside "women".
# ---------------------------------------------------------------------------
CATEGORY_RULES = [
    ("women", [r"women", r"woman", r"ladies", r"female"]),
    ("kids",  [r"\bkids?\b", r"\bgirls?\b", r"\bboys?\b", r"junior", r"infant"]),
    ("men",   [r"\bmen\b", r"\bman\b", r"\bmale\b", r"\bmens\b"]),
]

SUB_CATEGORY_RULES = [
    ("jeans",     [r"jeans?", r"jegging"]),
    ("trousers",  [r"trouser", r"\bpants?\b", r"palazzo", r"chino", r"cargo"]),
    ("shorts",    [r"shorts"]),
    ("skirts",    [r"skirt"]),
    ("dresses",   [r"dress\b", r"\bgown"]),
    ("sarees",    [r"saree", r"\bsari\b"]),
    ("kurtas",    [r"kurt[ai]"]),
    ("nightwear", [r"night", r"pyjama", r"pajama"]),
    ("shoes",     [r"shoes?", r"sneaker", r"sandal", r"footwear", r"loafer"]),
    # tshirts before shirts/tops: "t-shirt" contains "shirt"
    ("tshirts",   [r"t-?shirts?\b", r"\btees?\b"]),
    # tops before shirts: "Shirt Style Top" / "Shirts, Tops & Tunic" → tops
    ("tops",      [r"\btops?\b", r"blouse", r"\bcami", r"\btank\b", r"bandeau",
                   r"\btube\b", r"bodysuit", r"tunic", r"corset", r"bralette"]),
    ("shirts",    [r"shirts?\b"]),
]


def _match_rules(rules, text):
    if not text:
        return None
    t = text.lower()
    for label, pats in rules:
        if any(re.search(p, t) for p in pats):
            return label
    return None


def _infer_segment(rules, default, *texts):
    """Try each text in priority order (raw sub-category field, URL, name);
    fall back to the adapter default, then 'unknown'."""
    for txt in texts:
        hit = _match_rules(rules, txt)
        if hit:
            return hit
    return default or "unknown"


@dataclass
class Product:
    platform: str
    name: str
    brand: Optional[str]
    price: Optional[float]
    mrp: Optional[float]
    rating: Optional[float]
    rating_count: Optional[int]
    in_stock: Optional[bool]
    image_url: Optional[str]
    product_url: Optional[str]
    true_discount: Optional[int]      # ALWAYS recomputed from price/mrp
    date_proxy: Optional[date]        # image-upload approximation of freshness
    currency: str
    category: str = "unknown"         # women / men / kids / unknown
    sub_category: str = "unknown"     # tops / shirts / jeans / ... / unknown
    units_sold: Optional[float] = None  # pos_sales rows: the buyer's own till data
    returns: Optional[float] = None     # pos_sales rows: units returned


@dataclass
class PlatformData:
    key: str
    file: str
    roles: list                       # effective roles AFTER auto-detection
    declared_roles: list
    products: list = field(default_factory=list)
    raw_count: int = 0
    filtered_count: int = 0
    limitations: list = field(default_factory=list)
    scraped_at: Optional[str] = None
    static_limitations: list = field(default_factory=list)  # survive re-scoping
    has_date_regex: bool = False


def _dig(raw: dict, path: str):
    """Dot-path lookup for nested schemas: 'pricing.mrp' → raw['pricing']['mrp']."""
    cur = raw
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _get(raw: dict, key):
    """field_map value can be a key, a dot-path into nested objects, a list of
    fallback keys tried in order (first non-empty wins — lets ONE adapter entry
    absorb several vendor schema variants), or None."""
    if key is None:
        return None
    keys = key if isinstance(key, list) else [key]
    for k in keys:
        v = _dig(raw, k) if "." in k else raw.get(k)
        if v not in (None, ""):
            return v
    return None


def _num(v) -> Optional[float]:
    """Parse a number that may arrive as '£18.00', '₹399', 399, or '399'."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    cleaned = re.sub(r"[^\d.]", "", str(v))
    try:
        return float(cleaned) if cleaned else None
    except ValueError:
        return None


def _true_discount(price, mrp) -> Optional[int]:
    """round((1 - price/mrp) * 100). The ONLY discount the system uses."""
    if price is None or mrp is None or mrp <= 0 or price > mrp:
        return None
    return round((1 - price / mrp) * 100)


def _date_proxy(image_url, regex) -> Optional[date]:
    if not image_url or not regex:
        return None
    m = re.search(regex, image_url)
    if not m:
        return None
    try:
        y = int(m.group("y"))
        mo_raw = m.group("m")
        mo = int(mo_raw) if mo_raw.isdigit() else _MONTHS.get(mo_raw.lower(), 0)
        d = int(m.group("d"))
        return date(y, mo, d)
    except (ValueError, IndexError, KeyError):
        return None


def data_files() -> list:
    """All candidate JSONs: the repo /data dir plus the upload dir (if it's a
    different place). An uploaded file with the SAME NAME as a repo file
    replaces it for this process."""
    seen = {}
    dirs = [DATA_DIR]
    up = upload_dir()
    if up != DATA_DIR:
        dirs.append(up)          # later dirs win on name clash
    for d in dirs:
        if d.is_dir():
            for pattern in ("*.json", "*.csv"):
                for f in d.glob(pattern):
                    if f.name != "sample_output.json":
                        seen[f.name] = f
    return sorted(seen.values(), key=lambda f: f.name)


def read_rows(path: Path, cfg: dict) -> list:
    """Schema-tolerant row reader: JSON array, JSON object with the array nested
    under cfg['root_path'] (dot path, e.g. 'data.products'), or CSV (header row
    → dicts). Different sources, one canonical shape downstream."""
    if path.suffix.lower() == ".csv":
        with open(path, newline="", encoding="utf-8-sig") as fh:
            return list(csv.DictReader(fh))
    raw = json.loads(path.read_text())
    if cfg.get("root_path"):
        raw = _dig(raw, cfg["root_path"]) if isinstance(raw, dict) else raw
    if isinstance(raw, dict):
        # no root_path declared: take the first list value found (common
        # {"data": [...]} / {"products": [...]} wrappers)
        raw = next((v for v in raw.values() if isinstance(v, list)), [])
    return raw if isinstance(raw, list) else []


def load_platforms() -> list:
    """Read every data file that matches an adapter glob. Files matching no
    adapter are ignored (listed by /api/upload as 'unrecognised'). If SEVERAL
    files match the same adapter (e.g. you upload a fresh scrape without
    deleting last week's), only the newest by modification time is used and
    the superseded ones are disclosed as a limitation — silently double-
    counting two snapshots of the same platform would corrupt every share."""
    platforms = []
    files = data_files()
    for key, cfg in PLATFORM_ADAPTERS.items():
        matched = [f for f in files if fnmatch.fnmatch(f.name.lower(), cfg["glob"].lower())]
        if not matched:
            continue
        matched.sort(key=lambda f: f.stat().st_mtime, reverse=True)
        newest, superseded = matched[0], matched[1:]
        pd = _load_one(key, cfg, newest)
        if superseded:
            pd.static_limitations.append(
                "newest file used; superseded older file(s) ignored: "
                + ", ".join(f.name for f in superseded))
            apply_signal_checks(pd)
        platforms.append(pd)
    return platforms


def _load_one(key: str, cfg: dict, path: Path) -> PlatformData:
    raw_rows = read_rows(path, cfg)
    pd = PlatformData(key=key, file=path.name,
                      roles=list(cfg["roles"]), declared_roles=list(cfg["roles"]))
    pd.raw_count = len(raw_rows)
    fmap = cfg["field_map"]
    subf = cfg.get("sub_category_filter")
    regex = cfg.get("date_proxy_regex")

    for raw in raw_rows:
        if subf and raw.get(subf["field"]) not in subf["keep"]:
            continue
        name = _get(raw, fmap.get("name"))
        if not name:
            continue
        price = _num(_get(raw, fmap.get("price")))
        mrp = _num(_get(raw, fmap.get("mrp")))
        rc = _get(raw, fmap.get("rating_count"))
        url = _get(raw, fmap.get("product_url"))
        # segment: raw field → keyword inference (raw text, URL, name) → adapter default
        raw_cat = _get(raw, fmap.get("category"))
        raw_sub = _get(raw, fmap.get("sub_category"))
        category = _infer_segment(CATEGORY_RULES, cfg.get("default_category"),
                                  raw_cat, url, str(name))
        sub_category = _infer_segment(SUB_CATEGORY_RULES, cfg.get("default_sub_category"),
                                      raw_sub, url, str(name))
        pd.products.append(Product(
            platform=key,
            name=str(name),
            brand=_get(raw, fmap.get("brand")),
            price=price,
            mrp=mrp,
            rating=_num(_get(raw, fmap.get("rating"))),
            rating_count=int(rc) if rc is not None else None,
            in_stock=raw.get(fmap.get("in_stock")) if isinstance(fmap.get("in_stock"), str) else None,
            image_url=_get(raw, fmap.get("image_url")),
            product_url=url,
            true_discount=_true_discount(price, mrp),
            date_proxy=_date_proxy(_get(raw, fmap.get("image_url")), regex),
            currency=cfg.get("currency", "INR"),
            category=category,
            sub_category=sub_category,
            units_sold=_num(_get(raw, fmap.get("units_sold"))),
            returns=_num(_get(raw, fmap.get("returns"))),
        ))
        if pd.scraped_at is None and raw.get("scrapedAt"):
            pd.scraped_at = raw["scrapedAt"]

    pd.filtered_count = len(pd.products)
    pd.has_date_regex = bool(regex)
    if subf:
        dropped = pd.raw_count - pd.filtered_count
        if dropped:
            pd.static_limitations.append(
                f"{dropped}/{pd.raw_count} rows dropped by sub-category filter "
                f"({subf['field']} not in {subf['keep']})")

    apply_signal_checks(pd)
    return pd


def apply_signal_checks(pd: PlatformData):
    """(Re)derive effective roles and limitations from the CURRENT product set.
    Called at load time AND again after segment scoping — a platform may have
    live rating counts for tops but none for, say, shoes."""
    pd.roles = list(pd.declared_roles)
    pd.limitations = list(pd.static_limitations)

    # rating_count is the demand authority. If this platform claims india_demand
    # but every rating_count is missing or zero, it proves nothing about demand:
    # demote it and say so. (This is how AJIO is handled in the current dataset.)
    if "india_demand" in pd.roles:
        if not any((p.rating_count or 0) > 0 for p in pd.products):
            pd.roles.remove("india_demand")
            pd.limitations.append(
                "declared india_demand but rating_count is absent/all-zero in this "
                "slice of the data — contributes NO demand signal (auto-detected)")

    if not pd.has_date_regex:
        pd.limitations.append(
            "no date_proxy_regex — freshness unknown for this platform")
    elif pd.products:
        missing = sum(1 for p in pd.products if p.date_proxy is None)
        if missing:
            pd.limitations.append(
                f"date proxy (image-upload date) missing for {missing}/{len(pd.products)} products")

    if "west_supply" in pd.roles:
        pd.limitations.append(
            "Western platform: signals describe what was DROPPED (supply), never what sold (demand)")
    if "pos_sales" in pd.roles:
        pd.limitations.append(
            "buyer's own POS data: the strongest proof of LOCAL demand, but only "
            "for styles already stocked — silent on styles never bought")
    if not pd.products:
        pd.limitations.append("no products in the selected category/sub-category")


# ---------------------------------------------------------------------------
# Segment scoping — what powers the category / sub-category dropdowns
# ---------------------------------------------------------------------------
def list_segments(platforms: list) -> list:
    """Every (category, sub_category) pair present in the data, with counts.
    Computed, never predefined — new data with men's shoes grows the dropdown."""
    seg = {}
    for p in platforms:
        for prod in p.products:
            key = (prod.category, prod.sub_category)
            entry = seg.setdefault(key, {"category": key[0], "sub_category": key[1],
                                         "count": 0, "platforms": {}})
            entry["count"] += 1
            entry["platforms"][p.key] = entry["platforms"].get(p.key, 0) + 1
    return sorted(seg.values(), key=lambda e: -e["count"])


def scoped_platforms(platforms: list, category: str, sub_category: str) -> list:
    """Copies of each platform holding only products in the selected segment,
    with roles/limitations re-derived for that slice."""
    out = []
    for p in platforms:
        sub = PlatformData(key=p.key, file=p.file,
                           roles=list(p.declared_roles),
                           declared_roles=list(p.declared_roles))
        sub.products = [x for x in p.products
                        if x.category == category and x.sub_category == sub_category]
        sub.raw_count = p.raw_count
        sub.filtered_count = len(sub.products)
        sub.scraped_at = p.scraped_at
        sub.static_limitations = list(p.static_limitations)
        sub.has_date_regex = p.has_date_regex
        apply_signal_checks(sub)
        out.append(sub)
    return out
