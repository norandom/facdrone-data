# Credentials and the database address

The module stores no key and no database address. A run takes them from the command line, a
file or the environment, and refuses to start when the database address is missing.

## Database address

| Source | Order |
|---|---|
| `--dsn` | first |
| `FACDRONE_DATA_DSN` | otherwise |

There is no default. A run without either stops with `no database given: pass --dsn or set
FACDRONE_DATA_DSN`. Prefer the variable to the option: a command-line argument is visible in
the process list and the shell history.

## Provider keys

| Key | Environment variable | Key in a credentials file | Needed by |
|---|---|---|---|
| FRED | `FRED_API_KEY` | `fred_api_key` | the FRED family |
| Financial Modeling Prep | `FMP_API_KEY` | `fmp_api_key` | the FMP families and the ETF family |

Yahoo Finance, Kenneth French, CFTC and OECD need no key. Dukascopy's adapter needs the
`dukascopy` extra, not a key.

A run uses one source:

1. the file named by `--credentials`, or by `FACDRONE_DATA_CREDENTIALS_FILE` when the option is
   absent;
2. otherwise the two environment variables.

The sources are never merged. A named file that does not exist configures no key. The file is
YAML with the key names above:

```yaml
fred_api_key: "..."
fmp_api_key: "..."
```

Keep it outside the repository and readable by the owner only. An empty value counts as not
configured.

## Missing keys

A family whose key is missing is refused before any request. The download prints
`refused family=<writer> reason=<VARIABLE>` and exits 3 after running the families that can
run. A family whose extra is not installed prints `unavailable family=<writer> extra=<name>`.

## Redaction

`redact` removes key-shaped query parameters (`apikey`, `api_key`, `token`, `key`) and every
configured key value from a string. A logging filter applies it to every log record, its
arguments and its exception text, and the command line applies the parameter pattern to the error text it
prints. A provider URL that carries a key therefore reaches no log line, error message or
stored point-in-time row.
