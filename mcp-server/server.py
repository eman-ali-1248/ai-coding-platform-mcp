from __future__ import annotations

import logging
import os
import secrets
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from psycopg.rows import dict_row
from pydantic import BaseModel, Field, model_validator


PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Load local environment variables. Render supplies them directly in production.
load_dotenv(PROJECT_ROOT / ".env", override=True)

READ_DATABASE_URL = os.getenv("MCP_DATABASE_URL")
INGESTION_DATABASE_URL = os.getenv("INGESTION_DATABASE_URL")
INGESTION_ADMIN_KEY = os.getenv("INGESTION_ADMIN_KEY")

if not READ_DATABASE_URL:
    raise RuntimeError(
        "MCP_DATABASE_URL was not found in the environment."
    )

PORT = int(os.getenv("PORT", "8000"))

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("ai-coding-platform-mcp")


mcp = FastMCP(
    name="AI Coding Platform Catalog",
    instructions=(
        "Use the read tools as the authoritative source for the AI coding "
        "platform data stored in this catalog. Do not invent missing prices, "
        "features, model availability, or plan information. "
        "The ingest_entities tool is an administrative tool. Use it only "
        "after the administrator supplies verified structured facts, a valid "
        "admin key, and explicitly approves a live write. Always perform a "
        "dry run first. Never infer or guess data during ingestion."
    ),
    host="0.0.0.0",
    port=PORT,
    stateless_http=True,
    json_response=True,
)


# ---------------------------------------------------------------------------
# JSON cleaning and database helpers
# ---------------------------------------------------------------------------

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


def read_connection() -> psycopg.Connection:
    """Open a connection using the restricted read-only role."""

    return psycopg.connect(
        READ_DATABASE_URL,
        row_factory=dict_row,
        connect_timeout=10,
    )


def ingestion_connection() -> psycopg.Connection:
    """Open a connection using the restricted ingestion writer role."""

    if not INGESTION_DATABASE_URL:
        raise RuntimeError(
            "INGESTION_DATABASE_URL is not configured. "
            "The read tools remain available, but ingestion is disabled."
        )

    return psycopg.connect(
        INGESTION_DATABASE_URL,
        row_factory=dict_row,
        connect_timeout=10,
    )


def run_query(
    query: str,
    parameters: tuple[Any, ...] = (),
    maximum_rows: int = 100,
) -> list[dict[str, Any]]:
    """Run a predefined read-only query and return structured rows."""

    with read_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, parameters)
            rows = cursor.fetchmany(maximum_rows)

    return [clean_row(dict(row)) for row in rows]


def fetch_one(
    cursor: psycopg.Cursor[Any],
    query: str,
    parameters: tuple[Any, ...],
) -> dict[str, Any] | None:
    """Fetch one dictionary row inside an ingestion transaction."""

    cursor.execute(query, parameters)
    row = cursor.fetchone()
    return dict(row) if row else None


def require_admin_key(admin_key: str) -> None:
    """Require the private ingestion administrator key."""

    if not INGESTION_ADMIN_KEY:
        raise RuntimeError(
            "INGESTION_ADMIN_KEY is not configured. Ingestion is disabled."
        )

    if not secrets.compare_digest(
        admin_key,
        INGESTION_ADMIN_KEY,
    ):
        raise PermissionError("Invalid ingestion administrator key.")


def require_text(value: str, field_name: str) -> str:
    """Strip and validate a required text field."""

    cleaned = value.strip()

    if not cleaned:
        raise ValueError(f"{field_name} cannot be blank.")

    return cleaned


def new_id(prefix: str) -> str:
    """Generate a text ID compatible with the existing TEXT primary keys."""

    return f"{prefix}-{uuid4().hex[:12].upper()}"


# ---------------------------------------------------------------------------
# Read tools
# ---------------------------------------------------------------------------

@mcp.tool()
def get_dataset_metadata() -> dict[str, Any]:
    """Return the size and basic metadata of the public dataset."""

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


