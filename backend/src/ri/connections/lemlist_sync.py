"""Per-user Lemlist sync (placeholder until the Lemlist helper finishes): the job handler imports `run` from here."""
from __future__ import annotations

from typing import Any


def run(conn, job: dict[str, Any], *, settings) -> dict[str, Any]:  # pragma: no cover - replaced by the Lemlist helper
    raise NotImplementedError("Per-user Lemlist sync is not built yet")
