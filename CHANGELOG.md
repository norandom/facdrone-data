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

### Not yet present

- `facdrone-data db upgrade` and the module's own migration chain. Until then the tables are
  created from the declared schema (see the README quickstart).
- The generated schema reference and entity-relationship documents for the module's tables.
