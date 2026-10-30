"""Model catalog: every deployment the gateway can route to, with prices and capabilities.

Prices live here so cost is computed at request time from token counts and never needs a
provider billing export. The catalog is plain YAML so adding a model is a config change.
"""

import os
from decimal import Decimal
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

ProviderKind = Literal["openai_compat", "anthropic", "bedrock"]


class Pricing(BaseModel):
    """USD per one million tokens."""

    input_per_1m: Decimal = Decimal(0)
    output_per_1m: Decimal = Decimal(0)

    def cost(self, prompt_tokens: int, completion_tokens: int) -> Decimal:
        per_token_in = self.input_per_1m / Decimal(1_000_000)
        per_token_out = self.output_per_1m / Decimal(1_000_000)
        return per_token_in * prompt_tokens + per_token_out * completion_tokens


class Capabilities(BaseModel):
    chat: bool = True
    embeddings: bool = False
    tools: bool = False
    json_mode: bool = False
    vision: bool = False
    streaming: bool = True
    stream_usage: bool = True  # provider reports usage on the final stream chunk


class Deployment(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = Field(
        description="Public model id clients send, e.g. 'qwen2.5-1.5b' or 'gpt-4o-mini'"
    )
    provider: ProviderKind
    model: str = Field(description="Model name as the provider knows it")
    base_url: str | None = Field(default=None, description="openai_compat and anthropic only")
    api_key_env: str | None = Field(default=None, description="Env var holding the provider key")
    region: str | None = Field(default=None, description="bedrock only")
    context_window: int = 8192
    max_output_tokens: int = 4096
    pricing: Pricing = Field(default_factory=Pricing)
    capabilities: Capabilities = Field(default_factory=Capabilities)
    timeout_s: float = 60.0
    max_concurrency: int = 64
    tags: tuple[str, ...] = ()
    enabled: bool = True

    @field_validator("tags", mode="before")
    @classmethod
    def _tags_to_tuple(cls, v: object) -> object:
        return tuple(v) if isinstance(v, list) else v

    def api_key(self) -> str | None:
        if not self.api_key_env:
            return None
        return os.environ.get(self.api_key_env) or None


class CatalogFile(BaseModel):
    deployments: list[Deployment]


class Catalog:
    def __init__(self, deployments: list[Deployment]) -> None:
        ids = [d.id for d in deployments]
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            msg = f"duplicate deployment ids in catalog: {sorted(dupes)}"
            raise ValueError(msg)
        self._by_id = {d.id: d for d in deployments}

    def get(self, deployment_id: str) -> Deployment | None:
        d = self._by_id.get(deployment_id)
        return d if d is not None and d.enabled else None

    def list(self) -> list[Deployment]:
        return [d for d in self._by_id.values() if d.enabled]

    def __len__(self) -> int:
        return len(self.list())


def load_catalog(path: str | Path) -> Catalog:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return Catalog(CatalogFile.model_validate(raw).deployments)
