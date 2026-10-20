# Versioning

The module follows semantic versioning, `MAJOR.MINOR.PATCH`, and records every release in
`CHANGELOG.md` in keep-a-changelog format.

| Change | Version |
|---|---|
| A table, column, key or index changes in a way a reader or writer of the database notices, or a stored value changes meaning | major |
| A public Python name or a command-line option is removed or changes meaning | major |
| A table, family, command or option is added, or a provider is added | minor |
| A fix that changes no schema and no public name | patch |

A schema change is always a major release, including a new required column. A purely
additive change to a table that no existing read or write touches is a minor release only
when it needs no migration of existing rows; when in doubt it is major.

Public names are the `facdrone-data` commands and options, the names importable from
`facdrone_data` that the documents mention, and the declared tables. Underscore names and
anything under `tests` are not public.

## Releases

A release is a git tag `vMAJOR.MINOR.PATCH`. The release carries the built wheels as assets and
the changelog section as its notes. A consumer pins an exact version. The package is not
published to a package index (see the README).

The current version is in `pyproject.toml`. The module is below 1.0.0 until the first release
is tagged, and a minor version may break a name until then.
