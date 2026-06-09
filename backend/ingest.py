"""
INGEST — turn whatever JSON files are in /data into canonical product rows.

Nothing here knows about trends or scoring. It only:
  1. matches files in DATA_DIR against the adapter config globs,
  2. maps raw keys -> canonical fields,
  3. recomputes true discount from price/mrp (never trusts scraper discount fields),
  4. extracts a date proxy from image URLs (freshness approximation, labelled as such),
  5. AUTO-DETECTS dead demand signals: a platform declared "india_demand" whose
     rating_count is absent or all-zero is demoted — it contributes no demand —
     and the demotion is recorded as a disclosed limitation.
"""

import fnmatch
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from adapter_config import PLATFORM_ADAPTERS

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

_MONTHS = {m.lower(): i + 1 for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"])}


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


def _get(raw: dict, key):
    """field_map value can be a key, a list of fallback keys, or None."""
    if key is None:
        return None
    keys = key if isinstance(key, list) else [key]
    for k in keys:
        v = raw.get(k)
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


def load_platforms(data_dir: Path = DATA_DIR) -> list:
    """Read every file in /data that matches an adapter glob. Files matching no
    adapter are ignored (and that's fine — listed in meta as 'unrecognised')."""
    platforms = []
    files = sorted(p for p in data_dir.glob("*.json") if p.name != "sample_output.json")
    for key, cfg in PLATFORM_ADAPTERS.items():
        matched = [f for f in files if fnmatch.fnmatch(f.name.lower(), cfg["glob"].lower())]
        for f in matched:
            platforms.append(_load_one(key, cfg, f))
    return platforms


def _load_one(key: str, cfg: dict, path: Path) -> PlatformData:
    raw_rows = json.loads(path.read_text())
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
            product_url=_get(raw, fmap.get("product_url")),
            true_discount=_true_discount(price, mrp),
            date_proxy=_date_proxy(_get(raw, fmap.get("image_url")), regex),
            currency=cfg.get("currency", "INR"),
        ))
        if pd.scraped_at is None and raw.get("scrapedAt"):
            pd.scraped_at = raw["scrapedAt"]

    pd.filtered_count = len(pd.products)
    if subf:
        dropped = pd.raw_count - pd.filtered_count
        if dropped:
            pd.limitations.append(
                f"{dropped}/{pd.raw_count} rows dropped by sub-category filter "
                f"({subf['field']} not in {subf['keep']})")

    # --- AUTO-DETECT a dead demand signal -------------------------------------
    # rating_count is the demand authority. If this platform claims india_demand
    # but every rating_count is missing or zero, it proves nothing about demand:
    # demote it and say so. (This is how AJIO is handled in the current dataset.)
    if "india_demand" in pd.roles:
        if not any((p.rating_count or 0) > 0 for p in pd.products):
            pd.roles.remove("india_demand")
            pd.limitations.append(
                "declared india_demand but rating_count is absent/all-zero in this "
                "scrape — contributes NO demand signal (auto-detected)")

    if not regex:
        pd.limitations.append(
            "no date_proxy_regex — freshness unknown for this platform")
    else:
        missing = sum(1 for p in pd.products if p.date_proxy is None)
        if missing:
            pd.limitations.append(
                f"date proxy (image-upload date) missing for {missing}/{len(pd.products)} products")

    if "west_supply" in pd.roles:
        pd.limitations.append(
            "Western platform: signals describe what was DROPPED (supply), never what sold (demand)")
    return pd
