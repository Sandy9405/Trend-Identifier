"""
Trend Bet Workbench, FastAPI backend.

State is built lazily on first request (serverless-safe: on Vercel there is no
long-lived "startup") and cached in memory: ingest whatever JSONs are present
(repo /data + the upload dir), detect every (category, sub-category) segment,
and score segments on demand.

Data management API:
  POST /api/upload , push a new/replacement JSON data file, state reloads
  POST /api/reload , re-ingest from disk (e.g. you copied files in manually)
On a read-only host (Vercel) uploads land in a tmp dir that is EPHEMERAL per
serverless instance, the response says so; committing the file to /data and
redeploying is the durable path there.

All conclusions are computed at runtime, change the data and every segment,
slate, score, label and default recomputes. Nothing trend-specific is hardcoded.
"""

import copy
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import ingest
import scoring
from adapter_config import PLATFORM_ADAPTERS

app = FastAPI(title="Trend Bet Workbench")
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])

STATE = {}


class FitOverrides(BaseModel):
    climate_fit: float | None = None
    modesty_fit: float | None = None
    occasion_fit: float | None = None
    price_band_fit: float | None = None


class AllocateBody(BaseModel):
    budget: float
    # optional: live-adjusted confidences from India-fit overrides in the UI,
    # so an override that flips a verdict reshapes the allocation immediately
    adjustments: dict[str, float] | None = None
    # optional: amounts the buyer has dragged and locked, {bucket: rupees};
    # the engine re-solves the remainder around them (money conserved)
    pins: dict[str, float] | None = None


def _reload_state():
    """(Re)ingest everything and reset the per-segment score cache."""
    platforms = ingest.load_platforms()
    segments = ingest.list_segments(platforms)
    default = next((s for s in segments
                    if s["category"] == "women" and s["sub_category"] == "tops"),
                   segments[0] if segments else None)
    STATE.clear()
    STATE["platforms"] = platforms
    STATE["segments"] = segments
    STATE["cache"] = {}
    STATE["default_segment"] = (
        {"category": default["category"], "sub_category": default["sub_category"]}
        if default else None)
    if default:
        _write_frozen_sample()


def _ensure_loaded():
    if "platforms" not in STATE:
        _reload_state()
    if not STATE["segments"]:
        raise HTTPException(503, "no data files matched any adapter, drop JSONs "
                                 "into /data or POST /api/upload")


def _write_frozen_sample():
    """Frozen sample for reviewers, cached output for the default segment,
    regenerated from the current data. Skipped silently on read-only hosts."""
    try:
        entry = _compute_segment(**STATE["default_segment"])
        sample = {
            "_note": "FROZEN SAMPLE, cached output, regenerated from the current "
                     "data on reload. Not a source of truth.",
            "segments_detected": STATE["segments"],
            "default_segment": STATE["default_segment"],
            "meta": entry["computed"]["meta"],
            "slate": entry["slate"],
            "details": entry["computed"]["details"],
        }
        out = Path(ingest.DATA_DIR) / "sample_output.json"
        out.write_text(json.dumps(sample, indent=2, default=str))
    except OSError:
        pass  # read-only filesystem (serverless), sample lives in the repo


def _compute_segment(category: str, sub_category: str) -> dict:
    """Score one (category, sub_category) slice; cached per segment."""
    key = (category, sub_category)
    if key not in STATE["cache"]:
        scoped = ingest.scoped_platforms(STATE["platforms"], category, sub_category)
        computed = scoring.compute(scoped, sub_category=sub_category)
        computed["meta"]["segment"] = {"category": category, "sub_category": sub_category}
        # disclose scoping: rows outside the segment are filtered, not lost
        grand_total = sum(len(p.products) for p in STATE["platforms"])
        in_seg = computed["meta"]["products_total"]
        computed["meta"]["products_outside_segment"] = grand_total - in_seg
        STATE["cache"][key] = {"computed": computed,
                               "slate": scoring.build_slate(computed)}
    return STATE["cache"][key]


def _segment_or_default(category, sub_category):
    cat = category or STATE["default_segment"]["category"]
    sub = sub_category or STATE["default_segment"]["sub_category"]
    if not any(s["category"] == cat and s["sub_category"] == sub for s in STATE["segments"]):
        raise HTTPException(404, f"no products for segment {cat}/{sub} in current data")
    return cat, sub


