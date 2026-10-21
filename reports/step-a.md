# facdrone-data step (a): report and real-artifact check

Task 1.10 of `facdrone-data-manager`. Run on 2026-10-21 (download started 10:03:24 UTC) on the
operator host, workspace at `9fafc251`, with a FRED follow-up run at 10:07:29 UTC, by the module's own command line against an embedded
PostgreSQL 17.9 (the `pgembed` wheel's binaries, the same ones `facdrone_data.testing.pg_server`
uses), initialised in a scratch directory, listening on 127.0.0.1 on a free port, trust
authentication. The DSN carries no password. The operator database was not used.

## Commands

`<dsn>` is `postgresql+psycopg://postgres@127.0.0.1:<port>/facdrone_data`, passed as
`FACDRONE_DATA_DSN`. No credentials file was named and neither `FRED_API_KEY` nor
`FMP_API_KEY` was set.

```
facdrone-data db upgrade        # schema=fd0001_baseline, exit 0
facdrone-data db current        # schema=fd0001_baseline, exit 0
facdrone-data db check          # schema matches, exit 0
FACDRONE_DATA_CONFIG=<scratch>/config/facdrone-data.example.yml \
  facdrone-data download --source fred,yfinance                   # exit 3
facdrone-data status "^GSPC"; facdrone-data status "EURUSD=X"; facdrone-data status "GC=F"   # exit 0
facdrone-data quality --symbol <sym> --from 2021-10-22 --to 2026-10-21   # per symbol, exit 2
```

The configuration is `examples/facdrone-data.example.yml` and `examples/instruments.example.yml`,
copied unchanged: the identity file path is relative to the configuration file, so no path
needed adjusting. The example names no keyed provider. Its `default_source` is
`yfinance,fred`, and `--source fred,yfinance` selects the same families.

## Schema

The module's chain is at `fd0001_baseline`, recorded in `facdrone_data_alembic_version`. The
database holds 38 public tables: the 37 data tables and the version table. `db check`
reported no difference.

## Download

No `--from`/`--to` was given. On an empty panel this is a cold start, and the command printed
`resuming from the panel: 2021-10-22 .. 2026-10-21`.

| Family (writer) | Provider | Symbol / scope | Fetched | Inserted | Quarantined | Outcome |
|---|---|---|---|---|---|---|
| `refresh_daily_prices` | yfinance | EURUSD=X | 1299 | 1299 | 0 | provider_verified, cursor 2026-10-21 |
| `refresh_daily_prices` | yfinance | GC=F | 1257 | 1257 | 0 | provider_verified, cursor 2026-10-21 |
| `refresh_daily_prices` | yfinance | ^GSPC | 1253 | 1253 | 0 | provider_verified, cursor 2026-10-20 |
| `refresh_daily_prices` | yfinance | ^VIX | 0 | 0 | 0 | unmapped (skipped=1); `uncovered_symbol=^VIX` |
| `refresh_fred_series` | FRED | configured series | 0 | 0 | 0 | refused, `reason=FRED_API_KEY` (landed in the second run, see FRED below) |

Totals line: `families=4 fetched=3809 inserted=3809 updated=0 quarantined=0`. The count of 4 is
the four symbol lines of the price family, not four families.

- Attempted: 2 families (yfinance daily prices, FRED series).
- Landed: 1 family, `daily_prices`, 3809 rows. The database count matches: EURUSD=X 1299
  (2021-10-22..2026-10-21), GC=F 1257 (2021-10-22..2026-10-21), ^GSPC 1253
  (2021-10-22..2026-10-20).
- Refused: FRED, 0 rows in `fred_series`. FRED needs a key (free, but a key), and none was
  configured. The refusal came before any request, as `docs/credentials.md` describes. The
  run carried on and exited 3, as documented.
- Unavailable or outage: none. No provider error was seen, so no retry policy came into play.
- Not covered: `^VIX`, the example's `global_inputs` entry. It has no row in
  `instruments.example.yml`, so the yfinance family has no mapping for it. This is a gap in
  the example configuration, not a provider fault.

Wall time of the download command: 9.6 s (measured around the process).

Both runs together: 9.6 s (yfinance, FRED refused) + 27.0 s (FRED) = **36.6 s** of
download wall time. In total 19958 rows landed: 3809 in `daily_prices` and 16149 in
`fred_series`.

## Status and quality

`status` for each of the three symbols lists `daily_prices` as present, with gaps counted
against the trading calendar: ^GSPC 2021-10-22..2026-10-20 with 50 gaps, EURUSD=X
2021-10-22..2026-10-21 with 4, GC=F 2021-10-22..2026-10-21 with 47. The table is listed twice,
once under `refresh_daily_prices` and once under `refresh_fmp_daily`, because both families
write it. Every other family is `absent`. No provider was contacted.

`quality` over 2021-10-22..2026-10-21 on `daily_prices` exits 2 when any row is flagged. It
flagged rows for all three symbols:

