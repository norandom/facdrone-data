# Changelog

All notable changes to this module are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the module follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html) as described in
`docs/versioning.md`.

## [Unreleased]

## [1.0.0] - 2026-10-28

The first release: `facdrone-data` 1.0.0 and `facdrone-data-types` 1.0.0, released together
(`facdrone-data` requires `facdrone-data-types==1.0.0`). The wheels and sdists are attached to
the release; nothing is published to a package index.

### Added

- `facdrone-data` command line with `download`, `refetch`, `update`, `quality` and `status`.
- A module configuration file naming the universe, the identity files and the default
  family selector, with an example in `examples/`.
- Credential sources (environment or one named file), the database address from `--dsn` or
  `FACDRONE_DATA_DSN`, and redaction of keys from logs and error text.
- Optional extras: `kedro`, `fmp` and `dukascopy`. A family whose extra is missing is
  reported as unavailable and the download continues.
- Apache-2.0 `LICENSE`, `NOTICE` and the licence header on every Python file, checked by
  `tools/check_headers.py`.
- README, point-in-time rules, a guide to adding a family, credentials and versioning
  documents.
- The module's own migration chain (`facdrone_data/migrations`, revision `fd0001_baseline`,
  its version in `facdrone_data_alembic_version`) and `facdrone-data db upgrade|current|check|stamp`;
  a database that already holds the tables is adopted with `db check` then `db stamp`.
- A pytest plugin with an embedded PostgreSQL per test process (`facdrone_data.testing`, extra
  `testing`).
- The instrument tables key on `venue_symbol`, the venue's symbol for the instrument.
- One provider policy table (`facdrone_data.providers.policy`): every adapter reads its
  timeout and retries from it.
- The FX types live in `facdrone_data_types.fx`; `facdrone_data.fx` re-exports them.
- `InstrumentTable.without_intraday()` names the rows declared `intraday: unavailable`, so a
  reader can leave them out of its intraday scope. The field was documentation only before;
  a value other than `fmp` or `unavailable` is refused at the file boundary.
- An identity row may declare `intraday_zone`, the IANA zone its intraday bars are stamped
  in (the canonical key only; refused on a row declared `intraday: unavailable`).
  `InstrumentTable.intraday_zone()` returns it, or the provider's declared zone for a row
  without one; `InstrumentTable.intraday_zones()` builds the `IntradayZones` resolver. The
  shipped `cfd.yml` declares the nine non-US cash indices (London, Berlin, Amsterdam, Paris,
  Madrid, Tokyo, Hong Kong, Sydney).
- The FMP client tells an empty answer from a failure: `fmp_records` returns `[]` only when
  FMP answered with no records and raises `FmpRequestError` on a transport or HTTP fault;
  the price client raises `ProviderAnsweredEmpty` (`facdrone_data_types.ports`) when every
  request was answered empty. A failed intraday window no longer discards the other
  windows' bars.

### Changed

- `intraday_timestamp` and `intraday_or_skip` take the zone the stamp was written in
  instead of a provider; `provider_zone(provider)` reads a provider's declared default.
- The live provider clients are split by concern (routing client, the FMP families, the
  factor-return clients); the providers, their order, routes and keys are unchanged.

### Fixed

- An empty provider answer is `provider_empty`, not a provider outage: when every requested
  gap was answered empty, the refresher records `ProviderOutcome.PROVIDER_EMPTY` and
  `AcquisitionOutcome.failed` does not count it, so a closed market no longer fails a
  scheduled refresh. Any failed gap still reads `provider_unavailable`.
- Intraday completeness is judged in each symbol's own zone. A bar stamped in Frankfurt or
  Sydney time was read as New York time and looked unfinished for hours after it closed.
  Stored openings are unchanged.
- A family without a date axis lands with no cursor; the refresh no longer stops on a
  symbol-keyed family such as `etf_meta`.

### Not yet present

- The generated schema reference and entity-relationship documents for the module's tables.
