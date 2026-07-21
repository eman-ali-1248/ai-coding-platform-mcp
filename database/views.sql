CREATE OR REPLACE VIEW public_api.platforms AS
SELECT
    platform_id,
    platform_name,
    category,
    ai_native_ide,
    ide_integrated_copilot,
    offering_company
FROM catalog.platform;


CREATE OR REPLACE VIEW public_api.plans AS
SELECT
    p.platform_id,
    p.platform_name,
    p.category,
    t.tier_id,
    t.tier_name,
    t.license_type,
    t.usage_credits,
    t.rate_limit_window,
    t.team_seats,
    t.model_access_summary,
    t.core_privileges,
    t.additional_privileges,
    t.not_included,
    t.best_suited_for,
    pr.pricing_id,
    pr.currency,
    pr.billing_frequency,
    pr.commitment_months,
    pr.monthly_list_price,
    pr.annual_total_price,
    pr.annual_monthly_equivalent,
    pr.per_user_monthly,
    pr.base_team_monthly,
    pr.minimum_account_charge,
    pr.pricing_formula_type,
    pr.effective_date
FROM catalog.subscription_tier AS t
JOIN catalog.platform AS p
    ON p.platform_id = t.platform_id
LEFT JOIN catalog.plan_pricing AS pr
    ON pr.tier_id = t.tier_id;


CREATE OR REPLACE VIEW public_api.platform_models AS
SELECT
    p.platform_id,
    p.platform_name,
    mf.model_family_id,
    mf.family_name,
    mf.coverage_description,
    mf.grouping_note,
    mp.provider_id,
    mp.provider_name,
    mp.provider_type,
    availability.status_id,
    status.status_label,
    status.status_group,
    status.directly_selectable,
    status.counts_as_current
FROM catalog.platform_model_availability AS availability
JOIN catalog.platform AS p
    ON p.platform_id = availability.platform_id
JOIN catalog.model_family AS mf
    ON mf.model_family_id = availability.model_family_id
JOIN catalog.model_provider AS mp
    ON mp.provider_id = mf.provider_id
JOIN catalog.availability_status AS status
    ON status.status_id = availability.status_id;


CREATE OR REPLACE VIEW public_api.platform_features AS
SELECT
    p.platform_id,
    p.platform_name,
    f.feature_id,
    f.feature_name,
    f.feature_category,
    support.supported,
    support.support_value,
    support.support_note
FROM catalog.platform_feature_support AS support
JOIN catalog.platform AS p
    ON p.platform_id = support.platform_id
JOIN catalog.feature AS f
    ON f.feature_id = support.feature_id;


CREATE OR REPLACE VIEW public_api.dataset_metadata AS
SELECT
    (SELECT COUNT(*) FROM catalog.platform) AS platform_count,
    (SELECT COUNT(*) FROM catalog.subscription_tier) AS plan_count,
    (SELECT COUNT(*) FROM catalog.model_family) AS model_family_count,
    (SELECT COUNT(*) FROM catalog.feature) AS feature_count,
    (
        SELECT COUNT(*)
        FROM catalog.platform_model_availability
    ) AS platform_model_record_count,
    (
        SELECT COUNT(*)
        FROM catalog.platform_feature_support
    ) AS platform_feature_record_count,
    CURRENT_DATE AS database_checked_on;