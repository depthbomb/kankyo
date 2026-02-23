# 🌄 Kankyō

A type-safe, validated library for loading and retrieving environment variables.

## Features

- **Layered loading** — merges `.env` files in the standard priority order used by Next.js / Vite / dotenv-flow
- **Type coercion** — `str`, `int`, `float`, `bool`, `list`, `json`, `Path`, `URL`, `Enum`, and secrets
- **Extended types** — `Decimal`, `timedelta`, `IPv4/IPv6`, `email`, `UUID`, and literal values
- **Rich validation** — length bounds, numeric ranges, regex patterns, allowed-value lists, custom validators
- **Safe defaults** — defaults are validated/coerced and can use `default_factory` for mutable values
- **Variable expansion** — optional `${VAR}` expansion with cycle detection
- **Source tracing** — inspect winning source and override history with `env.trace("KEY")`
- **Strict mode toggles** — stricter parsing and mutable-default safeguards
- **Declarative schemas** — define all your variables in one place with `EnvSchema`, fail fast on startup
- **Schema composition** — optional/union/mapping specs plus nested/computed schema fields
- **Test-friendly** — `env.patch({...})` context manager for safe test isolation

---

## Loading Priority

Files are merged in this order (each one wins over the previous):

| Priority | Source                      |
|----------|-----------------------------|
| 1 (low)  | `.env`                      |
| 2        | `.env.<environment>`        |
| 3        | `.env.local`                |
| 4        | `.env.<environment>.local`  |
| 5 (high) | `os.environ` (process env)  |

`extra` kwargs (for tests) override everything.

---

## Installation

```bash
pip install kankyo
```

---

## Quick Start

```python
from kankyo import Env, EnvStr, EnvInt, EnvBool

env = Env(environment='production')  # loads .env, .env.production, .env.local, etc.

port  = env.get('PORT',  EnvInt(ge=1024, le=65535, default=8080))
debug = env.get('DEBUG', EnvBool(default=False))
host  = env.get('HOST',  EnvStr(default='0.0.0.0'))
```

Enable expansion/strict mode:

```python
env = Env(
    environment='production',
    expand_vars=True,   # resolve ${VAR} references
    strict=True,        # stricter behavior defaults
)
```

---

## Declarative Schema

Define all variables once and validate them eagerly at startup:

```python
from enum import StrEnum
from kankyo import Env, EnvSchema, EnvVar, EnvStr, EnvInt, EnvBool, EnvUrl, EnvEnum, EnvSecret

class LogLevel(StrEnum):
    DEBUG   = 'debug'
    INFO    = 'info'
    WARNING = 'warning'
    ERROR   = 'error'

class AppConfig(EnvSchema):
    # Required (no default) — missing → EnvSchemaError at startup
    database_url: str = EnvVar('DATABASE_URL', EnvStr())
    api_key:      str = EnvVar('API_KEY',      EnvSecret())

    # Optional with defaults
    port:      int      = EnvVar('PORT',      EnvInt(ge=1024, le=65535, default=8080))
    debug:     bool     = EnvVar('DEBUG',     EnvBool(default=False))
    log_level: LogLevel = EnvVar('LOG_LEVEL', EnvEnum(LogLevel, default=LogLevel.INFO))
    api_url:   str      = EnvVar('API_URL',   EnvUrl(allowed_schemes=['https']))

env = Env(environment='production')
cfg = AppConfig(env)          # raises EnvSchemaError listing ALL problems if any variable fails

print(cfg.port)               # 8080 (int)
print(cfg.log_level)          # LogLevel.INFO
print(cfg.as_dict())          # {'port': 8080, 'debug': False, ...}
```

Nested/computed schema composition:

