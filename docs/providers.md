# Providers: what the module fetches, under which terms

Status: the plan and endpoint cells reflect the operator's decision of 2026-10-17 (the adapters
are tested on the FMP Ultimate plan). Every licence cell is a reading of the provider's public
terms as of 2026-10-17 and is marked **to be verified** against each provider's current terms
before a public release; nothing here is legal advice. The module ships no provider data.

Provider membership: the base install carries the keyless or free-key sources (Yahoo Finance
through `yfinance`, FRED, OECD, CFTC, Kenneth French, AQR); FMP and Dukascopy are extras.
Yahoo Finance data is for personal use under Yahoo's terms and AQR's library is for personal,
non-commercial use (both to be verified); the module reads them, it does not redistribute them.


| Provider | What the module fetches (target tables) | Plan or tier assumed | Rate limit handled today | Publication delay stated | Credential env var | Licence note |
| --- | --- | --- | --- | --- | --- | --- |
| Financial Modeling Prep (`FMP`), extra `[fmp]` | `/stable` API: EOD prices `/historical-price-eod/full` (`daily_prices`), intraday bars in request windows sized under FMP's ~3,000-bar cap (`intraday_prices`), fundamentals `/income-statement`, `/balance-sheet-statement`, `/cash-flow-statement`, `/key-metrics`, `/ratios`, `/earnings` (`fmp_income_statement`, `fmp_balance_sheet`, `fmp_cash_flow`, `fmp_key_metrics`, `fmp_earnings_calendar`), analyst `/analyst-estimates`, `/grades-historical`, `/grades` (`fmp_analyst_*`, `fmp_grade_actions`), ownership `/insider-trading/search`, `/institutional-ownership/...` (`fmp_insider_trading`, `fmp_institutional_ownership`, `fmp_positions_summary`), COT `/commitment-of-traders-report`, `/commitment-of-traders-analysis` (`fmp_cot`, `fmp_cot_analysis`), macro `/economic-calendar`, `/economic-indicators`, `/treasury-rates`, `/sector-performance-snapshot` (`fmp_economic_*`, `fmp_treasury_rates`, `fmp_sector_performance`), `/etf/holdings` (`fmp_etf_holdings`), `/news/stock` (`fmp_news`) | **FMP Ultimate** (operator 2026-10-17); endpoint coverage assumed on Ultimate, to be verified against the plan page. The adapters call the `/stable` endpoint family, and fundamentals, ownership, COT and intraday are not on FMP's free tier as far as research.md knows (to be verified) | Timeout 30 s per request; throttle (HTTP 429/503): 3 retries after 1, 2 and 4 s; transient fault: no retry; other status: no retry; no request-count ceiling | no fixed delay: per dataset, daily bars after the venue close, intraday bars when the bar closes, statements at their filing date | `FMP_API_KEY` (or `fmp_api_key` in the file named by `--credentials` / `FACDRONE_DATA_CREDENTIALS_FILE`) | Commercial terms; redistribution of data not permitted without a licence (research.md 10, confidence low). Marked "not redistributable, to be verified" in NOTICE (to be verified) |
| ETF prices and metadata (`ETF`), extra `[fmp]` | FMP `/historical-price-eod/full` for the ETF universe (`etf_prices`, `etf_meta`) | as FMP | as FMP | as FMP | `FMP_API_KEY` (the ETF family uses the FMP key) | as FMP |
| Yahoo Finance via `yfinance` (`YFINANCE`), base install | `yf.download` daily OHLCV for symbols FMP cannot price (the yfinance fallback family, `daily_prices`) and FX quotes | keyless, unofficial | Timeout: the client library's own (the module passes none); no retry on any outcome; no request-count ceiling | no fixed delay: daily bars after the close | none | Yahoo's terms are for personal use and forbid redistribution; `yfinance` (Apache-2.0) is not affiliated with Yahoo. Marked "not redistributable" (to be verified) |
| FRED (`FRED`), base install | `fredapi` `Fred(api_key).get_series(series_id, observation_start)` for the configured series ids (`fred_series`) | free API key | Timeout: the client library's own (the module passes none); no retry on any outcome; no request-count ceiling | 1 day: the next US business day, late morning Central | `FRED_API_KEY` (or `fred_api_key` in the credentials file) | API terms allow use; many series are owned by third parties whose terms govern; US government series are public domain. Attribution notice required by the FRED API terms (to be verified) |
| OECD SDMX (`OECD`), base install | One SDMX-CSV series (Japan CPI) stored in `fred_series` under the sentinel id `OECD_JPN_CPI` | keyless | Timeout 30 s per request; no retry on any outcome; no request-count ceiling | no fixed delay: monthly releases on the OECD calendar | none | OECD data published under CC BY 4.0 since 2024 (to verify for this dataset) (to be verified) |
| CFTC Commitments of Traders (`CFTC`), base install | `cot_reports.cot_year(year, cot_report_type="legacy_fut")` per year (`cftc_cot`) | keyless | Timeout: the client library's own (the module passes none); no retry on any outcome; no request-count ceiling | 3 days: Tuesday positions released Friday 15:30 ET | none | US government work, generally public domain; name the CFTC as source |
| Kenneth R. French Data Library (`KEN_FRENCH`), base install | Zipped CSV factor files over HTTPS (`fama_french_factors`, `factor_returns` for the US and European factors) | keyless | Timeout 30 s per request; no retry on any outcome; no request-count ceiling | no fixed delay: monthly library updates | none | No licence stated on the site; customary academic use with citation; treated as "not redistributable, to be verified" |
| AQR data library (`AQR`), base install | QMJ and BAB daily Excel workbooks (`factor_returns`) | keyless | Timeout 120 s per request; no retry on any outcome; no request-count ceiling | no fixed delay: monthly library updates | none | Site terms limit use to personal and non-commercial purposes (research.md 10, low confidence); treated as "not redistributable". AQR is not in requirement 5.5's provider list |
| Dukascopy historical datafeed (`DUKASCOPY`), extra `[dukascopy]` | Hourly `.bi5` tick files from `https://www.dukascopy.com/datafeed`, aggregated to daily order flow (`duka_order_flow`, `order_flow_daily`) | keyless public feed; rate-limited (a 25-minute refresh became four hours on 2026-10-06) | Timeout 30 s per request; throttle (HTTP 429/503): 3 retries after 1, 2 and 4 s; transient fault: 2 retries after 1 and 2 s; other status: 2 retries, no wait; no request-count ceiling | no fixed delay: hour files after their UTC hour | none | The bank's terms restrict use of the feed; treated as "not redistributable". Requirement 6.2 calls it a paid provider; the adapter uses no subscription |

The two policy columns are printed from `POLICY` in `providers/policy.py`, one row per
`Provider` member, and a test (`tests/test_provider_policy.py`) holds each cell equal to its
row: the document cannot claim a retry, a wait or a ceiling the adapters do not apply. Every
adapter reads its timeout and retry schedule from its row. Outside the table, unchanged: each
adapter fails open (per request, symbol, window or hour file: a failure yields an empty frame,
and a scope that got nothing prints `provider_outcome=provider_unavailable` on the family's
summary line); FMP's requests are shaped by fixed page and window
caps (insider trading 500 rows x 5 pages; economic calendar and news in 90-day chunks; news
limit 200; intraday windows sized under the ~3,000-bar cap). FMP Ultimate's per-minute limit
is not known, so the module counts no requests; a throttle answer still standing after the
row's three retries fails open like any other error. A network fault on FMP is not retried.

The publication column states what the provider publishes, as the module knows it (to be
verified with the licence cells). A consumer that declares its own publication rules, such as
the trading system's `freshness:` block, stays authoritative for its families.
