"""Initialize the database — run migrations and seed initial data."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from alembic.config import Config
from alembic import command
from shared.config import DATABASE_URL_SYNC


def main():
    alembic_cfg = Config(os.path.join(os.path.dirname(__file__), "..", "alembic", "alembic.ini"))
    alembic_cfg.set_main_option("sqlalchemy.url", DATABASE_URL_SYNC)
    alembic_cfg.set_main_option("script_location", os.path.join(os.path.dirname(__file__), "..", "alembic"))

    print("Running database migrations...")
    command.upgrade(alembic_cfg, "head")
    print("Database initialized successfully.")


if __name__ == "__main__":
    main()
