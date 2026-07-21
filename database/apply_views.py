from __future__ import annotations

import os
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = PROJECT_ROOT / ".env"
VIEWS_FILE = Path(__file__).resolve().with_name("views.sql")


def main() -> None:
    load_dotenv(ENV_FILE)

    database_url = os.getenv("OWNER_DATABASE_URL")

    if not database_url:
        print("ERROR: OWNER_DATABASE_URL was not found in .env")
        sys.exit(1)

    if not VIEWS_FILE.exists():
        print(f"ERROR: Views file was not found: {VIEWS_FILE}")
        sys.exit(1)

    views_sql = VIEWS_FILE.read_text(encoding="utf-8")

    try:
        with psycopg.connect(database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(views_sql)

        print("Public API views created successfully!")
        print("Views created: 5")

    except Exception as error:
        print("Failed to create the public views.")
        print(error)
        sys.exit(1)


if __name__ == "__main__":
    main()