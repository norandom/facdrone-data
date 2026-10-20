# Point-in-time rules

A point-in-time read answers one question: what could a reader have known at a given moment.
This page states the rules the store applies and works four examples, one per kind of
family. Every Python block below is executed by the test suite and needs no database.

## The rules

**Knowledge date.** Every value has an event date (the day it describes) and a knowledge
date (the first moment a reader could have had it). They differ for a macro release, a
weekly report and anything computed from a full day of data. Evidence rows store both
(`event_date`, `knowledge_date`), and a row whose knowledge date is earlier than its event
date is rejected when it is built.

**Publication delay.** The market panels (`daily_prices`, `fred_series`, `cftc_cot`,
`duka_order_flow` and the others) store the event date as `date` and carry no knowledge
column. The write-time gate stamps a transient `knowledge_date` from the family's
availability column and fails the write if it precedes the event date, then drops it; every
family listed above uses `date` as that column. A reader who needs the delay applies it: `facdrone_data.freshness.publication_rules.publication_moment(rule, observation)`
returns the UTC moment an observation becomes knowable under a declared rule, and the
examples below use it to choose the as-of date.

**Reads.** `as_of_select(table, time_column=...)` builds `SELECT * FROM table WHERE
time_column <= :as_of` with `as_of` as a bound parameter. The time column is required and
has no default, because it differs per table. `point_in_time_view(frame, as_of)` filters an
evidence frame to rows with `knowledge_date <= as_of` and, when the frame has a `matures_on`
column, to rows whose forward window has closed by the as-of date. It raises on a naive
`as_of`.

**Revisions.** The panels merge with `UpsertPanel`: a re-fetched key replaces the stored
values, and `_updated_at` records the write. A panel therefore holds the latest revision,
not the history of revisions. The evidence ledger is append-only and first-write-wins: a
second write to an existing key is counted as a conflict and the stored row stays.
Changing an evidence value means writing a new `factor_version`.

**First-write-wins.** `merge_panel` and `EvidenceLedger.append` insert with `ON CONFLICT DO
NOTHING`, so repeating a merge writes nothing and preserves earlier rows.

## Example 1: daily prices

A daily bar for date D is final after the close of D. Reading as of D therefore includes
a bar that may still be forming if the read happens during D. A decision taken before the
close should read as of the previous day.

```python
from datetime import date

from facdrone_data.acquisition.queries import as_of_select
from facdrone_data.acquisition.schema import DAILY_PRICES

statement = as_of_select(DAILY_PRICES, time_column="date")
sql = str(statement.compile())
assert "daily_prices.date <= :as_of" in sql
assert "2025" not in sql  # the date travels as a bound parameter, never in the text
parameters = {"as_of": date(2025, 1, 8)}  # pass to the connection with the statement
print(sql)
```

Executed with `pd.read_sql(statement, connection, params=parameters)`, this returns every
stored bar with `date <= 2025-01-08`, for all symbols.

## Example 2: a macro release

A FRED observation dated with the period it describes is published later. Suppose a series
is published on the next business day at 13:00 UTC (the rule values are illustrative, not
a statement about a particular series). The newest observation a reader at a given moment
may use is the latest one whose publication moment is at or before that moment.

```python
from dataclasses import dataclass
from datetime import UTC, date, datetime, time

from sqlalchemy import select

from facdrone_data.acquisition.queries import as_of_select
from facdrone_data.acquisition.schema import FRED_SERIES
from facdrone_data.freshness.publication_rules import expected_observation, publication_moment


@dataclass(frozen=True)
class Rule:
    kind: str
    publish_after_utc: time | None = None
    release_weekday: int | None = None
    release_time: time | None = None
    release_timezone: str | None = None
    as_of_offset_days: int = 0
    holiday_tolerance_days: int = 0
    bar_minutes: int | None = None


rule = Rule(kind="next_business_day", publish_after_utc=time(13, 0))

# Friday's observation is published on Monday at 13:00 UTC.
assert publication_moment(rule, date(2025, 1, 10)) == datetime(2025, 1, 13, 13, 0, tzinfo=UTC)

# A reader at Monday 09:00 UTC may use Thursday's observation, not Friday's.
reader_at = datetime(2025, 1, 13, 9, 0, tzinfo=UTC)
usable = expected_observation(rule, reader_at, calendar=[])
assert usable == date(2025, 1, 9)

statement = as_of_select(FRED_SERIES, time_column="date").where(FRED_SERIES.c.series_id == "DGS10")
assert "fred_series.date <= :as_of" in str(statement.compile())
parameters = {"as_of": usable}
print(usable)
```

