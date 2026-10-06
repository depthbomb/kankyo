# 🌅 Kankyō

Define your application's environment variables in a Python schema, then generate a typed configuration client.

## Define, generate, load

Install into your application's virtual environment:

```shell
python -m pip install kankyo
```

Create `env_schema.py`. Keyword names become attributes on the generated client:

```python
from kankyo import env, url, Schema, secret, boolean, integer, one_of

schema = Schema(
    database_url=env('DATABASE_URL', url()),
    port=env('PORT', integer(minimum=1, maximum=65535), default=8080),
    debug=env('DEBUG', boolean(), optional=True),
    log_level=env('LOG_LEVEL', one_of('debug', 'info', 'warning', 'error'), default='info'),
    api_token=env('API_TOKEN', secret(min_length=1)),
)
```

Generate the client:

```shell
kankyo generate env_schema.py --output config.py
```

The equivalent module command is `python -m kankyo generate env_schema.py --output config.py`.
Use the client in your application:

```python
from config import load

config = load(environment='production')

config.database_url  # str
config.port          # int
config.debug         # bool | None
config.log_level     # Literal['debug', 'info', 'warning', 'error']
config.api_token     # Secret

token = config.api_token.reveal()
```

The generated module contains a frozen dataclass, concrete annotations, and `load()` / `load_from()` functions.
Editors and type checkers can inspect it directly. Commit it alongside your schema and regenerate after schema
changes. Its schema is embedded in the generated file, so your application needs Kankyo at runtime but does not
need to import or ship `env_schema.py`. Importing the client does not read environment files or process variables.

See [the example schema](examples/env_schema.py) and [its generated client](examples/config_gen.py).

## Loading priority

`load()` uses the current directory as its root. Pass `root=...` to select another directory. Each layer overrides
the preceding layers in this order:

| Priority | Source |
| --- | --- |
| 1 | `.env` |
| 2 | `.env.<environment>` |
| 3 | `.env.local` |
| 4 | `.env.<environment>.local` |
| 5 | `os.environ` |
| 6 | Explicit `overrides` |

```python
config = load(root='settings', environment='production', overrides={'PORT': '9000'})
```

Environment-specific files are used only when you supply `environment`. Missing files are fine; malformed or
unreadable files raise `SourceError`, even if a later layer would override their values. Loading never changes
`os.environ`. Keep local environment files out of version control.

Files support UTF-8, an optional BOM, `export NAME=value`, empty values, comments, and single or double quotes.
Unquoted inline comments start with `#` after whitespace. Quoted values can span lines. Double quotes recognize
`\n`, `\r`, `\t`, `\"`, and `\\`; single quotes preserve backslashes. Values are literal: `${NAME}` is not expanded
and shell commands are never evaluated. Duplicate keys in a file use the last value.

## Fields and defaults

Every field is required unless it has a default or `optional=True`:

```python
from kankyo import env, Schema, string, boolean, integer

schema = Schema(
    host=env('HOST', string(min_length=1)),
    port=env('PORT', integer(minimum=1), default=8080),
    debug=env('DEBUG', boolean(), optional=True),
    region=env('REGION', string(), optional=True, default='us-east'),
)
```

An optional field without a default becomes `T | None`. A field with a default always has type `T`, including when
`optional=True` is set. Defaults accept Python values or environment strings and are parsed and validated when the
schema is defined. Mutable defaults are snapshotted and copied through parsing on each load. Use `optional=True`
for missing values; `None` is not a default value.

An empty string counts as present. It never activates a default or an alias. Add `min_length=1` when a string must
be non-empty. Environment mappings must contain strings; Python values are accepted only for schema defaults.

Aliases let a field read from alternative environment variable names:

```python
database_url=env('DATABASE_URL', url(), aliases=('DB_URL',))
```

The primary name wins when present, followed by aliases in their declared order. An invalid primary value is an
error. Use `description='...'` to include a field comment in the generated dataclass.

## Rules

