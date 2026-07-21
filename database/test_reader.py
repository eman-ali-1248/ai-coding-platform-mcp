from __future__ import annotations

import os
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")


def main() -> None:
    database_url = os.getenv("MCP_DATABASE_URL")

    if not database_url:
        print("ERROR: MCP_DATABASE_URL was not found in .env")
        sys.exit(1)

    try:
        with psycopg.connect(database_url) as connection:
            with connection.cursor() as cursor:
                # This should work.
                cursor.execute(
                    """
                    SELECT
                        platform_count,
                        plan_count,
                        model_family_count,
                        feature_count
                    FROM public_api.dataset_metadata;
                    """
                )

                metadata = cursor.fetchone()

                print("Public view access: PASSED")
                print(f"Platforms: {metadata[0]}")
                print(f"Plans: {metadata[1]}")
                print(f"Model families: {metadata[2]}")
                print(f"Features: {metadata[3]}")

        # Use a separate connection for the forbidden-access test.
        try:
            with psycopg.connect(database_url) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT *
                        FROM catalog.platform
                        LIMIT 1;
                        """
                    )

            print("ERROR: Reader unexpectedly accessed catalog.platform")
            sys.exit(1)

        except psycopg.errors.InsufficientPrivilege:
            print("Internal catalog access: CORRECTLY BLOCKED")

        print("\nRead-only MCP account configured successfully!")

    except Exception as error:
        print("Reader-account test failed.")
        print(error)
        sys.exit(1)


if __name__ == "__main__":
    main()