# Adding a family

A family is one provider dataset written into one table. It is a `FamilyPort`
(`facdrone_data/acquisition/families/ports.py`) that binds a target table, a provider, a
resume cursor, a merge strategy and a normaliser. The refresher runs every registered port;
the registry is an explicit list, so a family exists only once it is registered. The steps
below follow the order a change is reviewed in.

## 1. The table

Declare it in `facdrone_data/acquisition/schema.py` on the shared `metadata`, with a primary
key. The primary key is the family's natural key: the registry refuses a port whose
`natural_key` differs from it. A panel keyed on an event date also carries `_updated_at`
(a `DateTime` column) when the write time should be recorded; the normaliser fills it from the
injected clock.

Migration: add a revision under `facdrone_data/migrations/versions/` (plain DDL, no import
of application metadata, `down_revision` the current head) and declare the table in
`facdrone_data/acquisition/schema.py`. `facdrone-data db check` must report no difference
on a database built by the chain. Under `docs/versioning.md` a schema change is a major
version.

Review rule (requirement 2.5): a revision that alters a table a consumer reads names the
consumer's dependent code in its docstring and its pull request. The first consumer reads
these tables (`CORE_READ_TABLES`, maintained from the consuming repository):
`cftc_cot`, `contract_specs`, `daily_prices`, `factor_returns`, `fmp_balance_sheet`,
`fmp_cash_flow`, `fmp_economic_calendar`, `fmp_income_statement`, `fred_series`,
`instrument_metadata_version`, `intraday_prices`.

## 2. The provider client

A client implements `MarketDataClient` (`facdrone_data_types.ports`): `datasets()` returns the
dataset names it serves and `fetch(FetchRequest)` returns a raw `DataFrame` for one provider
symbol and an inclusive date range. A new source gets a `Provider` member in
`facdrone_data_types/symbols.py`. Construct the client in
`facdrone_data/providers/live_clients.py`. A paid or optional source builds only when its key
is present, and its HTTP dependency goes into an extra in `pyproject.toml`. Share the timeout
and timezone defaults in `providers/policy.py` instead of copying them.

A key is read through `ProviderCredentials.require(provider)`, which fails before any request
when the key is missing. Add the key name to `KEY_VARIABLES` only for a new keyed provider.

## 3. The normaliser

A class with `normalize(raw, *, symbol, as_of) -> DataFrame` that returns exactly the target
table's columns. Use `project_to_table`, `coerce_date` and `stamp_updated_at` from
`families/normalizers/_base.py`. Clean at write time: drop unusable rows, deduplicate on the
natural key (a duplicate key inside one batch is an error in the upsert), and never call the
wall clock; `as_of` is the injected time.

## 4. The cursor

An `IncrementalStrategy` (`families/cursors.py`) turns the stored state and the wanted window
into the inclusive ranges still to fetch. `MinMaxRangeGapFill` fills gaps inside the stored
range, `MaxDateBySymbol` and `MaxDateGlobal` resume after the newest row, and `FullWindow`
always refetches the window. An empty result means no provider call.

## 5. The port and the gate

Build the `FamilyPort` in a `*_families()` function next to the normaliser:

- `merge_strategy`: `UpsertPanel()` replaces a revised key; `MergePanel()` keeps the first
  write. Choose by whether the provider revises history.
- `availability_column`: the column the write gate stamps `knowledge_date` from. The gate
  fails the write when it precedes the event date. `docs/point_in_time.md` explains why the
  panels use `date`.
- `quality_policy_default`: `"keep_all"` or `"quarantine"`. Quarantine runs the quality
  ensemble before the merge and drops contaminated rows.
- `symbol_mapper` for symbol-keyed families, or `scopes` for series-keyed ones.

## 6. Register it

Add the factory to the tuple in `download_families()` (`families/registry.py`). A duplicate
`v1_writer` name or a natural key that differs from the primary key raises when the registry is
built. Selecting by provider name (`--source`) and by `--exclude-family` then covers the new
family.

## 7. The tests

- The normaliser on a small raw frame: columns, dtypes, deduplication, the empty case.
- The port through the refresher on the embedded database with a fake client
  (see `tests/download_support.py`): rows land, a second run inserts nothing, the cursor advances.
- A key-missing run reports the family as refused, and a missing extra reports it as
  unavailable.
- The redaction test continues to hold: a request URL that carries a key appears in no log
  line, exception text or stored row.

Run `ruff format --check .`, `ruff check .`, `mypy` and `python tools/check_headers.py` from
the module directory. Every new Python file starts with the two licence header lines.
