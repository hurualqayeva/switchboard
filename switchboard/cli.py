"""Operator CLI: migrations, tenants and API keys.

switchboard-admin migrate
switchboard-admin bootstrap                      # admin tenant + admin key, printed once
switchboard-admin create-tenant --name acme
switchboard-admin create-key --tenant ten_... --name ci --role developer
"""

import argparse
import asyncio
import sys

from switchboard.core.logging import configure_logging
from switchboard.core.settings import get_settings
from switchboard.storage.postgres import PostgresStorage


async def _with_storage(auto_migrate: bool) -> PostgresStorage:
    settings = get_settings()
    storage = PostgresStorage(str(settings.database_url), auto_migrate=auto_migrate)
    await storage.start()
    return storage


async def cmd_migrate(_: argparse.Namespace) -> int:
    storage = await _with_storage(auto_migrate=True)
    await storage.close()
    print("migrations applied")
    return 0


async def cmd_bootstrap(args: argparse.Namespace) -> int:
    storage = await _with_storage(auto_migrate=True)
    try:
        tenants = await storage.auth.list_tenants()
        existing = next((t for t in tenants if t.name == args.name), None)
        tenant = existing or await storage.auth.create_tenant(args.name, "internal")
        key, secret = await storage.auth.create_key(tenant.id, "bootstrap-admin", "admin")
    finally:
        await storage.close()
    print(f"tenant: {tenant.id} ({tenant.name})")
    print(f"key id: {key.id}")
    print(f"secret: {secret}")
    print("store the secret now; it is not shown again")
    return 0


async def cmd_create_tenant(args: argparse.Namespace) -> int:
    storage = await _with_storage(auto_migrate=False)
    try:
        tenant = await storage.auth.create_tenant(args.name, args.tier)
    finally:
        await storage.close()
    print(f"{tenant.id}\t{tenant.name}\t{tenant.tier}")
    return 0


async def cmd_create_key(args: argparse.Namespace) -> int:
    storage = await _with_storage(auto_migrate=False)
    try:
        if await storage.auth.get_tenant(args.tenant) is None:
            print(f"unknown tenant {args.tenant}", file=sys.stderr)
            return 2
        key, secret = await storage.auth.create_key(
            args.tenant, args.name, args.role, rate_limit_rpm=args.rpm, rate_limit_tpm=args.tpm
        )
    finally:
        await storage.close()
    print(f"key id: {key.id}")
    print(f"secret: {secret}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="switchboard-admin")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("migrate", help="apply pending migrations").set_defaults(func=cmd_migrate)

    b = sub.add_parser("bootstrap", help="create the admin tenant and an admin key")
    b.add_argument("--name", default="admin")
    b.set_defaults(func=cmd_bootstrap)

    t = sub.add_parser("create-tenant")
    t.add_argument("--name", required=True)
    t.add_argument("--tier", default="standard")
    t.set_defaults(func=cmd_create_tenant)

    k = sub.add_parser("create-key")
    k.add_argument("--tenant", required=True, help="tenant id")
    k.add_argument("--name", required=True)
    k.add_argument("--role", choices=["admin", "developer", "viewer"], default="developer")
    k.add_argument("--rpm", type=int, default=None)
    k.add_argument("--tpm", type=int, default=None)
    k.set_defaults(func=cmd_create_key)
    return p


def main(argv: list[str] | None = None) -> int:
    configure_logging(level="WARNING")
    args = build_parser().parse_args(argv)
    return int(asyncio.run(args.func(args)))


if __name__ == "__main__":
    sys.exit(main())