# ───────────────────────────── data management ─────────────────────────────

@app.post("/api/upload")
async def upload(file: UploadFile):
    """Add or replace a data file, VALIDATE BEFORE ACCEPT. An upload either
    changes the computation (and the response says exactly what changed) or it
    is rejected with the reason and the fix. A stored-but-ignored file is not
    an acceptable outcome: that is how data silently goes missing."""
    if "platforms" not in STATE:
        _reload_state()   # so the before/after diff reflects this upload only
    fname = (file.filename or "").lower()
    if not (fname.endswith(".json") or fname.endswith(".csv")):
        raise HTTPException(400, "expected a .json or .csv file")
    body = await file.read()
    if fname.endswith(".json"):
        try:
            json.loads(body)
        except json.JSONDecodeError as e:
            raise HTTPException(400, f"not valid JSON: {e}")

    # 1. The filename must route to an adapter, otherwise nothing can parse it.
    matched = [(key, cfg) for key, cfg in PLATFORM_ADAPTERS.items()
               if ingest.glob_match(fname, cfg)]
    if not matched:
        raise HTTPException(422, detail={
            "accepted": False,
            "reason": f"filename '{file.filename}' matches no adapter pattern, so no "
                      "parser knows how to read it, rejected rather than stored-and-ignored.",
            "how_to_fix": "Rename the file so it matches a pattern below (e.g. a POS "
                          "export → 'buyer_sales.csv'), or add a new adapter entry in "
                          "backend/adapter_config.py.",
            "known_patterns": {k: c["glob"] for k, c in PLATFORM_ADAPTERS.items()},
        })
    adapter_key, cfg = matched[0]

    # 2. Dry-run parse: does the adapter actually extract products from it?
    dest_dir = ingest.upload_dir()
    pending = dest_dir / (".pending-" + Path(file.filename).name)
    pending.write_bytes(body)
    try:
        trial = ingest._load_one(adapter_key, cfg, pending)
        if trial.filtered_count == 0:
            raise HTTPException(422, detail={
                "accepted": False,
                "reason": f"matched adapter '{adapter_key}' but 0 of {trial.raw_count} rows "
                          "yielded a usable product, the column names don't line up with "
                          "the adapter's field_map.",
                "how_to_fix": "At minimum a product/style NAME column is required. Either "
                              "rename your columns, or extend the fallback key lists for "
                              f"'{adapter_key}' in backend/adapter_config.py.",
                "expected_name_keys": cfg["field_map"].get("name"),
            })
        # POS files must carry an actual sales signal, not just style names.
        if "pos_sales" in cfg["roles"]:
            usable = sum(1 for p in trial.products if (p.units_sold or 0) > 0)
            if usable == 0:
                raise HTTPException(422, detail={
                    "accepted": False,
                    "reason": f"matched the POS adapter and parsed {trial.filtered_count} "
                              "rows, but none carries units sold > 0, the file would "
                              "contribute no sales signal.",
                    "how_to_fix": "Make sure a units column exists and is named one of the "
                                  "accepted keys (or extend the list in adapter_config.py).",
                    "expected_units_keys": cfg["field_map"].get("units_sold"),
                })
    except HTTPException:
        pending.unlink(missing_ok=True)
        raise
    except Exception as e:
        pending.unlink(missing_ok=True)
        raise HTTPException(422, detail={
            "accepted": False,
            "reason": f"adapter '{adapter_key}' failed to parse the file: {e}",
            "how_to_fix": "Check the file structure (array of rows, or set root_path in "
                          "the adapter for nested wrappers).",
        })

    # 3. Commit: replace atomically, re-ingest, and report what actually changed.
    before = {(s["category"], s["sub_category"]): s["count"]
              for s in STATE.get("segments", [])}
    dest = dest_dir / Path(file.filename).name
    pending.replace(dest)
    _reload_state()
    after = {(s["category"], s["sub_category"]): s["count"] for s in STATE["segments"]}
    changes = [{"category": c, "sub_category": s,
                "products_delta": after.get((c, s), 0) - before.get((c, s), 0)}
               for c, s in sorted(set(before) | set(after))
               if after.get((c, s), 0) != before.get((c, s), 0)]

    persistent = dest_dir == ingest.DATA_DIR
    return {
        "accepted": True,
        "saved_as": dest.name,
        "matched_adapter": adapter_key,
        "roles": cfg["roles"],
        "products_parsed": trial.filtered_count,
        "rows_in_file": trial.raw_count,
        "segment_changes": changes,
        "persistence": (
            "saved into /data, survives restarts" if persistent else
            "host filesystem is read-only: saved to a TEMPORARY dir, active now "
            "but lost on the next cold start. For a durable update on Vercel, "
            "commit the file to /data and redeploy."),
        "segments": STATE["segments"],
        "default": STATE["default_segment"],
    }


