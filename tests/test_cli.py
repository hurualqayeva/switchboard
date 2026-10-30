import asyncio

import pytest

from switchboard import cli
from switchboard.storage.memory import MemoryStorage


@pytest.fixture
def memory_cli(monkeypatch: pytest.MonkeyPatch) -> MemoryStorage:
    storage = MemoryStorage()

    async def fake_with_storage(auto_migrate: bool) -> MemoryStorage:
        return storage

    monkeypatch.setattr(cli, "_with_storage", fake_with_storage)
    return storage


def test_migrate(memory_cli: MemoryStorage, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["migrate"]) == 0
    assert "migrations applied" in capsys.readouterr().out


def test_bootstrap_then_create_key(
    memory_cli: MemoryStorage, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["bootstrap"]) == 0
    out = capsys.readouterr().out
    assert "secret: sb_" in out
    tenant_id = out.split("tenant: ")[1].split(" ")[0]
    secret = out.split("secret: ")[1].split()[0]
    principal = asyncio.run(memory_cli.auth.resolve(secret))
    assert principal is not None
    assert principal.role == "admin"

    # bootstrapping again reuses the tenant and mints another key
    assert cli.main(["bootstrap"]) == 0
    assert len(asyncio.run(memory_cli.auth.list_keys(tenant_id))) == 2

    assert cli.main(["create-key", "--tenant", tenant_id, "--name", "ci", "--role", "viewer"]) == 0
    assert "secret: sb_" in capsys.readouterr().out
    assert cli.main(["create-key", "--tenant", "ten_missing", "--name", "ci"]) == 2


def test_create_tenant(memory_cli: MemoryStorage, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["create-tenant", "--name", "acme", "--tier", "gold"]) == 0
    assert "acme\tgold" in capsys.readouterr().out


def test_parser_requires_command() -> None:
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args([])
