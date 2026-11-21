"""Serve the built SPA from the API container (no separate Node runtime in production)."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

DIST = Path(__file__).resolve().parents[4] / "frontend" / "dist"


def mount_frontend(app: FastAPI, dist: Path = DIST) -> bool:
    if not (dist / "index.html").exists():
        return False
    app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith(("api/", "webhooks/", "metrics", "healthz", "readyz", "kb/", "brand/logo")):
            from fastapi import HTTPException
            raise HTTPException(404)
        root = dist.resolve()
        try:
            f = (root / path).resolve()
        except (OSError, ValueError):
            f = root / "index.html"
        # never serve anything outside the build folder (path traversal): fall back to the SPA shell
        return FileResponse(f if f.is_file() and f.is_relative_to(root) else root / "index.html")
    return True
