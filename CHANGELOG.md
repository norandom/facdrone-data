# Changelog

All notable changes to this module are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the module follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html) as described in
`docs/versioning.md`.

## [Unreleased]

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

### Not yet present

- The generated schema reference and entity-relationship documents for the module's tables.
