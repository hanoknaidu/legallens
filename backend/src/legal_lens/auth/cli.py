"""Account management from a terminal.

    uv run python -m legal_lens.auth.cli list
    uv run python -m legal_lens.auth.cli create  you@firm.com --name "Your Name"
    uv run python -m legal_lens.auth.cli passwd  you@firm.com
    uv run python -m legal_lens.auth.cli disable you@firm.com

There is no sign-up route, by design: a firm's document library is not something a
stranger should be able to join, so accounts are made deliberately by whoever runs the
service. This is how. In a container with no shell, `LEGAL_LENS_BOOTSTRAP_EMAIL` and
`LEGAL_LENS_BOOTSTRAP_PASSWORD` create the first account at startup instead.

The password is read with `getpass` and never taken as an argument — an argument ends
up in shell history, in `ps` output, and in whatever ships the logs.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys

from ..config import get_settings
from ..db.session import Database
from ..errors import ApiProblem
from ..services import Services


async def _with_services(fn):
    settings = get_settings()
    db = Database(settings.db_path)
    await db.connect()
    try:
        return await fn(Services(db, settings))
    finally:
        await db.close()


def _read_new_password() -> str:
    first = getpass.getpass("Password (at least 12 characters): ")
    if first != getpass.getpass("Repeat: "):
        print("error: the two passwords do not match.", file=sys.stderr)
        raise SystemExit(2)
    return first


async def _create(args: argparse.Namespace) -> int:
    password = _read_new_password()

    async def run(services: Services) -> int:
        first = await services.users.count() == 0
        user = await services.auth.create_user(
            email=args.email, name=args.name or args.email.split("@")[0], password=password
        )
        await services.bootstrap_owner(user.id)
        print(f"created {user.email} ({user.id})")
        if first:
            # Worth saying out loud: this is the account that inherits anything
            # uploaded before there were accounts.
            print("this is the first account, so it owns any pre-existing documents")
        return 0

    return await _with_services(run)


async def _passwd(args: argparse.Namespace) -> int:
    password = _read_new_password()

    async def run(services: Services) -> int:
        user = await services.users.by_email(args.email)
        if user is None:
            print(f"error: no account for {args.email}", file=sys.stderr)
            return 1
        await services.auth.set_password(user, password)
        print(f"password changed for {user.email}; all their sessions were ended")
        return 0

    return await _with_services(run)


async def _disable(args: argparse.Namespace) -> int:
    async def run(services: Services) -> int:
        user = await services.users.by_email(args.email)
        if user is None:
            print(f"error: no account for {args.email}", file=sys.stderr)
            return 1
        # Deactivated rather than deleted: the row is what every document,
        # conversation and audit entry they own points at.
        await services.db.execute("UPDATE users SET is_active = 0 WHERE id = ?", (user.id,))
        await services.users.delete_sessions_for_user(user.id)
        print(f"disabled {user.email}; their documents are untouched")
        return 0

    return await _with_services(run)


async def _list(_: argparse.Namespace) -> int:
    async def run(services: Services) -> int:
        rows = await services.db.fetch_all(
            "SELECT email, name, is_active, created_at, last_login_at "
            "FROM users ORDER BY created_at"
        )
        if not rows:
            print("no accounts yet — create one with `create you@firm.com`")
            return 0
        for row in rows:
            state = "active" if row["is_active"] else "disabled"
            seen = row["last_login_at"] or "never"
            print(f"{row['email']:<34} {state:<9} last sign-in: {seen}")
        return 0

    return await _with_services(run)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="legal_lens.auth.cli", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create", help="create an account")
    create.add_argument("email")
    create.add_argument("--name", default="")
    create.set_defaults(fn=_create)

    passwd = sub.add_parser("passwd", help="change a password and end that user's sessions")
    passwd.add_argument("email")
    passwd.set_defaults(fn=_passwd)

    disable = sub.add_parser("disable", help="stop an account signing in, keeping its data")
    disable.add_argument("email")
    disable.set_defaults(fn=_disable)

    sub.add_parser("list", help="list accounts").set_defaults(fn=_list)

    args = parser.parse_args(argv)
    try:
        return asyncio.run(args.fn(args))
    except ApiProblem as exc:
        print(f"error: {exc.detail}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
