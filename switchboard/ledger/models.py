from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

CacheStatus = Literal["miss", "hit_exact", "hit_semantic", "bypass"]
EventStatus = Literal["ok", "error", "cancelled"]


class UsageEvent(BaseModel):
    """One ledger row per request. Written asynchronously by the LedgerWriter."""

    ts: datetime
    request_id: str
    tenant_id: str
    api_key_id: str | None
    requested_model: str
    deployment_id: str | None = None
    provider: str | None = None
    task_class: str | None = None
    route_mode: str = "explicit"
    route_reason: str | None = None
    experiment_id: str | None = None
    cache_status: CacheStatus = "miss"
    stream: bool = False
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: Decimal = Decimal(0)
    ttft_ms: int | None = None
    total_ms: int = 0
    status: EventStatus = "ok"
    error_code: str | None = None


class UsageSummaryRow(BaseModel):
    deployment_id: str | None
    requests: int
    errors: int
    prompt_tokens: int
    completion_tokens: int
    cost_usd: Decimal
    cache_hits: int
    avg_total_ms: float | None = None
    p95_total_ms: float | None = None


class UsageSummary(BaseModel):
    tenant_id: str | None
    since: datetime
    until: datetime
    rows: list[UsageSummaryRow] = Field(default_factory=list)

    @property
    def total_cost_usd(self) -> Decimal:
        return sum((r.cost_usd for r in self.rows), Decimal(0))
