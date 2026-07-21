CREATE SCHEMA IF NOT EXISTS catalog;
CREATE SCHEMA IF NOT EXISTS public_api;


CREATE TABLE IF NOT EXISTS catalog.platform (
    platform_id TEXT PRIMARY KEY,
    platform_name TEXT NOT NULL UNIQUE,
    category TEXT,
    ai_native_ide BOOLEAN,
    ide_integrated_copilot BOOLEAN,
    offering_company TEXT
);


CREATE TABLE IF NOT EXISTS catalog.model_provider (
    provider_id TEXT PRIMARY KEY,
    provider_name TEXT NOT NULL UNIQUE,
    provider_type TEXT
);


CREATE TABLE IF NOT EXISTS catalog.model_family (
    model_family_id TEXT PRIMARY KEY,
    provider_id TEXT NOT NULL,
    family_name TEXT NOT NULL,
    coverage_description TEXT,
    grouping_note TEXT,

    CONSTRAINT fk_model_family_provider
        FOREIGN KEY (provider_id)
        REFERENCES catalog.model_provider(provider_id),

    CONSTRAINT uq_provider_family
        UNIQUE (provider_id, family_name)
);


CREATE TABLE IF NOT EXISTS catalog.feature (
    feature_id TEXT PRIMARY KEY,
    feature_name TEXT NOT NULL UNIQUE,
    feature_category TEXT
);


CREATE TABLE IF NOT EXISTS catalog.availability_status (
    status_id TEXT PRIMARY KEY,
    status_label TEXT NOT NULL UNIQUE,
    status_group TEXT,
    directly_selectable BOOLEAN NOT NULL,
    counts_as_current BOOLEAN NOT NULL
);


CREATE TABLE IF NOT EXISTS catalog.subscription_tier (
    tier_id TEXT PRIMARY KEY,
    platform_id TEXT NOT NULL,
    tier_name TEXT NOT NULL,
    license_type TEXT,
    usage_credits TEXT,
    rate_limit_window TEXT,
    team_seats TEXT,
    model_access_summary TEXT,
    core_privileges TEXT,
    additional_privileges TEXT,
    not_included TEXT,
    best_suited_for TEXT,

    CONSTRAINT fk_subscription_platform
        FOREIGN KEY (platform_id)
        REFERENCES catalog.platform(platform_id),

    CONSTRAINT uq_platform_tier
        UNIQUE (platform_id, tier_name)
);


CREATE TABLE IF NOT EXISTS catalog.plan_pricing (
    pricing_id TEXT PRIMARY KEY,
    tier_id TEXT NOT NULL,
    currency TEXT NOT NULL,
    billing_frequency TEXT,
    commitment_months INTEGER,
    monthly_list_price NUMERIC(12, 2),
    annual_total_price NUMERIC(12, 2),
    annual_monthly_equivalent NUMERIC(12, 2),
    per_user_monthly NUMERIC(12, 2),
    base_team_monthly NUMERIC(12, 2),
    minimum_account_charge NUMERIC(12, 2),
    pricing_formula_type TEXT,
    effective_date DATE NOT NULL,

    CONSTRAINT fk_pricing_tier
        FOREIGN KEY (tier_id)
        REFERENCES catalog.subscription_tier(tier_id),

    CONSTRAINT chk_commitment_months
        CHECK (
            commitment_months IS NULL
            OR commitment_months > 0
        ),

    CONSTRAINT chk_monthly_price
        CHECK (
            monthly_list_price IS NULL
            OR monthly_list_price >= 0
        ),

    CONSTRAINT chk_annual_price
        CHECK (
            annual_total_price IS NULL
            OR annual_total_price >= 0
        ),

    CONSTRAINT chk_annual_equivalent
        CHECK (
            annual_monthly_equivalent IS NULL
            OR annual_monthly_equivalent >= 0
        ),

    CONSTRAINT chk_per_user_price
        CHECK (
            per_user_monthly IS NULL
            OR per_user_monthly >= 0
        ),

    CONSTRAINT chk_base_team_price
        CHECK (
            base_team_monthly IS NULL
            OR base_team_monthly >= 0
        ),

    CONSTRAINT chk_minimum_charge
        CHECK (
            minimum_account_charge IS NULL
            OR minimum_account_charge >= 0
        )
);


CREATE TABLE IF NOT EXISTS catalog.platform_model_availability (
    platform_id TEXT NOT NULL,
    model_family_id TEXT NOT NULL,
    status_id TEXT NOT NULL,

    CONSTRAINT pk_platform_model
        PRIMARY KEY (platform_id, model_family_id),

    CONSTRAINT fk_pma_platform
        FOREIGN KEY (platform_id)
        REFERENCES catalog.platform(platform_id),

    CONSTRAINT fk_pma_model_family
        FOREIGN KEY (model_family_id)
        REFERENCES catalog.model_family(model_family_id),

    CONSTRAINT fk_pma_status
        FOREIGN KEY (status_id)
        REFERENCES catalog.availability_status(status_id)
);


CREATE TABLE IF NOT EXISTS catalog.platform_feature_support (
    platform_id TEXT NOT NULL,
    feature_id TEXT NOT NULL,
    supported BOOLEAN NOT NULL,
    support_value NUMERIC(4, 2) NOT NULL,
    support_note TEXT,

    CONSTRAINT pk_platform_feature
        PRIMARY KEY (platform_id, feature_id),

    CONSTRAINT fk_pfs_platform
        FOREIGN KEY (platform_id)
        REFERENCES catalog.platform(platform_id),

    CONSTRAINT fk_pfs_feature
        FOREIGN KEY (feature_id)
        REFERENCES catalog.feature(feature_id),

    CONSTRAINT chk_support_value
        CHECK (
            support_value >= 0
            AND support_value <= 1
        ),

    CONSTRAINT chk_supported_matches_value
        CHECK (
            (supported = TRUE AND support_value > 0)
            OR
            (supported = FALSE AND support_value = 0)
        )
);


CREATE INDEX IF NOT EXISTS ix_model_family_provider
    ON catalog.model_family(provider_id);

CREATE INDEX IF NOT EXISTS ix_subscription_platform
    ON catalog.subscription_tier(platform_id);

CREATE INDEX IF NOT EXISTS ix_pricing_tier
    ON catalog.plan_pricing(tier_id);

CREATE INDEX IF NOT EXISTS ix_pma_status
    ON catalog.platform_model_availability(status_id);

CREATE INDEX IF NOT EXISTS ix_pfs_feature
    ON catalog.platform_feature_support(feature_id);