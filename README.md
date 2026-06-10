# Trend Bet Workbench

A data-driven buying-decision tool for a **value-fashion category buyer (women's tops, India)**
who must commit inventory **before** demand is obvious. For every silhouette trend found in the
data it recommends one of three bets, **Monitor / Small Trial / Deeper Buy**, and shows, next
to every number, exactly how it was computed and what could be distorting it.

The product is not a confident score. The product is **honest reasoning under uncertainty**:
raw signal vs distortion-adjusted signal, side by side, with one decisive resolver per conflict.

---

## Architecture in one paragraph

A FastAPI backend ingests **whatever JSON files are in `/data`** at startup (via a platform
adapter config), derives a category/sub-category segment for every product, clusters each
segment's products into silhouette buckets by keyword, computes all signals normalized
*across the buckets present in the current dataset*, and serves the results as JSON. A React (Vite) frontend renders the ranked slate and per-trend detail pages entirely
from those APIs. **Nothing trend-specific is hardcoded anywhere**, no trend name, rank, share,
label, or fit-default. Replace the files in `/data` tomorrow and the entire output recomputes
with zero code changes. The reasoning *method* (formulas, thresholds, keyword rules) lives in
`backend/scoring.py`; all *conclusions* are computed at runtime.

## How to run

Backend (Python 3.10+):

```bash
cd backend
pip install -r requirements.txt
uvicorn main:app --port 8000
```

Frontend (Node 18+), in a second terminal:

```bash
cd frontend
npm install
npm run dev
```

Open **http://localhost:5173**. The Vite dev server proxies `/api/*` to the backend on :8000.

### Deploying to Vercel

The repo is Vercel-ready: `api/index.py` is auto-detected as a Python serverless function
exposing the FastAPI app (dependencies from the root `requirements.txt`; `/backend` and
`/data` are bundled with it), while `vercel.json` builds the frontend as a static Vite site
(`installCommand`/`buildCommand`/`outputDirectory`) and rewrites `/api/*` to the function.
Import the repo in Vercel, **leave the project's Root Directory as the repo root** (not
`frontend/`), with framework preset "Other". The frontend calls `/api/*` on the same origin,
so nothing points at localhost in production.

One serverless caveat, handled and disclosed by the app itself: Vercel's filesystem is
read-only, so files pushed through `POST /api/upload` land in a **temporary directory**,
active immediately, but lost on the next cold start and not shared across instances. The
upload response (and the UI notice) tells you which mode you're in. **The durable way to
update data on Vercel is to commit the file into `/data` and push, Vercel redeploys and
every number recomputes.** Running locally, uploads are saved straight into `/data` and
survive restarts.

No live run needed to inspect results: on every backend startup a frozen sample of the full
computed output is written to **`data/sample_output.json`** (slate + per-trend details + meta).
It is a cache of the current `/data` contents and regenerates each run.

## How to add or replace data (no code change)

Three ways, all triggering a full recompute:

- **UI**: the "upload data file" button in the header → `POST /api/upload`. Uploads are
  **validated before they are accepted**, an upload either changes the computation (the
  response and the UI banner say exactly which segments changed and by how much) or it is
  **rejected with the reason and the fix** (HTTP 422): filename matching no adapter pattern,
  columns that don't map to the adapter's field_map, or a POS file with no units sold.
  A stored-but-ignored file is never an outcome.
- **API**: `curl -X POST -F "file=@new_scrape.json" <host>/api/upload`, or copy files into
  `/data` and call `POST /api/reload`.
- **Files** (durable on Vercel): commit the file into `/data` and redeploy.

The **filename decides which platform adapter parses it** (matched against the adapter
globs, e.g. anything containing `myntra`). Uploading a file with the same name replaces it.
**Multiple files per platform coexist**, a women's-tops scrape and a men's-footwear scrape
from Myntra cover different segments and never touch each other. Where two files of one
platform DO overlap inside a segment (a fresh scrape re-covering the same products), the
duplicates are deduplicated by product URL with the **newest file winning row-by-row**, and
the merge is disclosed as a limitation, so re-uploads supersede exactly what they re-cover,
nothing more. A filename matching no adapter is stored but ignored, with an explicit
warning listing the known globs.

