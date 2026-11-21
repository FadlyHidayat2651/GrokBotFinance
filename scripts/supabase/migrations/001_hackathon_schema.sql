-- Hackathon fin-crime datasets: one schema covering all four institutions.
-- Idempotent: safe to re-run.

CREATE SCHEMA IF NOT EXISTS hackathon;

-- ---------------------------------------------------------------------------
-- Core entity tables
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS hackathon.customers (
    institution              text        NOT NULL,
    customer_id              text        NOT NULL,
    kind                     text        NOT NULL,
    name                     text        NOT NULL,
    date_of_birth            date,
    occupation               text,
    employer                 text,
    company_number           text,
    incorporated_on          date,
    business_activity        text,
    address                  text,
    onboarded_on             date,
    risk_rating              text,
    -- flattened declared{}
    declared_source_of_funds text,
    declared_account_purpose text,
    declared_expected_countries text[],
    declared_expected_channels  text[],
    declared_monthly_income_gbp numeric,
    declared_annual_turnover_gbp numeric,
    account_ids              text[],
    PRIMARY KEY (institution, customer_id)
);

CREATE TABLE IF NOT EXISTS hackathon.cases (
    institution          text        NOT NULL,
    case_id              text        NOT NULL,
    customer_id          text        NOT NULL,
    opened_at            timestamptz NOT NULL,
    -- flattened review_window{}
    review_window_from   date,
    review_window_to     date,
    alert_ids            text[],
    held_payment_id      text,
    PRIMARY KEY (institution, case_id)
);

CREATE TABLE IF NOT EXISTS hackathon.alerts (
    institution      text        NOT NULL,
    alert_id         text        NOT NULL,
    case_id          text        NOT NULL,
    customer_id      text        NOT NULL,
    rule_name        text        NOT NULL,
    rule_id          text        NOT NULL,
    created_at       timestamptz NOT NULL,
    message          text        NOT NULL,
    measured         jsonb       NOT NULL DEFAULT '{}'::jsonb,
    transaction_ids  text[],
    PRIMARY KEY (institution, alert_id)
);

CREATE TABLE IF NOT EXISTS hackathon.transactions (
    institution           text        NOT NULL,
    transaction_id        text        NOT NULL,
    customer_id           text        NOT NULL,
    account_id            text        NOT NULL,
    booked_at             timestamptz NOT NULL,
    direction             text        NOT NULL,
    channel               text        NOT NULL,
    amount_gbp            numeric     NOT NULL,
    amount                numeric     NOT NULL,
    currency              text        NOT NULL,
    counterparty_name     text,
    counterparty_account  text,
    counterparty_country  text,
    reference             text,
    location              text,
    PRIMARY KEY (institution, transaction_id)
);

CREATE TABLE IF NOT EXISTS hackathon.evidence (
    institution  text NOT NULL,
    doc_id       text NOT NULL,
    customer_id  text NOT NULL,
    type         text NOT NULL,
    title        text NOT NULL,
    dated        date,
    file_path    text NOT NULL,
    body_md      text,
    PRIMARY KEY (institution, doc_id)
);

CREATE TABLE IF NOT EXISTS hackathon.previous_reviews (
    institution  text NOT NULL,
    review_id    text NOT NULL,
    customer_id  text NOT NULL,
    reviewed_on  date,
    rule_id      text,
    verdict      text,
    note         text,
    PRIMARY KEY (institution, review_id)
);

CREATE TABLE IF NOT EXISTS hackathon.internal_list (
    institution             text NOT NULL,
    entry_id                text NOT NULL,
    kind                    text NOT NULL,
    name                    text NOT NULL,
    date_of_birth           date,
    address                 text,
    reason                  text,
    added_on                date,
    -- flattened identifiers{}
    identifier_internal_case text,
    identifier_swift         text,
    PRIMARY KEY (institution, entry_id)
);

CREATE TABLE IF NOT EXISTS hackathon.policies (
    institution  text PRIMARY KEY,
    file_path    text NOT NULL,
    body_md      text NOT NULL,
    loaded_at    timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Bridge / junction tables (normalized list columns for join-friendly queries)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS hackathon.case_alerts (
    institution  text NOT NULL,
    case_id      text NOT NULL,
    alert_id     text NOT NULL,
    PRIMARY KEY (institution, case_id, alert_id)
);

CREATE TABLE IF NOT EXISTS hackathon.alert_transactions (
    institution      text NOT NULL,
    alert_id         text NOT NULL,
    transaction_id   text NOT NULL,
    PRIMARY KEY (institution, alert_id, transaction_id)
);

CREATE TABLE IF NOT EXISTS hackathon.customer_accounts (
    institution  text NOT NULL,
    customer_id  text NOT NULL,
    account_id   text NOT NULL,
    PRIMARY KEY (institution, customer_id, account_id)
);

-- ---------------------------------------------------------------------------
-- Indexes
-- ---------------------------------------------------------------------------

CREATE INDEX IF NOT EXISTS idx_customers_risk
    ON hackathon.customers (institution, risk_rating);

CREATE INDEX IF NOT EXISTS idx_cases_customer
    ON hackathon.cases (institution, customer_id);

CREATE INDEX IF NOT EXISTS idx_alerts_case
    ON hackathon.alerts (institution, case_id);

CREATE INDEX IF NOT EXISTS idx_alerts_customer
    ON hackathon.alerts (institution, customer_id);

CREATE INDEX IF NOT EXISTS idx_alerts_rule
    ON hackathon.alerts (institution, rule_id);

CREATE INDEX IF NOT EXISTS idx_alerts_measured
    ON hackathon.alerts USING gin (measured);

CREATE INDEX IF NOT EXISTS idx_tx_customer
    ON hackathon.transactions (institution, customer_id);

CREATE INDEX IF NOT EXISTS idx_tx_account
    ON hackathon.transactions (institution, account_id);

CREATE INDEX IF NOT EXISTS idx_tx_booked
    ON hackathon.transactions (institution, booked_at);

CREATE INDEX IF NOT EXISTS idx_evidence_customer
    ON hackathon.evidence (institution, customer_id);

CREATE INDEX IF NOT EXISTS idx_reviews_customer
    ON hackathon.previous_reviews (institution, customer_id);

CREATE INDEX IF NOT EXISTS idx_internal_list_name
    ON hackathon.internal_list (institution, name);
