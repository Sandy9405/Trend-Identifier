"""
SCORING ENGINE, the reviewer-facing deliverable.
=================================================

Everything below is METHOD, not conclusion. No trend name, rank, share, label or
fit-default for a specific trend is hardcoded anywhere. The method is:

  1. Cluster every product into silhouette buckets by keyword match on name.
  2. For every bucket PRESENT IN THE CURRENT DATA, compute signals normalized
     ACROSS the buckets present (relative scores, so they self-adjust when the
     files in /data change).
  3. Derive a lead-lag label, distortion penalties, an India-replication
     probability (Early trends only) and a final raw-vs-adjusted confidence.
  4. Map adjusted confidence to a bet size via named threshold constants.

Every number carries a plain-English "derivation" string the UI can show.
Replace the files in /data → every output below recomputes. Zero code changes.
"""

import math
import statistics
from datetime import date

# ════════════════════════════════════════════════════════════════════════════
# 1. SILHOUETTE KEYWORD DICTIONARIES  (extend-friendly, add a line, get a bucket)
#    A product may match multiple buckets. Matching is case-insensitive on name.
#    The dictionary is CHOSEN PER SUB-CATEGORY: clustering running shoes with a
#    tops vocabulary would file mesh sneakers under "mesh_sheer" and lace-ups
#    under "tie_knot", nonsense. New sub-category vocabularies are one dict
#    away; anything unmapped falls back to the apparel dictionary.
# ════════════════════════════════════════════════════════════════════════════
APPAREL_SILHOUETTES = {
    "puff_balloon":   ["puff", "balloon"],
    "peplum":         ["peplum"],
    "halter":         ["halter", "halterneck"],
    "embroidered":    ["schiffli", "embroider", "broderie", "anglaise"],
    "mesh_sheer":     ["mesh", "sheer", "lace", "organza", "net"],
    "crop":           ["crop"],
    "shirt":          ["shirt"],
    "sleeveless_tank": ["sleeveless", "tank", "cami", "vest"],
    "bandeau_tube":   ["bandeau", "tube"],
    "corset_fitted":  ["corset", "fitted", "bodycon", "cinch"],
    "square_neck":    ["square neck", "square-neck"],
    "wrap":           ["wrap"],
    "ruffle":         ["ruffle", "flutter", "frill"],
    "off_shoulder":   ["off shoulder", "off-shoulder", "bardot"],
    "smocked":        ["smock", "shirred", "shirring"],
    "tie_knot":       ["tie", "knot"],
}

FOOTWEAR_SILHOUETTES = {
    "sneakers":        ["sneaker", "trainer"],
    "running_sports":  ["running", "sports shoe", "training", "gym", "walking"],
    "casual_shoes":    ["casual"],
    "loafers_slipons": ["loafer", "slip-on", "slip on", "moccasin", "espadrille"],
    "sandals_slides":  ["sandal", "slider", "slide", "flip-flop", "flip flop", "floater"],
    "boots":           ["boot", "chelsea"],
    "formal_shoes":    ["formal", "oxford", "derby", "brogue", "monk"],
    "ethnic_footwear": ["kolhapuri", "jutti", "mojari"],
    "canvas_shoes":    ["canvas"],
    "chunky_platform": ["chunky", "platform"],
    "mesh_knit":       ["mesh", "knit", "flyknit"],
    "leather_suede":   ["leather", "suede"],
    "clogs_mules":     ["clog", "mule"],
    "high_top":        ["high top", "high-top", "hi-top"],
}

DICTIONARY_FOR_SUBCATEGORY = {
    "shoes": ("footwear", FOOTWEAR_SILHOUETTES),
    # everything else (tops, tshirts, shirts, dresses, ...) → apparel vocabulary
}

# ── second-axis vocabularies: colors & fabrics (extend-friendly) ─────────────
# Same pattern as silhouettes: case-insensitive keyword match on product name;
# unmatched words are simply ignored. These turn a trend flag into a buy
# instruction ("strongest in cotton, in pastels").
COLOR_KEYWORDS = {
    "white":    ["white", "ivory"],
    "black":    ["black"],
    "blue":     ["blue", "cobalt", "teal"],
    "navy":     ["navy"],
    "pink":     ["pink", "blush", "rose"],
    "red":      ["red", "maroon", "burgundy", "wine"],
    "green":    ["green"],
    "sage":     ["sage"],
    "olive":    ["olive", "khaki"],
    "yellow":   ["yellow", "mustard"],
    "brown":    ["brown", "tan", "chocolate", "mocha"],
    "beige":    ["beige", "nude", "sand", "camel"],
    "cream":    ["cream", "off white", "off-white", "offwhite", "ecru"],
    "lavender": ["lavender", "lilac"],
    "purple":   ["purple", "violet", "plum"],
    "orange":   ["orange", "rust", "coral", "peach", "apricot"],
    "grey":     ["grey", "gray", "charcoal"],
    "pastel":   ["pastel"],
}
FABRIC_KEYWORDS = {
    "cotton":    ["cotton"],
    "linen":     ["linen"],
    "denim":     ["denim"],
    "satin":     ["satin"],
    "silk":      ["silk"],
    "chiffon":   ["chiffon"],
    "georgette": ["georgette"],
    "knit":      ["knit", "knitted", "ribbed"],
    "crochet":   ["crochet"],
    "velvet":    ["velvet", "velour"],
    "organza":   ["organza"],
    "rayon":     ["rayon"],
    "viscose":   ["viscose", "modal"],
    "net":       ["net"],
    "mesh":      ["mesh"],
    "lace":      ["lace"],
    "crepe":     ["crepe"],
    "polyester": ["polyester"],
}


def keywords_for(sub_category):
    """Pick the silhouette vocabulary for the segment being scored."""
    return DICTIONARY_FOR_SUBCATEGORY.get(sub_category, ("apparel", APPAREL_SILHOUETTES))


# Backwards-compatible alias (README references): the apparel dictionary is
# the default vocabulary.
SILHOUETTE_KEYWORDS = APPAREL_SILHOUETTES
# Products matching no bucket fall into "other": excluded from the slate but
# counted in totals so shares stay honest.

# ════════════════════════════════════════════════════════════════════════════
# 2. NAMED THRESHOLD CONSTANTS  (the tuning surface, change here, not in logic)
# ════════════════════════════════════════════════════════════════════════════
# Lead-lag classification (all on the 0–100 normalized scales)
WEST_HIGH = 55          # west_signal at/above this = "the West is pushing it"
WEST_LOW = 25           # below this = "the West has moved on / never pushed it"
DEMAND_HIGH = 55        # demand_strength at/above this = real Indian demand
DEMAND_LOW = 40         # below this = demand not yet visible
FRESH_HIGH = 50         # freshness% above this = supply is currently being refreshed

# Discount distortion penalty (E): median true discount % → 0–40 points.
# Indian value e-commerce discounts EVERYTHING (the market norm in a typical
# scrape is 50–60% off), so an absolute cutoff would flatten every bucket.
# Instead the penalty is RELATIVE: it fires on discounting IN EXCESS of the
# current dataset's own market-wide median, i.e. "this trend is being pushed
# harder on price than the market around it", which is the actual distortion.
# The market norm is computed from the data and disclosed in every derivation.
DISCOUNT_EXCESS_SPAN = 20   # bucket at market_norm+20pp of discount → full penalty
DISCOUNT_PENALTY_MAX = 40