Silhouette vocabularies are also **chosen per sub-category**: footwear segments cluster on
a footwear dictionary (sneakers, loafers, boots, mesh-knit…) instead of the apparel one,
otherwise mesh running shoes would land in "mesh_sheer" and lace-ups in "tie_knot". Adding
a vocabulary for a new sub-category is one dict in `scoring.py`
(`DICTIONARY_FOR_SUBCATEGORY`); unmapped sub-categories fall back to the apparel dictionary.

### Different schemas from different sources

The adapter layer is built for schema drift, so a new vendor's export needs a mapping,
never code:

- **CSV or JSON** files both ingest (CSV via its header row).
- **Nested JSON**: `root_path: "data.products"` descends to the row array; common
  single-list wrappers (`{"data": [...]}`) unwrap automatically.
- **Dot paths** in `field_map` reach nested values: `"price": "pricing.selling_price"`.
- **Fallback key lists** absorb variants in one entry:
  `"price": ["sellingPrice", "price", "asp"]`, first non-empty wins. This is how one
  `buyer_pos` entry already handles most POS export headers.

Step by step for a brand-new source:

1. Drop the new JSON/CSV file into `/data` (or POST it to `/api/upload`).
2. If it comes from a platform already configured, name it so it matches that platform's glob
   (e.g. anything containing `myntra`), done, nothing else to touch.
3. If it's a **new platform**, add **one entry** to `backend/adapter_config.py` declaring:
   its file glob, its role(s) (`india_demand`, `india_supply`, `west_supply`), a field map from
   its raw keys to the canonical fields, an optional date-proxy regex for its image URLs, and an
   optional sub-category filter.
4. Restart the backend. The slate, scores, lead-lag labels, India-fit defaults, and resolvers
   all recompute from the new data.

If a role disappears (e.g. you remove the only demand source), the engine degrades gracefully:
scores renormalize over the signals that exist, and every affected derivation plus `/api/meta`
says so explicitly ("no demand signal available for this dataset").

## Segment scoping, what happens when the data isn't (only) women's tops

Scrapes are messy: AJIO's "tops" search returns sarees, jeans and kurtas; a future file might
be men's shoes. Nothing is silently discarded. At ingest every product gets a canonical
**category** (women / men / kids) and **sub-category** (tops / shirts / tshirts / jeans /
sarees / shoes / …), resolved in priority order:

1. a raw field the platform provides (mapped in the adapter, e.g. AJIO's `segment` /
   `subCategory`),
2. generic keyword inference over that raw value, the product URL, then the name
   (ordered rule tables in `ingest.py`, e.g. `tshirts` is checked before `shirts` because
   "t-shirt" contains "shirt"; "women" before "men" with a word boundary),
