"""Revenue forecast: funnel history, outbound actuals and the simulated year (ri/forecast). Read for sales managers and administrators;
only they change the assumptions. Team-wide totals, so not offered to roles that see only their own accounts."""
from __future__ import annotations

from typing import Any

from fastapi import Depends, FastAPI, Request
from pydantic import BaseModel, Field, field_validator

from ..access import Principal
from ..audit import audit
from .auth import require


class ForecastSettingsIn(BaseModel):
    year: int | None = Field(None, ge=2024, le=2100)
    currency: str | None = Field(None, max_length=5)
    target: float | None = Field(None, ge=0, le=1e11)
    pipeline_id: int | None = None
    parking_stage_names: list[str] | None = Field(None, max_length=20)
    won_stage_names: list[str] | None = Field(None, max_length=20)
    outbound_entry_stage_id: int | None = None
    inbound_entry_stage_id: int | None = None
    prior_strength: int | None = Field(None, ge=0, le=500)
    prior_conv: dict[str, float] | None = None
    prior_days: dict[str, float] | None = None
    deal_value: float | None = Field(None, ge=0, le=1e10)
    leads_per_month: float | None = Field(None, ge=0, le=1e6)
    reply_rate: float | None = Field(None, ge=0, le=100)
    positive_share: float | None = Field(None, ge=0, le=100)
    deal_from_positive: float | None = Field(None, ge=0, le=100)
    inbound_per_month: float | None = Field(None, ge=0, le=1e5)
    fx_to_eur: dict[str, float] | None = None                          # 1 unit of each currency in EUR

    @field_validator("fx_to_eur")
    @classmethod
    def _fx(cls, v: dict[str, float] | None) -> dict[str, float] | None:
        if v is not None and (len(v) > 30 or any(not (k.isalpha() and len(k) == 3) for k in v) or any(not (0 < x < 1e6) for x in v.values())):
            raise ValueError("exchange rates are positive numbers keyed by three-letter currency codes")
        return {k.upper(): x for k, x in v.items()} if v is not None else v
    clear: list[str] = Field(default_factory=list, max_length=20)     # fields to put back to "measured / default" (None)

    @field_validator("prior_conv")
    @classmethod
    def _conv(cls, v: dict[str, float] | None) -> dict[str, float] | None:
        if v is not None and (len(v) > 50 or any(not (0 <= x <= 100) for x in v.values()) or any(not k.lstrip("-").isdigit() for k in v)):
            raise ValueError("stage rates are percentages between 0 and 100, keyed by stage id")
        return v

    @field_validator("prior_days")
    @classmethod
    def _days(cls, v: dict[str, float] | None) -> dict[str, float] | None:
        if v is not None and (len(v) > 50 or any(not (1 <= x <= 1000) for x in v.values()) or any(not k.lstrip("-").isdigit() for k in v)):
            raise ValueError("stage durations are days between 1 and 1000, keyed by stage id")
        return v

    @field_validator("parking_stage_names", "won_stage_names")
    @classmethod
    def _names(cls, v: list[str] | None) -> list[str] | None:
        if v is not None and any(len(x) > 120 for x in v):
            raise ValueError("stage names are at most 120 characters")
        return v


CLEARABLE = {"year", "currency", "pipeline_id", "outbound_entry_stage_id", "inbound_entry_stage_id", "leads_per_month", "inbound_per_month"}


def register(app: FastAPI) -> None:
    @app.get("/api/forecast")
    def get_forecast(request: Request, fresh: bool = False, p: Principal = Depends(require("forecast.read"))):
        from ..forecast.service import build
        with request.app.state.db.tx() as conn:          # fresh: recalculate now instead of using the last 10 minutes' result
            out = build(conn, request.app.state.settings, use_cache=not fresh)
            return {**out, "can": {"manage": p.can("forecast.manage"), "sync": p.can("admin.jobs") or p.role == "sales_manager"}}

    @app.put("/api/forecast/settings")
    def put_settings(body: ForecastSettingsIn, request: Request, p: Principal = Depends(require("forecast.manage"))):
        from ..forecast.service import build, load_settings, log_forecast, save_settings
        patch: dict[str, Any] = body.model_dump(exclude_unset=True, exclude={"clear"})
        patch = {k: v for k, v in patch.items() if v is not None}
        for k in body.clear:
            if k in CLEARABLE:
                patch[k] = None
        with request.app.state.db.tx() as conn:
            before = {k: v for k, v in load_settings(conn).items() if not k.startswith("_")}
            after = save_settings(conn, patch, p.email)
            audit(conn, actor=p.email, operation="forecast_settings_changed", record_type="forecast_settings", record_id="1", before=before, after=after,
                  detail={"changed": sorted(k for k in after if after.get(k) != before.get(k))})
            payload = build(conn, request.app.state.settings, use_cache=False)
            log_forecast(conn, payload, "settings_saved")
            return {**payload, "can": {"manage": True, "sync": p.can("admin.jobs") or p.role == "sales_manager"}}
