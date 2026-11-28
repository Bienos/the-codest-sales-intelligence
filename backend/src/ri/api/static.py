"""Serve the built SPA from the API container (no separate Node runtime in production)."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

DIST = Path(__file__).resolve().parents[4] / "frontend" / "dist"


def mount_frontend(app: FastAPI, dist: Path = DIST) -> bool:
    if not (dist / "index.html").exists():
        return False
    app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith(("api/", "webhooks/", "metrics", "healthz", "readyz", "kb/", "brand/logo", "mcp", "oauth/", ".well-known/")):
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
        settings = getattr(app.state, "settings", None)
        headers = {"Referrer-Policy": "strict-origin-when-cross-origin"} if file == shell and getattr(settings, "google_client_id", None) else None
        public_url = getattr(settings, "public_url", None)
        if file == shell and public_url:
            # Link previews need an absolute image address; the build only knows the path.
            html = shell.read_text(encoding="utf-8").replace('content="/og-image.png"', f'content="{public_url}/og-image.png"')
            return HTMLResponse(html, headers=headers)
        return FileResponse(file, headers=headers)
    return True