@app.post("/api/reload")
def reload_data():
    """Re-ingest from disk, use after copying files into /data manually."""
    _reload_state()
    return {"segments": STATE["segments"], "default": STATE["default_segment"],
            "platforms": [{"key": p.key, "file": p.file, "products": p.filtered_count}
                          for p in STATE["platforms"]]}


# ─────────────────────────────── analysis ───────────────────────────────────

@app.get("/api/segments")
def segments():
    """Every category/sub-category present in the current data, with counts,
    powers the UI dropdowns. Recomputed from the data, never predefined."""
    _ensure_loaded()
    return {"segments": STATE["segments"], "default": STATE["default_segment"]}


@app.get("/api/slate")
def slate(category: str | None = Query(None), sub_category: str | None = Query(None)):
    _ensure_loaded()
    cat, sub = _segment_or_default(category, sub_category)
    entry = _compute_segment(cat, sub)
    return {"slate": entry["slate"], "meta": entry["computed"]["meta"]}


@app.get("/api/trend/{bucket}")
def trend(bucket: str, category: str | None = Query(None),
          sub_category: str | None = Query(None)):
    _ensure_loaded()
    cat, sub = _segment_or_default(category, sub_category)
    d = _compute_segment(cat, sub)["computed"]["details"].get(bucket)
    if not d:
        raise HTTPException(404, f"bucket '{bucket}' not present in segment {cat}/{sub}")
    return d


@app.post("/api/recompute/{bucket}")
def recompute(bucket: str, overrides: FitOverrides,
              category: str | None = Query(None), sub_category: str | None = Query(None)):
    """Buyer overrides the India-fit axes → recompute adjusted confidence & bet
    live, WITHOUT mutating the cached baseline."""
    _ensure_loaded()
    cat, sub = _segment_or_default(category, sub_category)
    d = _compute_segment(cat, sub)["computed"]["details"].get(bucket)
    if not d:
        raise HTTPException(404, f"bucket '{bucket}' not present in segment {cat}/{sub}")
    fresh = copy.deepcopy(d)
    return scoring._finalize(fresh, overrides.model_dump(exclude_none=True))


@app.post("/api/allocate")
def allocate(body: AllocateBody, category: str | None = Query(None),
             sub_category: str | None = Query(None)):
    """Open-to-buy allocation across the current slate: WATCH gets ₹0, TRIAL
    gets small capped tests, BUY splits the rest proportional to adjusted
    confidence. Optional `adjustments` lets the UI feed live India-fit override
    results so a changed verdict reshapes the plan."""
    if body.budget <= 0:
        raise HTTPException(400, "budget must be > 0")
    _ensure_loaded()
    cat, sub = _segment_or_default(category, sub_category)
    slate = copy.deepcopy(_compute_segment(cat, sub)["slate"])
    if body.adjustments:
        for row in slate:
            adj = body.adjustments.get(row["bucket"])
            if adj is None:
                continue
            row["confidence"]["adjusted"] = adj
            row["verdict"] = ("BUY" if adj >= scoring.BET_DEEPER else
                              "TRIAL" if adj >= scoring.BET_TRIAL else
                              "WATCH" if adj >= scoring.BET_WATCH else "SKIP")
        slate.sort(key=lambda r: -r["confidence"]["adjusted"])
    return scoring.allocate_budget(slate, body.budget, body.pins)


@app.get("/api/meta")
def meta(category: str | None = Query(None), sub_category: str | None = Query(None)):
    _ensure_loaded()
    cat, sub = _segment_or_default(category, sub_category)
    return _compute_segment(cat, sub)["computed"]["meta"]