# Supply-without-demand penalty (F): platforms pushing stock nobody asked for
SUPPLY_HIGH = 60        # supply_conviction at/above this counts as "heavy push"
SWD_PENALTY_MAX = 30

# Cross-platform presence: a bucket "exists" on a platform if it holds at least
# this share of that platform's (filtered) assortment
MIN_PRESENCE_SHARE = 0.02

# Freshness: a product is "fresh" if its image-upload date proxy is within this
# many days of the NEWEST upload seen on the same platform
FRESH_WINDOW_DAYS = 120

# base_confidence weights (renormalized automatically if a signal is missing).
# The buyer's OWN till data, when present, is the heaviest signal, real local
# purchases beat any scraped proxy. With no pos_sales file the remaining
# weights renormalize to ≈ demand .46 / supply .31 / agreement .23.
W_OWN_SALES, W_DEMAND, W_SUPPLY, W_AGREEMENT = 0.35, 0.30, 0.20, 0.15

# Own-sales return rate at/above this % = product comes back, fit/quality flag
RETURN_RATE_HIGH = 25

# supply_conviction blend: assortment share vs freshness
W_SHARE, W_FRESH = 0.7, 0.3

# Replication probability below this counts as "low" (resolver pattern table)
REPL_LOW = 70

# Bet-size thresholds on ADJUSTED confidence
BET_DEEPER = 65         # ≥ 65 → Deeper Buy
BET_TRIAL = 40          # 40–64 → Small Trial; < 40 → Monitor

# Slate qualification: buckets with fewer total products are noise, not trends
MIN_BUCKET_PRODUCTS = 10

# Trajectory cohorts (listing-date proxy): recent-listing share thresholds
TRAJ_ACCELERATING = 0.60   # ≥ 60% of the bucket's dated items are recent → accelerating
TRAJ_FADING = 0.40         # ≤ 40% recent → fading

# West→India lag estimate (Early trends): coarser the Western lead vs Indian
# presence, the longer the runway. Signal-gap thresholds, not per-trend values.
LAG_GAP_WIDE = 60          # west minus India ≥ this → ~2 quarters of runway
LAG_GAP_MID = 30           # ≥ this → ~1–2 quarters; below → ~1 quarter or less

# Budget allocation: trial-tier caps (the allocator is allocate_budget below)
TRIAL_POOL_MAX = 0.25      # all TRIAL bets together get at most 25% of budget
TRIAL_CAP_EACH = 0.08      # one TRIAL bet gets at most 8% of budget

# India value band (INR) for price_band_fit
VALUE_BAND = (299, 799)

# ════════════════════════════════════════════════════════════════════════════
# 3. INDIA-FIT AXIS RULE TABLE (replication probability, Early trends only)
#    Defaults are inferred from BUCKET KEYWORDS, never per-trend hardcoding.
#    A brand-new silhouette next month gets sensible, explainable defaults.
#    Format: first matching rule wins; otherwise the axis BASE applies.
# ════════════════════════════════════════════════════════════════════════════
AXIS_RULES = {
    "climate_fit": {
        "base": (75, "no heavy-volume/layering cue in bucket keywords"),
        "rules": [
            (["puff", "balloon", "corset", "cinch", "smock", "shirred", "shirring"],
             45, "keywords imply volume/structure/layering, heat risk in Indian climate"),
        ],
    },
    "modesty_fit": {
        "base": (75, "no sheer/bare cue in bucket keywords"),
        "rules": [
            (["mesh", "sheer", "organza", "net", "lace", "bandeau", "tube",
              "off shoulder", "off-shoulder", "bardot", "halter", "halterneck"],
             45, "keywords imply sheer/bare styling, modesty constraint in mass-market India"),
        ],
    },
    "occasion_fit": {
        "base": (70, "no strong occasion cue in bucket keywords"),
        "rules": [
            (["mesh", "sheer", "corset", "bandeau", "tube", "organza"],
             50, "keywords imply party/occasion-only wear, fewer wear occasions, slower turns"),
            (["shirt", "crop", "tank", "vest", "cami", "sleeveless"],
             85, "keywords imply everyday wear, wide occasion coverage"),
        ],
    },
    # price_band_fit is computed from the bucket's median INR price (see below),
    # not from keywords, but the rule is still generic, not per-trend.
}

# ════════════════════════════════════════════════════════════════════════════
# 4. RESOLVER PATTERN TABLE (disagreement view). Keyed on the SIGNAL PATTERN,
#    never the trend name. First matching pattern (priority order) wins,
#    one conflict summary + ONE resolver, kept decisive.
# ════════════════════════════════════════════════════════════════════════════
def _resolver_patterns():
    return [
        ("high return rate in own sales",
         lambda m: m.get("own_sales", {}).get("return_rate") is not None
         and m["own_sales"]["return_rate"] >= RETURN_RATE_HIGH,
         "Fit/quality audit before scaling, your own tills show it sells but "
         "comes back. Fix the block (sizing, fabric, finish) or the volume is fake."),
        ("market demand without own sell-through",
         lambda m: m.get("own_sales", {}).get("value") is not None
         and m["own_sales"]["value"] <= 25
         and (m["demand_strength"]["value"] or 0) >= DEMAND_HIGH,
         "Market wants it but your stores don't move it, check assortment depth, "
         "store placement and price position before concluding the trend is wrong."),
        ("high supply + low demand",
         lambda m: m["supply_conviction"]["value"] is not None
         and m["supply_conviction"]["value"] >= SUPPLY_HIGH
         and (m["demand_strength"]["value"] or 0) <= DEMAND_LOW,
         "Full-price sell-through & return-rate on the new drop (buyer's own POS data), "
         "platforms are pushing stock; only till data proves anyone wants it."),
        ("lead-lag Early + low replication",
         lambda m: m["lead_lag"]["label"].startswith("Early")
         and m["replication"]["value"] < REPL_LOW,
         "Small live trial in 2–3 target stores/regions, the India-fit doubts "
         "(climate/modesty/occasion/price) can only be answered by real shoppers."),
        ("high discount + high demand",
         lambda m: m["discount_penalty"]["value"] >= 20
         and (m["demand_strength"]["value"] or 0) >= DEMAND_HIGH,
         "Is demand surviving at full price? Margin check: re-read rating velocity "
         "after the discount event ends before trusting this volume."),
        ("india-led + strong demand",
         lambda m: m["lead_lag"]["label"].startswith("India-led")
         and (m["demand_strength"]["value"] or 0) >= DEMAND_HIGH,
         "Confirm size-curve & repeat-rate before scaling, demand is proven, "
         "the open risk is buying the wrong depth per size."),
        ("no decisive conflict",
         lambda m: True,
         "Watch rating_count velocity on the demand authority over the next 2 "
         "scrape cycles before changing the bet."),
    ]