| Symbol | Score | Findings | Notes |
|---|---|---|---|
| ^GSPC | 99.8404 | 2 | 2025-04-04 and 2025-04-07 (hampel, isolation_forest; consensus 0.5) |
| EURUSD=X | 99.6151 | 5 | highest consensus 0.65 on 2022-03-07 |
| GC=F | 97.7725 | 28 | runs in 2025-09, 2025-10, 2026-01 and 2026-08 (robust_z, flash_crash and others; highest consensus 0.8 on 2026-01-28) |

The flagged dates fall on known large market moves (the April 2025 tariff selloff, the gold
rallies of 2025 and 2026). They read as real moves, not as corrupt rows. Download ran with
the price family's default `quarantine` policy (`QuarantineUnmeasurable`). That policy drops
only rows with no measurable price, such as a non-positive close, and keeps every statistical
finding for the operator. No row was quarantined at write time (`quarantined=0`).

## FRED (second run, with the operator's key)

The first run had no key, so FRED was refused. A second run on the same embedded database
(server restarted on the surviving data directory; `db current` printed `fd0001_baseline`
and `db check` printed `schema matches`) named the operator's credentials file through the
module's own option. The file was read only by the module's `FileCredentials`, which takes
the key fields and no database address. The DSN still came from `FACDRONE_DATA_DSN` (the
embedded server).

```
FACDRONE_DATA_CONFIG=<scratch>/config/facdrone-data.example.yml \
  facdrone-data --credentials <the operator's credentials file, outside the module> \
  download --source fred                                           # exit 0
facdrone-data status VIXCLS                                        # exit 0
facdrone-data quality --symbol VIXCLS --from 2021-10-22 --to 2026-10-21 --family fred_series   # exit 2, usage
facdrone-data quality --symbol VIXCLS --from 2021-10-22 --to 2026-10-21                       # exit 0
```

Started 2026-10-21 10:07:29 UTC. The window was resumed as 2021-10-22 .. 2026-10-21, the same
cold-start window. The resume point is read from the universe's price panel, and ^VIX
still had no row there, so the run started cold. Wall time 27.0 s. A check
through the module's loader confirmed that the key value appears in neither stdout nor stderr
(stderr was empty).

Family `refresh_fred_series`: 25 series. Each one was `provider_verified`, with
quarantined=0 and updated=0.

| Series | Fetched | Inserted | Cursor |
|---|---|---|---|
| VIXCLS | 1285 | 1285 | 2026-10-19 |
| BAMLH0A0HYM2 | 784 | 784 | 2026-10-19 |
| BAMLC0A0CM | 783 | 783 | 2026-10-19 |
| T10Y2Y | 1247 | 1247 | 2026-10-20 |
| DFF | 1824 | 1824 | 2026-10-19 |
| DGS10 | 1246 | 1246 | 2026-10-19 |
| DGS2 | 1246 | 1246 | 2026-10-19 |
| CPIAUCSL | 59 | 59 | 2026-09-01 |
| CP0000EZ19M086NEST | 60 | 60 | 2026-09-01 |
| GBRCPIALLMINMEI | 42 | 42 | 2025-03-01 |
| AUSCPIALLQINMEI | 14 | 14 | 2025-01-01 |
| CANCPIALLMINMEI | 42 | 42 | 2025-03-01 |
| CHECPIALLMINMEI | 43 | 43 | 2025-04-01 |
| ECBMRRFR | 1825 | 1825 | 2026-10-20 |
| ECBESTRVOLWGTTRMDMNRT | 1279 | 1279 | 2026-10-20 |
| IUDSOIA | 1259 | 1259 | 2026-10-19 |
| IR3TIB01JPM156N | 59 | 59 | 2026-08-01 |
| IR3TIB01AUM156N | 59 | 59 | 2026-08-01 |
| IR3TIB01CAM156N | 60 | 60 | 2026-09-01 |
| IR3TIB01NZM156N | 60 | 60 | 2026-09-01 |
| IR3TIB01CHM156N | 60 | 60 | 2026-09-01 |
| INDPRO | 60 | 60 | 2026-09-01 |
| T10YIE | 1247 | 1247 | 2026-10-20 |
| DTWEXBGS | 1246 | 1246 | 2026-10-16 |
| NFCI | 260 | 260 | 2026-10-09 |

Totals line: `families=25 fetched=16149 inserted=16149 updated=0 quarantined=0`. A database
count gives 16149 rows in `fred_series` over 25 distinct `series_id`. The run also printed
`uncovered_symbol=` for EURUSD=X, GC=F, ^GSPC and ^VIX. That is expected with
`--source fred`, because the price family was not selected and FRED does not price those
symbols. The 3809 `daily_prices` rows from the first run are untouched.

The OECD family (`refresh_oecd_japan_cpi`) shares `fred_series` but is its own provider
(`oecd`), and `--source fred` did not select it.