The as-of date passed to the bound parameter is the usable observation date, not the
reader's calendar date. Reading as of the calendar date would return Friday's value on
Monday morning, before it was published.

## Example 3: a weekly positioning report

The CFTC Commitments of Traders report describes positions on a Tuesday and is released
on the Friday after. The `cftc_cot.date` column is the Tuesday. A `weekly_release` rule
carries the release weekday, time and timezone, and the offset in days from the
observation to the release (illustrative values: Friday is weekday 4, the offset is 3 days).

```python
from dataclasses import dataclass
from datetime import UTC, date, datetime, time

from facdrone_data.acquisition.queries import as_of_select
from facdrone_data.acquisition.schema import CFTC_COT
from facdrone_data.freshness.publication_rules import expected_observation, publication_moment


@dataclass(frozen=True)
class Rule:
    kind: str
    publish_after_utc: time | None = None
    release_weekday: int | None = None
    release_time: time | None = None
    release_timezone: str | None = None
    as_of_offset_days: int = 0
    holiday_tolerance_days: int = 0
    bar_minutes: int | None = None


rule = Rule(
    kind="weekly_release",
    release_weekday=4,
    release_time=time(15, 30),
    release_timezone="America/New_York",
    as_of_offset_days=3,
)

tuesday = date(2025, 1, 7)
assert publication_moment(rule, tuesday) == datetime(2025, 1, 10, 20, 30, tzinfo=UTC)

# On the Thursday before, the Tuesday report is not out; the previous Tuesday's is.
thursday = datetime(2025, 1, 9, 12, 0, tzinfo=UTC)
assert expected_observation(rule, thursday, calendar=[]) == date(2024, 12, 31)
# After the Friday release it is.
saturday = datetime(2025, 1, 11, 12, 0, tzinfo=UTC)
assert expected_observation(rule, saturday, calendar=[]) == tuesday

statement = as_of_select(CFTC_COT, time_column="date")
parameters = {"as_of": expected_observation(rule, saturday, calendar=[])}
print(parameters["as_of"])
```

A weekly value read as of the Tuesday would have been visible three days before anyone
could have had it. Using the usable observation date as the as-of removes that lookahead.

## Example 4: order flow derived from ticks

`duka_order_flow` holds one row per symbol and day, aggregated from every tick of the day
(`n_ticks`, `avg_spread`, `tick_imbalance` and so on). The row for D is complete only
after the last tick of D. A factor computed from it has a knowledge date at the end of D,
and a forward-return label has a maturity date. Both are columns of an evidence frame, and
`point_in_time_view` applies both filters.

```python
from datetime import UTC, datetime

import pandas as pd

from facdrone_data.pit import point_in_time_view

evidence = pd.DataFrame(
    {
        "symbol": ["EURUSD", "EURUSD", "EURUSD"],
        "event_date": ["2025-01-06", "2025-01-07", "2025-01-08"],
        "value": [0.12, -0.05, 0.31],
        "knowledge_date": [
            datetime(2025, 1, 6, 23, 59, tzinfo=UTC),
            datetime(2025, 1, 7, 23, 59, tzinfo=UTC),
            datetime(2025, 1, 8, 23, 59, tzinfo=UTC),
        ],
        "matures_on": ["2025-01-08", "2025-01-09", "2025-01-10"],
    }
)

# At noon on 2025-01-08 the first two rows are known, and only the first has matured.
visible = point_in_time_view(evidence, datetime(2025, 1, 8, 12, 0, tzinfo=UTC))
assert list(visible["event_date"]) == ["2025-01-06"]
print(len(visible))
```

At noon on 2025-01-08 the row for 2025-01-07 is already known (its knowledge date is the
evening of the 7th) but its forward window closes on the 9th, so the view withholds it. The
row for the 8th is not yet known. Only the row for the 6th is returned.
