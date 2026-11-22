"""Build the right connector for a registered source. Shared by the worker (runs) and the bulk validation job."""
from __future__ import annotations

from ..db import Conn
from . import registry
from .safe import SafeClient


class NoConnector(NotImplementedError):
    pass


def build_connector(conn: Conn, source_id: str):
    from .procurement import ContractsFinderConnector, FindTenderConnector, PolishBzpConnector, TedConnector
    from .jobs_market import JobTechConnector
    from .webwatch import FeedConnector, PageWatchConnector
    src = registry.get(conn, source_id)
    cfg = src["config"] or {}
    client = SafeClient(timeout=60)
    if source_id == "procurement-ted":
        return TedConnector(client, query=cfg["query"])
    if source_id == "procurement-uk-fts":
        return FindTenderConnector(client)
    if source_id == "procurement-uk-contracts-finder":
        return ContractsFinderConnector(client)
    if source_id == "procurement-ezamowienia":
        return PolishBzpConnector(client)
    if source_id == "jobs-jobtech-se":
        return JobTechConnector(client, queries=cfg.get("queries"))
    url = cfg.get("url")
    if url:
        if src["collection_method"] == "feed":
            return FeedConnector(client, url, source_id=source_id)
        return PageWatchConnector(client, url, source_id=source_id, selector=cfg.get("selector"))
    raise NoConnector(f"no connector or address for {source_id}")