| Rule | Generated Python type | Options / behavior |
| --- | --- | --- |
| `string()` | `str` | `strip`, `min_length`, `max_length`, full-match `pattern` |
| `integer()` | `int` | Base-10 integers; `minimum`, `maximum` |
| `number()` | `float` | Finite numbers; `minimum`, `maximum` |
| `decimal()` | `Decimal` | Exact finite decimals; `minimum`, `maximum` |
| `boolean()` | `bool` | Case-insensitive `true/false`, `yes/no`, `on/off`, `1/0` |
| `one_of('dev', 'prod')` | `Literal['dev', 'prod']` | Exact, case-sensitive string choices |
| `duration()` | `timedelta` | Numeric seconds or compound units: `d`, `h`, `m`, `s`, `ms`, `us` |
| `path()` | `Path` | Non-empty paths; expands `~`; no filesystem existence check |
| `url()` | `str` | Absolute URL with a host and valid port; optional `schemes=('https',)` |
| `uuid()` | `UUID` | UUID strings |
| `ip_address()` | `IPv4Address | IPv6Address` | IPv4 or IPv6 strings |
| `secret()` | `Secret` | `strip`, `min_length`, `max_length`; redacted display |
| `list_of(rule)` | `list[T]` | Typed CSV or JSON arrays; `separator`, `min_length`, `max_length`, `unique` |

String whitespace is preserved unless `strip=True`. Numeric and boolean strings allow surrounding whitespace.
Numeric bounds are inclusive. Duration fractions are truncated to Python's microsecond precision. Paths are
relative to the application's current directory, including when dotenv files come from another root.

Lists validate every item, including items in defaults. CSV supports quoted separators, and whitespace after a
separator is skipped. Empty input produces an empty list. Nested lists use JSON arrays:

```python
from kankyo import env, Schema, string, integer, list_of

schema = Schema(
    hosts=env('HOSTS', list_of(string(min_length=1), unique=True), default=['localhost']),
    ports=env('PORTS', list_of(integer(minimum=1)), default=[8080, 8081]),
    groups=env('GROUPS', list_of(list_of(integer())), default=[[1, 2], [3]]),
)
```

Rules are immutable and can be reused across fields. Kankyo deliberately keeps the rule set focused on common
configuration values. Application-specific transformations can run after loading.

## Validation and secrets

`load()` and `load_from()` parse all fields before returning a client. They raise one `ConfigError` containing every
invalid or missing field. Unrelated environment variables are ignored.

```python
from config import load
from kankyo import ConfigError

try:
    config = load()
except ConfigError as error:
    for issue in error.issues:
        print(issue.field, issue.variable, issue.reason)
    raise SystemExit(1)
```

Errors identify fields and rules without including input values. `str(secret)`, `repr(secret)`, and the generated
client's representation redact secrets. Call `.reveal()` to retrieve the actual string. Redaction is for display;
it is not encryption. Schema defaults are embedded in generated source, so real credentials belong in the
environment rather than defaults.

`SchemaError` covers invalid definitions, `SourceError` covers dotenv failures, and `GenerationError` covers
generation failures. All inherit from `KankyoError`.

## Testing your application

`load_from()` reads only the supplied mapping. It does not read dotenv files or merge process variables:

```python
from config import load_from

config = load_from({
    'DATABASE_URL': 'postgres://localhost/app',
    'API_TOKEN': 'test-token',
    'PORT': '9000',
})

assert config.port == 9000
assert config.debug is None
```

The dataclass prevents attribute reassignment. List fields remain ordinary mutable Python lists, with independent
values for each load. Constructing the dataclass directly does not run validation; use the generated loaders for
environment input.

## Generator options

```shell
kankyo generate env_schema.py --output app/config.py --class-name Settings
kankyo generate env_schema.py --name production --output app/config.py
kankyo generate env_schema.py --output app/config.py --check
```

Without `--output`, the client is written to `config_gen.py` beside the schema. A file must define exactly one public
`Schema` instance unless `--name` selects one. Schemas are normal Python files executed during generation, so use
files you trust. Put schema files in modules separate from their generated clients.

Generation is deterministic and writes atomically. `--check` exits with a failure when output is missing or stale
and never writes the output. Handwritten files require `--force` to replace. The schema file itself cannot be used
as the output. Don't format generated files separately, since that would cause freshness checks to fail.

For programmatic generation, `kankyo.generate` exposes `load_schema()`, `render()`, and `generate()`.

## Development

```shell
python -m venv .venv
# Activate .venv using your shell's activation command.
python -m pip install -e '.[dev]'
python -m mypy
python -m kankyo generate examples/env_schema.py --check
python -m pytest
python -m build
python -m twine check --strict dist/*
```

`Tests` runs on Linux, Windows, and macOS with Python 3.14. It checks types and generated output, builds
the wheel from the sdist, verifies package metadata, and tests the installed wheel outside the checkout with
coverage. Distribution and coverage artifacts are retained for 14 days.