```python
from kankyo import EnvSchema, EnvVar, EnvNested, EnvComputed, EnvStr, EnvInt

class DBConfig(EnvSchema):
    host: str = EnvVar('HOST', EnvStr())
    port: int = EnvVar('PORT', EnvInt())

class AppConfig(EnvSchema):
    db: DBConfig = EnvNested(DBConfig, prefix='DB')  # DB__HOST, DB__PORT
    database_url: str = EnvComputed(lambda cfg: f'postgres://{cfg.db.host}:{cfg.db.port}')
```

---

## All Types

### `EnvStr`

```python
env.get('NAME', EnvStr(
    min_length=1,
    max_length=128,
    pattern=r'[a-z][a-z0-9_-]*',   # re.fullmatch
    choices=['dev', 'staging', 'production'],
    strip=True,                      # default
    default='unnamed',
))
```

### `EnvInt`

```python
env.get('PORT', EnvInt(
    ge=1024,      # >= 1024
    le=65535,     # <= 65535
    gt=0,         # > 0  (exclusive)
    lt=100,       # < 100 (exclusive)
    base=10,      # use base=0 for 0x… / 0o… / 0b… auto-detection
    choices=[80, 443, 8080],
    default=8080,
))
```

### `EnvFloat`

```python
env.get('LEARNING_RATE', EnvFloat(gt=0.0, le=1.0, default=1e-3))
```

### `EnvDecimal`

```python
from decimal import Decimal
env.get('PRICE', EnvDecimal(ge=Decimal('0')))
```

### `EnvBool`

Truthy strings: `1 true yes on enable enabled`
Falsy strings:  `0 false no off disable disabled`
(case-insensitive)

```python
env.get('DEBUG', EnvBool(default=False))
```

### `EnvList`

```python
env.get('ALLOWED_HOSTS', EnvList(
    subtype=EnvStr(),    # applied to each element
    delimiter=',',       # default
    min_length=1,
    max_length=10,
    default=['localhost'],
))

# List of ints:
env.get('PORTS', EnvList(subtype=EnvInt(ge=1)))
```

### `EnvJson`

```python
env.get('FEATURE_FLAGS', EnvJson(
    expected_type=dict,   # validated after JSON decode
    default={},
))
```

### `EnvPath`

```python
env.get('CONFIG_FILE', EnvPath(
    must_exist=True,
    must_be_file=True,
    expanduser=True,      # expand ~ (default)
    default='~/.myapp/config.yaml',
))
```

### `EnvTimedelta`

```python
env.get('CACHE_TTL', EnvTimedelta())      # '1h30m', '45s', or numeric seconds
```

### `EnvUrl`

```python
env.get('API_ENDPOINT', EnvUrl(
    allowed_schemes=['https'],
    require_tld=True,
))
```

### `EnvIPv4` / `EnvIPv6`

```python
env.get('BIND_IPV4', EnvIPv4())
env.get('BIND_IPV6', EnvIPv6())
```

### `EnvEmail`

```python
env.get('SUPPORT_EMAIL', EnvEmail())
```

### `EnvUUID`

```python
env.get('REQUEST_ID', EnvUUID())
```

### `EnvLiteral`

```python
env.get('MODE', EnvLiteral(['dev', 'staging', 'prod']))
env.get('RETRIES', EnvLiteral([0, 1, 2, 3]))
```

### `EnvOptional`

```python
env.get('OPTIONAL_PORT', EnvOptional(EnvInt()))  # int | None
```

### `EnvUnion`

```python
env.get('WORKERS', EnvUnion([EnvInt(ge=1), EnvLiteral(['auto'])]))
```

### `EnvMapping`

```python
env.get('DB', EnvMapping({
    'host': EnvStr(),
    'port': EnvInt(ge=1),
    'ssl': EnvBool(default=False),
}))
```

### `EnvListOfSchema`

```python
env.get('BACKENDS', EnvListOfSchema({
    'name': EnvStr(min_length=1),
    'port': EnvInt(ge=1),
}))
```

### `EnvEnum`