# ════════════════════════════════════════════════════════════════════════════
# Helpers
# ════════════════════════════════════════════════════════════════════════════
def _minmax(values: dict) -> dict:
    """Min-max normalize a {bucket: raw} dict to 0–100 ACROSS BUCKETS PRESENT.
    This is what makes every score relative to the current dataset."""
    nums = [v for v in values.values() if v is not None]
    if not nums:
        return {k: None for k in values}
    lo, hi = min(nums), max(nums)
    if hi == lo:
        return {k: (50.0 if v is not None else None) for k, v in values.items()}
    return {k: (round((v - lo) / (hi - lo) * 100, 1) if v is not None else None)
            for k, v in values.items()}


def _clamp(x, lo=0.0, hi=100.0):
    return max(lo, min(hi, x))


def assign_buckets(name: str, vocab: dict) -> list:
    """Case-insensitive keyword match; a product may land in several buckets."""
    n = name.lower()
    return [b for b, kws in vocab.items() if any(k in n for k in kws)]


def _subtrends(prods, vocab, top_n=5):
    """Second-axis clustering (colors / fabrics) by keyword match on names.
    Returns the top_n entries with counts and share of matched products."""
    counts = {}
    for pr in prods:
        n = pr.name.lower()
        for label, kws in vocab.items():
            if any(k in n for k in kws):
                counts[label] = counts.get(label, 0) + 1
    matched = sum(counts.values())
    return [{"name": k, "count": c,
             "share": round(c / matched * 100, 1) if matched else 0}
            for k, c in sorted(counts.items(), key=lambda kv: -kv[1])[:top_n]]


# Buyer-facing direction glyphs derived from the lead-lag label (computed, the
# mapping itself is method, not conclusion).
_DIRECTIONS = [
    ("Early",      "↑", "Early",     "West moving, India hasn't caught up"),
    ("Landed",     "=", "Landed",    "West and India aligned, buy on India merits"),
    ("Late-cycle", "↓", "Late",      "West cooling while India is hot, ride it, don't over-commit"),
    ("India-led",  "◆", "India-led", "India ahead of the West, a local bet on local merits"),
    ("Unclear",    "•", "Unclear",   "Limited or mixed Western signal"),
]


def _direction(lead_lag_label, west_sources_present, west_sources_total):
    for prefix, glyph, short, phrase in _DIRECTIONS:
        if lead_lag_label.startswith(prefix):
            break
    single = west_sources_total > 0 and west_sources_present <= 1 \
        and prefix in ("Early", "Landed", "Late-cycle")
    return {
        "glyph": glyph, "short": short, "phrase": phrase,
        "single_source": single,
        "caution": "(1 source)" if single else None,
    }


def _merchant_line(d):
    """One or two sentences in a merchant's voice, ASSEMBLED from the computed
    signal pattern, template by pattern, variables from the numbers, never
    per-trend text."""
    m, name = d["metrics"], d["display_name"]
    ll = m["lead_lag"]["label"]
    dv = m["demand_strength"]["value"]
    n = d["counts"]["total"]
    disc, swd = m["discount_penalty"], m["supply_without_demand_penalty"]
    top_distortion = (
        f"median discounts of {disc['median_discount']}% mean some demand is bought"
        if disc["value"] >= swd["value"] and disc["value"] > 0 else
        "platforms are pushing supply shoppers haven't validated"
        if swd["value"] > 0 else "no major distortion detected")
    axes = m["replication"]["axes"]
    weakest = min(axes, key=lambda a: axes[a].get("override", axes[a]["default"]))

    if ll.startswith("India-led") and (dv or 0) >= DEMAND_HIGH:
        return (f"{name} is locally validated, {n} SKUs across platforms with strong Indian "
                f"demand, and India is ahead of the West, so this isn't an import bet. "
                f"Watch-out: {top_distortion}.")
    if ll.startswith("Early"):
        return (f"{name} is rising in the West but unproven in India; "
                f"{weakest.replace('_', ' ')} is the biggest fit risk. "
                f"Test small before committing.")
    if ll.startswith("Landed") and disc["value"] >= 20:
        return (f"{name} is selling on both sides, but {top_distortion}, verify full-price "
                f"appetite before going deeper.")
    if ll.startswith("Landed"):
        return (f"{name} has landed, West and India aligned. Buy on India merits: "
                f"{d['bet']['band']}.")
    if ll.startswith("Late"):
        return (f"{name} is past its Western peak but India still wants it, ride the existing "
                f"demand, keep markdown exit easy, don't over-commit. Watch-out: {top_distortion}.")
    return (f"{name} shows mixed signals. Before changing the bet: "
            f"{d['disagreement']['resolver']['action']}")


def allocate_budget(slate: list, budget: float) -> dict:
    """Turn the slate into a money plan. Method (all constants named above):
      WATCH  → ₹0 (monitoring is free).
      TRIAL  → small test allocations: equal-sized, each capped at
               TRIAL_CAP_EACH of budget, all trials together capped at
               TRIAL_POOL_MAX of budget.
      BUY    → everything left, split PROPORTIONAL TO ADJUSTED CONFIDENCE
               (the honest number, distortion-adjusted, never raw).
    If there are no BUY trends, the un-allocated remainder is explicitly held
    back rather than force-spent. Amounts rounded to the nearest ₹1,000."""
    buys = [r for r in slate if r["verdict"] == "BUY"]
    trials = [r for r in slate if r["verdict"] == "TRIAL"]
    watches = [r for r in slate if r["verdict"] == "WATCH"]

    rows = []
    trial_each = min(TRIAL_CAP_EACH * budget,
                     (TRIAL_POOL_MAX * budget) / len(trials)) if trials else 0
    trial_total = trial_each * len(trials)
    buy_pool = budget - trial_total if buys else 0
    conf_sum = sum(r["confidence"]["adjusted"] for r in buys) or 1

    for r in buys:
        amt = round(buy_pool * r["confidence"]["adjusted"] / conf_sum / 1000) * 1000
        rows.append({**_alloc_row(r, amt),
                     "rationale": f"BUY tier: {r['confidence']['adjusted']:.0f}/"
                                  f"{conf_sum:.0f} of tier confidence → "
                                  f"{amt / budget * 100:.0f}% of budget"})
    for r in trials:
        amt = round(trial_each / 1000) * 1000
        rows.append({**_alloc_row(r, amt),
                     "rationale": f"TRIAL tier: equal test allocations, capped at "
                                  f"{TRIAL_CAP_EACH * 100:.0f}% each / "
                                  f"{TRIAL_POOL_MAX * 100:.0f}% combined, read 4-week "
                                  f"sell-through before scaling"})
    for r in watches:
        rows.append({**_alloc_row(r, 0), "rationale": "WATCH: monitoring, no spend"})

    allocated = sum(x["amount"] for x in rows)
    return {
        "budget": budget,
        "allocated": allocated,
        "held_back": max(0, round(budget - allocated)),
        "held_back_note": (None if buys else
                           "no BUY-grade trend in this slate, the remainder is held back, "
                           "not force-spent"),
        "rows": rows,
        "method": f"WATCH ₹0; TRIAL equal & capped ({TRIAL_CAP_EACH*100:.0f}% each, "
                  f"{TRIAL_POOL_MAX*100:.0f}% pool); BUY splits the rest proportional "
                  f"to adjusted (distortion-honest) confidence.",
    }


