"""Build the right connector for a registered source. Shared by the worker (runs) and the bulk validation job."""
from __future__ import annotations

from ..db import Conn
from . import registry
from .safe import SafeClient


class NoConnector(NotImplementedError):
    pass


def ats_connectors() -> dict[str, type]:
    """The job-board readers the account scan uses, by the provider name stored on `account_endpoints.provider` and `job_postings.provider`.
    One place to register a new career system: the scan, its tests and the detection in account_scan.py all start from this list."""
    from .ats import AshbyConnector, GreenhouseConnector, LeverConnector, LeverEuConnector, SmartRecruitersConnector
    from .ats_more import MORE_CONNECTORS
    return {"greenhouse": GreenhouseConnector, "lever": LeverConnector, "ashby": AshbyConnector, "smartrecruiters": SmartRecruitersConnector,
            "lever_eu": LeverEuConnector, **MORE_CONNECTORS}


def build_connector(conn: Conn, source_id: str):
    from .procurement import ContractsFinderConnector, FindTenderConnector, PolishBzpConnector, TedConnector
    from .jobs_market import ArbeitnowConnector, JobTechConnector
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
    if source_id == "jobs-arbeitnow-de":
        return ArbeitnowConnector(client)
    # round 8: remote-job boards and the German national tender service (sources/jobs_market.py, sources/tenders_more.py)
    if source_id == "jobs-jobicy-remote":
        from .jobs_market import JobicyConnector
        return JobicyConnector(client)
    if source_id == "jobs-remoteok":
        from .jobs_market import RemoteOkConnector
        return RemoteOkConnector(client)
    if source_id == "procurement-de-bekanntmachung":
        from .tenders_more import GermanNoticeConnector
        return GermanNoticeConnector(client, backfill_days=int(cfg.get("backfill_days") or 21))
    url = cfg.get("url")
    if url:
        if src["collection_method"] == "feed":
            return FeedConnector(client, url, source_id=source_id)
        return PageWatchConnector(client, url, source_id=source_id, selector=cfg.get("selector"))
    raise NoConnector(f"no connector or address for {source_id}")
