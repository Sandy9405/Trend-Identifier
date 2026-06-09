"""
Trend Bet Workbench — FastAPI backend.

On startup: ingest whatever JSONs are in /data (per adapter_config), detect every
(category, sub-category) segment present, and score the DEFAULT segment
(women/tops if present, else the largest). Any other segment is scored lazily on
first request and cached — the UI's dropdowns can scope the whole analysis to any
slice of the data.

All conclusions are computed at runtime — replace the files in /data and
restart: the segments, slate, scores, labels and defaults all change with the data.
"""

import copy
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import ingest
import scoring

app = FastAPI(title="Trend Bet Workbench")
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])

STATE = {}


class FitOverrides(BaseModel):
    climate_fit: float | None = None
    modesty_fit: float | None = None
    occasion_fit: float | None = None
    price_band_fit: float | None = None


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


@app.on_event("startup")
def startup():
    platforms = ingest.load_platforms()
    STATE["platforms"] = platforms
    STATE["cache"] = {}
    segments = ingest.list_segments(platforms)
    STATE["segments"] = segments

    # Default segment: women/tops when the data contains it, otherwise the
    # largest segment found. Computed, not assumed.
    default = next((s for s in segments
                    if s["category"] == "women" and s["sub_category"] == "tops"),
                   segments[0] if segments else None)
    if default is None:
        STATE["default_segment"] = {"category": "unknown", "sub_category": "unknown"}
        return
    STATE["default_segment"] = {"category": default["category"],
                                "sub_category": default["sub_category"]}

    entry = _compute_segment(default["category"], default["sub_category"])

    # Frozen sample for reviewers — cached output for the default segment,
    # regenerated from whatever is in /data on every startup.
    sample = {
        "_note": "FROZEN SAMPLE — cached at backend startup, regenerated from "
                 "whatever is in /data each run. Not a source of truth.",
        "segments_detected": segments,
        "default_segment": STATE["default_segment"],
        "meta": entry["computed"]["meta"],
        "slate": entry["slate"],
        "details": entry["computed"]["details"],
    }
    out = Path(ingest.DATA_DIR) / "sample_output.json"
    out.write_text(json.dumps(sample, indent=2, default=str))


@app.get("/api/segments")
def segments():
    """Every category/sub-category present in the current data, with counts —
    powers the UI dropdowns. Recomputed from /data, never predefined."""
    return {"segments": STATE["segments"], "default": STATE["default_segment"]}


@app.get("/api/slate")
def slate(category: str | None = Query(None), sub_category: str | None = Query(None)):
    cat, sub = _segment_or_default(category, sub_category)
    entry = _compute_segment(cat, sub)
    return {"slate": entry["slate"], "meta": entry["computed"]["meta"]}


@app.get("/api/trend/{bucket}")
def trend(bucket: str, category: str | None = Query(None),
          sub_category: str | None = Query(None)):
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
    cat, sub = _segment_or_default(category, sub_category)
    d = _compute_segment(cat, sub)["computed"]["details"].get(bucket)
    if not d:
        raise HTTPException(404, f"bucket '{bucket}' not present in segment {cat}/{sub}")
    fresh = copy.deepcopy(d)
    return scoring._finalize(fresh, overrides.model_dump(exclude_none=True))


@app.get("/api/meta")
def meta(category: str | None = Query(None), sub_category: str | None = Query(None)):
    cat, sub = _segment_or_default(category, sub_category)
    return _compute_segment(cat, sub)["computed"]["meta"]
