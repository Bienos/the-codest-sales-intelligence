"""Environment-driven settings. Secrets are read from env / mounted files, never from the repo."""
from __future__ import annotations

import dataclasses
import os
from dataclasses import dataclass, field


def _b(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _f(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


def read_secret(name: str) -> str | None:
    """`NAME` env var, or contents of the file named by `NAME_FILE` (Docker/K8s secrets)."""
    v = os.environ.get(name)
    if v:
        return v
    p = os.environ.get(f"{name}_FILE")
    if p and os.path.exists(p):
        with open(p, encoding="utf-8") as fh:
            return fh.read().strip()
    return None


# list prices in dollars per million tokens (input, output), prompts up to 100K tokens; from the published price list, check before relying on them
LIST_PRICES = {"claude-haiku-5-5": (0.10, 0.50), "claude-sonnet-5-5": (2.0, 10.0), "claude-opus-5-5": (4.0, 20.0)}


@dataclass(frozen=True)
class Settings:
    env: str = "development"  # development | staging | production
    database_url: str = "postgresql://postgres@localhost:5432/ri"
    # Hard safety switches. Production writes are OFF unless explicitly enabled per system.
    crm_writes_enabled: bool = False
    outbound_enabled: bool = False
    paid_enrichment_enabled: bool = False
    apollo_credits_per_reveal: float | None = None             # unknown price means no paid reveal can be approved
    # Budgets (EUR-agnostic "units"; provider rates are NOT assumed - see docs/14)
    default_monthly_provider_budget: float = 0.0
    default_hypothesis_research_budget: float = 0.0
    # Auth
    auth_mode: str = "oidc"  # oidc | dev (dev only allowed when env != production)
    oidc_issuer: str | None = None
    oidc_audience: str | None = None
    oidc_jwks_url: str | None = None
    dev_auth_secret: str | None = None
    # Secrets (resolved lazily; absence => integration reported 'blocked', never faked)
    google_credentials_file: str | None = field(default=None, repr=False)
    google_service_account_json: str | None = field(default=None, repr=False)
    anthropic_research_model: str = "claude-sonnet-5-5"       # writes account briefs (low volume, so quality over price); falls back to the reading model on any error
    session_secret: str | None = field(default=None, repr=False)      # signs 12-hour sessions; falls back to RI_LINK_SECRET
    anthropic_model: str = "claude-haiku-5-5"      # reads and verifies pages; switch with RI_ANTHROPIC_MODEL (prices below follow the model unless overridden)
    pipedrive_api_token: str | None = field(default=None, repr=False)
    pipedrive_webhook_secret: str | None = field(default=None, repr=False)
    pipedrive_write_token: str | None = field(default=None, repr=False)     # falls back to the API token: one key is enough to start
    pipedrive_domain: str | None = None                      # the part before .pipedrive.com (yours is shown in Pipedrive's address bar)
    pipedrive_pipeline_id: int = 2                            # Business Pipeline
    pipedrive_stage_id: int = 7                               # Discovery: where a deal starts when a prospect replies positively
    lemlist_api_key: str | None = field(default=None, repr=False)
    lemlist_webhook_secret: str | None = field(default=None, repr=False)
    apollo_api_key: str | None = field(default=None, repr=False)
    exa_api_key: str | None = field(default=None, repr=False)
    anthropic_api_key: str | None = field(default=None, repr=False)
    slack_webhook_url: str | None = field(default=None, repr=False)
    approval_ttl_hours: int = 72
    google_client_id: str | None = None
    anthropic_monthly_token_cap: float = 0.0     # tokens/month; 0 = model extraction cannot run
    exa_monthly_cap_usd: float = 0.0
    exa_est_cost_per_search: float | None = None  # reservation estimate only; real cost is taken from each Exa response. None => 0.01 default
    anthropic_price_in_per_mtok: float | None = 0.10   # list price, Claude Haiku 5.5 (prompts up to 100K tokens); override with RI_ANTHROPIC_PRICE_IN_PER_MTOK
    anthropic_price_out_per_mtok: float | None = 0.50
    discovery_interval_days: int = 7
    discovery_max_searches_per_run: int = 16
    discovery_max_new_requests_per_run: int = 30
    allowed_email_domain: str | None = None
    bootstrap_admin_emails: tuple[str, ...] = ()

    @classmethod
    def from_env(cls, require_auth: bool = True) -> Settings:
        env = os.environ.get("RI_ENV", "development")
        s = cls(
            env=env,
            database_url=os.environ.get("DATABASE_URL", cls.database_url),
            crm_writes_enabled=_b("RI_CRM_WRITES_ENABLED"),
            outbound_enabled=_b("RI_OUTBOUND_ENABLED"),
            paid_enrichment_enabled=_b("RI_PAID_ENRICHMENT_ENABLED"),
            apollo_credits_per_reveal=float(os.environ["RI_APOLLO_CREDITS_PER_REVEAL"]) if os.environ.get("RI_APOLLO_CREDITS_PER_REVEAL") else None,
            default_monthly_provider_budget=_f("RI_DEFAULT_MONTHLY_PROVIDER_BUDGET", 0.0),
            default_hypothesis_research_budget=_f("RI_DEFAULT_HYPOTHESIS_BUDGET", 0.0),
            auth_mode=os.environ.get("RI_AUTH_MODE", "oidc"),
            oidc_issuer=os.environ.get("RI_OIDC_ISSUER"),
            oidc_audience=os.environ.get("RI_OIDC_AUDIENCE"),
            oidc_jwks_url=os.environ.get("RI_OIDC_JWKS_URL"),
            dev_auth_secret=read_secret("RI_DEV_AUTH_SECRET"),
            google_credentials_file=os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"),
            google_service_account_json=read_secret("GOOGLE_SERVICE_ACCOUNT_JSON"),
            pipedrive_domain=os.environ.get("PIPEDRIVE_DOMAIN") or None,
            pipedrive_pipeline_id=int(os.environ.get("RI_PIPEDRIVE_PIPELINE_ID") or 2),
            pipedrive_stage_id=int(os.environ.get("RI_PIPEDRIVE_STAGE_ID") or 7),
            lemlist_api_key=read_secret("LEMLIST_API_KEY"),
            lemlist_webhook_secret=read_secret("RI_LEMLIST_WEBHOOK_SECRET"),
            apollo_api_key=read_secret("APOLLO_API_KEY"),
            session_secret=read_secret("RI_SESSION_SECRET") or read_secret("RI_LINK_SECRET"),
            anthropic_model=os.environ.get("RI_ANTHROPIC_MODEL") or "claude-haiku-5-5",
            anthropic_research_model=os.environ.get("RI_RESEARCH_MODEL") or "claude-sonnet-5-5",
            pipedrive_api_token=read_secret("PIPEDRIVE_API_TOKEN"),
            pipedrive_webhook_secret=read_secret("PIPEDRIVE_WEBHOOK_SECRET"),
            pipedrive_write_token=read_secret("PIPEDRIVE_WRITE_TOKEN") or read_secret("PIPEDRIVE_API_TOKEN"),
            exa_api_key=read_secret("EXA_API_KEY"),
            anthropic_api_key=read_secret("ANTHROPIC_API_KEY"),
            slack_webhook_url=read_secret("SLACK_WEBHOOK_URL"),
            approval_ttl_hours=int(os.environ.get("RI_APPROVAL_TTL_HOURS", "72")),
            google_client_id=os.environ.get("RI_GOOGLE_CLIENT_ID") or None,
            anthropic_monthly_token_cap=_f("RI_ANTHROPIC_MONTHLY_TOKEN_CAP", 0.0),
            exa_monthly_cap_usd=_f("RI_EXA_MONTHLY_CAP_USD", 0.0),
            anthropic_price_in_per_mtok=float(os.environ.get("RI_ANTHROPIC_PRICE_IN_PER_MTOK") or LIST_PRICES.get(os.environ.get("RI_ANTHROPIC_MODEL") or "claude-haiku-5-5", (0.10, 0.50))[0]),
            anthropic_price_out_per_mtok=float(os.environ.get("RI_ANTHROPIC_PRICE_OUT_PER_MTOK") or LIST_PRICES.get(os.environ.get("RI_ANTHROPIC_MODEL") or "claude-haiku-5-5", (0.10, 0.50))[1]),
            discovery_interval_days=int(os.environ.get("RI_DISCOVERY_INTERVAL_DAYS", "7")),
            discovery_max_searches_per_run=int(os.environ.get("RI_DISCOVERY_MAX_SEARCHES_PER_RUN", "16")),
            discovery_max_new_requests_per_run=int(os.environ.get("RI_DISCOVERY_MAX_NEW_REQUESTS_PER_RUN", "30")),
            exa_est_cost_per_search=float(os.environ["RI_EXA_EST_COST_PER_SEARCH"]) if os.environ.get("RI_EXA_EST_COST_PER_SEARCH") else None,
            allowed_email_domain=(os.environ.get("RI_ALLOWED_EMAIL_DOMAIN") or "").lower() or None,
            bootstrap_admin_emails=tuple(e.strip().lower() for e in os.environ.get("RI_BOOTSTRAP_ADMIN_EMAILS", "").split(",") if e.strip()),
        )
        if s.google_client_id and s.auth_mode == "oidc":      # Google Sign-In: ID tokens are RS256 JWTs signed with Google's published keys
            s = dataclasses.replace(s, oidc_audience=s.oidc_audience or s.google_client_id,
                                    oidc_issuer=s.oidc_issuer or "https://accounts.google.com,accounts.google.com",
                                    oidc_jwks_url=s.oidc_jwks_url or "https://www.googleapis.com/oauth2/v3/certs")
        s.validate(require_auth=require_auth)
        return s

    def validate(self, require_auth: bool = True) -> None:
        if self.env == "production":
            if self.auth_mode == "dev":
                raise RuntimeError("RI_AUTH_MODE=dev is forbidden in production")
            if require_auth and self.auth_mode == "oidc" and not (self.oidc_issuer and self.oidc_jwks_url and self.oidc_audience):
                raise RuntimeError("production requires RI_OIDC_ISSUER, RI_OIDC_AUDIENCE and RI_OIDC_JWKS_URL")
