from __future__ import annotations

import os
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")


def main() -> None:
    database_url = os.getenv("OWNER_DATABASE_URL")

    if not database_url:
        print("ERROR: OWNER_DATABASE_URL was not found in the .env file.")
        sys.exit(1)

    try:
        with psycopg.connect(database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                        current_database(),
                        current_user,
                        NOW();
                    """
                )

                database_name, database_user, database_time = cursor.fetchone()

        print("Connection successful!")
        print(f"Database: {database_name}")
        print(f"User: {database_user}")
        print(f"Database time: {database_time}")

    except Exception as error:
        print("Connection failed.")
        print(error)
        sys.exit(1)


if __name__ == "__main__":
    main()