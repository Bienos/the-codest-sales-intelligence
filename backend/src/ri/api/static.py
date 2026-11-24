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
        # Fall back to the shell for client-side routes and paths outside the build.
        shell = root / "index.html"
        file = f if f.is_file() and f.is_relative_to(root) else shell
        # Google Identity Services needs the parent origin to validate its sign-in iframe.
        headers = {"Referrer-Policy": "strict-origin-when-cross-origin"} if file == shell and app.state.settings.google_client_id else None
        return FileResponse(file, headers=headers)
    return True
