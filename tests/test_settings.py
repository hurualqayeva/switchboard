import pytest

from switchboard.core.settings import Settings


def test_defaults_are_local() -> None:
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.env == "local"
    assert s.is_local
    assert s.port == 8000


def test_env_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SWITCHBOARD_PORT", "9999")
    monkeypatch.setenv("SWITCHBOARD_ENV", "aws")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.port == 9999
    assert not s.is_local