```python
class Mode(str, Enum):
    DEBUG   = 'debug'
    RELEASE = 'release'

env.get('BUILD_MODE', EnvEnum(Mode, default=Mode.RELEASE))
```

Lookup tries **value** first, then **name**, case-insensitively by default.

### `EnvSecret`

Like `EnvStr` but the value is masked in `repr()` so it never leaks into logs:

```python
token = env.get('API_TOKEN', EnvSecret())
print(repr(token))   # '********'
print(str(token))    # actual value
```

---

## Custom Validators

Every type accepts a `validators` list of callables `(key: str, value: T) -> None`.
Raise `EnvValidationError` to fail.

```python
from kankyo import EnvStr
from kankyo.exceptions import EnvValidationError

def must_be_slug(key, value):
    import re
    if not re.fullmatch(r'[a-z0-9-]+', value):
        raise EnvValidationError(key, value, 'must be a URL slug (a-z, 0-9, hyphens)')

env.get('APP_SLUG', EnvStr(validators=[must_be_slug]))
```

---

## Bulk Retrieval

Collect all errors in one call rather than failing on the first:

```python
result = env.get_many({
    'PORT':  EnvInt(default=8080),
    'DEBUG': EnvBool(default=False),
    'HOST':  EnvStr(default='0.0.0.0'),
})
# result = {'PORT': 8080, 'DEBUG': False, 'HOST': '0.0.0.0'}
```

---

## Test Isolation

```python
def test_uses_custom_port():
    env = Env(root=Path('fixtures'))
    with env.patch({'PORT': '9999', 'DEBUG': 'true'}):
        cfg = AppConfig(env)
        assert cfg.port == 9999
    # original values restored after the with block
```

---

## Env API Reference

| Method                           | Description                                         |
|----------------------------------|-----------------------------------------------------|
| `env.get(key, spec)`             | Retrieve a typed value; uses spec default if absent |
| `env.require(key, spec)`         | Like `get` but raises even when spec has a default  |
| `env.get_raw(key, default=None)` | Return the raw string (or default)                  |
| `env.is_set(key)`                | `True` if the key exists in any source              |
| `env.get_many(specs)`            | Bulk retrieval, collects all errors                 |
| `env.snapshot()`                 | Shallow copy of raw merged data                     |
| `env.reload()`                   | Re-read all source files                            |
| `env.patch(overrides)`           | Context manager for test injection                  |
| `env.trace(key)`                 | Show winner + source/value history for a key        |

---

## Source Tracing

```python
trace = env.trace('DATABASE_URL')
if trace:
    print(trace.winner)   # e.g. 'os.environ', 'extra', '.env.local'
    for entry in trace.history:
        print(entry.source, entry.value)
```

---

## Strict Mode

You can enable strict mode at the environment or type level:

```python
env = Env(strict=True, expand_vars=True)
port = env.get('PORT', EnvInt(strict=True))
```

In strict mode:
- Mutable defaults must use `default_factory`
- Some implicit default coercions are rejected
- Expansion can fail on unresolved `${VAR}` references
- `Env(strict=True)` applies strict parsing to specs that do not set `strict=...` explicitly

---

## .env File Format

```bash
# Full-line comments
APP_NAME=my-service

# Quoted values (whitespace preserved)
GREETING='Hello, World!'
PATH_VAL='/home/user/data'

# Double-quoted: escape sequences interpreted (\n \t \r)
MULTILINE="line1\nline2"

# export prefix supported
export SECRET_KEY=abc123

# Inline comments stripped for unquoted values
PORT=8080   # web port
```

---

## Error Types

| Exception            | Raised when                                            |
|----------------------|--------------------------------------------------------|
| `EnvMissingError`    | Required variable not found in any source              |
| `EnvParseError`      | Raw string cannot be coerced to target type            |
| `EnvValidationError` | Coerced value fails a validation constraint            |
| `EnvSchemaError`     | `EnvSchema` construction fails / bad schema definition |

All inherit from `EnvError`.