def _alloc_row(r, amount):
    return {"bucket": r["bucket"], "display_name": r["display_name"],
            "verdict": r["verdict"], "adjusted": r["confidence"]["adjusted"],
            "amount": amount}


# ════════════════════════════════════════════════════════════════════════════
# THE ENGINE
# ════════════════════════════════════════════════════════════════════════════
def compute(platforms: list, sub_category: str = None) -> dict:
    """platforms: list[ingest.PlatformData], already scoped to one segment.
    `sub_category` selects the silhouette vocabulary (footwear vs apparel).
    Returns the full computed payload: slate + per-bucket details + meta.
    Pure function of the data on disk."""
    vocab_name, vocab = keywords_for(sub_category)

    demand_platforms = [p for p in platforms if "india_demand" in p.roles]
    india_supply = [p for p in platforms if "india_supply" in p.roles]
    west_supply = [p for p in platforms if "west_supply" in p.roles]
    pos_platforms = [p for p in platforms if "pos_sales" in p.roles
                     and any((x.units_sold or 0) > 0 for x in p.products)]

    # ---- bucket membership (computed, never predefined) ----------------------
    # buckets[bucket][platform_key] = list[Product]
    buckets, other_count, total_count = {}, 0, 0
    for plat in platforms:
        for prod in plat.products:
            total_count += 1
            bs = assign_buckets(prod.name, vocab)
            if not bs:
                other_count += 1
                continue
            for b in bs:
                buckets.setdefault(b, {}).setdefault(plat.key, []).append(prod)

    # ---- per-platform totals & freshness anchors ------------------------------
    plat_total = {p.key: len(p.products) for p in platforms}
    plat_anchor = {}   # newest upload-date proxy per platform = "now" for freshness
    for p in platforms:
        ds = [x.date_proxy for x in p.products if x.date_proxy]
        plat_anchor[p.key] = max(ds) if ds else None

    def freshness_pct(prods, plat_key):
        """% of a bucket's products on one platform uploaded within
        FRESH_WINDOW_DAYS of that platform's newest upload. None = unknown."""
        anchor = plat_anchor.get(plat_key)
        if anchor is None:
            return None
        dated = [x for x in prods if x.date_proxy]
        if not dated:
            return None
        fresh = sum(1 for x in dated if (anchor - x.date_proxy).days <= FRESH_WINDOW_DAYS)
        return round(fresh / len(dated) * 100, 1)

    # ════════════════════════════════════════════════════════════════════════
    # A. demand_strength, from india_demand rating_count totals.
    #    log-scaled (one 50k-review hero shouldn't drown every other signal),
    #    then min-max across buckets present.
    # ════════════════════════════════════════════════════════════════════════
    raw_demand, demand_detail = {}, {}
    for b, by_plat in buckets.items():
        if not demand_platforms:
            raw_demand[b] = None
            continue
        tot = sum((pr.rating_count or 0)
                  for p in demand_platforms for pr in by_plat.get(p.key, []))
        raw_demand[b] = math.log1p(tot)
        demand_detail[b] = tot
    demand_norm = _minmax(raw_demand)

    # ════════════════════════════════════════════════════════════════════════
    # B. supply_conviction, Indian assortment share blended with freshness,
    #    normalized across buckets.
    # ════════════════════════════════════════════════════════════════════════
    raw_share, fresh_by_bucket = {}, {}
    for b, by_plat in buckets.items():
        shares, freshes = [], []
        for p in india_supply:
            n = len(by_plat.get(p.key, []))
            if plat_total[p.key]:
                shares.append(n / plat_total[p.key])
            f = freshness_pct(by_plat.get(p.key, []), p.key)
            if f is not None:
                freshes.append(f)
        raw_share[b] = statistics.mean(shares) if shares else None
        fresh_by_bucket[b] = round(statistics.mean(freshes), 1) if freshes else None
    share_norm = _minmax(raw_share)

    # ════════════════════════════════════════════════════════════════════════
    # C. west_signal, share of west_supply assortment. SUPPLY, NOT DEMAND.
    # ════════════════════════════════════════════════════════════════════════
    raw_west = {}
    for b, by_plat in buckets.items():
        shares = [len(by_plat.get(p.key, [])) / plat_total[p.key]
                  for p in west_supply if plat_total[p.key]]
        raw_west[b] = statistics.mean(shares) if shares else None
    west_norm = _minmax(raw_west)

    # ════════════════════════════════════════════════════════════════════════
    # H. own_sales, the buyer's OWN till data (pos_sales role). Real local
    #    purchases: the strongest demand evidence available, log-scaled and
    #    normalized across buckets like A. Also yields a return-rate flag.
    # ════════════════════════════════════════════════════════════════════════
    raw_own, own_units, own_returns = {}, {}, {}
    for b, by_plat in buckets.items():
        units = sum((x.units_sold or 0)
                    for p in pos_platforms for x in by_plat.get(p.key, []))
        rets = sum((x.returns or 0)
                   for p in pos_platforms for x in by_plat.get(p.key, []))
        raw_own[b] = math.log1p(units) if pos_platforms else None
        own_units[b], own_returns[b] = units, rets
    own_norm = _minmax(raw_own)

    # ---- trajectory cohorts: per-platform median listing-date proxy ----------
    # Items uploaded after their platform's median date = "recently-listed".
    # A LISTING-DATE APPROXIMATION (image-upload date), labelled as such.
    plat_median = {}
    for p in platforms:
        ds = sorted(x.date_proxy for x in p.products if x.date_proxy)
        plat_median[p.key] = ds[len(ds) // 2] if ds else None

    # Segment-wide demand-pace norm: ratings accumulate with age, so recent
    # items ALWAYS show fewer ratings. A bucket's recent/earlier ratio is only
    # meaningful relative to the whole segment's ratio, compute that norm here.
    def _cohort_means(prods, plat_key):
        med = plat_median.get(plat_key)
        if not med:
            return None, None
        e = [x.rating_count or 0 for x in prods if x.date_proxy and x.date_proxy <= med]
        r = [x.rating_count or 0 for x in prods if x.date_proxy and x.date_proxy > med]
        return (statistics.mean(e) if e else None, statistics.mean(r) if r else None)

    seg_demand_ratio = None
    for p in demand_platforms:
        e, r = _cohort_means(p.products, p.key)
        if e and r is not None and e > 0:
            seg_demand_ratio = r / e
            break

    # ---- market-wide discount norm (for the relative discount penalty) -------
    all_inr_disc = [pr.true_discount for p in india_supply for pr in p.products
                    if pr.true_discount is not None]
    market_disc = statistics.median(all_inr_disc) if all_inr_disc else None

    # ════════════════════════════════════════════════════════════════════════
    # Per-bucket assembly
    # ════════════════════════════════════════════════════════════════════════
    details = {}
    for b, by_plat in buckets.items():
        all_prods = [pr for prods in by_plat.values() for pr in prods]
        n_total = len(all_prods)
        counts = {k: len(v) for k, v in by_plat.items()}

        # --- A ---
        dv = demand_norm.get(b)
        if not demand_platforms:
            demand = {"value": None, "derivation":
                      "No india_demand source present in /data, no demand signal "
                      "available for this dataset."}
        else:
            demand = {"value": dv, "raw_rating_count": demand_detail.get(b, 0),
                      "derivation":
                      f"Sum of rating_count on demand platforms "
                      f"({', '.join(p.key for p in demand_platforms)}) = "
                      f"{demand_detail.get(b, 0):,}; log-scaled then min-max "
                      f"normalized across the {len(buckets)} buckets present → {dv}."}

        # --- B ---
        sv_share, fr = share_norm.get(b), fresh_by_bucket.get(b)
        if sv_share is None:
            supply = {"value": None, "derivation": "No india_supply source present."}
        else:
            if fr is not None:
                sval = round(_clamp(W_SHARE * sv_share + W_FRESH * fr), 1)
                fr_txt = f"freshness {fr}% (share of items image-uploaded within {FRESH_WINDOW_DAYS}d of platform's newest upload, a proxy, not a true listing date)"
            else:
                sval, fr_txt = sv_share, "freshness unknown (no date proxy)"
            shares_txt = "; ".join(
                f"{p.key}: {counts.get(p.key, 0)}/{plat_total[p.key]} items "
                f"({(counts.get(p.key, 0) / plat_total[p.key] * 100):.1f}% of assortment)"
                for p in india_supply if plat_total[p.key])
            supply = {"value": sval, "freshness_pct": fr, "derivation":
                      f"Indian assortment share [{shares_txt}], normalized across "
                      f"buckets → {sv_share}; blended {W_SHARE}/{W_FRESH} with {fr_txt} → {sval}."}

        # --- C ---
        wv = west_norm.get(b)
        if not west_supply:
            west = {"value": None, "derivation": "No west_supply source present."}
        else:
            wc = sum(counts.get(p.key, 0) for p in west_supply)
            west = {"value": wv, "derivation":
                    f"{wc} of {sum(plat_total[p.key] for p in west_supply)} items in the "
                    f"Western new-drop feed ({', '.join(p.key for p in west_supply)}), "
                    f"share normalized across buckets → {wv}. This is SUPPLY (what was "
                    f"dropped), not demand, nobody has proven a Western shopper bought it."}

        # --- west_agreement: do the Western sources AGREE? ---------------------
        # Generic over N west_supply platforms (ASOS + H&M + any future source,
        # never special-cased). A direction backed by ≥2 sources is high
        # confidence; by 1 source it is explicitly flagged.
        w_present = [p.key for p in west_supply
                     if plat_total[p.key]
                     and len(by_plat.get(p.key, [])) / plat_total[p.key] >= MIN_PRESENCE_SHARE]
        if not west_supply:
            west_agreement = {"sources_present": 0, "sources_total": 0, "level": "none",
                              "derivation": "No west_supply sources in the data."}
        else:
            lvl = ("multi-source" if len(w_present) >= 2 else
                   "single-source" if len(w_present) == 1 else "absent")
            west_agreement = {
                "sources_present": len(w_present),
                "sources_total": len(west_supply),
                "present_on": w_present, "level": lvl,
                "derivation":
                    f"Present at ≥{MIN_PRESENCE_SHARE*100:.0f}% assortment share on "
                    f"{len(w_present)} of {len(west_supply)} Western sources "
                    f"({', '.join(w_present) or 'none'}). "
                    + ("Two+ independent Western sources agree, high-confidence direction."
                       if lvl == "multi-source" else
                       "Single Western source, direction is low-confidence, do not overstate."
                       if lvl == "single-source" else
                       "Not meaningfully present on any Western source."),
            }

        # --- H. own_sales (buyer's POS) ----------------------------------------
        ov = own_norm.get(b)
        if not pos_platforms:
            own = {"value": None, "return_rate": None, "derivation":
                   "No pos_sales source in the data, the buyer's own sell-through is "
                   "unavailable; scoring runs on market signals only. Drop a sales "
                   "export (CSV or JSON with style name + units sold) matching the "
                   "buyer_pos adapter into /data to activate this signal."}
        else:
            u, r = own_units.get(b, 0), own_returns.get(b, 0)
            rr = round(r / u * 100, 1) if u else None
            own = {"value": ov, "units": u, "return_rate": rr, "derivation":
                   f"{u:,.0f} units sold ({', '.join(p.key for p in pos_platforms)}, the "
                   f"buyer's own till data), log-scaled then min-max normalized across "
                   f"buckets → {ov}."
                   + (f" Return rate {rr}% ({r:,.0f} returned)"
                      + (f", at/above {RETURN_RATE_HIGH}%: sells but comes back."
                         if rr is not None and rr >= RETURN_RATE_HIGH else ".")
                      if rr is not None else " No returns column found.")}

        # --- D. lead_lag, derived from C vs A (or B as fallback) -------------
        if dv is not None:
            d_for_lag, d_name = dv, "demand_strength"
        else:
            d_for_lag, d_name = (sv_share or 0), "supply_conviction (no demand source, weaker basis)"
        w_for_lag = wv if wv is not None else 0
        fresh_ok = (fr or 0) >= FRESH_HIGH
        if wv is None:
            label, cmp_txt = "Unclear", "no west_supply source to compare against"
        elif w_for_lag >= WEST_HIGH and d_for_lag < DEMAND_LOW:
            label = "Early (West ahead)"
            cmp_txt = f"west {w_for_lag} ≥ WEST_HIGH({WEST_HIGH}) and {d_name} {d_for_lag} < DEMAND_LOW({DEMAND_LOW})"
        elif w_for_lag >= WEST_HIGH and d_for_lag >= DEMAND_HIGH:
            label = "Landed"
            cmp_txt = f"west {w_for_lag} ≥ WEST_HIGH({WEST_HIGH}) and {d_name} {d_for_lag} ≥ DEMAND_HIGH({DEMAND_HIGH})"
        elif w_for_lag <= WEST_LOW and d_for_lag >= DEMAND_HIGH and fresh_ok:
            label = "India-led"
            cmp_txt = (f"{d_name} {d_for_lag} ≥ DEMAND_HIGH({DEMAND_HIGH}), west {w_for_lag} ≤ "
                       f"WEST_LOW({WEST_LOW}), and supply is fresh ({fr}% ≥ FRESH_HIGH({FRESH_HIGH}))")
        elif w_for_lag <= WEST_LOW and d_for_lag >= DEMAND_HIGH:
            label = "Late-cycle (West moved on)"
            cmp_txt = (f"{d_name} {d_for_lag} ≥ DEMAND_HIGH({DEMAND_HIGH}) but west {w_for_lag} ≤ "
                       f"WEST_LOW({WEST_LOW}) and supply not fresh ({fr}%)")
        else:
            label = "Unclear"
            cmp_txt = (f"west {w_for_lag} / {d_name} {d_for_lag} sit between thresholds "
                       f"(WEST {WEST_LOW}–{WEST_HIGH}, DEMAND {DEMAND_LOW}–{DEMAND_HIGH})")
        ll_deriv = f"Comparison: {cmp_txt}."
        if west_agreement.get("level") == "single-source" and label != "Unclear":
            ll_deriv += (" CAUTION: the Western side of this call rests on a single "
                         "source, low confidence until a second Western source agrees.")
        lead_lag = {"label": label, "west_agreement": west_agreement.get("level"),
                    "derivation": ll_deriv}

        # --- West→India lag estimate (Early only): a buying calendar ----------
        # Coarse, signal-derived (gap between Western push and Indian presence),
        # NOT a per-trend constant. Labelled an estimate.
        if label.startswith("Early"):
            gap = w_for_lag - d_for_lag
            if gap >= LAG_GAP_WIDE:
                window = "~2 quarters"
            elif gap >= LAG_GAP_MID:
                window = "~1–2 quarters"
            else:
                window = "~1 quarter or less"
            lag_estimate = {
                "window": window,
                "derivation": f"ESTIMATE from the signal gap: west {w_for_lag} − India "
                              f"{d_for_lag} = {gap:.0f} (≥{LAG_GAP_WIDE} → ~2 qtrs, "
                              f"≥{LAG_GAP_MID} → ~1–2 qtrs, else ~1 qtr). Western "
                              f"fast-fashion typically leads Indian value retail by this "
                              f"order for silhouette trends, a rough buying calendar, "
                              f"not a forecast.",
            }
        else:
            lag_estimate = None

        # --- E. discount distortion penalty (0–40) ----------------------------
        inr_disc = [pr.true_discount for p in india_supply
                    for pr in by_plat.get(p.key, []) if pr.true_discount is not None]
        if inr_disc and market_disc is not None:
            med = statistics.median(inr_disc)
            excess = med - market_disc
            pen = round(_clamp(excess / DISCOUNT_EXCESS_SPAN * DISCOUNT_PENALTY_MAX,
                               0, DISCOUNT_PENALTY_MAX), 1)
            disc = {"value": pen, "median_discount": med, "market_median_discount": market_disc,
                    "derivation":
                    f"Median TRUE discount (recomputed as round((1−price/mrp)×100), scraper "
                    f"discount fields never trusted) = {med}% across {len(inr_disc)} Indian "
                    f"listings, vs a market-wide norm of {market_disc}% in this dataset. "
                    f"Penalty fires on the EXCESS ({med}−{market_disc} = {excess:+.0f}pp), scaling "
                    f"0→{DISCOUNT_PENALTY_MAX} over {DISCOUNT_EXCESS_SPAN}pp → −{pen}. Being "
                    f"pushed harder on price than the surrounding market means observed "
                    f"traction may be bought, not organic."}
        else:
            disc = {"value": 0, "median_discount": None,
                    "derivation": "No price/mrp pairs on Indian platforms, penalty 0, "
                                  "but discount distortion is UNKNOWN, not absent."}

        # --- F. supply-without-demand penalty (0–30) --------------------------
        sv = supply["value"]
        if (sv is not None and dv is not None
                and sv >= SUPPLY_HIGH and dv <= DEMAND_LOW):
            swd_pen = round(min(SWD_PENALTY_MAX, 0.5 * (sv - dv)), 1)
            swd = {"value": swd_pen, "derivation":
                   f"supply_conviction {sv} ≥ SUPPLY_HIGH({SUPPLY_HIGH}) while demand_strength "
                   f"{dv} ≤ DEMAND_LOW({DEMAND_LOW}): platforms are pushing stock shoppers "
                   f"haven't validated. Penalty = min({SWD_PENALTY_MAX}, 0.5×gap {sv}−{dv}) = −{swd_pen}."}
        else:
            swd = {"value": 0, "derivation":
                   ("Not fired: needs supply_conviction ≥ "
                    f"{SUPPLY_HIGH} AND demand_strength ≤ {DEMAND_LOW} "
                    f"(actual: supply {sv}, demand {dv}).")}

        # --- cross_platform_agreement ------------------------------------------
        if len(india_supply) >= 1:
            present, notes = 0, []
            for p in india_supply:
                share = counts.get(p.key, 0) / plat_total[p.key] if plat_total[p.key] else 0
                f = freshness_pct(by_plat.get(p.key, []), p.key)
                ok = share >= MIN_PRESENCE_SHARE and (f is None or f >= FRESH_HIGH)
                present += ok
                notes.append(f"{p.key}: share {share*100:.1f}% "
                             f"({'≥' if share >= MIN_PRESENCE_SHARE else '<'}"
                             f"{MIN_PRESENCE_SHARE*100:.0f}%), freshness "
                             f"{f if f is not None else 'unknown'} → "
                             f"{'counts' if ok else 'does not count'}")
            agree_val = round(present / len(india_supply) * 100, 1)
            agreement = {"value": agree_val, "derivation":
                         f"{present}/{len(india_supply)} Indian platforms carry this bucket "
                         f"fresh and at meaningful share. " + "; ".join(notes)}
        else:
            agreement = {"value": None, "derivation": "No india_supply platforms present."}

        # --- conflicts (computed) ----------------------------------------------
        conflicts, agrees = [], []
        if sv is not None and dv is not None:
            if sv >= SUPPLY_HIGH and dv <= DEMAND_LOW:
                conflicts.append(f"Indian platforms push supply (conviction {sv}) while the "
                                 f"demand authority shows weak demand ({dv}).")
            elif abs(sv - dv) <= 25:
                agrees.append(f"Indian supply ({sv}) and demand ({dv}) tell the same story.")
            elif dv - sv > 25:
                conflicts.append(f"Demand ({dv}) outruns Indian supply ({sv}), "
                                 f"possible under-assortment / stockout risk.")
        if wv is not None and dv is not None and wv >= WEST_HIGH and dv <= DEMAND_LOW:
            conflicts.append(f"The West is dropping this hard (west_signal {wv}) but Indian "
                             f"shoppers haven't moved (demand {dv}), classic lead-or-trap.")
        if wv is not None and dv is not None and wv >= WEST_HIGH and dv >= DEMAND_HIGH:
            agrees.append(f"West drops ({wv}) and Indian demand ({dv}) both high, landed.")
        if disc["value"] >= 20:
            conflicts.append(f"Median discount {disc['median_discount']}%, any demand "
                             f"reading is partly bought.")
        if own["value"] is not None and dv is not None:
            if own["value"] >= 60 and dv <= DEMAND_LOW:
                agrees.append(f"Your own stores already sell this (own-sales {own['value']}) "
                              f"despite weak market demand ({dv}), a local edge the "
                              f"market hasn't priced in.")
            elif own["value"] <= 25 and dv >= DEMAND_HIGH:
                conflicts.append(f"Market demand is high ({dv}) but your own sell-through "
                                 f"is weak (own-sales {own['value']}), assortment or "
                                 f"execution gap, not necessarily a bad trend.")
            elif own["value"] >= 60 and dv >= DEMAND_HIGH:
                agrees.append(f"Your tills (own-sales {own['value']}) and market demand "
                              f"({dv}) confirm each other, the strongest case possible.")
        if own.get("return_rate") is not None and own["return_rate"] >= RETURN_RATE_HIGH:
            conflicts.append(f"Own return rate {own['return_rate']}%, units sell but "
                             f"come back; net demand is weaker than gross.")
        if not agrees and not conflicts:
            agrees.append("Signals sit mid-range with no sharp divergence.")

        # --- G. replication probability (India-fit axes; defaults from keywords) ---
        kws = vocab[b]
        axes = {}
        for axis, spec in AXIS_RULES.items():
            score, why = spec["base"]
            for trig, s, r in spec["rules"]:
                hit = [k for k in kws if any(t in k or k in t for t in trig)]
                if hit:
                    score, why = s, f"{r} (matched keyword(s): {', '.join(hit)})"
                    break
            axes[axis] = {"default": score, "rationale": why}
        # price_band_fit from median INR price (generic rule, not per-trend)
        inr_prices = [pr.price for p in india_supply
                      for pr in by_plat.get(p.key, []) if pr.price]
        if inr_prices:
            medp = statistics.median(inr_prices)
            lo, hi = VALUE_BAND
            if lo <= medp <= hi:
                pscore, pwhy = 85, f"median Indian price ₹{medp:.0f} sits inside the value band ₹{lo}–₹{hi}"
            elif medp <= hi * 1.5:
                pscore, pwhy = 60, f"median Indian price ₹{medp:.0f} is above the value band ₹{lo}–₹{hi} but within 1.5×"
            else:
                pscore, pwhy = 40, f"median Indian price ₹{medp:.0f} is far above the value band ₹{lo}–₹{hi}"
        else:
            pscore, pwhy = 50, "no INR price data, neutral default"
        axes["price_band_fit"] = {"default": pscore, "rationale": pwhy}

        repl_val = round(statistics.mean(a["default"] for a in axes.values()), 1)
        replication = {
            "value": repl_val, "axes": axes,
            "applies": label.startswith("Early"),
            "derivation":
                f"Mean of 4 India-fit axes (engine defaults from the keyword rule table; "
                f"buyer can override each): "
                + ", ".join(f"{k}={v['default']}" for k, v in axes.items())
                + f" → {repl_val}. Applied as a multiplier ONLY when lead-lag is Early"
                + (" (it is)." if label.startswith("Early") else " (it is not, so shown for context only)."),
        }

        # --- trajectory from the listing-date proxy (no new scrape needed) ----
        # Cohorts: earlier- vs recently-listed (per-platform median split).
        # Assortment trajectory = are brands betting MORE recently? Demand pace
        # = bucket's recent/earlier rating ratio vs the segment norm (ratings
        # accumulate with age, so only the relative comparison is meaningful).
        earlier_n = recent_n = 0
        for p in india_supply:
            med = plat_median.get(p.key)
            if not med:
                continue
            for x in by_plat.get(p.key, []):
                if x.date_proxy:
                    if x.date_proxy > med:
                        recent_n += 1
                    else:
                        earlier_n += 1
        dated = earlier_n + recent_n
        if dated < 5:
            trajectory = {"arrow": "·", "label": "unknown",
                          "derivation": f"Only {dated} dated items (image-upload proxy), "
                                        "too few to read a trajectory."}
        else:
            rshare = recent_n / dated
            if rshare >= TRAJ_ACCELERATING:
                a_lbl, arrow = "accelerating", "↑"
            elif rshare <= TRAJ_FADING:
                a_lbl, arrow = "fading", "↓"
            else:
                a_lbl, arrow = "steady", "→"
            pace_txt = ""
            if demand_platforms and seg_demand_ratio:
                be = br = None
                for p in demand_platforms:
                    be, br = _cohort_means(by_plat.get(p.key, []), p.key)
                    if be:
                        break
                if be and br is not None and be > 0:
                    pace = "keeping pace" if (br / be) >= seg_demand_ratio else "flat/lagging"
                    pace_txt = f", demand {pace} vs segment norm"
                    label_combo = {
                        ("accelerating", "keeping pace"): "Assortment accelerating with demand, window open",
                        ("accelerating", "flat/lagging"): "Assortment accelerating, demand flat, supply getting ahead of demand, caution",
                        ("steady", "keeping pace"): "Steady supply, demand holding",
                        ("steady", "flat/lagging"): "Steady supply, demand cooling",
                        ("fading", "keeping pace"): "Assortment fading though demand holds, window narrowing",
                        ("fading", "flat/lagging"): "Assortment fading, demand flat, the best window may be gone",
                    }[(a_lbl, pace)]
                else:
                    label_combo = f"Assortment {a_lbl} (no demand cohort readable)"
            else:
                label_combo = f"Assortment {a_lbl} (no demand source to compare)"
            trajectory = {
                "arrow": arrow, "label": label_combo,
                "recent_share": round(rshare * 100, 1),
                "derivation":
                    f"PROXY based on image-upload dates, not true listing dates: "
                    f"{recent_n}/{dated} of this bucket's dated Indian items were uploaded "
                    f"after their platform's median upload date → recent share "
                    f"{rshare*100:.0f}% (≥{TRAJ_ACCELERATING*100:.0f}% accelerating, "
                    f"≤{TRAJ_FADING*100:.0f}% fading){pace_txt}. Re-shot images and "
                    f"platform image pipelines can distort this.",
            }

        # --- color & fabric sub-trends (second-axis clustering) ----------------
        colors = _subtrends(all_prods, COLOR_KEYWORDS)
        fabrics = _subtrends(all_prods, FABRIC_KEYWORDS)
        instruction = None
        if fabrics or colors:
            bits = []
            if fabrics:
                bits.append(f"strongest in {fabrics[0]['name']}"
                            + (f"/{fabrics[1]['name']}" if len(fabrics) > 1 else ""))
            if colors:
                bits.append(f"in {colors[0]['name']}"
                            + (f" and {colors[1]['name']}" if len(colors) > 1 else ""))
            instruction = ", ".join(bits)

        metrics = {"demand_strength": demand, "supply_conviction": supply,
                   "west_signal": west, "own_sales": own, "lead_lag": lead_lag,
                   "west_agreement": west_agreement,
                   "discount_penalty": disc, "supply_without_demand_penalty": swd,
                   "cross_platform_agreement": agreement, "replication": replication}

        details[b] = {
            "bucket": b,
            "display_name": b.replace("_", " ").title(),
            "keywords": kws,
            "counts": {"total": n_total, "by_platform": counts},
            "metrics": metrics,
            "examples": [{"name": pr.name, "platform": pr.platform,
                          "price": pr.price, "currency": pr.currency,
                          "url": pr.product_url}
                         for pr in sorted(all_prods, key=lambda x: -(x.rating_count or 0))[:5]],
            "qualified": n_total >= MIN_BUCKET_PRODUCTS,
            "disagreement": {"agree": agrees, "conflict": conflicts},
            "direction": _direction(label, west_agreement.get("sources_present", 0),
                                    west_agreement.get("sources_total", 0)),
            "lag_estimate": lag_estimate,
            "trajectory": trajectory,
            "subtrends": {"colors": colors, "fabrics": fabrics,
                          "buy_instruction": instruction,
                          "note": "second-axis keyword clustering on product names; "
                                  "unmatched words ignored"},
        }
        _finalize(details[b])           # confidence, bet, resolver, why, merchant line

    meta = {
        "platforms": [{
            "key": p.key, "file": p.file, "declared_roles": p.declared_roles,
            "effective_roles": p.roles, "products_used": p.filtered_count,
            "raw_rows": p.raw_count, "scraped_at": p.scraped_at,
            "newest_upload_proxy": str(plat_anchor[p.key]) if plat_anchor[p.key] else None,
            "limitations": p.limitations,
        } for p in platforms],
        "missing_roles": [r for r in ("india_demand", "india_supply", "west_supply", "pos_sales")
                          if not any(r in p.roles for p in platforms)],
        "silhouette_dictionary": vocab_name,
        "west_sources": {
            "count": len(west_supply),
            "names": [p.key for p in west_supply],
            "note": ("multiple Western sources, cross-source agreement raises "
                     "lead-lag confidence" if len(west_supply) >= 2 else
                     "single Western source, every West-based direction is flagged "
                     "low-confidence" if len(west_supply) == 1 else
                     "no Western source present"),
        },
        "buckets_found": len(buckets),
        "products_total": total_count,
        "products_unbucketed": other_count,
        "qualification_rule": f"bucket needs ≥ {MIN_BUCKET_PRODUCTS} products to enter the slate",
        "computed_at": str(date.today()),
        "note": "Every number above is recomputed from the files in /data at startup. "
                "Nothing trend-specific is hardcoded.",
    }
    if not demand_platforms:
        meta["missing_roles_note"] = "No demand signal available for this dataset, " \
            "confidence relies on supply only and says so in every derivation."
    return {"details": details, "meta": meta}


def _finalize(d: dict, overrides: dict = None):
    """Compute base/adjusted confidence, bet size, why-line and resolver for one
    bucket. `overrides` = buyer's India-fit axis values from the UI → live
    recompute (this is exactly what POST /api/recompute calls)."""
    m = d["metrics"]
    repl = m["replication"]

    # apply buyer overrides to the India-fit axes
    if overrides:
        for axis, val in overrides.items():
            if axis in repl["axes"] and val is not None:
                repl["axes"][axis]["override"] = _clamp(float(val))
        vals = [a.get("override", a["default"]) for a in repl["axes"].values()]
        repl["value"] = round(statistics.mean(vals), 1)
        repl["derivation"] += f" Buyer overrides applied → {repl['value']}."

    # --- base_confidence: weighted average of the positive signals present ----
    parts = [(m.get("own_sales", {}).get("value"), W_OWN_SALES, "own-sales"),
             (m["demand_strength"]["value"], W_DEMAND, "demand"),
             (m["supply_conviction"]["value"], W_SUPPLY, "supply"),
             (m["cross_platform_agreement"]["value"], W_AGREEMENT, "agreement")]
    avail = [(v, w, n) for v, w, n in parts if v is not None]
    if avail:
        wsum = sum(w for _, w, _ in avail)
        base = round(sum(v * w for v, w, _ in avail) / wsum, 1)
        base_txt = " + ".join(f"{n} {v}×{w / wsum:.2f}" for v, w, n in avail)
        missing = [n for v, _, n in parts if v is None]
        if missing:
            base_txt += f" (weights renormalized, missing: {', '.join(missing)})"
    else:
        base, base_txt = 0.0, "no positive signals available"

    # --- adjusted: subtract distortions, then Early-gate on replication -------
    adj = base - m["discount_penalty"]["value"] - m["supply_without_demand_penalty"]["value"]
    adj_txt = (f"base {base} − discount penalty {m['discount_penalty']['value']} "
               f"− supply-without-demand penalty {m['supply_without_demand_penalty']['value']}")
    if repl["applies"]:
        adj *= repl["value"] / 100
        adj_txt += f", × replication {repl['value']}/100 (lead-lag is Early)"
    adj = round(_clamp(adj), 1)

    d["confidence"] = {"base": base, "adjusted": adj,
                       "derivation": f"base = {base_txt} = {base}; adjusted = {adj_txt} = {adj}."}

    # --- bet size from named thresholds ---------------------------------------
    # verdict = the buyer-facing word on the tile face (BUY / TRIAL / WATCH)
    if adj >= BET_DEEPER:
        bet = {"label": "Deeper Buy", "verdict": "BUY",
               "band": "anchor SKUs, size the spend with the budget allocator",
               "rule": f"adjusted {adj} ≥ {BET_DEEPER}"}
    elif adj >= BET_TRIAL:
        bet = {"label": "Small Trial", "verdict": "TRIAL",
               "band": "3–5 test SKUs, read 4-week sell-through before scaling",
               "rule": f"{BET_TRIAL} ≤ adjusted {adj} < {BET_DEEPER}"}
    else:
        bet = {"label": "Monitor", "verdict": "WATCH",
               "band": "no buy, watch list, recheck next data drop",
               "rule": f"adjusted {adj} < {BET_TRIAL}"}
    d["bet"] = bet
    d["verdict"] = bet["verdict"]

    # --- one computed "why" line ----------------------------------------------
    ll = m["lead_lag"]["label"]
    dv, sv = m["demand_strength"]["value"], m["supply_conviction"]["value"]
    why = f"{ll}; "
    why += (f"demand {dv}" if dv is not None else "no demand signal")
    why += f", Indian supply {sv}" if sv is not None else ""
    pens = m["discount_penalty"]["value"] + m["supply_without_demand_penalty"]["value"]
    if pens:
        why += f"; distortions cost {round(pens, 1)} pts"
    if repl["applies"]:
        why += f"; Early-gate × {repl['value']}%"
    d["why"] = why

    # --- resolver: first matching signal pattern ------------------------------
    for pattern, test, action in _resolver_patterns():
        if test(m):
            d["disagreement"]["resolver"] = {"pattern": pattern, "action": action}
            break

    # --- merchant-voice line: assembled from the pattern, recomputes with
    #     overrides since verdict/axes may have changed ------------------------
    d["merchant_line"] = _merchant_line(d)
    return d


def build_slate(computed: dict) -> list:
    """Qualified buckets ranked by adjusted confidence, the landing payload.
    FACE fields (verdict, face_confidence, direction) carry the 3-second read;
    everything else feeds the expanded view. The face number is ALWAYS the
    adjusted (distortion-honest) confidence, never the raw signal."""
    rows = [d for d in computed["details"].values() if d["qualified"]]
    rows.sort(key=lambda d: -d["confidence"]["adjusted"])
    return [{
        # ── tile face ──
        "bucket": d["bucket"], "display_name": d["display_name"],
        "verdict": d["verdict"],
        "face_confidence": round(d["confidence"]["adjusted"]),
        "direction": d["direction"],
        "merchant_line": d["merchant_line"],
        "buy_instruction": d["subtrends"]["buy_instruction"],
        # ── expanded view / backward compatibility ──
        "bet": d["bet"], "lead_lag": d["metrics"]["lead_lag"]["label"],
        "west_agreement": d["metrics"]["west_agreement"]["level"],
        "confidence": d["confidence"], "why": d["why"],
        "counts": d["counts"],
        "demand": d["metrics"]["demand_strength"]["value"],
        "supply": d["metrics"]["supply_conviction"]["value"],
        "west": d["metrics"]["west_signal"]["value"],
        "own_sales": d["metrics"]["own_sales"]["value"],
    } for d in rows]