Status, `facdrone-data status VIXCLS`: `fred_series` present, 2021-10-01 .. 2026-10-20, 1
gap. The table is listed under `refresh_fred_series` and again under
`refresh_oecd_japan_cpi`, because both families write it. Every other family is absent.

Quality: the module's quality command reads price tables only (`daily_prices`,
`hourly_prices`, `intraday_prices`). `--family fred_series` is refused with
`unknown --family 'fred_series'` (exit 2, a usage error), so no quality verdict exists for a
FRED series through the command line. Without `--family`, the command reads `daily_prices`,
which holds no VIXCLS row, and prints `score=100.0 findings=0`. That score covers zero rows,
not a clean series. The command reports a perfect score over an empty selection rather than
saying there are no rows, which may be worth a follow-up.

## T1

The T1 probe was not run. No file of the consuming project's measured path changed in this task. The pin stays
`c164dc0f695580d15fafa94e80055d3ca9828de03dab2504c1dc059d1402d2a4`, as recorded in
`.kiro/steering/tech.md` and last measured 2026-10-20 11:59 (task 2.1).

## Code lines moved from the core

The step (a) landing commit is `a1bdaa7b` (2026-10-15). Its `git show --stat` reports 295
files changed, with 3029 insertions and 949 deletions. Lines were measured with
`scripts/complexity_caps.code_lines` (not blank, not a comment, not inside a docstring) on
`git archive` trees of `a1bdaa7b^` and `a1bdaa7b`:

| Area | Before (`a1bdaa7b^`) code / physical | After (`a1bdaa7b`) code / physical | Change in code lines |
|---|---|---|---|
| core `src/facdrone` | 45462 / 62671 | 45319 / 62465 | -143 |
| data `facdrone_data` | 8651 / 13184 | 9415 / 14361 | +764 |
| types `facdrone_data_types` | 0 / 0 | 193 / 384 | +193 |
| kernel `facdrone_kernel` | 4212 / 6612 | 4030 / 6273 | -182 |

These match the commit message's own figures to within 5 lines. The message gives data
8651 -> 9410 and core 62671 -> 62460 physical.

What left the core:

- `src/facdrone/shells/wiring/download_execution.py` was deleted. The stat shows it as a
  rename to `packages/facdrone-data/facdrone_data/runner.py` (+153 lines changed). Before the
  move it held 185 code lines (288 physical).
- The data command bodies in `src/facdrone/shells/cli/app.py` went from 98 lines changed to a
  delegation to `facdrone-data`. The file's total barely moved (797 -> 796 code lines), because
  the delegation and identity wiring replaced what was removed.
- The smoke pipeline moved the other way, from the module back to `src/facdrone/pipelines/smoke`.

Under `src/`, numstat shows 319 lines added and 525 deleted. Most of the remainder is
one-line import rewrites to `facdrone_data_types`.

What grew in the data unit: the `facdrone-data` command (`cli/app.py` 180, `cli/commands.py`
107, `cli/compose.py` 110, `cli/render.py` 79 physical lines added), `config.py` (71),
`credentials.py` (147) and the runner. The kernel's moved types became the types package.

The data unit was measured again today (2026-10-21, `9fafc251`) after the later step (b) and
(c) work. `facdrone_data` has 8769 code lines (13516 physical, 94 files) and
`facdrone_data_types` has 351 (734 physical). The module's `tools/size_gate.py` reports
`code_lines 9608 -> 9120`, `physical_lines 14945 -> 14250`, `OK, no growth`, against
`.gates/size-baseline.json`. Since step (a), later work has taken the data unit below the
size it reached at that landing.

## Open items

- FRED: done in the second run with the operator's key (above). The free families need no
  key except FRED, which needs a free key. The README states this, and the first run
  confirmed it.
- ^VIX: the example configuration named `^VIX` in `global_inputs` without an identity row.
  A patch adds the row to `examples/instruments.example.yml`, declared the way the `^GSPC`
  row is (`kind: cfd`, `asset_class: indices`, `settlement_ccy: USD`,
  `providers: {fmp: null, yfinance: "^VIX"}`, `intraday: unavailable`). An identity row has
  no venue-symbol field (`_Row` forbids extra fields), so nothing blocks declaring it, and
  removing ^VIX from the config was not needed. With the row, the example table covers all
  four symbols and `ProviderSymbols.yfinance_fallback("^VIX")` returns `^VIX`. Before, FMP
  claimed it by identity fallback and yfinance skipped it as unmapped. The module's
  `tests/test_documents.py` and `tests/test_data_cli.py` pass with the row (22 passed). No
  live download of ^VIX was run after the patch.
- `quality` has no FRED (series-table) mode, and it scores an empty selection as 100.0.
- Operator checkpoint, unchanged: the `data_max` / `data_loc` cap decision in `caps.toml`.
- `scripts/gates.sh` was not run in this task. No repository file was changed.
