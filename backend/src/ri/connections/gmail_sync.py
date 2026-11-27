"""Gmail sync (placeholder until the Gmail helper finishes): the job handler imports `run` from here."""
from __future__ import annotations

from typing import Any


def run(conn, job: dict[str, Any], *, settings) -> dict[str, Any]:  # pragma: no cover - replaced by the Gmail helper
    raise NotImplementedError("Gmail sync is not built yet")
