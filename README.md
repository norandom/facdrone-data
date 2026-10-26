# facdrone-data

A generic point-in-time market-data store in PostgreSQL. It downloads prices, macro series,
positioning reports and order-flow aggregates from public and paid providers, writes them
idempotently into one schema, and reads them back as of a chosen date so that a backtest
cannot see a value before it was knowable. The facdrone trading research system is its first
consumer; nothing in the module depends on it.

What it contains:

- download families, one per provider dataset, each with a normaliser, a resume cursor and a
  write-time quality gate (`facdrone_data/acquisition`);
- the provider adapters (`facdrone_data/providers`);
- the point-in-time filter (`facdrone_data.pit.point_in_time_view`) and the catalog
  dataset that applies it (`facdrone_data/datasets`);
- the store's own migration chain (`facdrone_data/migrations`, `facdrone-data db ...`);
- a pytest plugin with an embedded PostgreSQL per test process (`facdrone_data.testing`,
  extra `testing`);
- bars, quality detectors and freshness rules;
- the `facdrone-data` command line.

The schema has 37 tables: price panels, revisioned point-in-time observations, the provider
families and instrument reference data. The instrument tables key on `venue_symbol`, the
venue's symbol for the instrument.

## Install

Python 3.13 or later and a PostgreSQL server (the writes use `INSERT ... ON CONFLICT`, so
other databases are rejected).

```
pip install ./facdrone-data
```

(from a checkout or an unpacked release; see Package index below). The base install runs the families that need no paid subscription: Yahoo Finance (through
`yfinance`), FRED (free key), Kenneth French, CFTC and OECD. Extras:

| Extra | Adds | For |
|---|---|---|
| `kedro` | `kedro`, `kedro-datasets` | the Kedro pipeline wrapper and the catalog dataset |
| `fmp` | `requests` | the Financial Modeling Prep families (paid key; tested on the FMP Ultimate plan; endpoint coverage assumed on Ultimate, to be verified against the plan page) |
| `dukascopy` | `requests` | Dukascopy tick-derived order flow |
| `testing` | `pytest`, `pytest-xdist`, `pgembed` | the test plugin: `pytest_plugins = ["facdrone_data.testing.plugin"]` |

```
pip install "./facdrone-data[fmp,dukascopy]"
```

When a family's extra is not installed, the download reports it as `unavailable extra=<name>`
and carries on with the others.

### Package index

Releases are published as git tags with the built wheels attached as release assets; the
first release has not been tagged. The package is not published to PyPI, and a PyPI release
is a decision that has not been taken. Until it is, install from the release wheel or from a checkout:

```
pip install ./facdrone-data
```

## Environment variables

| Variable | Meaning | Command-line option |
|---|---|---|
| `FACDRONE_DATA_DSN` | PostgreSQL URL. There is no default. | `--dsn` |
| `FACDRONE_DATA_CONFIG` | Configuration file (universe, identity files, default source). | `--config` |
| `FACDRONE_DATA_CREDENTIALS_FILE` | YAML file holding the provider keys. | `--credentials` |
| `FRED_API_KEY` | FRED key; used when no credentials file is named. | none |
| `FMP_API_KEY` | Financial Modeling Prep key; used when no credentials file is named. | none |

An option on the command line wins over its variable. A run uses one credential source: the
file when one is named, otherwise the environment, never both. See `docs/credentials.md`.

## Quickstart

This creates a database, creates the tables, downloads one free family (Yahoo Finance daily
prices) for the example universe, and reads the stored prices as of a date. It needs a local
PostgreSQL server and network access to Yahoo Finance. Run it from the module directory so
the example configuration is found.

A URL without a password uses the libpq defaults (`PGUSER`, `PGPASSFILE`); a URL may also
carry the user and password.

```bash
createdb facdrone_data
export FACDRONE_DATA_DSN="postgresql+psycopg://localhost/facdrone_data"
export FACDRONE_DATA_CONFIG=examples/facdrone-data.example.yml
```

Create the tables and record the schema version:

```bash
facdrone-data db upgrade        # prints schema=fd0001_baseline
facdrone-data db current
```

The store records its revision in its own table, `facdrone_data_alembic_version`, so it
can share a database with a consumer's own migration chain. A database that already
holds the store's tables but no version table is refused: `facdrone-data db check` lists
every difference against the schema, and `facdrone-data db stamp fd0001_baseline` adopts
it once `check` reports none.

A consumer whose own Alembic environment applies this chain before its own revisions
inherits the same refusal, so on a database that already holds the store's tables the order
is: adopt first (`facdrone-data db check`, which must print `schema matches`, then
`facdrone-data db stamp fd0001_baseline`), then run the consumer's upgrade. The stamp writes
only the store's version table; the consumer's chain and its version table are untouched.

Download one window for the universe in the example configuration (`^GSPC`, `EURUSD=X`,
`GC=F`), then look at what is stored:

```bash
facdrone-data download --source yfinance --from 2025-01-06 --to 2025-01-10
facdrone-data status "^GSPC"
```

`download` prints one line per family and instrument, then a `families=` totals line. It
exits 0 on success, 3 when a family was refused for a missing key or is unavailable, 2 on a
usage error, and 1 on any other failure. Without `--from` and `--to` it resumes from the
stored panel and prints the window it chose.

Query a point-in-time view. `as_of_select` builds a statement with a bound `as_of`
parameter, so the date never enters the SQL text:

```python
import os
from datetime import date

import pandas as pd
from sqlalchemy import create_engine

from facdrone_data.acquisition.queries import as_of_select
from facdrone_data.acquisition.schema import DAILY_PRICES

engine = create_engine(os.environ["FACDRONE_DATA_DSN"])
statement = as_of_select(DAILY_PRICES, time_column="date")
with engine.connect() as connection:
    frame = pd.read_sql(statement, connection, params={"as_of": date(2025, 1, 8)})
print(frame.sort_values(["symbol", "date"]).to_string(index=False))
engine.dispose()
```

The rules behind the view, and three more worked examples, are in `docs/point_in_time.md`.

## Documents

- `docs/point_in_time.md`: knowledge date, publication delay, revisions, first-write-wins.
- `docs/adding_a_family.md`: what a new download family needs.
- `docs/credentials.md`: where keys and the database address come from.
- `docs/versioning.md` and `CHANGELOG.md`: version policy and release notes.

## Licence

Apache-2.0. See `LICENSE`. `NOTICE` names the copyright holder and the terms of the data
providers the adapters connect to. The module ships no provider data; data you download stays
under each provider's own terms, and several providers' data may not be redistributed.
[`docs/providers.md`](docs/providers.md) lists each provider, what is fetched, the plan assumed,
the credential variable and the terms reading, each marked to be verified.