def _group_by_platform(
    rows: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Group rows that include a platform_id column, by that column."""

    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(row["platform_id"], []).append(row)
    return grouped


@mcp.tool()
def search_catalog(
    platform_id: str | None = None,
    search: str | None = None,
    maximum_monthly_price: float | None = None,
    license_type: str | None = None,
    model_family: str | None = None,
    feature: str | None = None,
    include: list[str] | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """
    Search the AI coding platform catalog. Replaces the old
    list_platforms / find_plans / get_platform_details tools with one
    flexible entry point.

    Two modes:

    - Single platform (set platform_id): returns full detail for that one
      platform - its plans, supported model families, and feature
      support - ignoring every other filter below.

    - List/search (leave platform_id unset): returns
      {"total_matches": N, "returned": M, "results": [...]} using any
      combination of these filters:
        - search: free text against platform name, company, or category
        - maximum_monthly_price: keep only platforms with at least one
          plan at or under this price (per-user monthly, monthly list,
          or annual monthly equivalent, in that priority order)
        - license_type: substring match against plan license type
        - model_family: keep only platforms that support a model family
          whose name matches this substring
        - feature: keep only platforms that support (supported = true) a
          feature whose name matches this substring
      total_matches is the full count before limit is applied, so a
      truncated result list is never mistaken for the complete answer.
      When maximum_monthly_price and/or license_type are set, each
      platform also carries a "matching_plans" list - the specific
      tier(s) that satisfied those filters - so a price/license match
      can be traced to the exact plan(s) responsible, not just the
      platform as a whole.
      By default list results are otherwise lean (platform summary rows
      only). Pass include=["plans"], ["models"], ["features"], or any
      combination, to attach those full sections to each platform too.

    limit applies to list mode only (1-100, default 20).

    Note: model and feature availability is currently recorded mainly at
    the platform level. A match does not automatically prove every
    subscription tier includes that model or feature.
    """

    if platform_id:
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
                "mainly at platform level. It does not automatically "
                "prove that every subscription tier includes the item."
            ),
        }

    # --- list/search mode ---

    limit = max(1, min(limit, 100))
    include = include or []
    unknown_sections = set(include) - {"plans", "models", "features"}
    if unknown_sections:
        raise ValueError(
            "include may only contain 'plans', 'models', 'features'. "
            f"Got unknown value(s): {sorted(unknown_sections)}"
        )

    if maximum_monthly_price is not None and maximum_monthly_price < 0:
        raise ValueError("maximum_monthly_price cannot be negative.")

    conditions = ["1 = 1"]
    parameters: list[Any] = []

    if search:
        search_text = f"%{search.strip()}%"
        conditions.append(
            """
            (
                plat.platform_name ILIKE %s
                OR plat.offering_company ILIKE %s
                OR plat.category ILIKE %s
            )
            """
        )
        parameters.extend([search_text, search_text, search_text])

    # Kept separate from `conditions` (which filters platforms) so the
    # exact same predicate can be reused afterwards to fetch the specific
    # plans that matched, rather than just proving a match existed.
    matching_plan_conditions = ["pl.platform_id = plat.platform_id"]
    matching_plan_parameters: list[Any] = []

    if maximum_monthly_price is not None:
        matching_plan_conditions.append(
            """
            COALESCE(
                pl.per_user_monthly,
                pl.monthly_list_price,
                pl.annual_monthly_equivalent
            ) <= %s
            """
        )
        matching_plan_parameters.append(maximum_monthly_price)

    if license_type:
        matching_plan_conditions.append("pl.license_type ILIKE %s")
        matching_plan_parameters.append(f"%{license_type.strip()}%")

    has_plan_level_filter = (
        maximum_monthly_price is not None or license_type is not None
    )

    if has_plan_level_filter:
        conditions.append(
            f"""
            EXISTS (
                SELECT 1 FROM public_api.plans pl
                WHERE {" AND ".join(matching_plan_conditions)}
            )
            """
        )
        parameters.extend(matching_plan_parameters)

    if model_family:
        conditions.append(
            """
            EXISTS (
                SELECT 1 FROM public_api.platform_models pm
                WHERE pm.platform_id = plat.platform_id
                  AND pm.family_name ILIKE %s
            )
            """
        )
        parameters.append(f"%{model_family.strip()}%")

    if feature:
        conditions.append(
            """
            EXISTS (
                SELECT 1 FROM public_api.platform_features pf
                WHERE pf.platform_id = plat.platform_id
                  AND pf.feature_name ILIKE %s
                  AND pf.supported = true
            )
            """
        )
        parameters.append(f"%{feature.strip()}%")

    where_clause = " AND ".join(conditions)

    total_matches_rows = run_query(
        f"""
        SELECT COUNT(*) AS total
        FROM public_api.platforms plat
        WHERE {where_clause};
        """,
        tuple(parameters),
        maximum_rows=1,
    )
    total_matches = total_matches_rows[0]["total"] if total_matches_rows else 0

    platforms = run_query(
        f"""
        SELECT
            plat.platform_id,
            plat.platform_name,
            plat.category,
            plat.ai_native_ide,
            plat.ide_integrated_copilot,
            plat.offering_company
        FROM public_api.platforms plat
        WHERE {where_clause}
        ORDER BY plat.platform_name
        LIMIT %s;
        """,
        tuple(parameters) + (limit,),
        maximum_rows=limit,
    )

    if not platforms:
        return {"total_matches": total_matches, "returned": 0, "results": []}

    matched_ids = [row["platform_id"] for row in platforms]

    if has_plan_level_filter:
        matching_plan_rows = run_query(
            f"""
            SELECT
                pl.platform_id,
                pl.tier_id,
                pl.tier_name,
                pl.license_type,
                pl.currency,
                pl.billing_frequency,
                pl.monthly_list_price,
                pl.annual_total_price,
                pl.annual_monthly_equivalent,
                pl.per_user_monthly,
                pl.minimum_account_charge,
                pl.effective_date,
                pl.best_suited_for
            FROM public_api.plans pl
            WHERE pl.platform_id = ANY(%s)
              AND {" AND ".join(matching_plan_conditions[1:])}
            ORDER BY pl.platform_id, pl.tier_name;
            """,
            (matched_ids, *matching_plan_parameters),
            maximum_rows=len(matched_ids) * 50,
        )
        matching_plans_by_platform = _group_by_platform(matching_plan_rows)
        for row in platforms:
            row["matching_plans"] = matching_plans_by_platform.get(
                row["platform_id"], []
            )

    if "plans" in include:
        plan_rows = run_query(
            """
            SELECT
                platform_id,
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
            WHERE platform_id = ANY(%s)
            ORDER BY platform_id, tier_name;
            """,
            (matched_ids,),
            maximum_rows=len(matched_ids) * 50,
        )
        plans_by_platform = _group_by_platform(plan_rows)
        for row in platforms:
            row["plans"] = plans_by_platform.get(row["platform_id"], [])

    if "models" in include:
        model_rows = run_query(
            """
            SELECT
                platform_id,
                model_family_id,
                family_name,
                provider_name,
                status_id,
                status_label,
                counts_as_current
            FROM public_api.platform_models
            WHERE platform_id = ANY(%s)
            ORDER BY platform_id, family_name;
            """,
            (matched_ids,),
            maximum_rows=len(matched_ids) * 50,
        )
        models_by_platform = _group_by_platform(model_rows)
        for row in platforms:
            row["models"] = models_by_platform.get(
                row["platform_id"], []
            )

    if "features" in include:
        feature_rows = run_query(
            """
            SELECT
                platform_id,
                feature_id,
                feature_name,
                feature_category,
                supported,
                support_value,
                support_note
            FROM public_api.platform_features
            WHERE platform_id = ANY(%s)
            ORDER BY platform_id, feature_category, feature_name;
            """,
            (matched_ids,),
            maximum_rows=len(matched_ids) * 50,
        )
        features_by_platform = _group_by_platform(feature_rows)
        for row in platforms:
            row["features"] = features_by_platform.get(
                row["platform_id"], []
            )

    return {
        "total_matches": total_matches,
        "returned": len(platforms),
        "results": platforms,
    }


@mcp.tool()
def list_facet_values() -> dict[str, Any]:
    """
    List the distinct values that actually exist in the catalog for the
    filterable fields: platform categories, plan license types,
    currencies in use, feature names (grouped by category), and model
    family names (grouped by provider).

    Call this before guessing filter strings for search_catalog (e.g.
    "Claude" vs "Claude Sonnet" vs "Anthropic Claude") - it grounds the
    filter in real data instead of a substring guess that may silently
    match nothing.
    """

    categories = run_query(
        """
        SELECT DISTINCT category
        FROM public_api.platforms
        WHERE category IS NOT NULL
        ORDER BY category;
        """,
        maximum_rows=200,
    )
    license_types = run_query(
        """
        SELECT DISTINCT license_type
        FROM public_api.plans
        WHERE license_type IS NOT NULL
        ORDER BY license_type;
        """,
        maximum_rows=200,
    )
    currencies = run_query(
        """
        SELECT DISTINCT currency
        FROM public_api.plans
        WHERE currency IS NOT NULL
        ORDER BY currency;
        """,
        maximum_rows=50,
    )
    features = run_query(
        """
        SELECT DISTINCT feature_name, feature_category
        FROM public_api.platform_features
        ORDER BY feature_category, feature_name;
        """,
        maximum_rows=200,
    )
    model_families = run_query(
        """
        SELECT DISTINCT family_name, provider_name
        FROM public_api.platform_models
        ORDER BY provider_name, family_name;
        """,
        maximum_rows=300,
    )

    return {
        "categories": [row["category"] for row in categories],
        "license_types": [row["license_type"] for row in license_types],
        "currencies_in_use": [row["currency"] for row in currencies],
        "features": features,
        "model_families": model_families,
    }


@mcp.tool()
def compare_platforms(platform_ids: list[str]) -> dict[str, Any]:
    """
    Compare 2-5 platforms side by side: each platform's cheapest plan,
    full plan list, supported model families, and feature support,
    aligned together so differences are easy to spot without the caller
    having to diff several separate get_platform_details-style calls.

    platform_ids: the platform_id values to compare (from search_catalog
    or list_facet_values results).
    """

    if not platform_ids:
        raise ValueError("Provide at least one platform_id.")

    if len(platform_ids) > 5:
        raise ValueError(
            "Compare at most 5 platforms at a time for a readable result."
        )

    platform_rows = run_query(
        """
        SELECT *
        FROM public_api.platforms
        WHERE platform_id = ANY(%s)
        ORDER BY platform_name;
        """,
        (platform_ids,),
        maximum_rows=len(platform_ids),
    )

    found_ids = {row["platform_id"] for row in platform_rows}
    not_found = [pid for pid in platform_ids if pid not in found_ids]

    plans = run_query(
        """
        SELECT *
        FROM public_api.plans
        WHERE platform_id = ANY(%s)
        ORDER BY platform_id, tier_name;
        """,
        (platform_ids,),
        maximum_rows=len(platform_ids) * 50,
    )
    models = run_query(
        """
        SELECT *
        FROM public_api.platform_models
        WHERE platform_id = ANY(%s)
        ORDER BY platform_id, family_name;
        """,
        (platform_ids,),
        maximum_rows=len(platform_ids) * 100,
    )
    features = run_query(
        """
        SELECT *
        FROM public_api.platform_features
        WHERE platform_id = ANY(%s)
        ORDER BY platform_id, feature_category, feature_name;
        """,
        (platform_ids,),
        maximum_rows=len(platform_ids) * 100,
    )

    plans_by_platform = _group_by_platform(plans)
    models_by_platform = _group_by_platform(models)
    features_by_platform = _group_by_platform(features)

    currencies_in_play = sorted(
        {plan["currency"] for plan in plans if plan.get("currency")}
    )

    comparison = []
    for row in platform_rows:
        platform_id = row["platform_id"]
        platform_plans = plans_by_platform.get(platform_id, [])

        cheapest_plan = None
        cheapest_price = None
        for plan in platform_plans:
            price = (
                plan.get("per_user_monthly")
                or plan.get("monthly_list_price")
                or plan.get("annual_monthly_equivalent")
            )
            if price is not None and (
                cheapest_price is None or price < cheapest_price
            ):
                cheapest_price = price
                cheapest_plan = plan

        comparison.append(
            {
                "platform": row,
                "cheapest_plan": cheapest_plan,
                "all_plans": platform_plans,
                "models": models_by_platform.get(platform_id, []),
                "features": features_by_platform.get(platform_id, []),
            }
        )

    return {
        "compared": comparison,
        "not_found": not_found,
        "currency_warning": (
            None
            if len(currencies_in_play) <= 1
            else (
                "These platforms price plans in different currencies "
                f"({', '.join(currencies_in_play)}). Cheapest-plan and "
                "price comparisons across platforms are not "
                "apples-to-apples without a currency conversion."
            )
        ),
        "important_limitation": (
            "Model and feature availability is currently recorded "
            "mainly at the platform level. A model or feature shown "
            "here does not guarantee that the platform's cheapest (or "
            "any specific) plan tier includes it."
        ),
    }


# ---------------------------------------------------------------------------
# Ingestion input models
# ---------------------------------------------------------------------------

class PlatformInput(BaseModel):
    platform_id: str | None = None
    platform_name: str
    category: str | None = None
    ai_native_ide: bool | None = None
    ide_integrated_copilot: bool | None = None
    offering_company: str | None = None


class ModelProviderInput(BaseModel):
    provider_id: str | None = None
    provider_name: str
    provider_type: str | None = None


class ModelFamilyInput(BaseModel):
    model_family_id: str | None = None
    provider_name: str
    provider_type: str | None = None
    family_name: str
    coverage_description: str | None = None
    grouping_note: str | None = None


class FeatureInput(BaseModel):
    feature_id: str | None = None
    feature_name: str
    feature_category: str | None = None


class AvailabilityStatusInput(BaseModel):
    status_id: str | None = None
    status_label: str
    status_group: str | None = None
    directly_selectable: bool
    counts_as_current: bool


class SubscriptionTierInput(BaseModel):
    tier_id: str | None = None
    platform_name: str
    tier_name: str
    license_type: str | None = None
    usage_credits: str | None = None
    rate_limit_window: str | None = None
    team_seats: str | None = None
    model_access_summary: str | None = None
    core_privileges: str | None = None
    additional_privileges: str | None = None
    not_included: str | None = None
    best_suited_for: str | None = None


class PlanPricingInput(BaseModel):
    pricing_id: str | None = None
    platform_name: str
    tier_name: str
    currency: str
    billing_frequency: str | None = None
    commitment_months: int | None = Field(default=None, ge=1)
    monthly_list_price: float | None = Field(default=None, ge=0)
    annual_total_price: float | None = Field(default=None, ge=0)
    annual_monthly_equivalent: float | None = Field(
        default=None,
        ge=0,
    )
    per_user_monthly: float | None = Field(default=None, ge=0)
    base_team_monthly: float | None = Field(default=None, ge=0)
    minimum_account_charge: float | None = Field(
        default=None,
        ge=0,
    )
    pricing_formula_type: str | None = None
    effective_date: date


class PlatformModelAvailabilityInput(BaseModel):
    platform_name: str
    provider_name: str
    family_name: str
    status_label: str


class PlatformFeatureSupportInput(BaseModel):
    platform_name: str
    feature_name: str
    supported: bool
    support_value: float = Field(ge=0, le=1)
    support_note: str | None = None

    @model_validator(mode="after")
    def check_support_consistency(
        self,
    ) -> "PlatformFeatureSupportInput":
        if self.supported and self.support_value <= 0:
            raise ValueError(
                "supported=true requires support_value greater than 0."
            )

        if not self.supported and self.support_value != 0:
            raise ValueError(
                "supported=false requires support_value equal to 0."
            )

        return self


# ---------------------------------------------------------------------------
# Ingestion helpers
# ---------------------------------------------------------------------------

def upsert_platform(
    cursor: psycopg.Cursor[Any],
    item: PlatformInput,
) -> str:
    platform_name = require_text(
        item.platform_name,
        "platform_name",
    )
    platform_id = item.platform_id or new_id("PLAT")

    cursor.execute(
        """
        INSERT INTO catalog.platform (
            platform_id,
            platform_name,
            category,
            ai_native_ide,
            ide_integrated_copilot,
            offering_company
        )
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (platform_name) DO UPDATE SET
            category = COALESCE(
                EXCLUDED.category,
                catalog.platform.category
            ),
            ai_native_ide = COALESCE(
                EXCLUDED.ai_native_ide,
                catalog.platform.ai_native_ide
            ),
            ide_integrated_copilot = COALESCE(
                EXCLUDED.ide_integrated_copilot,
                catalog.platform.ide_integrated_copilot
            ),
            offering_company = COALESCE(
                EXCLUDED.offering_company,
                catalog.platform.offering_company
            )
        RETURNING platform_id;
        """,
        (
            platform_id,
            platform_name,
            item.category,
            item.ai_native_ide,
            item.ide_integrated_copilot,
            item.offering_company,
        ),
    )

    return cursor.fetchone()["platform_id"]


def upsert_model_provider(
    cursor: psycopg.Cursor[Any],
    provider_name: str,
    provider_type: str | None = None,
    provider_id: str | None = None,
) -> str:
    provider_name = require_text(
        provider_name,
        "provider_name",
    )
    resolved_id = provider_id or new_id("PROV")

    cursor.execute(
        """
        INSERT INTO catalog.model_provider (
            provider_id,
            provider_name,
            provider_type
        )
        VALUES (%s, %s, %s)
        ON CONFLICT (provider_name) DO UPDATE SET
            provider_type = COALESCE(
                EXCLUDED.provider_type,
                catalog.model_provider.provider_type
            )
        RETURNING provider_id;
        """,
        (
            resolved_id,
            provider_name,
            provider_type,
        ),
    )

    return cursor.fetchone()["provider_id"]


def upsert_model_family(
    cursor: psycopg.Cursor[Any],
    item: ModelFamilyInput,
) -> str:
    provider_id = upsert_model_provider(
        cursor,
        item.provider_name,
        item.provider_type,
    )
    family_name = require_text(
        item.family_name,
        "family_name",
    )
    family_id = item.model_family_id or new_id("MF")

    cursor.execute(
        """
        INSERT INTO catalog.model_family (
            model_family_id,
            provider_id,
            family_name,
            coverage_description,
            grouping_note
        )
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (provider_id, family_name) DO UPDATE SET
            coverage_description = COALESCE(
                EXCLUDED.coverage_description,
                catalog.model_family.coverage_description
            ),
            grouping_note = COALESCE(
                EXCLUDED.grouping_note,
                catalog.model_family.grouping_note
            )
        RETURNING model_family_id;
        """,
        (
            family_id,
            provider_id,
            family_name,
            item.coverage_description,
            item.grouping_note,
        ),
    )

    return cursor.fetchone()["model_family_id"]


def upsert_feature(
    cursor: psycopg.Cursor[Any],
    item: FeatureInput,
) -> str:
    feature_name = require_text(
        item.feature_name,
        "feature_name",
    )
    feature_id = item.feature_id or new_id("FEAT")

    cursor.execute(
        """
        INSERT INTO catalog.feature (
            feature_id,
            feature_name,
            feature_category
        )
        VALUES (%s, %s, %s)
        ON CONFLICT (feature_name) DO UPDATE SET
            feature_category = COALESCE(
                EXCLUDED.feature_category,
                catalog.feature.feature_category
            )
        RETURNING feature_id;
        """,
        (
            feature_id,
            feature_name,
            item.feature_category,
        ),
    )

    return cursor.fetchone()["feature_id"]


def upsert_availability_status(
    cursor: psycopg.Cursor[Any],
    item: AvailabilityStatusInput,
) -> str:
    status_label = require_text(
        item.status_label,
        "status_label",
    )
    status_id = item.status_id or new_id("STAT")

    cursor.execute(
        """
        INSERT INTO catalog.availability_status (
            status_id,
            status_label,
            status_group,
            directly_selectable,
            counts_as_current
        )
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (status_label) DO UPDATE SET
            status_group = COALESCE(
                EXCLUDED.status_group,
                catalog.availability_status.status_group
            ),
            directly_selectable = EXCLUDED.directly_selectable,
            counts_as_current = EXCLUDED.counts_as_current
        RETURNING status_id;
        """,
        (
            status_id,
            status_label,
            item.status_group,
            item.directly_selectable,
            item.counts_as_current,
        ),
    )

    return cursor.fetchone()["status_id"]


def resolve_platform_id(
    cursor: psycopg.Cursor[Any],
    platform_name: str,
) -> str:
    platform_name = require_text(
        platform_name,
        "platform_name",
    )
    row = fetch_one(
        cursor,
        """
        SELECT platform_id
        FROM catalog.platform
        WHERE LOWER(platform_name) = LOWER(%s)
        LIMIT 1;
        """,
        (platform_name,),
    )

    if not row:
        raise ValueError(
            f"Unknown platform_name: {platform_name!r}. "
            "Include that platform in the same ingestion call first."
        )

    return row["platform_id"]


def resolve_tier_id(
    cursor: psycopg.Cursor[Any],
    platform_name: str,
    tier_name: str,
) -> str:
    platform_name = require_text(
        platform_name,
        "platform_name",
    )
    tier_name = require_text(
        tier_name,
        "tier_name",
    )
    row = fetch_one(
        cursor,
        """
        SELECT tier.tier_id
        FROM catalog.subscription_tier AS tier
        JOIN catalog.platform AS platform
          ON platform.platform_id = tier.platform_id
        WHERE LOWER(platform.platform_name) = LOWER(%s)
          AND LOWER(tier.tier_name) = LOWER(%s)
        LIMIT 1;
        """,
        (
            platform_name,
            tier_name,
        ),
    )

    if not row:
        raise ValueError(
            f"Unknown tier {tier_name!r} for "
            f"platform {platform_name!r}."
        )

    return row["tier_id"]


def upsert_subscription_tier(
    cursor: psycopg.Cursor[Any],
    item: SubscriptionTierInput,
) -> str:
    platform_id = resolve_platform_id(
        cursor,
        item.platform_name,
    )
    tier_name = require_text(
        item.tier_name,
        "tier_name",
    )
    tier_id = item.tier_id or new_id("TIER")

    cursor.execute(
        """
        INSERT INTO catalog.subscription_tier (
            tier_id,
            platform_id,
            tier_name,
            license_type,
            usage_credits,
            rate_limit_window,
            team_seats,
            model_access_summary,
            core_privileges,
            additional_privileges,
            not_included,
            best_suited_for
        )
        VALUES (
            %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s
        )
        ON CONFLICT (platform_id, tier_name) DO UPDATE SET
            license_type = COALESCE(
                EXCLUDED.license_type,
                catalog.subscription_tier.license_type
            ),
            usage_credits = COALESCE(
                EXCLUDED.usage_credits,
                catalog.subscription_tier.usage_credits
            ),
            rate_limit_window = COALESCE(
                EXCLUDED.rate_limit_window,
                catalog.subscription_tier.rate_limit_window
            ),
            team_seats = COALESCE(
                EXCLUDED.team_seats,
                catalog.subscription_tier.team_seats
            ),
            model_access_summary = COALESCE(
                EXCLUDED.model_access_summary,
                catalog.subscription_tier.model_access_summary
            ),
            core_privileges = COALESCE(
                EXCLUDED.core_privileges,
                catalog.subscription_tier.core_privileges
            ),
            additional_privileges = COALESCE(
                EXCLUDED.additional_privileges,
                catalog.subscription_tier.additional_privileges
            ),
            not_included = COALESCE(
                EXCLUDED.not_included,
                catalog.subscription_tier.not_included
            ),
            best_suited_for = COALESCE(
                EXCLUDED.best_suited_for,
                catalog.subscription_tier.best_suited_for
            )
        RETURNING tier_id;
        """,
        (
            tier_id,
            platform_id,
            tier_name,
            item.license_type,
            item.usage_credits,
            item.rate_limit_window,
            item.team_seats,
            item.model_access_summary,
            item.core_privileges,
            item.additional_privileges,
            item.not_included,
            item.best_suited_for,
        ),
    )

    return cursor.fetchone()["tier_id"]


def upsert_plan_pricing(
    cursor: psycopg.Cursor[Any],
    item: PlanPricingInput,
) -> str:
    tier_id = resolve_tier_id(
        cursor,
        item.platform_name,
        item.tier_name,
    )

    if item.pricing_id:
        pricing_id = item.pricing_id
    else:
        existing = fetch_one(
            cursor,
            """
            SELECT pricing_id
            FROM catalog.plan_pricing
            WHERE tier_id = %s
              AND effective_date = %s
              AND billing_frequency IS NOT DISTINCT FROM %s
            LIMIT 1;
            """,
            (
                tier_id,
                item.effective_date,
                item.billing_frequency,
            ),
        )
        pricing_id = (
            existing["pricing_id"]
            if existing
            else new_id("PRICE")
        )

    cursor.execute(
        """
        INSERT INTO catalog.plan_pricing (
            pricing_id,
            tier_id,
            currency,
            billing_frequency,
            commitment_months,
            monthly_list_price,
            annual_total_price,
            annual_monthly_equivalent,
            per_user_monthly,
            base_team_monthly,
            minimum_account_charge,
            pricing_formula_type,
            effective_date
        )
        VALUES (
            %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s
        )
        ON CONFLICT (pricing_id) DO UPDATE SET
            currency = EXCLUDED.currency,
            billing_frequency = COALESCE(
                EXCLUDED.billing_frequency,
                catalog.plan_pricing.billing_frequency
            ),
            commitment_months = COALESCE(
                EXCLUDED.commitment_months,
                catalog.plan_pricing.commitment_months
            ),
            monthly_list_price = COALESCE(
                EXCLUDED.monthly_list_price,
                catalog.plan_pricing.monthly_list_price
            ),
            annual_total_price = COALESCE(
                EXCLUDED.annual_total_price,
                catalog.plan_pricing.annual_total_price
            ),
            annual_monthly_equivalent = COALESCE(
                EXCLUDED.annual_monthly_equivalent,
                catalog.plan_pricing.annual_monthly_equivalent
            ),
            per_user_monthly = COALESCE(
                EXCLUDED.per_user_monthly,
                catalog.plan_pricing.per_user_monthly
            ),
            base_team_monthly = COALESCE(
                EXCLUDED.base_team_monthly,
                catalog.plan_pricing.base_team_monthly
            ),
            minimum_account_charge = COALESCE(
                EXCLUDED.minimum_account_charge,
                catalog.plan_pricing.minimum_account_charge
            ),
            pricing_formula_type = COALESCE(
                EXCLUDED.pricing_formula_type,
                catalog.plan_pricing.pricing_formula_type
            ),
            effective_date = EXCLUDED.effective_date
        RETURNING pricing_id;
        """,
        (
            pricing_id,
            tier_id,
            item.currency,
            item.billing_frequency,
            item.commitment_months,
            item.monthly_list_price,
            item.annual_total_price,
            item.annual_monthly_equivalent,
            item.per_user_monthly,
            item.base_team_monthly,
            item.minimum_account_charge,
            item.pricing_formula_type,
            item.effective_date,
        ),
    )

    return cursor.fetchone()["pricing_id"]


def upsert_platform_model_availability(
    cursor: psycopg.Cursor[Any],
    item: PlatformModelAvailabilityInput,
) -> None:
    platform_id = resolve_platform_id(
        cursor,
        item.platform_name,
    )

    model_row = fetch_one(
        cursor,
        """
        SELECT family.model_family_id
        FROM catalog.model_family AS family
        JOIN catalog.model_provider AS provider
          ON provider.provider_id = family.provider_id
        WHERE LOWER(provider.provider_name) = LOWER(%s)
          AND LOWER(family.family_name) = LOWER(%s)
        LIMIT 1;
        """,
        (
            require_text(
                item.provider_name,
                "provider_name",
            ),
            require_text(
                item.family_name,
                "family_name",
            ),
        ),
    )

    if not model_row:
        raise ValueError(
            f"Unknown model family {item.family_name!r} "
            f"for provider {item.provider_name!r}."
        )

    status_row = fetch_one(
        cursor,
        """
        SELECT status_id
        FROM catalog.availability_status
        WHERE LOWER(status_label) = LOWER(%s)
        LIMIT 1;
        """,
        (
            require_text(
                item.status_label,
                "status_label",
            ),
        ),
    )

    if not status_row:
        raise ValueError(
            f"Unknown availability status: "
            f"{item.status_label!r}."
        )

    cursor.execute(
        """
        INSERT INTO catalog.platform_model_availability (
            platform_id,
            model_family_id,
            status_id
        )
        VALUES (%s, %s, %s)
        ON CONFLICT (
            platform_id,
            model_family_id
        ) DO UPDATE SET
            status_id = EXCLUDED.status_id;
        """,
        (
            platform_id,
            model_row["model_family_id"],
            status_row["status_id"],
        ),
    )


def upsert_platform_feature_support(
    cursor: psycopg.Cursor[Any],
    item: PlatformFeatureSupportInput,
) -> None:
    platform_id = resolve_platform_id(
        cursor,
        item.platform_name,
    )

    feature_row = fetch_one(
        cursor,
        """
        SELECT feature_id
        FROM catalog.feature
        WHERE LOWER(feature_name) = LOWER(%s)
        LIMIT 1;
        """,
        (
            require_text(
                item.feature_name,
                "feature_name",
            ),
        ),
    )

    if not feature_row:
        raise ValueError(
            f"Unknown feature_name: {item.feature_name!r}."
        )

    cursor.execute(
        """
        INSERT INTO catalog.platform_feature_support (
            platform_id,
            feature_id,
            supported,
            support_value,
            support_note
        )
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (
            platform_id,
            feature_id
        ) DO UPDATE SET
            supported = EXCLUDED.supported,
            support_value = EXCLUDED.support_value,
            support_note = COALESCE(
                EXCLUDED.support_note,
                catalog.platform_feature_support.support_note
            );
        """,
        (
            platform_id,
            feature_row["feature_id"],
            item.supported,
            item.support_value,
            item.support_note,
        ),
    )


# ---------------------------------------------------------------------------
# Ingestion tools
# ---------------------------------------------------------------------------

@mcp.tool()
def describe_ingestion_schema() -> dict[str, Any]:
    """
    Describe the structured records accepted by ingest_entities.

    Call this before preparing an ingestion payload.
    """

    return {
        "security": {
            "admin_key_required": True,
            "dry_run_default": True,
            "live_confirmation_required": (
                "APPLY_VERIFIED_CHANGES"
            ),
        },
        "accepted_entities": {
            "platforms": PlatformInput.model_json_schema(),
            "model_providers": (
                ModelProviderInput.model_json_schema()
            ),
            "model_families": (
                ModelFamilyInput.model_json_schema()
            ),
            "features": FeatureInput.model_json_schema(),
            "availability_statuses": (
                AvailabilityStatusInput.model_json_schema()
            ),
            "subscription_tiers": (
                SubscriptionTierInput.model_json_schema()
            ),
            "plan_pricing": (
                PlanPricingInput.model_json_schema()
            ),
            "platform_model_availability": (
                PlatformModelAvailabilityInput.model_json_schema()
            ),
            "platform_feature_support": (
                PlatformFeatureSupportInput.model_json_schema()
            ),
        },
        "rules": [
            "Use only facts explicitly stated in the verified source.",
            "Do not guess missing prices, models, features, or dates.",
            "Run dry_run=true before every live ingestion.",
            "Use dry_run=false only after reviewing the dry-run result.",
            "The tool inserts new records and updates matching records.",
            "The tool does not delete records or alter the database schema.",
        ],
    }


@mcp.tool()
def ingest_entities(
    admin_key: str,
    source_reference: str,
    platforms: list[PlatformInput] | None = None,
    model_providers: list[ModelProviderInput] | None = None,
    model_families: list[ModelFamilyInput] | None = None,
    features: list[FeatureInput] | None = None,
    availability_statuses: (
        list[AvailabilityStatusInput] | None
    ) = None,
    subscription_tiers: (
        list[SubscriptionTierInput] | None
    ) = None,
    plan_pricing: list[PlanPricingInput] | None = None,
    platform_model_availability: (
        list[PlatformModelAvailabilityInput] | None
    ) = None,
    platform_feature_support: (
        list[PlatformFeatureSupportInput] | None
    ) = None,
    dry_run: bool = True,
    live_confirmation: str | None = None,
) -> dict[str, Any]:
    """
    Insert or update verified structured records in the existing catalog.

    This is an administrative write tool. It requires the private admin key.
    It defaults to dry_run=true, which performs all validation and SQL work
    inside a transaction and then rolls the transaction back.

    For a live write, set dry_run=false and set:
    live_confirmation="APPLY_VERIFIED_CHANGES"

    Never pass raw source text. The LLM should first read the source and
    construct only the structured facts explicitly stated by that source.
    """

    require_admin_key(admin_key)
    source_reference = require_text(
        source_reference,
        "source_reference",
    )

    if (
        not dry_run
        and live_confirmation != "APPLY_VERIFIED_CHANGES"
    ):
        raise ValueError(
            "Live ingestion requires "
            'live_confirmation="APPLY_VERIFIED_CHANGES".'
        )

    counts = {
        "platforms": len(platforms or []),
        "model_providers": len(model_providers or []),
        "model_families": len(model_families or []),
        "features": len(features or []),
        "availability_statuses": len(
            availability_statuses or []
        ),
        "subscription_tiers": len(
            subscription_tiers or []
        ),
        "plan_pricing": len(plan_pricing or []),
        "platform_model_availability": len(
            platform_model_availability or []
        ),
        "platform_feature_support": len(
            platform_feature_support or []
        ),
    }

    if sum(counts.values()) == 0:
        raise ValueError(
            "No entities were supplied for ingestion."
        )

    logger.info(
        "Ingestion requested: source=%s dry_run=%s counts=%s",
        source_reference,
        dry_run,
        counts,
    )

    with ingestion_connection() as connection:
        try:
            with connection.cursor() as cursor:
                for item in platforms or []:
                    upsert_platform(cursor, item)

                for item in model_providers or []:
                    upsert_model_provider(
                        cursor,
                        item.provider_name,
                        item.provider_type,
                        item.provider_id,
                    )

                for item in model_families or []:
                    upsert_model_family(cursor, item)

                for item in features or []:
                    upsert_feature(cursor, item)

                for item in availability_statuses or []:
                    upsert_availability_status(cursor, item)

                for item in subscription_tiers or []:
                    upsert_subscription_tier(cursor, item)

                for item in plan_pricing or []:
                    upsert_plan_pricing(cursor, item)

                for item in (
                    platform_model_availability or []
                ):
                    upsert_platform_model_availability(
                        cursor,
                        item,
                    )

                for item in platform_feature_support or []:
                    upsert_platform_feature_support(
                        cursor,
                        item,
                    )

            if dry_run:
                connection.rollback()
                status = "dry_run_passed"
                message = (
                    "Validation and database operations succeeded. "
                    "All changes were rolled back."
                )
            else:
                connection.commit()
                status = "ingestion_completed"
                message = (
                    "Verified records were committed successfully."
                )

        except Exception:
            connection.rollback()
            logger.exception(
                "Ingestion failed: source=%s",
                source_reference,
            )
            raise

    return {
        "status": status,
        "message": message,
        "source_reference": source_reference,
        "processed": counts,
        "important_warning": (
            "Your existing Excel importer truncates and replaces the "
            "catalog. A later full Excel import can erase records added "
            "through this ingestion tool unless those changes are also "
            "added to the workbook."
        ),
    }


if __name__ == "__main__":
    mcp.run(transport="streamable-http")