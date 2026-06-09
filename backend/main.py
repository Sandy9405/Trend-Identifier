"""
Trend Bet Workbench — FastAPI backend.

State is built lazily on first request (serverless-safe: on Vercel there is no
long-lived "startup") and cached in memory: ingest whatever JSONs are present
(repo /data + the upload dir), detect every (category, sub-category) segment,
and score segments on demand.

Data management API:
  POST /api/upload  — push a new/replacement JSON data file, state reloads
  POST /api/reload  — re-ingest from disk (e.g. you copied files in manually)
On a read-only host (Vercel) uploads land in a tmp dir that is EPHEMERAL per
serverless instance — the response says so; committing the file to /data and
redeploying is the durable path there.

All conclusions are computed at runtime — change the data and every segment,
slate, score, label and default recomputes. Nothing trend-specific is hardcoded.
"""

import copy
import fnmatch
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
        raise HTTPException(503, "no data files matched any adapter — drop JSONs "
                                 "into /data or POST /api/upload")


def _write_frozen_sample():
    """Frozen sample for reviewers — cached output for the default segment,
    regenerated from the current data. Skipped silently on read-only hosts."""
    try:
        entry = _compute_segment(**STATE["default_segment"])
        sample = {
            "_note": "FROZEN SAMPLE — cached output, regenerated from the current "
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
        pass  # read-only filesystem (serverless) — sample lives in the repo


def _compute_segment(category: str, sub_category: str) -> dict:
    """Score one (category, sub_category) slice; cached per segment."""
    key = (category, sub_category)
    if key not in STATE["cache"]:
        scoped = ingest.scoped_platforms(STATE["platforms"], category, sub_category)
        computed = scoring.compute(scoped)
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
    """Add or replace a data file, then re-ingest. The filename decides which
    platform adapter it feeds (matched against the adapter globs); an uploaded
    file with the same name as an existing one replaces it."""
    if not file.filename or not file.filename.lower().endswith(".json"):
        raise HTTPException(400, "expected a .json file")
    body = await file.read()
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as e:
        raise HTTPException(400, f"not valid JSON: {e}")
    if not isinstance(parsed, list) or not parsed:
        raise HTTPException(400, "expected a non-empty JSON array of product rows")

    matched_adapter = next(
        (key for key, cfg in PLATFORM_ADAPTERS.items()
         if fnmatch.fnmatch(file.filename.lower(), cfg["glob"].lower())), None)

    dest_dir = ingest.upload_dir()
    dest = dest_dir / Path(file.filename).name
    dest.write_bytes(body)
    _reload_state()

    persistent = dest_dir == ingest.DATA_DIR
    return {
        "saved_as": dest.name,
        "rows": len(parsed),
        "matched_adapter": matched_adapter,
        "warning": None if matched_adapter else (
            "no adapter glob matches this filename — the file is stored but "
            "IGNORED until you add an entry to backend/adapter_config.py "
            f"(known globs: {[c['glob'] for c in PLATFORM_ADAPTERS.values()]})"),
        "persistence": (
            "saved into /data — survives restarts" if persistent else
            "host filesystem is read-only: saved to a TEMPORARY dir, active now "
            "but lost on the next cold start. For a durable update on Vercel, "
            "commit the file to /data and redeploy."),
        "segments": STATE["segments"],
        "default": STATE["default_segment"],
    }


@app.post("/api/reload")
def reload_data():
    """Re-ingest from disk — use after copying files into /data manually."""
    _reload_state()
    return {"segments": STATE["segments"], "default": STATE["default_segment"],
            "platforms": [{"key": p.key, "file": p.file, "products": p.filtered_count}
                          for p in STATE["platforms"]]}


# ─────────────────────────────── analysis ───────────────────────────────────

@app.get("/api/segments")
def segments():
    """Every category/sub-category present in the current data, with counts —
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


@app.get("/api/meta")
def meta(category: str | None = Query(None), sub_category: str | None = Query(None)):
    _ensure_loaded()
    cat, sub = _segment_or_default(category, sub_category)
    return _compute_segment(cat, sub)["computed"]["meta"]
