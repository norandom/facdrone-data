# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""SQLAlchemy Core schema of the market-data store: 37 tables in one ``MetaData``.

Price panels, revisioned point-in-time observations, the provider families (fundamentals,
positioning, macro, news, order flow) and instrument reference data, with the nullable
revisioned futures payload. The reflect-check guards drift between this declaration and a
migrated database. A consumer that owns further tables declares them in its own ``MetaData``;
no foreign key crosses into this one.
"""

from __future__ import annotations

from sqlalchemy import (
    CHAR,
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    Index,
    Integer,
    MetaData,
    PrimaryKeyConstraint,
    String,
    Table,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB

metadata = MetaData()


CFTC_COT = Table(
    "cftc_cot",
    metadata,
    Column("symbol", String),
    Column("date", Date),
    Column("open_interest", BigInteger),
    Column("noncomm_long", BigInteger),
    Column("noncomm_short", BigInteger),
    Column("noncomm_net", BigInteger),
    Column("comm_long", BigInteger),
    Column("comm_short", BigInteger),
    Column("comm_net", BigInteger),
    Column("pct_noncomm_long", Float),
    Column("pct_noncomm_short", Float),
    Column("change_noncomm_long", BigInteger),
    Column("change_noncomm_short", BigInteger),
    Column("_updated_at", DateTime(timezone=False)),
    PrimaryKeyConstraint("symbol", "date"),
)


CONTRACT_SPECS = Table(
    "contract_specs",
    metadata,
    Column("group", String),
    Column("venue_symbol", String),
    Column("yf_symbol", String),
    Column("contract_size", Float),
    Column("tick_value", Float),
    Column("tick_size", Float),
    Column("margin_initial", Float),
    Column("bid", Float),
    Column("leverage", Float),
    Column("currency_profit", String),
    Column("currency_margin", String),
    Column("digits", BigInteger),
    Column("volume_min", Float),
    Column("volume_max", Float),
    Column("volume_step", Float),
    Column("spread", BigInteger),
    # The broker's quoting unit over the canonical panel's (100 for XCUUSD, else 1): the
    # factor the reference screen folds into the contract size the simulation prices with.
    Column("quote_scale", Float, nullable=False, server_default="1"),
    PrimaryKeyConstraint("venue_symbol"),
)


INSTRUMENT_METADATA_VERSION = Table(
    "instrument_metadata_version",
    metadata,
    Column("venue_symbol", String, nullable=False),
    Column("yf_symbol", String, nullable=False),
    Column("asset_class", String(32), nullable=False),
    Column("group", String),
    Column("alias", String),
    Column("aliases_json", JSONB, nullable=False),
    Column("currency_profit", String),
    Column("currency_margin", String),
    Column("strategy_sleeve", String(64)),
    Column("contract_size", Float),
    Column("tick_value", Float),
    Column("tick_size", Float),
    Column("margin_initial", Float),
    Column("bid", Float),
    Column("leverage", Float),
    Column("digits", BigInteger),
    Column("volume_min", Float),
    Column("volume_max", Float),
    Column("volume_step", Float),
    Column("spread", BigInteger),
    Column("known_at", DateTime(timezone=True), nullable=False),
    Column("source_name", String(64), nullable=False),
    Column("revision_id", String(128), nullable=False),
    Column("futures_payload", JSONB),
    PrimaryKeyConstraint("venue_symbol", "known_at", "source_name", "revision_id"),
    Index("ix_instrument_metadata_version_venue_known", "venue_symbol", "known_at"),
    Index("ix_instrument_metadata_version_yf_known", "yf_symbol", "known_at"),
)


FUTURES_CONTRACT_SPECS = Table(
    "futures_contract_specs",
    metadata,
    Column("symbol", String(64), primary_key=True),
    Column("exchange", String(64), nullable=False),
    Column("parent_symbol", String(64), nullable=False),
    Column("multiplier", Float, nullable=False),
    Column("tick_size", Float, nullable=False),
    Column("tick_value", Float, nullable=False),
    Column("micro_of", String(64)),
    Column("limit_weight", Float, nullable=False),
    Column("session_open", String(5), nullable=False),
    Column("session_close", String(5), nullable=False),
    Column("commission_per_side", Float, nullable=False),
    Column("fees_per_side", Float, nullable=False),
    Column("initial_margin", Float, nullable=False),
    Column("sizing_contract", Boolean, nullable=False),
    Column("currency", CHAR(3), nullable=False),
)


DAILY_PRICES = Table(
    "daily_prices",
    metadata,
    Column("symbol", String),
    Column("date", Date),
    Column("open", Float),
    Column("high", Float),
    Column("low", Float),
    Column("close", Float),
    Column("volume", BigInteger),
    PrimaryKeyConstraint("symbol", "date"),
    Index("idx_daily_prices_date", "date"),
    Index("idx_daily_prices_symbol", "symbol"),
)


PRICE_OBSERVATION = Table(
    "price_observation",
    metadata,
    Column("symbol", String, nullable=False),
    Column("event_time", DateTime(timezone=True), nullable=False),
    Column("known_at", DateTime(timezone=True), nullable=False),
    Column("source_name", String(64), nullable=False),
    Column("revision_id", String(128), nullable=False),
    Column("open", Float),
    Column("high", Float),
    Column("low", Float),
    Column("close", Float),
    Column("volume", BigInteger),
    PrimaryKeyConstraint("symbol", "event_time", "known_at", "source_name", "revision_id"),
    Index("ix_price_observation_symbol_event_known", "symbol", "event_time", "known_at"),
    Index("ix_price_observation_symbol_known", "symbol", "known_at"),
)


FX_OBSERVATION = Table(
    "fx_observation",
    metadata,
    Column("quote_symbol", String, nullable=False),
    Column("base_currency", String(8), nullable=False),
    Column("quote_currency", String(8), nullable=False),
    Column("event_time", DateTime(timezone=True), nullable=False),
    Column("known_at", DateTime(timezone=True), nullable=False),
    Column("source_name", String(64), nullable=False),
    Column("revision_id", String(128), nullable=False),
    Column("rate", Float, nullable=False),
    PrimaryKeyConstraint("quote_symbol", "event_time", "known_at", "source_name", "revision_id"),
    Index("ix_fx_observation_quote_event_known", "quote_symbol", "event_time", "known_at"),
    Index(
        "ix_fx_observation_ccy_event_known",
        "base_currency",
        "quote_currency",
        "event_time",
        "known_at",
    ),
)


DUKA_ORDER_FLOW = Table(
    "duka_order_flow",
    metadata,
    Column("symbol", String),
    Column("date", Date),
    Column("n_ticks", BigInteger),
    Column("avg_spread", Float),
    Column("max_spread", Float),
    Column("spread_std", Float),
    Column("tick_vol", Float),
    Column("vwap", Float),
    Column("high", Float),
    Column("low", Float),
    Column("close", Float),
    Column("upticks", BigInteger),
    Column("downticks", BigInteger),
    Column("tick_imbalance", Float),
    PrimaryKeyConstraint("symbol", "date"),
)


ETF_META = Table(
    "etf_meta",
    metadata,
    Column("symbol", String),
    Column("name", String),
    Column("wkn", String),
    Column("isin", String),
    Column("category", String),
    Column("notes", String),
    Column("_updated_at", DateTime(timezone=False)),
    PrimaryKeyConstraint("symbol"),
)


ETF_PRICES = Table(
    "etf_prices",
    metadata,
    Column("symbol", String),
    Column("date", Date),
    Column("open", Float),
    Column("high", Float),
    Column("low", Float),
    Column("close", Float),
    Column("volume", BigInteger),
    PrimaryKeyConstraint("symbol", "date"),
    Index("idx_etf_prices_date", "date"),
    Index("idx_etf_prices_symbol", "symbol"),
)


FACTOR_DEFINITIONS = Table(
    "factor_definitions",
    metadata,
    Column("factor_id", String(16)),
    Column("label", String(64)),
    Column("family", String(32)),
    Column("description", Text),
    Column("source", String(128)),
    Column("region", String(32)),
    Column("frequency", String(16)),
    Column("sign_convention", Text),
    Column("_updated_at", DateTime(timezone=False)),
    PrimaryKeyConstraint("factor_id"),
)


FACTOR_RETURNS = Table(
    "factor_returns",
    metadata,
    Column("factor_id", String(16)),
    Column("date", Date),
    Column("ret", Float, nullable=False),
    Column("_updated_at", DateTime(timezone=False)),
    PrimaryKeyConstraint("factor_id", "date"),
    Index("ix_factor_returns_factor_date", "factor_id", "date"),
)


FAMA_FRENCH_FACTORS = Table(
    "fama_french_factors",
    metadata,
    Column("date", Date),
    Column("mkt_rf", Float),
    Column("smb", Float),
    Column("hml", Float),
    Column("rf", Float),
    Column("_updated_at", DateTime(timezone=False)),
    PrimaryKeyConstraint("date"),
)


FMP_ANALYST_ESTIMATES = Table(
    "fmp_analyst_estimates",
    metadata,
    Column("symbol", String),
    Column("date", Date),
    Column("estimated_revenue_avg", Float),
    Column("estimated_revenue_high", Float),
    Column("estimated_revenue_low", Float),
    Column("estimated_ebitda_avg", Float),
    Column("estimated_eps_avg", Float),
    Column("estimated_eps_high", Float),
    Column("estimated_eps_low", Float),
    Column("number_analysts_estimated_revenue", Integer),
    Column("number_analysts_estimated_eps", Integer),
    Column("_updated_at", DateTime(timezone=False)),
    PrimaryKeyConstraint("symbol", "date"),
)


FMP_ANALYST_GRADES = Table(
    "fmp_analyst_grades",
    metadata,
    Column("symbol", String),
    Column("date", Date),
    Column("strong_buy", Integer),
    Column("buy", Integer),
    Column("hold", Integer),
    Column("sell", Integer),
    Column("strong_sell", Integer),
    Column("_updated_at", DateTime(timezone=False)),
    PrimaryKeyConstraint("symbol", "date"),
)


FMP_BALANCE_SHEET = Table(
    "fmp_balance_sheet",
    metadata,
    Column("symbol", String),
    Column("date", Date),
    Column("fiscal_year", Integer),
    Column("period", String),
    Column("total_assets", Float),
    Column("total_debt", Float),
    Column("total_equity", Float),
    Column("cash_and_equivalents", Float),
    Column("total_current_assets", Float),
    Column("total_current_liabilities", Float),
    Column("filing_date", Date),
    Column("_updated_at", DateTime(timezone=False)),
    PrimaryKeyConstraint("symbol", "date", "period"),
)


FMP_CASH_FLOW = Table(
    # Fresh-path acquisition (R3.1/R3.2).
    # The primary key INCLUDES filing_date: a restatement lands as a NEW row and
    # the as-of read bounds on filing_date, so history is never rewritten.
    "fmp_cash_flow",
    metadata,
    Column("symbol", String),
    Column("date", Date),
    Column("fiscal_year", Integer),
    Column("period", String),
    Column("operating_cash_flow", Float),
    Column("capital_expenditure", Float),
    Column("free_cash_flow", Float),
    Column("stock_based_compensation", Float),
    # Joined from /income-statement on (date, period) at fetch time (the
    # key-metrics /ratios join precedent) — the daily market-cap series the
    # owner-yield leg needs is close × these FY-constant shares.
    Column("weighted_average_shares", Float),
    Column("filing_date", Date),
    Column("_updated_at", DateTime(timezone=False)),
    PrimaryKeyConstraint("symbol", "date", "period", "filing_date"),
)


FMP_COT = Table(
    "fmp_cot",
    metadata,
    Column("symbol", String),
    Column("date", Date),
    Column("short_name", String),
    Column("sector", String),
    Column("current_long_all", Float),
    Column("current_short_all", Float),
    Column("change_long_all", Float),
    Column("change_short_all", Float),
    Column("pct_long_all", Float),
    Column("pct_short_all", Float),
    Column("net_position", Float),
    Column("_updated_at", DateTime(timezone=False)),
    PrimaryKeyConstraint("symbol", "date"),
)


FMP_COT_ANALYSIS = Table(
    "fmp_cot_analysis",
    metadata,
    Column("symbol", String),
    Column("date", Date),
    Column("current_long", Float),
    Column("current_short", Float),
    Column("net_position", Float),
    Column("change_long", Float),
    Column("change_short", Float),
    Column("reversal_trend", String),
    Column("net_position_trend", String),
    Column("market_situation", String),
    Column("_updated_at", DateTime(timezone=False)),
    PrimaryKeyConstraint("symbol", "date"),
)


FMP_EARNINGS_CALENDAR = Table(
    # Fresh-path acquisition (R3.1). The
    # earnings/ex-earnings provenance split (R12.8) reads announcement dates here.
    "fmp_earnings_calendar",
    metadata,
    Column("symbol", String),
    Column("date", Date),
    Column("eps_actual", Float),
    Column("eps_estimate", Float),
    Column("revenue_actual", Float),
    Column("revenue_estimate", Float),
    Column("_updated_at", DateTime(timezone=False)),
    PrimaryKeyConstraint("symbol", "date"),
)


FMP_ECONOMIC_CALENDAR = Table(
    "fmp_economic_calendar",
    metadata,
    Column("date", Date),
    Column("time", String),
    Column("country", String),
    Column("event", String),
    Column("actual", Float),
    Column("estimate", Float),
    Column("previous", Float),
    Column("impact", String),
    Column("_updated_at", DateTime(timezone=False)),
    PrimaryKeyConstraint("date", "country", "event"),
)


FMP_ECONOMIC_INDICATORS = Table(
    "fmp_economic_indicators",
    metadata,
    Column("name", String),
    Column("date", Date),
    Column("value", Float),
    Column("_updated_at", DateTime(timezone=False)),
    PrimaryKeyConstraint("name", "date"),
)


FMP_ETF_HOLDINGS = Table(
    "fmp_etf_holdings",
    metadata,
    Column("symbol", String),
    Column("date", Date),
    Column("asset", String),
    Column("shares", Float),
    Column("weight_percentage", Float),
    Column("market_value", Float),
    Column("_updated_at", DateTime(timezone=False)),
    PrimaryKeyConstraint("symbol", "date", "asset"),
)


FMP_GRADE_ACTIONS = Table(
    "fmp_grade_actions",
    metadata,
    Column("symbol", String),
    Column("date", Date),
    Column("grading_company", String),
    Column("previous_grade", String),
    Column("new_grade", String),
    Column("action", String),
    Column("_updated_at", DateTime(timezone=False)),
    PrimaryKeyConstraint("symbol", "date", "grading_company"),
)


FMP_INCOME_STATEMENT = Table(
    "fmp_income_statement",
    metadata,
    Column("symbol", String),
    Column("date", Date),
    Column("fiscal_year", Integer),
    Column("period", String),
    Column("revenue", Float),
    Column("gross_profit", Float),
    Column("ebitda", Float),
    Column("operating_income", Float),
    Column("net_income", Float),
    Column("eps", Float),
    Column("eps_diluted", Float),
    Column("gross_profit_ratio", Float),
    Column("operating_income_ratio", Float),
    Column("net_income_ratio", Float),
    Column("filing_date", Date),
    Column("_updated_at", DateTime(timezone=False)),
    PrimaryKeyConstraint("symbol", "date", "period"),
)


FMP_INSIDER_TRADING = Table(
    "fmp_insider_trading",
    metadata,
    Column("symbol", String),
    Column("filing_date", Date),
    Column("transaction_date", Date),
    Column("transaction_type", String),
    Column("securities_transacted", Float),
    Column("price", Float),
    Column("reporting_name", String),
    Column("type_of_owner", String),
    Column("acquisition_or_disposition", String),
    Column("_updated_at", DateTime(timezone=False)),
    PrimaryKeyConstraint("symbol", "filing_date", "reporting_name", "transaction_type"),
)


FMP_INSTITUTIONAL_OWNERSHIP = Table(
    "fmp_institutional_ownership",
    metadata,
    Column("symbol", String),
    Column("year", Integer),
    Column("quarter", Integer),
    Column("filing_date", Date),
    Column("n_holders", Integer),
    Column("n_new", Integer),
    Column("n_sold_out", Integer),
    Column("n_increased", Integer),
    Column("n_decreased", Integer),
    Column("total_shares", BigInteger),
    Column("net_shares_change", BigInteger),
    Column("net_flow_pct", Float),
    Column("_updated_at", DateTime(timezone=False)),
    PrimaryKeyConstraint("symbol", "year", "quarter"),
)


FMP_KEY_METRICS = Table(
    "fmp_key_metrics",
    metadata,
    Column("symbol", String),
    Column("date", Date),
    Column("fiscal_year", Integer),
    Column("period", String),
    Column("market_cap", Float),
    Column("enterprise_value", Float),
    Column("pe_ratio", Float),
    Column("ev_to_ebitda", Float),
    Column("ev_to_sales", Float),
    Column("roic", Float),
    Column("roe", Float),
    Column("roa", Float),
    Column("current_ratio", Float),
    Column("debt_to_equity", Float),
    Column("fcf_per_share", Float),
    Column("fcf_yield", Float),
    Column("dividend_yield", Float),
    Column("payout_ratio", Float),
    Column("revenue_per_share", Float),
    Column("book_value_per_share", Float),
    Column("_updated_at", DateTime(timezone=False)),
    PrimaryKeyConstraint("symbol", "date", "period"),
)


FMP_NEWS = Table(
    "fmp_news",
    metadata,
    Column("symbol", String),
    Column("published_date", DateTime(timezone=False)),
    Column("title", Text),
    Column("publisher", String),
    Column("_updated_at", DateTime(timezone=False)),
    PrimaryKeyConstraint("symbol", "published_date", "title"),
)


FMP_POSITIONS_SUMMARY = Table(
    "fmp_positions_summary",
    metadata,
    Column("symbol", String),
    Column("year", Integer),
    Column("quarter", Integer),
    Column("investors_holding", Integer),
    Column("investors_holding_change", Integer),
    Column("new_positions", Integer),
    Column("closed_positions", Integer),
    Column("increased_positions", Integer),
    Column("reduced_positions", Integer),
    Column("put_call_ratio", Float),
    Column("put_call_ratio_change", Float),
    Column("total_invested_change", Float),
    Column("ownership_pct_change", Float),
    Column("_updated_at", DateTime(timezone=False)),
    PrimaryKeyConstraint("symbol", "year", "quarter"),
)


FMP_SECTOR_PERFORMANCE = Table(
    "fmp_sector_performance",
    metadata,
    Column("date", Date),
    Column("sector", String),
    Column("change_percentage", Float),
    Column("_updated_at", DateTime(timezone=False)),
    PrimaryKeyConstraint("date", "sector"),
)


FMP_TREASURY_RATES = Table(
    "fmp_treasury_rates",
    metadata,
    Column("date", Date),
    Column("month1", Float),
    Column("month2", Float),
    Column("month3", Float),
    Column("month6", Float),
    Column("year1", Float),
    Column("year2", Float),
    Column("year3", Float),
    Column("year5", Float),
    Column("year7", Float),
    Column("year10", Float),
    Column("year20", Float),
    Column("year30", Float),
    Column("_updated_at", DateTime(timezone=False)),
    PrimaryKeyConstraint("date"),
)


FRED_SERIES = Table(
    "fred_series",
    metadata,
    Column("series_id", String),
    Column("date", Date),
    Column("value", Float),
    Column("_updated_at", DateTime(timezone=False)),
    PrimaryKeyConstraint("series_id", "date"),
)


HOURLY_PRICES = Table(
    "hourly_prices",
    metadata,
    Column("symbol", String),
    Column("timestamp", DateTime(timezone=False)),
    Column("open", Float),
    Column("high", Float),
    Column("low", Float),
    Column("close", Float),
    Column("volume", Float),
    PrimaryKeyConstraint("symbol", "timestamp"),
)


INTRADAY_PRICES = Table(
    "intraday_prices",
    metadata,
    Column("symbol", String),
    Column("datetime", DateTime(timezone=False)),
    Column("open", Float),
    Column("high", Float),
    Column("low", Float),
    Column("close", Float),
    Column("volume", BigInteger),
    Column("interval", String),
    PrimaryKeyConstraint("symbol", "datetime", "interval"),
    Index("idx_intraday_dt", "datetime"),
)


ORDER_FLOW_DAILY = Table(
    "order_flow_daily",
    metadata,
    Column("symbol", String),
    Column("date", Date),
    Column("total_volume", BigInteger),
    Column("n_trades", BigInteger),
    Column("avg_trade_size", Float),
    Column("vwap", Float),
    Column("avg_spread", Float),
    PrimaryKeyConstraint("symbol", "date"),
)


SYMBOLS = Table(
    "symbols",
    metadata,
    Column("symbol", String),
    PrimaryKeyConstraint("symbol"),
)


# Every owned table keyed by name (``metadata.tables`` is the authority).
TABLES = metadata.tables
