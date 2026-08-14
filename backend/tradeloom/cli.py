"""Operational commands.

Used by the Docker entrypoints and by developers::

    python -m tradeloom.cli wait-for-db --timeout 90
    python -m tradeloom.cli ensure-bucket
    python -m tradeloom.cli seed --demo
    python -m tradeloom.cli reset --force        # development only
    python -m tradeloom.cli create-admin --email you@example.com
    python -m tradeloom.cli run-jobs             # execute queued backtests without a worker
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from typing import Any

from sqlalchemy import select, text

from tradeloom import __version__
from tradeloom.core.config import get_settings
from tradeloom.core.logging import configure_logging, get_logger

logger = get_logger("tradeloom.cli")


async def _wait_for_db(timeout: int) -> int:
    from tradeloom.db.session import dispose_engine, get_engine

    settings = get_settings()
    deadline = asyncio.get_running_loop().time() + timeout
    attempt = 0
    while True:
        attempt += 1
        try:
            engine = get_engine()
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
            print(f"database ready after {attempt} attempt(s)")
            await dispose_engine()
            return 0
        except Exception as exc:
            if asyncio.get_running_loop().time() >= deadline:
                print(
                    f"database not reachable after {timeout}s: {type(exc).__name__}",
                    file=sys.stderr,
                )
                await dispose_engine()
                return 1
            await dispose_engine()
            await asyncio.sleep(min(3.0, 0.5 * attempt))
            _ = settings


async def _ensure_bucket() -> int:
    from tradeloom.services.storage import get_storage

    try:
        get_storage().ensure_bucket()
    except Exception as exc:
        print(f"object storage unavailable: {type(exc).__name__}", file=sys.stderr)
        return 1
    print("object storage bucket ready")
    return 0


async def _seed(demo: bool, trades: int, days: int) -> int:
    from tradeloom.db.session import dispose_engine, session_scope
    from tradeloom.seed.generator import DemoSeeder

    settings = get_settings()
    if not demo:
        print("nothing to do; pass --demo to load the demo workspace")
        return 0

    async with session_scope() as session:
        seeder = DemoSeeder(session, seed=settings.seed_random_seed)
        result = await seeder.run(
            email=settings.demo_user_email,
            password=settings.demo_user_password,
            trade_count=trades,
            candle_days=days,
        )
    await dispose_engine()

    if result.get("skipped"):
        print(f"demo data already present ({result['email']}); nothing was changed")
        return 0

    print("demo workspace created")
    print(f"  sign in with : {result['email']} / {result['password']}")
    print(f"  workspace    : {result['organization']}")
    print(f"  accounts     : {result['accounts']}")
    print(f"  instruments  : {result['instruments']}")
    print(f"  trades       : {result['trades']}")
    print(f"  strategies   : {result['strategies']}")
    print(f"  backtests    : {result['backtests']}")
    return 0


async def _reset(force: bool) -> int:
    from tradeloom.db.session import dispose_engine, get_engine

    # Importing the models package is what registers the tables on the metadata. Importing only
    # `Base` yields empty metadata, and `create_all` would then silently create nothing.
    from tradeloom.models import Base

    settings = get_settings()
    if settings.is_production:
        print("refusing to reset a production database", file=sys.stderr)
        return 2
    if not force:
        print("refusing to drop tables without --force", file=sys.stderr)
        return 2

    engine = get_engine()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    await dispose_engine()
    print("database reset (schema recreated from models)")
    print("run 'alembic stamp head' if you intend to continue using migrations")
    return 0


async def _create_admin(email: str, password: str | None) -> int:
    import getpass

    from tradeloom.core import security
    from tradeloom.core.enums import UserRole, UserStatus
    from tradeloom.core.timeutil import utcnow
    from tradeloom.db.session import dispose_engine, session_scope
    from tradeloom.models.identity import User
    from tradeloom.schemas.auth import validate_password_strength
    from tradeloom.services.auth import AuthService

    resolved = password or getpass.getpass("Password: ")
    try:
        validate_password_strength(resolved)
    except ValueError as exc:
        print(f"password rejected: {exc}", file=sys.stderr)
        return 2

    async with session_scope() as session:
        existing = await session.execute(select(User).where(User.email == email.lower()))
        user = existing.scalar_one_or_none()
        if user is not None:
            user.role = UserRole.ADMIN
            print(f"{email} promoted to administrator")
        else:
            user = User(
                email=email.lower(),
                password_hash=security.hash_password(resolved),
                full_name="Administrator",
                display_name="Admin",
                role=UserRole.ADMIN,
                status=UserStatus.ACTIVE,
                email_verified_at=utcnow(),
                password_changed_at=utcnow(),
            )
            session.add(user)
            await session.flush()
            await AuthService(session).create_personal_organization(user, "Admin workspace")
            print(f"administrator {email} created")
    await dispose_engine()
    return 0


async def _run_jobs(limit: int) -> int:
    """Execute queued backtest runs in this process.

    Submitting a backtest queues it for a Celery worker, which needs a broker. On a laptop that is
    a lot of infrastructure to stand up just to see a result, so this drains the queue directly.

    It is not a substitute for the worker and does not pretend to be one: it runs the very same
    ``BacktestService.execute`` the worker calls, one run at a time, in the foreground. Nothing is
    simulated and no status is invented — a run that fails here fails there.
    """
    from tradeloom.core.enums import JobStatus
    from tradeloom.db.session import dispose_engine, session_scope
    from tradeloom.models.backtest import BacktestRun
    from tradeloom.services.backtests import BacktestService

    executed = 0
    async with session_scope() as session:
        result = await session.execute(
            select(BacktestRun)
            .where(BacktestRun.status == JobStatus.QUEUED)
            .order_by(BacktestRun.created_at.asc())
            .limit(limit)
        )
        runs = list(result.scalars().all())
        if not runs:
            print("no queued runs")
            await dispose_engine()
            return 0

        for run in runs:
            service = BacktestService(
                session, run.organization_id, actor_user_id=run.triggered_by_user_id
            )
            print(f"running {run.id} …", flush=True)
            try:
                finished = await service.execute(run.id)
                await session.commit()
                print(f"  {finished.status.value}: {finished.trade_count} trades")
                executed += 1
            except Exception as error:  # report and carry on to the next run
                await session.rollback()
                print(f"  failed: {error}", file=sys.stderr)

    await dispose_engine()
    print(f"executed {executed} run(s)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tradeloom", description="Tradeloom operations")
    parser.add_argument("--version", action="version", version=f"tradeloom {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    wait = subparsers.add_parser("wait-for-db", help="Block until the database accepts queries")
    wait.add_argument("--timeout", type=int, default=60)

    subparsers.add_parser("ensure-bucket", help="Create the object storage bucket if missing")

    seed = subparsers.add_parser("seed", help="Load demo data")
    seed.add_argument("--demo", action="store_true", help="Create the demo workspace")
    seed.add_argument("--trades", type=int, default=1200)
    seed.add_argument("--days", type=int, default=540, help="Days of candles to generate")

    reset = subparsers.add_parser("reset", help="Drop and recreate all tables (development only)")
    reset.add_argument("--force", action="store_true")

    admin = subparsers.add_parser("create-admin", help="Create or promote a platform admin")
    admin.add_argument("--email", required=True)
    admin.add_argument("--password", default=None, help="Prompted for if omitted")

    jobs = subparsers.add_parser(
        "run-jobs", help="Execute queued backtest runs here, without a Celery worker"
    )
    jobs.add_argument("--limit", type=int, default=10)

    return parser


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    args = build_parser().parse_args(argv)

    handlers: dict[str, Any] = {
        "wait-for-db": lambda: _wait_for_db(args.timeout),
        "ensure-bucket": _ensure_bucket,
        "seed": lambda: _seed(args.demo, args.trades, args.days),
        "reset": lambda: _reset(args.force),
        "create-admin": lambda: _create_admin(args.email, args.password),
        "run-jobs": lambda: _run_jobs(args.limit),
    }
    return asyncio.run(handlers[args.command]())


if __name__ == "__main__":
    raise SystemExit(main())
