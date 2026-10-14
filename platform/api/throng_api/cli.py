import argparse
import asyncio
import logging
from pathlib import Path
from uuid import UUID

import uvicorn
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

from throng_api.config import Settings
from throng_api.database import Database
from throng_api.models import User


def migration_config(settings):
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    config.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))
    return config


def migrate(settings):
    url = make_url(settings.database_url)
    if url.get_backend_name() == "sqlite" and url.database not in (None, "", ":memory:"):
        Path(url.database).parent.mkdir(parents=True, exist_ok=True)
    command.upgrade(migration_config(settings), "head")


async def set_system_admin(settings, user_id, enabled):
    database = Database(settings.database_url)
    try:
        async with database.write() as session:
            user = await session.get(User, str(user_id))
            if not user:
                raise ValueError("User not found")
            user.is_system_admin = enabled
    finally:
        await database.close()


def main():
    parser = argparse.ArgumentParser(description="Throng API with its embedded durable controller")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("migrate", help="Apply database migrations")
    administrator = subparsers.add_parser(
        "system-admin", help="Grant or revoke a user's system administrator role"
    )
    administrator.add_argument("user_id", type=UUID)
    administrator.add_argument("--revoke", action="store_true")
    serve = subparsers.add_parser("serve", help="Migrate and start API + controller")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    arguments = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = Settings()
    migrate(settings)
    if arguments.command == "system-admin":
        try:
            asyncio.run(set_system_admin(settings, arguments.user_id, not arguments.revoke))
        except ValueError as error:
            parser.error(str(error))
    if arguments.command == "serve":
        uvicorn.run("throng_api.app:app", host=arguments.host, port=arguments.port)


if __name__ == "__main__":
    main()
