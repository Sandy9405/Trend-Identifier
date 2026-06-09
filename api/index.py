"""Vercel serverless entrypoint — exposes the FastAPI app from /backend.

Vercel auto-detects this file as a Python function and looks for the ASGI
`app` object; the rewrite in vercel.json sends every /api/* request here,
and the app receives the ORIGINAL path (e.g. /api/segments), so the FastAPI
routes match unchanged. The Python runtime bundles the whole repo, so
/backend and /data are available at runtime; the engine computes from them
on cold start, lazily, in memory.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from main import app  # noqa: E402,F401
