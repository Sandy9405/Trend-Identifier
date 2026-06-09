"""
SCORING ENGINE — the reviewer-facing deliverable.
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
# 1. SILHOUETTE KEYWORD DICTIONARY  (extend-friendly — add a line, get a bucket)
#    A product may match multiple buckets. Matching is case-insensitive on name.
# ════════════════════════════════════════════════════════════════════════════
SILHOUETTE_KEYWORDS = {
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
# Products matching no bucket fall into "other": excluded from the slate but
# counted in totals so shares stay honest.

# ════════════════════════════════════════════════════════════════════════════
# 2. NAMED THRESHOLD CONSTANTS  (the tuning surface — change here, not in logic)
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
# current dataset's own market-wide median — i.e. "this trend is being pushed
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

# base_confidence weights (renormalized automatically if a signal is missing)
W_DEMAND, W_SUPPLY, W_AGREEMENT = 0.45, 0.35, 0.20

# supply_conviction blend: assortment share vs freshness
W_SHARE, W_FRESH = 0.7, 0.3

# Replication probability below this counts as "low" (resolver pattern table)
REPL_LOW = 70

# Bet-size thresholds on ADJUSTED confidence
BET_DEEPER = 65         # ≥ 65 → Deeper Buy
BET_TRIAL = 40          # 40–64 → Small Trial; < 40 → Monitor

# Slate qualification: buckets with fewer total products are noise, not trends
MIN_BUCKET_PRODUCTS = 10

# India value band (INR) for price_band_fit
VALUE_BAND = (299, 799)

# ════════════════════════════════════════════════════════════════════════════
# 3. INDIA-FIT AXIS RULE TABLE (replication probability, Early trends only)
#    Defaults are inferred from BUCKET KEYWORDS — never per-trend hardcoding.
#    A brand-new silhouette next month gets sensible, explainable defaults.
#    Format: first matching rule wins; otherwise the axis BASE applies.
# ════════════════════════════════════════════════════════════════════════════
AXIS_RULES = {
    "climate_fit": {
        "base": (75, "no heavy-volume/layering cue in bucket keywords"),
        "rules": [
            (["puff", "balloon", "corset", "cinch", "smock", "shirred", "shirring"],
             45, "keywords imply volume/structure/layering — heat risk in Indian climate"),
        ],
    },
    "modesty_fit": {
        "base": (75, "no sheer/bare cue in bucket keywords"),
        "rules": [
            (["mesh", "sheer", "organza", "net", "lace", "bandeau", "tube",
              "off shoulder", "off-shoulder", "bardot", "halter", "halterneck"],
             45, "keywords imply sheer/bare styling — modesty constraint in mass-market India"),
        ],
    },
    "occasion_fit": {
        "base": (70, "no strong occasion cue in bucket keywords"),
        "rules": [
            (["mesh", "sheer", "corset", "bandeau", "tube", "organza"],
             50, "keywords imply party/occasion-only wear — fewer wear occasions, slower turns"),
            (["shirt", "crop", "tank", "vest", "cami", "sleeveless"],
             85, "keywords imply everyday wear — wide occasion coverage"),
        ],
    },
    # price_band_fit is computed from the bucket's median INR price (see below),
    # not from keywords — but the rule is still generic, not per-trend.
}

# ════════════════════════════════════════════════════════════════════════════
# 4. RESOLVER PATTERN TABLE (disagreement view). Keyed on the SIGNAL PATTERN,
#    never the trend name. First matching pattern (priority order) wins —
#    one conflict summary + ONE resolver, kept decisive.
# ════════════════════════════════════════════════════════════════════════════
def _resolver_patterns():
    return [
        ("high supply + low demand",
         lambda m: m["supply_conviction"]["value"] is not None
         and m["supply_conviction"]["value"] >= SUPPLY_HIGH
         and (m["demand_strength"]["value"] or 0) <= DEMAND_LOW,
         "Full-price sell-through & return-rate on the new drop (buyer's own POS data) — "
         "platforms are pushing stock; only till data proves anyone wants it."),
        ("lead-lag Early + low replication",
         lambda m: m["lead_lag"]["label"].startswith("Early")
         and m["replication"]["value"] < REPL_LOW,
         "Small live trial in 2–3 target stores/regions — the India-fit doubts "
         "(climate/modesty/occasion/price) can only be answered by real shoppers."),
        ("high discount + high demand",
         lambda m: m["discount_penalty"]["value"] >= 20
         and (m["demand_strength"]["value"] or 0) >= DEMAND_HIGH,
         "Is demand surviving at full price? Margin check: re-read rating velocity "
         "after the discount event ends before trusting this volume."),
        ("india-led + strong demand",
         lambda m: m["lead_lag"]["label"].startswith("India-led")
         and (m["demand_strength"]["value"] or 0) >= DEMAND_HIGH,
         "Confirm size-curve & repeat-rate before scaling — demand is proven, "
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


def assign_buckets(name: str) -> list:
    """Case-insensitive keyword match; a product may land in several buckets."""
    n = name.lower()
    return [b for b, kws in SILHOUETTE_KEYWORDS.items() if any(k in n for k in kws)]


# ════════════════════════════════════════════════════════════════════════════
# THE ENGINE
# ════════════════════════════════════════════════════════════════════════════
def compute(platforms: list) -> dict:
    """platforms: list[ingest.PlatformData]. Returns the full computed payload:
    slate + per-bucket details + meta. Pure function of the data on disk."""

    demand_platforms = [p for p in platforms if "india_demand" in p.roles]
    india_supply = [p for p in platforms if "india_supply" in p.roles]
    west_supply = [p for p in platforms if "west_supply" in p.roles]

    # ---- bucket membership (computed, never predefined) ----------------------
    # buckets[bucket][platform_key] = list[Product]
    buckets, other_count, total_count = {}, 0, 0
    for plat in platforms:
        for prod in plat.products:
            total_count += 1
            bs = assign_buckets(prod.name)
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
    # A. demand_strength — from india_demand rating_count totals.
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
    # B. supply_conviction — Indian assortment share blended with freshness,
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
    # C. west_signal — share of west_supply assortment. SUPPLY, NOT DEMAND.
    # ════════════════════════════════════════════════════════════════════════
    raw_west = {}
    for b, by_plat in buckets.items():
        shares = [len(by_plat.get(p.key, [])) / plat_total[p.key]
                  for p in west_supply if plat_total[p.key]]
        raw_west[b] = statistics.mean(shares) if shares else None
    west_norm = _minmax(raw_west)

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
                      "No india_demand source present in /data — no demand signal "
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
                fr_txt = f"freshness {fr}% (share of items image-uploaded within {FRESH_WINDOW_DAYS}d of platform's newest upload — a proxy, not a true listing date)"
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
                    f"dropped), not demand — nobody has proven a Western shopper bought it."}

        # --- D. lead_lag — derived from C vs A (or B as fallback) -------------
        if dv is not None:
            d_for_lag, d_name = dv, "demand_strength"
        else:
            d_for_lag, d_name = (sv_share or 0), "supply_conviction (no demand source — weaker basis)"
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
        lead_lag = {"label": label, "derivation": f"Comparison: {cmp_txt}."}

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
                    "derivation": "No price/mrp pairs on Indian platforms — penalty 0, "
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
                conflicts.append(f"Demand ({dv}) outruns Indian supply ({sv}) — "
                                 f"possible under-assortment / stockout risk.")
        if wv is not None and dv is not None and wv >= WEST_HIGH and dv <= DEMAND_LOW:
            conflicts.append(f"The West is dropping this hard (west_signal {wv}) but Indian "
                             f"shoppers haven't moved (demand {dv}) — classic lead-or-trap.")
        if wv is not None and dv is not None and wv >= WEST_HIGH and dv >= DEMAND_HIGH:
            agrees.append(f"West drops ({wv}) and Indian demand ({dv}) both high — landed.")
        if disc["value"] >= 20:
            conflicts.append(f"Median discount {disc['median_discount']}% — any demand "
                             f"reading is partly bought.")
        if not agrees and not conflicts:
            agrees.append("Signals sit mid-range with no sharp divergence.")

        # --- G. replication probability (India-fit axes; defaults from keywords) ---
        kws = SILHOUETTE_KEYWORDS[b]
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
            pscore, pwhy = 50, "no INR price data — neutral default"
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

        metrics = {"demand_strength": demand, "supply_conviction": supply,
                   "west_signal": west, "lead_lag": lead_lag,
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
        }
        _finalize(details[b])           # confidence, bet, resolver, why

    meta = {
        "platforms": [{
            "key": p.key, "file": p.file, "declared_roles": p.declared_roles,
            "effective_roles": p.roles, "products_used": p.filtered_count,
            "raw_rows": p.raw_count, "scraped_at": p.scraped_at,
            "newest_upload_proxy": str(plat_anchor[p.key]) if plat_anchor[p.key] else None,
            "limitations": p.limitations,
        } for p in platforms],
        "missing_roles": [r for r in ("india_demand", "india_supply", "west_supply")
                          if not any(r in p.roles for p in platforms)],
        "buckets_found": len(buckets),
        "products_total": total_count,
        "products_unbucketed": other_count,
        "qualification_rule": f"bucket needs ≥ {MIN_BUCKET_PRODUCTS} products to enter the slate",
        "computed_at": str(date.today()),
        "note": "Every number above is recomputed from the files in /data at startup. "
                "Nothing trend-specific is hardcoded.",
    }
    if not demand_platforms:
        meta["missing_roles_note"] = "No demand signal available for this dataset — " \
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
    parts = [(m["demand_strength"]["value"], W_DEMAND, "demand"),
             (m["supply_conviction"]["value"], W_SUPPLY, "supply"),
             (m["cross_platform_agreement"]["value"], W_AGREEMENT, "agreement")]
    avail = [(v, w, n) for v, w, n in parts if v is not None]
    if avail:
        wsum = sum(w for _, w, _ in avail)
        base = round(sum(v * w for v, w, _ in avail) / wsum, 1)
        base_txt = " + ".join(f"{n} {v}×{w / wsum:.2f}" for v, w, n in avail)
        missing = [n for v, _, n in parts if v is None]
        if missing:
            base_txt += f" (weights renormalized — missing: {', '.join(missing)})"
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
    if adj >= BET_DEEPER:
        bet = {"label": "Deeper Buy", "band": "anchor SKUs, ~₹8–15L open-to-buy",
               "rule": f"adjusted {adj} ≥ {BET_DEEPER}"}
    elif adj >= BET_TRIAL:
        bet = {"label": "Small Trial", "band": "3–5 SKUs, ~₹2–5L, read 4-week sell-through",
               "rule": f"{BET_TRIAL} ≤ adjusted {adj} < {BET_DEEPER}"}
    else:
        bet = {"label": "Monitor", "band": "no buy — watch list, recheck next data drop",
               "rule": f"adjusted {adj} < {BET_TRIAL}"}
    d["bet"] = bet

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
    return d


def build_slate(computed: dict) -> list:
    """Qualified buckets ranked by adjusted confidence — the landing payload."""
    rows = [d for d in computed["details"].values() if d["qualified"]]
    rows.sort(key=lambda d: -d["confidence"]["adjusted"])
    return [{
        "bucket": d["bucket"], "display_name": d["display_name"],
        "bet": d["bet"], "lead_lag": d["metrics"]["lead_lag"]["label"],
        "confidence": d["confidence"], "why": d["why"],
        "counts": d["counts"],
        "demand": d["metrics"]["demand_strength"]["value"],
        "supply": d["metrics"]["supply_conviction"]["value"],
        "west": d["metrics"]["west_signal"]["value"],
    } for d in rows]