3. an optional adapter default describing how the scrape was taken (e.g. the ASOS feed is the
   women's new-in page even though product names never say "women"),
4. `unknown`, kept, counted, and disclosed.

`GET /api/segments` returns every (category, sub-category) pair actually present, with counts
per platform, that's what populates the two dropdowns in the UI header. Selecting a segment
re-scopes the **entire analysis**: bucket membership, normalization, lead-lag, penalties,
defaults, and even the per-platform signal checks are recomputed for that slice (a platform's
demand role is re-validated per segment, AJIO might have live review counts for jeans but
none for tops). The default segment is women/tops when present, otherwise the largest segment
found. Segments with too little data show an honest empty slate rather than fake trends.

## The source roles, what each CAN and CANNOT prove

| Role | Current source | Can prove | Cannot prove |
|---|---|---|---|
| `india_demand` | Myntra (`ratingCount`) | Accumulated Indian purchase interest, people bought and bothered to rate | Recency (ratings accumulate over years); full-price appetite |
| `india_supply` | Myntra + AJIO assortment | What Indian platforms are betting shelf space on, and how fresh that bet is | That anyone wants it, supply is a merchant's opinion |
| `west_supply` | ASOS new-in feed | What Western fast-fashion just **dropped** | Demand of any kind, anywhere. It is labelled "supply, not demand" everywhere it appears |
| `pos_sales` | *(none yet, drop a file to activate)* | The buyer's OWN till data: real local purchases and returns, the strongest demand evidence | Anything about styles never stocked, it is silent on what was never bought |

## The buyer's own sales data (`pos_sales`)

No sales file is attached yet, so the engine runs market-only **and says so** (the
"Your own sales (POS)" signal shows "n/a" with instructions, and `/api/meta` lists
`pos_sales` under missing roles). To activate it, drop **any CSV or JSON whose filename
contains `sales`, `sell` or `pos`** into `/data` (or use the upload button), a column template is
in `data/buyer_sales_template.csv.example`. Minimum useful columns: a style **name**
(bucketed by the same silhouette keywords) and **units_sold**; optional: returns, ASP, MRP,
category/sub-category. The `buyer_pos` adapter ships with fallback key lists
(`units_sold` / `qty_sold` / `quantity_sold`…), so most POS/ERP export headers map without
touching config.

When present, own sales becomes the **heaviest signal** in base confidence
(weights: own-sales 0.35, demand 0.30, supply 0.20, agreement 0.15, renormalized
automatically when absent), and unlocks new computed reasoning:

- **Local edge**: your stores sell it while market demand is weak → flagged as an
  advantage the market hasn't priced in.
- **Execution gap**: market demand high but your sell-through weak → resolver asks for an
  assortment/placement check before blaming the trend.
- **Return-rate flag**: returns ≥ 25% of units → "sells but comes back" conflict, and the
  top-priority resolver becomes a fit/quality audit, gross volume is not net demand.

Auto-detected and disclosed in this dataset (see `/api/meta`):

- **AJIO declares `india_demand` but every `reviewCount` is zero** → its demand role is
  automatically demoted; it contributes supply only. If a future AJIO scrape has real review
  counts, it starts contributing demand automatically, no config change.
- **Myntra's `discountPercent` field is wrong** (it stores the rupee saving, e.g. "599%").
  This is why the system **always recomputes true discount** as `round((1 − price/mrp) × 100)`
  and never trusts scraper discount fields.
- **Freshness is a proxy**: the upload date embedded in image URLs (Myntra:
  `/images/2025/NOVEMBER/22/`, AJIO: `/20250918/`). ASOS images carry no date → freshness
  "unknown" for ASOS, disclosed as such.
- AJIO's "tops" search returns sarees, jeans, kurtas… → these are routed into their own
  segments (browsable via the dropdowns) instead of being discarded.

## Scoring method (full detail in `backend/scoring.py`, heavily commented)

Products are clustered into silhouette buckets by case-insensitive keyword match on the product
name (dictionary at the top of `scoring.py`; a product may match several buckets; matching none
→ "other", excluded from the slate but counted in totals). For **every bucket present in the
current data**:

- **A. demand_strength**, sum of `rating_count` on demand platforms, log-scaled (so one
  50k-review hero doesn't drown everything), then min-max normalized **across the buckets
  present** (all normalization is relative, so scores self-adjust to new data).
- **B. supply_conviction**, Indian assortment share blended 70/30 with freshness% (share of
  items image-uploaded within 120 days of that platform's newest upload).
- **C. west_signal**, share of the Western new-drop feed. Supply, never demand.
- **D. lead_lag**, derived by comparing C against A (or B if no demand source) using named
  constants (`WEST_HIGH=55`, `WEST_LOW=25`, `DEMAND_HIGH=55`, `DEMAND_LOW=40`, `FRESH_HIGH=50`):
  *Early (West ahead) / Landed / Late-cycle (West moved on) / India-led / Unclear*. The exact
  comparison that produced the label is stored in its derivation.
- **E. discount distortion penalty (0–40)**, fires on median true discount **in excess of the
  dataset's own market-wide median** (Indian value e-commerce discounts everything; the
  distortion is being pushed harder on price *than the surrounding market*). The market norm is
  computed from the data and disclosed in the derivation.
- **F. supply-without-demand penalty (0–30)**, fires when supply_conviction ≥ 60 while
  demand_strength ≤ 40: platforms pushing stock shoppers haven't validated.
- **G. replication probability (0–100)**, applied as a multiplier **only when lead-lag is
  Early**. Four India-fit axes (climate, modesty, occasion, price-band) with engine-inferred
  defaults from a **keyword rule table** (e.g. sheer/bare keywords lower modesty_fit) plus a
  median-price rule for price-band, never per-trend hardcoding, so a brand-new silhouette next
  month gets sensible, explainable defaults. The buyer can override each axis in the UI →
  `POST /api/recompute` → live update.

**Final:**

```
base_confidence   = weighted_avg(demand 0.45, supply 0.35, cross_platform_agreement 0.20)
                    (weights renormalize automatically over the signals present)
adjusted          = base − discount_penalty − supply_without_demand_penalty
if lead_lag Early: adjusted ×= replication/100
clamp 0–100
```

Bet sizing (named constants): **≥ 65 Deeper Buy** (anchor SKUs) ·
**40–64 Small Trial** (3–5 test SKUs, read 4-week sell-through) · **< 40 Monitor**.
Rupee amounts come from the budget allocator, never from fixed bands.
Slate qualification: a bucket needs ≥ 10 products, otherwise it's noise, not a trend.

### Multiple Western sources & west_agreement

`west_supply` is handled **generically over N sources**, the engine aggregates assortment
share across every platform with that role and never special-cases ASOS, H&M or any future
feed. Adding H&M was literally one adapter entry (`hm` in `adapter_config.py`), no logic
change, proof the architecture generalizes. Per bucket the engine also computes
**west_agreement**: present at meaningful share on ≥2 Western sources = a high-confidence
direction; on 1 source = the direction is explicitly flagged "(1 source), single-source,
low confidence" on the tile, in the lead-lag derivation, and in `/api/meta.west_sources`.

### Tile face vs depth

The slate is a traffic-light board, scannable in 3 seconds. Each tile FACE shows only the
decision: the trend name, ONE verdict (**BUY** / **TRIAL** / **WATCH**, mapping to Deeper
Buy / Small Trial / Monitor), ONE number, the **adjusted, distortion-honest confidence,
never the raw signal**, and ONE West→India direction glyph (↑ Early, = Landed, ↓ Late,
◆ India-led, • Unclear). Everything else, raw-vs-adjusted bars, distortion derivations,
India-fit sliders, disagreement + resolver, merchant line, trajectory, sub-trends, counts,
lives behind the click.

### Trajectory (listing-date proxy)

Each bucket's Indian products are split into earlier- vs recently-listed cohorts by their
platform's median image-upload date. Recent share ≥60% = assortment accelerating (brands
betting more); ≤40% = fading. Where demand data exists, the bucket's recent/earlier
rating-count ratio is compared against the segment-wide norm (ratings accumulate with age,
so only the relative comparison means anything) to read whether demand keeps pace,
"assortment accelerating, demand flat = supply getting ahead of demand, caution". **Stated
limits**: image-upload dates approximate listing dates; re-shot images and platform image
pipelines distort it. It's labelled a proxy everywhere it appears.

### Color & fabric sub-trends

Second-axis keyword clustering over product names (same pattern as silhouettes; dictionaries
at the top of `scoring.py`, extend-friendly; unmatched words ignored). Each trend reports its
top colors and fabrics with shares, collapsed into a buy instruction, e.g. "puff balloon,
strongest in cotton, in white and pink", so the output is an order spec, not just a flag.

### Budget allocation

Enter an open-to-buy amount on the slate → `POST /api/allocate` turns the slate into a money
plan (`allocate_budget` in `scoring.py`, fully commented): **WATCH** gets ₹0 (monitoring is
free), **TRIAL** bets get equal test allocations capped at 8% each / 25% combined, **BUY**
bets split the remainder proportional to adjusted (never raw) confidence. With no BUY-grade
trend the remainder is explicitly held back, not force-spent. The endpoint accepts live
India-fit-override adjustments so a changed verdict reshapes the plan immediately.

### West→India lag estimate

Early trends additionally show a coarse buying calendar, "Western lead of ~1–2 quarters",
derived from the gap between the Western signal and Indian presence (named thresholds, not
per-trend constants), labelled clearly as an estimate.

### The West→India lead-lag concept

Western fast-fashion drops (ASOS "new in") often lead the Indian value market by one or more
seasons, but only *sometimes* replicate, because climate, modesty norms, wear occasions, and
price bands differ. So a heavy Western drop with no Indian demand is **either a lead or a
trap**. The engine names that situation "Early (West ahead)", gates its confidence by the
India-fit replication probability, and routes its resolver to a small live trial, the only
thing that actually distinguishes lead from trap.

### Disagreement view & resolvers

Per bucket the engine computes where sources agree vs conflict (from the numbers, e.g. "AJIO
pushes fresh supply while the demand authority shows weak demand"), then picks **one** resolver
from a priority-ordered pattern table keyed on the *signal pattern*, never the trend name:
high-supply+low-demand → full-price sell-through & returns from the buyer's POS data;
Early+low-replication → live trial in 2–3 stores; high-discount+high-demand → does demand
survive at full price; India-led+strong-demand → confirm size-curve & repeat-rate. Decisive on
purpose: one conflict summary, one resolver, not a wall of caveats.

## Failure modes (known, by design)

- **Keyword clustering is lexical.** "Tie" matches tie-dye and tie-front alike; "fitted"
  catches "fitted peplum". Buckets overlap deliberately (a product can be both crop and
  halter), but a misleading product *name* misleads the bucket.
- **Freshness is an image-upload proxy**, not a listing date; re-shot images look "new".
- **rating_count measures accumulated demand**, so it structurally favours older trends,
  exactly why Late-cycle exists as a label, and why the resolver for hot-but-stale buckets
  asks for rating *velocity*.
- **One scrape = one snapshot.** Lead-lag is inferred from levels, not real time series; two
  scrape cycles apart would make it far stronger (see next steps).
- **Cross-currency**: ASOS prices (GBP) are never mixed into INR metrics; the West contributes
  presence/share only.

## Feedback loop

The detail page captures agree / disagree / would-buy per trend (component state in this
build). In the next iteration these land in a small store keyed by
`(bucket, dataset_version)`, and buyer disagreement becomes a prior-adjustment input to the
next scoring run, the system learns which signal patterns this buyer has beaten before.

## Business measurement

Adoption is measured the way a buying office actually works: **4-week sell-through** of Small
Trials vs control picks; **markdown reduction** on Deeper Buys (did distortion-adjusted picks
need fewer end-of-season cuts?); **stockout avoidance** on India-led/Landed trends;
**decision speed** (slate review minutes vs manual scrape-trawling); and **tool adoption**
(share of seasonal buys that went through a recorded slate decision).

## Next steps

1. Ingest a second scrape cycle → real deltas (rating-count velocity, assortment growth)
   instead of level-based lead-lag.
2. Persist buyer feedback and trial outcomes; backtest threshold constants against them.
3. Add more demand authorities (Flipkart/Nykaa ratings), one adapter entry each.
4. Embedding-based clustering behind the same bucket interface to fix lexical false matches.

## Repository layout

```
vercel.json                # Vercel: static frontend + Python serverless function
api/index.py               # Vercel entrypoint re-exporting the FastAPI app
data/                      # drop scrapes here (3 current files + frozen sample_output.json)
backend/
  adapter_config.py        # THE config: one entry per platform, edit only this for new data
  ingest.py                # schema-tolerant ingestion + auto-detection of dead signals
  scoring.py               # the scoring engine, method fixed, all conclusions computed
  main.py                  # FastAPI app + frozen-sample writer
frontend/                  # React (Vite): slate + 4-panel detail, all content from the API
```

## API

All analysis endpoints accept optional `?category=&sub_category=` query params (default:
women/tops if present, else the largest segment detected).

- `POST /api/upload`, multipart upload of a scrape JSON; re-ingests and returns the new segment list, the matched adapter, and a persistence notice
- `POST /api/reload`, re-ingest from disk after manual file changes
- `POST /api/allocate`, body `{budget, adjustments?}` → per-trend ₹ allocation with rationale
- `GET /api/segments`, every category/sub-category present in the data, with counts; powers the dropdowns
- `GET /api/slate`, ranked computed slate + meta for the segment
- `GET /api/trend/{bucket}`, full detail payload incl. derivations & India-fit defaults
- `POST /api/recompute/{bucket}`, body `{climate_fit?, modesty_fit?, occasion_fit?, price_band_fit?}` → recomputed confidence/bet without mutating the baseline
- `GET /api/meta`, detected platforms/roles, data dates, auto-detected limitations, segment coverage
