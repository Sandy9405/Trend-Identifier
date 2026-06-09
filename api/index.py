"""Vercel serverless entrypoint — exposes the FastAPI app from /backend.

Vercel's Python runtime detects the ASGI `app` object. The repo's /data JSONs
are bundled with the function (see vercel.json includeFiles), so the engine
computes from them on cold start, lazily, in memory.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from main import app  # noqa: E402,F401
