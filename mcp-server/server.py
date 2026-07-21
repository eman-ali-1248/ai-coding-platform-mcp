from __future__ import annotations

import os
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from psycopg.rows import dict_row


PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Load the private connection details from the main project folder.
load_dotenv(PROJECT_ROOT / ".env", override=True)

DATABASE_URL = os.getenv("MCP_DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError(
        "MCP_DATABASE_URL was not found in the .env file."
    )


# Render will provide PORT later.
# Locally, the server uses port 8000.
PORT = int(os.getenv("PORT", "8000"))


mcp = FastMCP(
    name="AI Coding Platform Catalog",
    instructions=(
        "Use these tools to search the public, read-only AI coding "
        "platform catalogue. Do not invent missing prices, features, "
        "model availability, or plan information."
    ),
    host="0.0.0.0",
    port=PORT,
    stateless_http=True,
    json_response=True,
)


def clean_value(value: Any) -> Any:
    """Convert PostgreSQL values into JSON-friendly values."""

    if isinstance(value, Decimal):
        return float(value)

    if isinstance(value, (date, datetime)):
        return value.isoformat()

    return value


def clean_row(row: dict[str, Any]) -> dict[str, Any]:
    """Clean every value in one database row."""

    return {
        key: clean_value(value)
        for key, value in row.items()
    }


def run_query(
    query: str,
    parameters: tuple[Any, ...] = (),
    maximum_rows: int = 100,
) -> list[dict[str, Any]]:
    """
    Run a predefined read-only query and return structured rows.
    """

    with psycopg.connect(
        DATABASE_URL,
        row_factory=dict_row,
        connect_timeout=10,
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, parameters)
            rows = cursor.fetchmany(maximum_rows)

    return [clean_row(dict(row)) for row in rows]


@mcp.tool()
def get_dataset_metadata() -> dict[str, Any]:
    """
    Return the size and basic metadata of the public dataset.
    """

    rows = run_query(
        """
        SELECT *
        FROM public_api.dataset_metadata;
        """,
        maximum_rows=1,
    )

    if not rows:
        return {"error": "Dataset metadata was not found."}

    return rows[0]


@mcp.tool()
def list_platforms(
    search: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """
    List AI coding platforms.

    Optionally search by platform name, company, or category.
    """

    limit = max(1, min(limit, 100))

    if search:
        search_text = f"%{search.strip()}%"

        return run_query(
            """
            SELECT
                platform_id,
                platform_name,
                category,
                ai_native_ide,
                ide_integrated_copilot,
                offering_company
            FROM public_api.platforms
            WHERE platform_name ILIKE %s
               OR offering_company ILIKE %s
               OR category ILIKE %s
            ORDER BY platform_name
            LIMIT %s;
            """,
            (
                search_text,
                search_text,
                search_text,
                limit,
            ),
            maximum_rows=limit,
        )

    return run_query(
        """
        SELECT
            platform_id,
            platform_name,
            category,
            ai_native_ide,
            ide_integrated_copilot,
            offering_company
        FROM public_api.platforms
        ORDER BY platform_name
        LIMIT %s;
        """,
        (limit,),
        maximum_rows=limit,
    )


@mcp.tool()
def find_plans(
    maximum_monthly_price: float | None = None,
    license_type: str | None = None,
    platform_name: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """
    Find subscription plans using optional price, licence,
    and platform filters.

    The comparable monthly price uses, in order:
    per-user monthly price, monthly list price, or annual
    monthly equivalent.
    """

    limit = max(1, min(limit, 100))

    conditions = ["1 = 1"]
    parameters: list[Any] = []

    if maximum_monthly_price is not None:
        if maximum_monthly_price < 0:
            raise ValueError(
                "maximum_monthly_price cannot be negative."
            )

        conditions.append(
            """
            COALESCE(
                per_user_monthly,
                monthly_list_price,
                annual_monthly_equivalent
            ) <= %s
            """
        )
        parameters.append(maximum_monthly_price)

    if license_type:
        conditions.append("license_type ILIKE %s")
        parameters.append(f"%{license_type.strip()}%")

    if platform_name:
        conditions.append("platform_name ILIKE %s")
        parameters.append(f"%{platform_name.strip()}%")

    parameters.append(limit)

    query = f"""
        SELECT
            platform_id,
            platform_name,
            tier_id,
            tier_name,
            license_type,
            currency,
            billing_frequency,
            monthly_list_price,
            annual_total_price,
            annual_monthly_equivalent,
            per_user_monthly,
            minimum_account_charge,
            COALESCE(
                per_user_monthly,
                monthly_list_price,
                annual_monthly_equivalent
            ) AS comparable_monthly_price,
            pricing_formula_type,
            effective_date,
            best_suited_for
        FROM public_api.plans
        WHERE {" AND ".join(conditions)}
        ORDER BY
            comparable_monthly_price NULLS LAST,
            platform_name,
            tier_name
        LIMIT %s;
    """

    return run_query(
        query,
        tuple(parameters),
        maximum_rows=limit,
    )


@mcp.tool()
def get_platform_details(
    platform_id: str,
) -> dict[str, Any]:
    """
    Return one platform with its plans, models, and features.
    """

    platform_rows = run_query(
        """
        SELECT *
        FROM public_api.platforms
        WHERE platform_id = %s;
        """,
        (platform_id,),
        maximum_rows=1,
    )

    if not platform_rows:
        return {
            "error": "Platform was not found.",
            "platform_id": platform_id,
        }

    plans = run_query(
        """
        SELECT
            tier_id,
            tier_name,
            license_type,
            currency,
            billing_frequency,
            monthly_list_price,
            annual_total_price,
            annual_monthly_equivalent,
            per_user_monthly,
            minimum_account_charge,
            effective_date,
            best_suited_for
        FROM public_api.plans
        WHERE platform_id = %s
        ORDER BY tier_name;
        """,
        (platform_id,),
    )

    models = run_query(
        """
        SELECT
            model_family_id,
            family_name,
            provider_name,
            status_id,
            status_label,
            counts_as_current
        FROM public_api.platform_models
        WHERE platform_id = %s
        ORDER BY family_name;
        """,
        (platform_id,),
    )

    features = run_query(
        """
        SELECT
            feature_id,
            feature_name,
            feature_category,
            supported,
            support_value,
            support_note
        FROM public_api.platform_features
        WHERE platform_id = %s
        ORDER BY feature_category, feature_name;
        """,
        (platform_id,),
    )

    return {
        "platform": platform_rows[0],
        "plans": plans,
        "models": models,
        "features": features,
        "important_limitation": (
            "Model and feature availability is currently recorded "
            "mainly at platform level. It does not automatically prove "
            "that every subscription tier includes the item."
        ),
    }


if __name__ == "__main__":
    mcp.run(transport="streamable-http")