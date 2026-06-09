"""
Trend Bet Workbench — FastAPI backend.

On startup: ingest whatever JSONs are in /data (per adapter_config), run the
scoring engine, cache the result, and freeze a copy to /data/sample_output.json
so reviewers can see computed results without running anything live.

All conclusions are computed at runtime — replace the files in /data and
restart: the slate, scores, labels and defaults all change with the data.
"""

import copy
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
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


@app.on_event("startup")
def startup():
    platforms = ingest.load_platforms()
    computed = scoring.compute(platforms)
    STATE["computed"] = computed
    STATE["slate"] = scoring.build_slate(computed)

    # Frozen sample for reviewers — cached output, regenerates from current /data
    # on every startup.
    sample = {
        "_note": "FROZEN SAMPLE — cached at backend startup, regenerated from "
                 "whatever is in /data each run. Not a source of truth.",
        "meta": computed["meta"],
        "slate": STATE["slate"],
        "details": computed["details"],
    }
    out = Path(ingest.DATA_DIR) / "sample_output.json"
    out.write_text(json.dumps(sample, indent=2, default=str))


@app.get("/api/slate")
def slate():
    return {"slate": STATE["slate"], "meta": STATE["computed"]["meta"]}


@app.get("/api/trend/{bucket}")
def trend(bucket: str):
    d = STATE["computed"]["details"].get(bucket)
    if not d:
        raise HTTPException(404, f"bucket '{bucket}' not present in current data")
    return d


@app.post("/api/recompute/{bucket}")
def recompute(bucket: str, overrides: FitOverrides):
    """Buyer overrides the India-fit axes → recompute adjusted confidence & bet
    live, WITHOUT mutating the cached baseline."""
    d = STATE["computed"]["details"].get(bucket)
    if not d:
        raise HTTPException(404, f"bucket '{bucket}' not present in current data")
    fresh = copy.deepcopy(d)
    return scoring._finalize(fresh, overrides.model_dump(exclude_none=True))


@app.get("/api/meta")
def meta():
    return STATE["computed"]["meta"]
