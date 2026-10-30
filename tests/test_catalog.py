from decimal import Decimal
from pathlib import Path

import pytest

from switchboard.catalog.models import Catalog, Deployment, Pricing, load_catalog

FIXTURE = Path(__file__).parent / "fixtures" / "catalog.yaml"


def test_load_catalog_lists_only_enabled() -> None:
    cat = load_catalog(FIXTURE)
    ids = {d.id for d in cat.list()}
    assert "stub-chat" in ids
    assert "disabled-model" not in ids
    assert cat.get("disabled-model") is None
    assert cat.get("stub-chat") is not None
    assert len(cat) == len(ids)


def test_pricing_cost_is_exact_decimal() -> None:
    p = Pricing(input_per_1m=Decimal("3.00"), output_per_1m=Decimal("15.00"))
    assert p.cost(1_000_000, 1_000_000) == Decimal("18.00")
    assert p.cost(1000, 500) == Decimal("0.0105")
    assert Pricing().cost(100, 100) == 0


def test_duplicate_ids_rejected() -> None:
    d = Deployment(id="x", provider="openai_compat", model="m", base_url="http://x")
    with pytest.raises(ValueError, match="duplicate"):
        Catalog([d, d])


def test_api_key_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    d = Deployment(id="x", provider="openai_compat", model="m", api_key_env="MY_KEY")
    assert d.api_key() is None
    monkeypatch.setenv("MY_KEY", "secret")
    assert d.api_key() == "secret"
    assert Deployment(id="y", provider="openai_compat", model="m").api_key() is None


def test_tags_are_tuple() -> None:
    d = Deployment.model_validate(
        {"id": "x", "provider": "openai_compat", "model": "m", "tags": ["a", "b"]}
    )
    assert d.tags == ("a", "b")
