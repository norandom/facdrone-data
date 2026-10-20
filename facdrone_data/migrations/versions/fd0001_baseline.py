# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
# ruff: noqa: E501 — each statement is kept verbatim as the server prints it
"""Baseline: the market-data store's 37 tables (facdrone-data-manager 2.4).

Self-contained DDL: the tables, keys and indexes exactly as the store's schema stands at its
first release, as plain statements, so a fresh upgrade never depends on application metadata.
The European factor catalogue rows are seeded idempotently: an id already present is kept.
The downgrade drops the tables, and with them every row they hold.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "fd0001_baseline"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None

TABLES: tuple[str, ...] = (
    "cftc_cot",
    "contract_specs",
    "daily_prices",
    "duka_order_flow",
    "etf_meta",
    "etf_prices",
    "factor_definitions",
    "factor_returns",
    "fama_french_factors",
    "fmp_analyst_estimates",
    "fmp_analyst_grades",
    "fmp_balance_sheet",
    "fmp_cash_flow",
    "fmp_cot",
    "fmp_cot_analysis",
    "fmp_earnings_calendar",
    "fmp_economic_calendar",
    "fmp_economic_indicators",
    "fmp_etf_holdings",
    "fmp_grade_actions",
    "fmp_income_statement",
    "fmp_insider_trading",
    "fmp_institutional_ownership",
    "fmp_key_metrics",
    "fmp_news",
    "fmp_positions_summary",
    "fmp_sector_performance",
    "fmp_treasury_rates",
    "fred_series",
    "futures_contract_specs",
    "fx_observation",
    "hourly_prices",
    "instrument_metadata_version",
    "intraday_prices",
    "order_flow_daily",
    "price_observation",
    "symbols",
)

_CREATE_SQL: tuple[str, ...] = (
    """CREATE TABLE cftc_cot (
    symbol character varying NOT NULL,
    date date NOT NULL,
    open_interest bigint,
    noncomm_long bigint,
    noncomm_short bigint,
    noncomm_net bigint,
    comm_long bigint,
    comm_short bigint,
    comm_net bigint,
    pct_noncomm_long double precision,
    pct_noncomm_short double precision,
    change_noncomm_long bigint,
    change_noncomm_short bigint,
    _updated_at timestamp without time zone
)""",
    """CREATE TABLE contract_specs (
    "group" character varying,
    venue_symbol character varying NOT NULL,
    yf_symbol character varying,
    contract_size double precision,
    tick_value double precision,
    tick_size double precision,
    margin_initial double precision,
    bid double precision,
    leverage double precision,
    currency_profit character varying,
    currency_margin character varying,
    digits bigint,
    volume_min double precision,
    volume_max double precision,
    volume_step double precision,
    spread bigint,
    quote_scale double precision DEFAULT '1'::double precision NOT NULL
)""",
    """CREATE TABLE daily_prices (
    symbol character varying NOT NULL,
    date date NOT NULL,
    open double precision,
    high double precision,
    low double precision,
    close double precision,
    volume bigint
)""",
    """CREATE TABLE duka_order_flow (
    symbol character varying NOT NULL,
    date date NOT NULL,
    n_ticks bigint,
    avg_spread double precision,
    max_spread double precision,
    spread_std double precision,
    tick_vol double precision,
    vwap double precision,
    high double precision,
    low double precision,
    close double precision,
    upticks bigint,
    downticks bigint,
    tick_imbalance double precision
)""",
    """CREATE TABLE etf_meta (
    symbol character varying NOT NULL,
    name character varying,
    wkn character varying,
    isin character varying,
    category character varying,
    notes character varying,
    _updated_at timestamp without time zone
)""",
    """CREATE TABLE etf_prices (
    symbol character varying NOT NULL,
    date date NOT NULL,
    open double precision,
    high double precision,
    low double precision,
    close double precision,
    volume bigint
)""",
    """CREATE TABLE factor_definitions (
    factor_id character varying(16) NOT NULL,
    label character varying(64),
    family character varying(32),
    description text,
    source character varying(128),
    region character varying(32),
    frequency character varying(16),
    sign_convention text,
    _updated_at timestamp without time zone
)""",
    """CREATE TABLE factor_returns (
    factor_id character varying(16) NOT NULL,
    date date NOT NULL,
    ret double precision NOT NULL,
    _updated_at timestamp without time zone
)""",
    """CREATE TABLE fama_french_factors (
    date date NOT NULL,
    mkt_rf double precision,
    smb double precision,
    hml double precision,
    rf double precision,
    _updated_at timestamp without time zone
)""",
    """CREATE TABLE fmp_analyst_estimates (
    symbol character varying NOT NULL,
    date date NOT NULL,
    estimated_revenue_avg double precision,
    estimated_revenue_high double precision,
    estimated_revenue_low double precision,
    estimated_ebitda_avg double precision,
    estimated_eps_avg double precision,
    estimated_eps_high double precision,
    estimated_eps_low double precision,
    number_analysts_estimated_revenue integer,
    number_analysts_estimated_eps integer,
    _updated_at timestamp without time zone
)""",
    """CREATE TABLE fmp_analyst_grades (
    symbol character varying NOT NULL,
    date date NOT NULL,
    strong_buy integer,
    buy integer,
    hold integer,
    sell integer,
    strong_sell integer,
    _updated_at timestamp without time zone
)""",
    """CREATE TABLE fmp_balance_sheet (
    symbol character varying NOT NULL,
    date date NOT NULL,
    fiscal_year integer,
    period character varying NOT NULL,
    total_assets double precision,
    total_debt double precision,
    total_equity double precision,
    cash_and_equivalents double precision,
    total_current_assets double precision,
    total_current_liabilities double precision,
    filing_date date,
    _updated_at timestamp without time zone
)""",
    """CREATE TABLE fmp_cash_flow (
    symbol character varying NOT NULL,
    date date NOT NULL,
    fiscal_year integer,
    period character varying NOT NULL,
    operating_cash_flow double precision,
    capital_expenditure double precision,
    free_cash_flow double precision,
    stock_based_compensation double precision,
    weighted_average_shares double precision,
    filing_date date NOT NULL,
    _updated_at timestamp without time zone
)""",
    """CREATE TABLE fmp_cot (
    symbol character varying NOT NULL,
    date date NOT NULL,
    short_name character varying,
    sector character varying,
    current_long_all double precision,
    current_short_all double precision,
    change_long_all double precision,
    change_short_all double precision,
    pct_long_all double precision,
    pct_short_all double precision,
    net_position double precision,
    _updated_at timestamp without time zone
)""",
    """CREATE TABLE fmp_cot_analysis (
    symbol character varying NOT NULL,
    date date NOT NULL,
    current_long double precision,
    current_short double precision,
    net_position double precision,
    change_long double precision,
    change_short double precision,
    reversal_trend character varying,
    net_position_trend character varying,
    market_situation character varying,
    _updated_at timestamp without time zone
)""",
    """CREATE TABLE fmp_earnings_calendar (
    symbol character varying NOT NULL,
    date date NOT NULL,
    eps_actual double precision,
    eps_estimate double precision,
    revenue_actual double precision,
    revenue_estimate double precision,
    _updated_at timestamp without time zone
)""",
    """CREATE TABLE fmp_economic_calendar (
    date date NOT NULL,
    "time" character varying,
    country character varying NOT NULL,
    event character varying NOT NULL,
    actual double precision,
    estimate double precision,
    previous double precision,
    impact character varying,
    _updated_at timestamp without time zone
)""",
    """CREATE TABLE fmp_economic_indicators (
    name character varying NOT NULL,
    date date NOT NULL,
    value double precision,
    _updated_at timestamp without time zone
)""",
    """CREATE TABLE fmp_etf_holdings (
    symbol character varying NOT NULL,
    date date NOT NULL,
    asset character varying NOT NULL,
    shares double precision,
    weight_percentage double precision,
    market_value double precision,
    _updated_at timestamp without time zone
)""",
    """CREATE TABLE fmp_grade_actions (
    symbol character varying NOT NULL,
    date date NOT NULL,
    grading_company character varying NOT NULL,
    previous_grade character varying,
    new_grade character varying,
    action character varying,
    _updated_at timestamp without time zone
)""",
    """CREATE TABLE fmp_income_statement (
    symbol character varying NOT NULL,
    date date NOT NULL,
    fiscal_year integer,
    period character varying NOT NULL,
    revenue double precision,
    gross_profit double precision,
    ebitda double precision,
    operating_income double precision,
    net_income double precision,
    eps double precision,
    eps_diluted double precision,
    gross_profit_ratio double precision,
    operating_income_ratio double precision,
    net_income_ratio double precision,
    filing_date date,
    _updated_at timestamp without time zone
)""",
    """CREATE TABLE fmp_insider_trading (
    symbol character varying NOT NULL,
    filing_date date NOT NULL,
    transaction_date date,
    transaction_type character varying NOT NULL,
    securities_transacted double precision,
    price double precision,
    reporting_name character varying NOT NULL,
    type_of_owner character varying,
    acquisition_or_disposition character varying,
    _updated_at timestamp without time zone
)""",
    """CREATE TABLE fmp_institutional_ownership (
    symbol character varying NOT NULL,
    year integer NOT NULL,
    quarter integer NOT NULL,
    filing_date date,
    n_holders integer,
    n_new integer,
    n_sold_out integer,
    n_increased integer,
    n_decreased integer,
    total_shares bigint,
    net_shares_change bigint,
    net_flow_pct double precision,
    _updated_at timestamp without time zone
)""",
    """CREATE TABLE fmp_key_metrics (
    symbol character varying NOT NULL,
    date date NOT NULL,
    fiscal_year integer,
    period character varying NOT NULL,
    market_cap double precision,
    enterprise_value double precision,
    pe_ratio double precision,
    ev_to_ebitda double precision,
    ev_to_sales double precision,
    roic double precision,
    roe double precision,
    roa double precision,
    current_ratio double precision,
    debt_to_equity double precision,
    fcf_per_share double precision,
    fcf_yield double precision,
    dividend_yield double precision,
    payout_ratio double precision,
    revenue_per_share double precision,
    book_value_per_share double precision,
    _updated_at timestamp without time zone
)""",
    """CREATE TABLE fmp_news (
    symbol character varying NOT NULL,
    published_date timestamp without time zone NOT NULL,
    title text NOT NULL,
    publisher character varying,
    _updated_at timestamp without time zone
)""",
    """CREATE TABLE fmp_positions_summary (
    symbol character varying NOT NULL,
    year integer NOT NULL,
    quarter integer NOT NULL,
    investors_holding integer,
    investors_holding_change integer,
    new_positions integer,
    closed_positions integer,
    increased_positions integer,
    reduced_positions integer,
    put_call_ratio double precision,
    put_call_ratio_change double precision,
    total_invested_change double precision,
    ownership_pct_change double precision,
    _updated_at timestamp without time zone
)""",
    """CREATE TABLE fmp_sector_performance (
    date date NOT NULL,
    sector character varying NOT NULL,
    change_percentage double precision,
    _updated_at timestamp without time zone
)""",
    """CREATE TABLE fmp_treasury_rates (
    date date NOT NULL,
    month1 double precision,
    month2 double precision,
    month3 double precision,
    month6 double precision,
    year1 double precision,
    year2 double precision,
    year3 double precision,
    year5 double precision,
    year7 double precision,
    year10 double precision,
    year20 double precision,
    year30 double precision,
    _updated_at timestamp without time zone
)""",
    """CREATE TABLE fred_series (
    series_id character varying NOT NULL,
    date date NOT NULL,
    value double precision,
    _updated_at timestamp without time zone
)""",
    """CREATE TABLE futures_contract_specs (
    symbol character varying(64) NOT NULL,
    exchange character varying(64) NOT NULL,
    parent_symbol character varying(64) NOT NULL,
    multiplier double precision NOT NULL,
    tick_size double precision NOT NULL,
    tick_value double precision NOT NULL,
    micro_of character varying(64),
    limit_weight double precision NOT NULL,
    session_open character varying(5) NOT NULL,
    session_close character varying(5) NOT NULL,
    commission_per_side double precision NOT NULL,
    fees_per_side double precision NOT NULL,
    initial_margin double precision NOT NULL,
    sizing_contract boolean NOT NULL,
    currency character(3) NOT NULL
)""",
    """CREATE TABLE fx_observation (
    quote_symbol character varying NOT NULL,
    base_currency character varying(8) NOT NULL,
    quote_currency character varying(8) NOT NULL,
    event_time timestamp with time zone NOT NULL,
    known_at timestamp with time zone NOT NULL,
    source_name character varying(64) NOT NULL,
    revision_id character varying(128) NOT NULL,
    rate double precision NOT NULL
)""",
    """CREATE TABLE hourly_prices (
    symbol character varying NOT NULL,
    "timestamp" timestamp without time zone NOT NULL,
    open double precision,
    high double precision,
    low double precision,
    close double precision,
    volume double precision
)""",
    """CREATE TABLE instrument_metadata_version (
    venue_symbol character varying NOT NULL,
    yf_symbol character varying NOT NULL,
    asset_class character varying(32) NOT NULL,
    "group" character varying,
    alias character varying,
    aliases_json jsonb NOT NULL,
    currency_profit character varying,
    currency_margin character varying,
    strategy_sleeve character varying(64),
    contract_size double precision,
    tick_value double precision,
    tick_size double precision,
    margin_initial double precision,
    bid double precision,
    leverage double precision,
    digits bigint,
    volume_min double precision,
    volume_max double precision,
    volume_step double precision,
    spread bigint,
    known_at timestamp with time zone NOT NULL,
    source_name character varying(64) NOT NULL,
    revision_id character varying(128) NOT NULL,
    futures_payload jsonb
)""",
    """CREATE TABLE intraday_prices (
    symbol character varying NOT NULL,
    datetime timestamp without time zone NOT NULL,
    open double precision,
    high double precision,
    low double precision,
    close double precision,
    volume bigint,
    "interval" character varying NOT NULL
)""",
    """CREATE TABLE order_flow_daily (
    symbol character varying NOT NULL,
    date date NOT NULL,
    total_volume bigint,
    n_trades bigint,
    avg_trade_size double precision,
    vwap double precision,
    avg_spread double precision
)""",
    """CREATE TABLE price_observation (
    symbol character varying NOT NULL,
    event_time timestamp with time zone NOT NULL,
    known_at timestamp with time zone NOT NULL,
    source_name character varying(64) NOT NULL,
    revision_id character varying(128) NOT NULL,
    open double precision,
    high double precision,
    low double precision,
    close double precision,
    volume bigint
)""",
    """CREATE TABLE symbols (
    symbol character varying NOT NULL
)""",
    """ALTER TABLE cftc_cot
    ADD CONSTRAINT cftc_cot_pkey PRIMARY KEY (symbol, date)""",
    """ALTER TABLE contract_specs
    ADD CONSTRAINT contract_specs_pkey PRIMARY KEY (venue_symbol)""",
    """ALTER TABLE daily_prices
    ADD CONSTRAINT daily_prices_pkey PRIMARY KEY (symbol, date)""",
    """ALTER TABLE duka_order_flow
    ADD CONSTRAINT duka_order_flow_pkey PRIMARY KEY (symbol, date)""",
    """ALTER TABLE etf_meta
    ADD CONSTRAINT etf_meta_pkey PRIMARY KEY (symbol)""",
    """ALTER TABLE etf_prices
    ADD CONSTRAINT etf_prices_pkey PRIMARY KEY (symbol, date)""",
    """ALTER TABLE factor_definitions
    ADD CONSTRAINT factor_definitions_pkey PRIMARY KEY (factor_id)""",
    """ALTER TABLE factor_returns
    ADD CONSTRAINT factor_returns_pkey PRIMARY KEY (factor_id, date)""",
    """ALTER TABLE fama_french_factors
    ADD CONSTRAINT fama_french_factors_pkey PRIMARY KEY (date)""",
    """ALTER TABLE fmp_analyst_estimates
    ADD CONSTRAINT fmp_analyst_estimates_pkey PRIMARY KEY (symbol, date)""",
    """ALTER TABLE fmp_analyst_grades
    ADD CONSTRAINT fmp_analyst_grades_pkey PRIMARY KEY (symbol, date)""",
    """ALTER TABLE fmp_balance_sheet
    ADD CONSTRAINT fmp_balance_sheet_pkey PRIMARY KEY (symbol, date, period)""",
    """ALTER TABLE fmp_cash_flow
    ADD CONSTRAINT fmp_cash_flow_pkey PRIMARY KEY (symbol, date, period, filing_date)""",
    """ALTER TABLE fmp_cot_analysis
    ADD CONSTRAINT fmp_cot_analysis_pkey PRIMARY KEY (symbol, date)""",
    """ALTER TABLE fmp_cot
    ADD CONSTRAINT fmp_cot_pkey PRIMARY KEY (symbol, date)""",
    """ALTER TABLE fmp_earnings_calendar
    ADD CONSTRAINT fmp_earnings_calendar_pkey PRIMARY KEY (symbol, date)""",
    """ALTER TABLE fmp_economic_calendar
    ADD CONSTRAINT fmp_economic_calendar_pkey PRIMARY KEY (date, country, event)""",
    """ALTER TABLE fmp_economic_indicators
    ADD CONSTRAINT fmp_economic_indicators_pkey PRIMARY KEY (name, date)""",
    """ALTER TABLE fmp_etf_holdings
    ADD CONSTRAINT fmp_etf_holdings_pkey PRIMARY KEY (symbol, date, asset)""",
    """ALTER TABLE fmp_grade_actions
    ADD CONSTRAINT fmp_grade_actions_pkey PRIMARY KEY (symbol, date, grading_company)""",
    """ALTER TABLE fmp_income_statement
    ADD CONSTRAINT fmp_income_statement_pkey PRIMARY KEY (symbol, date, period)""",
    """ALTER TABLE fmp_insider_trading
    ADD CONSTRAINT fmp_insider_trading_pkey PRIMARY KEY (symbol, filing_date, reporting_name, transaction_type)""",
    """ALTER TABLE fmp_institutional_ownership
    ADD CONSTRAINT fmp_institutional_ownership_pkey PRIMARY KEY (symbol, year, quarter)""",
    """ALTER TABLE fmp_key_metrics
    ADD CONSTRAINT fmp_key_metrics_pkey PRIMARY KEY (symbol, date, period)""",
    """ALTER TABLE fmp_news
    ADD CONSTRAINT fmp_news_pkey PRIMARY KEY (symbol, published_date, title)""",
    """ALTER TABLE fmp_positions_summary
    ADD CONSTRAINT fmp_positions_summary_pkey PRIMARY KEY (symbol, year, quarter)""",
    """ALTER TABLE fmp_sector_performance
    ADD CONSTRAINT fmp_sector_performance_pkey PRIMARY KEY (date, sector)""",
    """ALTER TABLE fmp_treasury_rates
    ADD CONSTRAINT fmp_treasury_rates_pkey PRIMARY KEY (date)""",
    """ALTER TABLE fred_series
    ADD CONSTRAINT fred_series_pkey PRIMARY KEY (series_id, date)""",
    """ALTER TABLE futures_contract_specs
    ADD CONSTRAINT futures_contract_specs_pkey PRIMARY KEY (symbol)""",
    """ALTER TABLE fx_observation
    ADD CONSTRAINT fx_observation_pkey PRIMARY KEY (quote_symbol, event_time, known_at, source_name, revision_id)""",
    """ALTER TABLE hourly_prices
    ADD CONSTRAINT hourly_prices_pkey PRIMARY KEY (symbol, "timestamp")""",
    """ALTER TABLE instrument_metadata_version
    ADD CONSTRAINT instrument_metadata_version_pkey PRIMARY KEY (venue_symbol, known_at, source_name, revision_id)""",
    """ALTER TABLE intraday_prices
    ADD CONSTRAINT intraday_prices_pkey PRIMARY KEY (symbol, datetime, "interval")""",
    """ALTER TABLE order_flow_daily
    ADD CONSTRAINT order_flow_daily_pkey PRIMARY KEY (symbol, date)""",
    """ALTER TABLE price_observation
    ADD CONSTRAINT price_observation_pkey PRIMARY KEY (symbol, event_time, known_at, source_name, revision_id)""",
    """ALTER TABLE symbols
    ADD CONSTRAINT symbols_pkey PRIMARY KEY (symbol)""",
    """CREATE INDEX idx_daily_prices_date ON daily_prices USING btree (date)""",
    """CREATE INDEX idx_daily_prices_symbol ON daily_prices USING btree (symbol)""",
    """CREATE INDEX idx_etf_prices_date ON etf_prices USING btree (date)""",
    """CREATE INDEX idx_etf_prices_symbol ON etf_prices USING btree (symbol)""",
    """CREATE INDEX idx_intraday_dt ON intraday_prices USING btree (datetime)""",
    """CREATE INDEX ix_factor_returns_factor_date ON factor_returns USING btree (factor_id, date)""",
    """CREATE INDEX ix_fx_observation_ccy_event_known ON fx_observation USING btree (base_currency, quote_currency, event_time, known_at)""",
    """CREATE INDEX ix_fx_observation_quote_event_known ON fx_observation USING btree (quote_symbol, event_time, known_at)""",
    """CREATE INDEX ix_instrument_metadata_version_venue_known ON instrument_metadata_version USING btree (venue_symbol, known_at)""",
    """CREATE INDEX ix_instrument_metadata_version_yf_known ON instrument_metadata_version USING btree (yf_symbol, known_at)""",
    """CREATE INDEX ix_price_observation_symbol_event_known ON price_observation USING btree (symbol, event_time, known_at)""",
    """CREATE INDEX ix_price_observation_symbol_known ON price_observation USING btree (symbol, known_at)""",
)

# Ken French states every regional series in US dollars: the region is not the currency.
_FACTORS: tuple[tuple[str, str, str, str, str], ...] = (
    (
        "MKT_EU",
        "Market (Europe)",
        "Market",
        "Broad European equity market over the risk-free rate.",
        "market excess return (Mkt-RF), stated in USD",
    ),
    (
        "SMB_EU",
        "Size (Europe)",
        "Size",
        "Small-minus-big across European markets.",
        "long small / short big, stated in USD",
    ),
    (
        "HML_EU",
        "Value (Europe)",
        "Value",
        "High-minus-low book-to-market across European markets.",
        "long value / short growth, stated in USD",
    ),
    (
        "MOM_EU",
        "Momentum (Europe)",
        "Momentum",
        "Winners-minus-losers (WML) across European markets.",
        "long winners / short losers, stated in USD",
    ),
)


def upgrade() -> None:
    for statement in _CREATE_SQL:
        op.execute(statement)
    insert = sa.text(
        "INSERT INTO factor_definitions (factor_id, label, family, description, source, region,"
        " frequency, sign_convention) VALUES (:factor_id, :label, :family, :description,"
        " 'Ken French', 'Europe', 'daily', :sign_convention) ON CONFLICT (factor_id) DO NOTHING"
    )
    keys = ("factor_id", "label", "family", "description", "sign_convention")
    for row in _FACTORS:
        op.get_bind().execute(insert, dict(zip(keys, row, strict=True)))


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE {table}")
