from __future__ import annotations

import ipaddress
import json
import re
from copy import copy
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from enum import Enum
from pathlib import Path
from typing import Any, Callable, cast, Collection, Generic, Mapping, Pattern, Sequence, TypeVar
from urllib.parse import urlparse
from uuid import UUID

from .exceptions import EnvParseError, EnvValidationError

T = TypeVar('T')
_EnumT = TypeVar('_EnumT', bound=Enum)

_UNSET: Any = object()
_IMMUTABLE_DEFAULT_TYPES = (
    str,
    bytes,
    int,
    float,
    complex,
    bool,
    tuple,
    frozenset,
    type(None),
)
_MUTABLE_DEFAULT_TYPES = (list, dict, set, bytearray)

class _EnvType(Generic[T]):
    """
    Abstract base for all typed env-var descriptors.

    Sub-classes must implement ``_coerce(key, raw) -> T``.  They can also
    override ``_validate`` to add type-specific validation after coercion.
    """

    def __init__(
        self,
        *,
        default: Any = _UNSET,
        default_factory: Callable[[], T] | None = None,
        strict: bool | None = None,
        validators: Sequence[Callable[[str, T], None]] | None = None,
    ) -> None:
        if default is not _UNSET and default_factory is not None:
            raise ValueError('Provide either "default" or "default_factory", not both.')
        self._strict_explicit = strict is not None
        self._strict = bool(strict)
        if self._strict and isinstance(default, _MUTABLE_DEFAULT_TYPES):
            raise ValueError(
                'Strict mode requires mutable defaults to be provided via default_factory.'
            )
        self._default = default
        self._default_factory = default_factory
        self._validators: list[Callable[[str, T], None]] = list(validators or [])

    def has_default(self) -> bool:
        return self._default_factory is not None or self._default is not _UNSET

    @property
    def default(self) -> T:
        return self._make_default()

    def _make_default(self) -> T:
        if self._default_factory is not None:
            return self._default_factory()
        if isinstance(self._default, _IMMUTABLE_DEFAULT_TYPES) or isinstance(self._default, Enum):
            return cast(T, self._default)
        return cast(T, deepcopy(self._default))

    def has_explicit_strict(self) -> bool:
        return self._strict_explicit

    def with_strict(self, strict: bool) -> '_EnvType[T]':
        clone = copy(self)
        clone._strict = strict
        clone._strict_explicit = True
        return clone

    def parse(self, key: str, raw: str) -> T:
        """Coerce *raw* string then run all validators."""
        value = self._coerce(key, raw)
        self._run_validators(key, value)
        return value

    def parse_default(self, key: str) -> T:
        if not self.has_default():
            raise AttributeError(f'No default configured for "{key}".')
        value = self._make_default()
        if isinstance(value, str):
            return self.parse(key, value)
        self._run_validators(key, value)
        return value

    def _run_validators(self, key: str, value: T) -> None:
        self._validate(key, value)
        for v in self._validators:
            v(key, value)

    def _coerce(self, key: str, raw: str) -> T:
        raise NotImplementedError

    def _validate(self, key: str, value: T) -> None:  # noqa: B027
        pass

class EnvStr(_EnvType[str]):
    """
    Plain string variable with optional length and regex constraints.

    Parameters
    ----------
    default:
        Returned when the variable is absent and no value is found in any
        source file.  Omit to make the variable required.
    min_length / max_length:
        Enforce string length bounds (inclusive).
    pattern:
        A compiled ``re.Pattern`` or raw regex string the value must match.
    choices:
        An iterable of accepted values.  Comparison is case-sensitive.
    strip:
        Strip leading/trailing whitespace before returning (default ``True``).
    validators:
        Extra callables ``(key: str, value: str) -> None`` that raise
        ``EnvValidationError`` on failure.

    Example
    -------
    >>> env.get('APP_NAME', EnvStr(min_length=1, max_length=64))
    'my-service'
    """

    def __init__(
        self,
        *,
        default: str | Any = _UNSET,
        default_factory: Callable[[], str] | None = None,
        strict: bool | None = None,
        min_length: int | None = None,
        max_length: int | None = None,
        pattern: str | Pattern[str] | None = None,
        choices: Collection[str] | None = None,
        strip: bool = True,
        validators: Sequence[Callable[[str, str], None]] | None = None,
    ) -> None:
        super().__init__(
            default=default,
            default_factory=default_factory,
            strict=strict,
            validators=validators,
        )
        self._min_length = min_length
        self._max_length = max_length
        self._pattern = re.compile(pattern) if isinstance(pattern, str) else pattern
        self._choices = choices
        self._strip = strip

    def _coerce(self, key: str, raw: str) -> str:
        return raw.strip() if self._strip else raw

    def parse_default(self, key: str) -> str:
        if not self.has_default():
            raise AttributeError(f'No default configured for "{key}".')
        value = self._make_default()
        if not isinstance(value, str):
            raise EnvParseError(key, repr(value), 'str', 'default must be a string')
        return self.parse(key, value)

    def _validate(self, key: str, value: str) -> None:
        if self._min_length is not None and len(value) < self._min_length:
            raise EnvValidationError(
                key, value, f'length {len(value)} < min_length {self._min_length}'
            )
        if self._max_length is not None and len(value) > self._max_length:
            raise EnvValidationError(
                key, value, f'length {len(value)} > max_length {self._max_length}'
            )
        if self._pattern is not None and not self._pattern.fullmatch(value):
            raise EnvValidationError(
                key, value, f'does not match pattern r"{self._pattern.pattern}"'
            )
        if self._choices is not None and value not in self._choices:
            raise EnvValidationError(key, value, f'not in allowed choices {list(self._choices)!r}')

class EnvSecret(EnvStr):
    """
    Like ``EnvStr`` but the value is never surfaced in ``repr()`` or logs.

    >>> env.get('DB_PASSWORD', EnvSecret())
    '********'  # in repr — actual value accessible via .value
    """

    _MASK = '********'
    _REDACTED_REASON = 'secret validation failed'

    def parse(self, key: str, raw: str) -> '_SecretStr':
        try:
            value = super().parse(key, raw)
        except EnvParseError as exc:
            raise EnvParseError(key, self._MASK, exc.target_type, 'invalid secret value') from exc
        except Exception as exc:  # noqa: BLE001
            raise EnvValidationError(key, self._MASK, self._REDACTED_REASON) from exc
        return _SecretStr(value)

    def parse_default(self, key: str) -> '_SecretStr':
        try:
            value = super().parse_default(key)
        except EnvParseError as exc:
            raise EnvParseError(key, self._MASK, exc.target_type, 'invalid secret value') from exc
        except Exception as exc:  # noqa: BLE001
            raise EnvValidationError(key, self._MASK, self._REDACTED_REASON) from exc
        return _SecretStr(value)

class _SecretStr(str):
    """Thin str subclass that masks itself in repr."""

    def __repr__(self) -> str:
        return '"********"'

    def __str__(self) -> str:  # still returns the real value — use deliberately
        return super().__str__()

class EnvInt(_EnvType[int]):
    """
    Integer variable with optional range enforcement.

    Parameters
    ----------
    base:
        Numeric base for ``int()`` conversion (default 10, use 0 for auto).
    ge / gt / le / lt:
        Greater-than-or-equal / greater-than / less-than-or-equal / less-than
        bounds.  Both lower and upper may be supplied simultaneously.
    choices:
        Accepted integer values.

    Example
    -------
    >>> env.get('PORT', EnvInt(ge=1024, le=65535, default=8080))
    8080
    """

    def __init__(
        self,
        *,
        default: int | Any = _UNSET,
        default_factory: Callable[[], int] | None = None,
        strict: bool | None = None,
        base: int = 10,
        ge: int | None = None,
        gt: int | None = None,
        le: int | None = None,
        lt: int | None = None,
        choices: Collection[int] | None = None,
        validators: Sequence[Callable[[str, int], None]] | None = None,
    ) -> None:
        super().__init__(
            default=default,
            default_factory=default_factory,
            strict=strict,
            validators=validators,
        )
        self._base = base
        self._ge, self._gt, self._le, self._lt = ge, gt, le, lt
        self._choices = choices

    def _coerce(self, key: str, raw: str) -> int:
        if self._strict and raw != raw.strip():
            raise EnvParseError(key, raw, 'int', 'strict mode forbids surrounding whitespace')
        try:
            return int(raw.strip(), self._base)
        except ValueError as exc:
            raise EnvParseError(key, raw, 'int', str(exc)) from exc

    def parse_default(self, key: str) -> int:
        if not self.has_default():
            raise AttributeError(f'No default configured for "{key}".')
        value = self._make_default()
        if isinstance(value, bool):
            raise EnvParseError(key, repr(value), 'int', 'default must be an int or string')
        if isinstance(value, int):
            self._run_validators(key, value)
            return value
        if isinstance(value, str):
            return self.parse(key, value)
        raise EnvParseError(key, repr(value), 'int', 'default must be an int or string')

    def _validate(self, key: str, value: int) -> None:
        if self._ge is not None and value < self._ge:
            raise EnvValidationError(key, str(value), f'{value} < {self._ge} (ge)')
        if self._gt is not None and value <= self._gt:
            raise EnvValidationError(key, str(value), f'{value} <= {self._gt} (gt)')
        if self._le is not None and value > self._le:
            raise EnvValidationError(key, str(value), f'{value} > {self._le} (le)')
        if self._lt is not None and value >= self._lt:
            raise EnvValidationError(key, str(value), f'{value} >= {self._lt} (lt)')
        if self._choices is not None and value not in self._choices:
            raise EnvValidationError(
                key, str(value), f'{value} not in choices {list(self._choices)!r}'
            )

class EnvFloat(_EnvType[float]):
    """
    Floating-point variable with optional range enforcement.

    Example
    -------
    >>> env.get('LEARNING_RATE', EnvFloat(gt=0.0, le=1.0, default=0.001))
    0.001
    """

    def __init__(
        self,
        *,
        default: float | Any = _UNSET,
        default_factory: Callable[[], float] | None = None,
        strict: bool | None = None,
        ge: float | None = None,
        gt: float | None = None,
        le: float | None = None,
        lt: float | None = None,
        validators: Sequence[Callable[[str, float], None]] | None = None,
    ) -> None:
        super().__init__(
            default=default,
            default_factory=default_factory,
            strict=strict,
            validators=validators,
        )
        self._ge, self._gt, self._le, self._lt = ge, gt, le, lt

    def _coerce(self, key: str, raw: str) -> float:
        if self._strict and raw != raw.strip():
            raise EnvParseError(key, raw, 'float', 'strict mode forbids surrounding whitespace')
        try:
            return float(raw.strip())
        except ValueError as exc:
            raise EnvParseError(key, raw, 'float', str(exc)) from exc

    def parse_default(self, key: str) -> float:
        if not self.has_default():
            raise AttributeError(f'No default configured for "{key}".')
        value = self._make_default()
        if isinstance(value, bool):
            raise EnvParseError(key, repr(value), 'float', 'default must be a float or string')
        if isinstance(value, (int, float)):
            if self._strict and not isinstance(value, float):
                raise EnvParseError(
                    key, repr(value), 'float', 'strict mode requires float defaults, not int'
                )
            coerced = float(value)
            self._run_validators(key, coerced)
            return coerced
        if isinstance(value, str):
            return self.parse(key, value)
        raise EnvParseError(key, repr(value), 'float', 'default must be a float or string')

    def _validate(self, key: str, value: float) -> None:
        if self._ge is not None and value < self._ge:
            raise EnvValidationError(key, str(value), f'{value} < {self._ge} (ge)')
        if self._gt is not None and value <= self._gt:
            raise EnvValidationError(key, str(value), f'{value} <= {self._gt} (gt)')
        if self._le is not None and value > self._le:
            raise EnvValidationError(key, str(value), f'{value} > {self._le} (le)')
        if self._lt is not None and value >= self._lt:
            raise EnvValidationError(key, str(value), f'{value} >= {self._lt} (lt)')


_TRUE_STRINGS: frozenset[str] = frozenset({'1', 'true', 'yes', 'on', 'enable', 'enabled'})
_FALSE_STRINGS: frozenset[str] = frozenset({'0', 'false', 'no', 'off', 'disable', 'disabled'})

class EnvBool(_EnvType[bool]):
    """
    Boolean variable parsed from common truthy/falsy strings.

    Truthy  : ``1 true yes on enable enabled``
    Falsy   : ``0 false no off disable disabled``
    (case-insensitive)

    Example
    -------
    >>> env.get('DEBUG', EnvBool(default=False))
    False
    """

    def __init__(
        self,
        *,
        default: bool | Any = _UNSET,
        default_factory: Callable[[], bool] | None = None,
        strict: bool | None = None,
        validators: Sequence[Callable[[str, bool], None]] | None = None,
    ) -> None:
        super().__init__(
            default=default,
            default_factory=default_factory,
            strict=strict,
            validators=validators,
        )

    def _coerce(self, key: str, raw: str) -> bool:
        normalised = raw.strip().lower()
        if normalised in _TRUE_STRINGS:
            return True
        if normalised in _FALSE_STRINGS:
            return False
        raise EnvParseError(
            key,
            raw,
            'bool',
            f'expected one of {sorted(_TRUE_STRINGS | _FALSE_STRINGS)}',
        )

    def parse_default(self, key: str) -> bool:
        if not self.has_default():
            raise AttributeError(f'No default configured for "{key}".')
        value = self._make_default()
        if isinstance(value, bool):
            self._run_validators(key, value)
            return value
        if isinstance(value, str):
            return self.parse(key, value)
        raise EnvParseError(key, repr(value), 'bool', 'default must be a bool or string')

class EnvList(_EnvType[list[T]]):
    """
    Comma-separated (or custom delimiter) list variable.

    Parameters
    ----------
    subtype:
        An ``_EnvType`` instance applied to each element.  Defaults to
        ``EnvStr()``.
    delimiter:
        Character (or string) used to split the raw value (default ``','``).
    min_length / max_length:
        Bounds on the resulting list length.

    Example
    -------
    >>> env.get('ALLOWED_HOSTS', EnvList(subtype=EnvStr(), delimiter=','))
    ['localhost', '127.0.0.1']
    """

    def __init__(
        self,
        *,
        default: list[T] | Any = _UNSET,
        default_factory: Callable[[], list[T]] | None = None,
        strict: bool | None = None,
        subtype: _EnvType[T] | None = None,
        delimiter: str = ',',
        min_length: int | None = None,
        max_length: int | None = None,
        validators: Sequence[Callable[[str, list[T]], None]] | None = None,
    ) -> None:
        super().__init__(
            default=default,
            default_factory=default_factory,
            strict=strict,
            validators=validators,
        )
        self._subtype: _EnvType[T] = subtype or EnvStr()  # type: ignore[assignment]
        self._delimiter = delimiter
        self._min_length = min_length
        self._max_length = max_length

    def _coerce(self, key: str, raw: str) -> list[T]:
        parts = [p.strip() for p in raw.split(self._delimiter) if p.strip()]
        return [self._subtype.parse(key, p) for p in parts]

    def _validate(self, key: str, value: list[T]) -> None:
        if self._min_length is not None and len(value) < self._min_length:
            raise EnvValidationError(
                key, str(value), f'list length {len(value)} < min_length {self._min_length}'
            )
        if self._max_length is not None and len(value) > self._max_length:
            raise EnvValidationError(
                key, str(value), f'list length {len(value)} > max_length {self._max_length}'
            )

    def parse_default(self, key: str) -> list[T]:
        if not self.has_default():
            raise AttributeError(f'No default configured for "{key}".')
        raw_default = self._make_default()
        if isinstance(raw_default, str):
            return self.parse(key, raw_default)
        if not isinstance(raw_default, list):
            raise EnvValidationError(
                key, repr(raw_default), 'default must be a list or a delimited string'
            )
        parsed = [
            self._subtype.parse(
                key,
                item if isinstance(item, str) else _strict_list_item_to_str(key, item, self._strict),
            )
            for item in raw_default
        ]
        self._run_validators(key, parsed)
        return parsed

class EnvJson(_EnvType[Any]):
    """
    JSON-encoded variable, deserialized with ``json.loads``.

    Parameters
    ----------
    expected_type:
        Optional Python type (or tuple of types) the decoded value must be an
        instance of.

    Example
    -------
    >>> env.get('FEATURE_FLAGS', EnvJson(expected_type=dict, default={}))
    {'new_ui': True}
    """

    def __init__(
        self,
        *,
        default: Any = _UNSET,
        default_factory: Callable[[], Any] | None = None,
        strict: bool | None = None,
        expected_type: type | tuple[type, ...] | None = None,
        validators: Sequence[Callable[[str, Any], None]] | None = None,
    ) -> None:
        super().__init__(
            default=default,
            default_factory=default_factory,
            strict=strict,
            validators=validators,
        )
        self._expected_type = expected_type

    def _coerce(self, key: str, raw: str) -> Any:
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise EnvParseError(key, raw, 'JSON', str(exc)) from exc

    def _validate(self, key: str, value: Any) -> None:
        if self._expected_type is not None and not isinstance(value, self._expected_type):
            raise EnvValidationError(
                key,
                repr(value),
                f'expected type {self._expected_type!r}, got {type(value).__name__}',
            )

class EnvPath(_EnvType[Path]):
    """
    File-system path variable.

    Parameters
    ----------
    must_exist:
        Raise ``EnvValidationError`` if the path does not exist.
    must_be_file / must_be_dir:
        Further constrain the expected path type.
    expanduser:
        Expand ``~`` before validation (default ``True``).

    Example
    -------
    >>> env.get('CONFIG_FILE', EnvPath(must_exist=True, must_be_file=True))
    PosixPath('/etc/myapp/config.yaml')
    """

    def __init__(
        self,
        *,
        default: str | Path | Any = _UNSET,
        default_factory: Callable[[], str | Path] | None = None,
        strict: bool | None = None,
        must_exist: bool = False,
        must_be_file: bool = False,
        must_be_dir: bool = False,
        expanduser: bool = True,
        validators: Sequence[Callable[[str, Path], None]] | None = None,
    ) -> None:
        super().__init__(
            default=Path(default).expanduser() if isinstance(default, (str, Path)) else default,
            default_factory=cast(Callable[[], Path] | None, default_factory),
            strict=strict,
            validators=validators,
        )
        self._must_exist = must_exist
        self._must_be_file = must_be_file
        self._must_be_dir = must_be_dir
        self._expanduser = expanduser

    def _coerce(self, key: str, raw: str) -> Path:
        p = Path(raw.strip())
        return p.expanduser() if self._expanduser else p

    def parse_default(self, key: str) -> Path:
        if not self.has_default():
            raise AttributeError(f'No default configured for "{key}".')
        value = self._make_default()
        if isinstance(value, Path):
            resolved = value.expanduser() if self._expanduser else value
            self._run_validators(key, resolved)
            return resolved
        if isinstance(value, str):
            return self.parse(key, value)
        raise EnvParseError(key, repr(value), 'Path', 'default must be a path or string')

    def _validate(self, key: str, value: Path) -> None:
        if self._must_exist and not value.exists():
            raise EnvValidationError(key, str(value), 'path does not exist')
        if self._must_be_file and not value.is_file():
            raise EnvValidationError(key, str(value), 'path is not a file')
        if self._must_be_dir and not value.is_dir():
            raise EnvValidationError(key, str(value), 'path is not a directory')

class EnvUrl(_EnvType[str]):
    """
    URL variable with scheme and host validation.

    Parameters
    ----------
    allowed_schemes:
        Whitelist of URL schemes (e.g. ``['http', 'https']``).
    require_tld:
        Raise if the host looks like a bare hostname with no dot.

    Example
    -------
    >>> env.get('API_URL', EnvUrl(allowed_schemes=['https']))
    'https://api.example.com/v1'
    """

    def __init__(
        self,
        *,
        default: str | Any = _UNSET,
        default_factory: Callable[[], str] | None = None,
        strict: bool | None = None,
        allowed_schemes: Sequence[str] | None = None,
        require_tld: bool = False,
        validators: Sequence[Callable[[str, str], None]] | None = None,
    ) -> None:
        super().__init__(
            default=default,
            default_factory=default_factory,
            strict=strict,
            validators=validators,
        )
        self._allowed_schemes = [s.lower() for s in allowed_schemes] if allowed_schemes else None
        self._require_tld = require_tld

    def _coerce(self, key: str, raw: str) -> str:
        url = raw.strip()
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            raise EnvParseError(key, raw, 'URL', 'must include scheme and host')
        if self._allowed_schemes and parsed.scheme.lower() not in self._allowed_schemes:
            raise EnvValidationError(
                key,
                url,
                f'scheme "{parsed.scheme}" not in allowed schemes {self._allowed_schemes!r}',
            )
        if self._require_tld:
            hostname = parsed.hostname
            if hostname is None or '.' not in hostname:
                raise EnvValidationError(key, url, 'host appears to have no TLD')
        return url

    def parse_default(self, key: str) -> str:
        if not self.has_default():
            raise AttributeError(f'No default configured for "{key}".')
        value = self._make_default()
        if not isinstance(value, str):
            raise EnvParseError(key, repr(value), 'URL', 'default must be a string')
        return self.parse(key, value)

class EnvEnum(_EnvType[_EnumT]):
    """
    Enum variable that maps a raw string to an ``enum.Enum`` member.

    Lookup is attempted first by *value*, then by *name*.

    Example
    -------
    >>> class LogLevel(str, Enum):
    ...     DEBUG = 'debug'
    ...     INFO = 'info'
    ...     WARNING = 'warning'
    ...
    >>> env.get('LOG_LEVEL', EnvEnum(LogLevel, default=LogLevel.INFO))
    <LogLevel.INFO: 'info'>
    """

    def __init__(
        self,
        enum_class: type[_EnumT],
        *,
        default: _EnumT | Any = _UNSET,
        default_factory: Callable[[], _EnumT] | None = None,
        strict: bool | None = None,
        case_sensitive: bool = False,
        validators: Sequence[Callable[[str, _EnumT], None]] | None = None,
    ) -> None:
        super().__init__(
            default=default,
            default_factory=default_factory,
            strict=strict,
            validators=validators,
        )
        self._enum_class = enum_class
        self._case_sensitive = case_sensitive
        self._lookup: dict[str, _EnumT] = {}
        for member in self._enum_class:
            member_name = member.name if self._case_sensitive else member.name.lower()
            member_value = str(member.value)
            if not self._case_sensitive:
                member_value = member_value.lower()
            self._lookup.setdefault(member_value, member)
            self._lookup.setdefault(member_name, member)

    def _coerce(self, key: str, raw: str) -> _EnumT:
        candidate = raw.strip() if self._case_sensitive else raw.strip().lower()
        if candidate in self._lookup:
            return self._lookup[candidate]

        valid = [m.value for m in self._enum_class]
        raise EnvParseError(
            key, raw, self._enum_class.__name__, f'valid values are {valid!r}'
        )

    def parse_default(self, key: str) -> _EnumT:
        if not self.has_default():
            raise AttributeError(f'No default configured for "{key}".')
        value = self._make_default()
        if isinstance(value, self._enum_class):
            self._run_validators(key, value)
            return value
        if isinstance(value, str):
            return self.parse(key, value)
        raise EnvParseError(
            key,
            repr(value),
            self._enum_class.__name__,
            'default must be an enum member or a string',
        )

def _strict_list_item_to_str(key: str, value: Any, strict: bool) -> str:
    if strict:
        raise EnvParseError(
            key,
            repr(value),
            'list item',
            'strict mode requires default list items to be strings',
        )
    return str(value)

class EnvDecimal(_EnvType[Decimal]):
    """
    Decimal variable parsed with :class:`decimal.Decimal`.

    Supports optional numeric bounds (``ge``/``gt``/``le``/``lt``) and a
    finite set of allowed ``choices``.
    """

    def __init__(
        self,
        *,
        default: Decimal | str | Any = _UNSET,
        default_factory: Callable[[], Decimal] | None = None,
        strict: bool | None = None,
        ge: Decimal | None = None,
        gt: Decimal | None = None,
        le: Decimal | None = None,
        lt: Decimal | None = None,
        choices: Collection[Decimal] | None = None,
        validators: Sequence[Callable[[str, Decimal], None]] | None = None,
    ) -> None:
        super().__init__(
            default=default,
            default_factory=default_factory,
            strict=strict,
            validators=validators,
        )
        self._ge, self._gt, self._le, self._lt = ge, gt, le, lt
        self._choices = choices

    def _coerce(self, key: str, raw: str) -> Decimal:
        text = raw if self._strict else raw.strip()
        if self._strict and raw != raw.strip():
            raise EnvParseError(key, raw, 'Decimal', 'strict mode forbids surrounding whitespace')
        try:
            return Decimal(text)
        except (InvalidOperation, ValueError) as exc:
            raise EnvParseError(key, raw, 'Decimal', str(exc)) from exc

    def _validate(self, key: str, value: Decimal) -> None:
        if self._ge is not None and value < self._ge:
            raise EnvValidationError(key, str(value), f'{value} < {self._ge} (ge)')
        if self._gt is not None and value <= self._gt:
            raise EnvValidationError(key, str(value), f'{value} <= {self._gt} (gt)')
        if self._le is not None and value > self._le:
            raise EnvValidationError(key, str(value), f'{value} > {self._le} (le)')
        if self._lt is not None and value >= self._lt:
            raise EnvValidationError(key, str(value), f'{value} >= {self._lt} (lt)')
        if self._choices is not None and value not in self._choices:
            raise EnvValidationError(key, str(value), f'{value} not in choices {list(self._choices)!r}')

class EnvTimedelta(_EnvType[timedelta]):
    """
    Duration variable parsed into :class:`datetime.timedelta`.

    Accepted formats:
    - Numeric seconds (for example ``"30"``, ``"0.5"``)
    - Compact duration strings (for example ``"1d2h30m15s"``)

    Optional bounds can be enforced with ``ge`` and ``le``.
    """

    _DURATION_RE = re.compile(
        r'^\s*(?:(?P<days>\d+)d)?(?:(?P<hours>\d+)h)?(?:(?P<minutes>\d+)m)?(?:(?P<seconds>\d+)s)?\s*$'
    )

    def __init__(
        self,
        *,
        default: timedelta | str | int | float | Any = _UNSET,
        default_factory: Callable[[], timedelta] | None = None,
        strict: bool | None = None,
        ge: timedelta | None = None,
        le: timedelta | None = None,
        validators: Sequence[Callable[[str, timedelta], None]] | None = None,
    ) -> None:
        super().__init__(
            default=default,
            default_factory=default_factory,
            strict=strict,
            validators=validators,
        )
        self._ge = ge
        self._le = le

    def _coerce(self, key: str, raw: str) -> timedelta:
        text = raw if self._strict else raw.strip()
        if self._strict and raw != raw.strip():
            raise EnvParseError(key, raw, 'timedelta', 'strict mode forbids surrounding whitespace')
        try:
            return timedelta(seconds=float(text))
        except ValueError:
            pass
        match = self._DURATION_RE.fullmatch(text)
        if not match or not any(match.groupdict().values()):
            raise EnvParseError(
                key,
                raw,
                'timedelta',
                'expected seconds as number or duration like 1d2h30m15s',
            )
        parts = {name: int(value or 0) for name, value in match.groupdict().items()}
        return timedelta(
            days=parts['days'],
            hours=parts['hours'],
            minutes=parts['minutes'],
            seconds=parts['seconds'],
        )

    def parse_default(self, key: str) -> timedelta:
        if not self.has_default():
            raise AttributeError(f'No default configured for "{key}".')
        value = self._make_default()
        if isinstance(value, timedelta):
            self._run_validators(key, value)
            return value
        if isinstance(value, (str, int, float)):
            return self.parse(key, str(value))
        raise EnvParseError(
            key,
            repr(value),
            'timedelta',
            'default must be timedelta, number of seconds, or duration string',
        )

    def _validate(self, key: str, value: timedelta) -> None:
        if self._ge is not None and value < self._ge:
            raise EnvValidationError(key, str(value), f'{value} < {self._ge} (ge)')
        if self._le is not None and value > self._le:
            raise EnvValidationError(key, str(value), f'{value} > {self._le} (le)')

class EnvIPv4(_EnvType[ipaddress.IPv4Address]):
    """IPv4 address variable parsed into ``ipaddress.IPv4Address``."""

    def _coerce(self, key: str, raw: str) -> ipaddress.IPv4Address:
        text = raw if self._strict else raw.strip()
        if self._strict and raw != raw.strip():
            raise EnvParseError(key, raw, 'IPv4', 'strict mode forbids surrounding whitespace')
        try:
            return ipaddress.IPv4Address(text)
        except ipaddress.AddressValueError as exc:
            raise EnvParseError(key, raw, 'IPv4', str(exc)) from exc

    def parse_default(self, key: str) -> ipaddress.IPv4Address:
        if not self.has_default():
            raise AttributeError(f'No default configured for "{key}".')
        value = self._make_default()
        if isinstance(value, ipaddress.IPv4Address):
            self._run_validators(key, value)
            return value
        if isinstance(value, str):
            return self.parse(key, value)
        raise EnvParseError(key, repr(value), 'IPv4', 'default must be IPv4Address or string')

class EnvIPv6(_EnvType[ipaddress.IPv6Address]):
    """IPv6 address variable parsed into ``ipaddress.IPv6Address``."""

    def _coerce(self, key: str, raw: str) -> ipaddress.IPv6Address:
        text = raw if self._strict else raw.strip()
        if self._strict and raw != raw.strip():
            raise EnvParseError(key, raw, 'IPv6', 'strict mode forbids surrounding whitespace')
        try:
            return ipaddress.IPv6Address(text)
        except ipaddress.AddressValueError as exc:
            raise EnvParseError(key, raw, 'IPv6', str(exc)) from exc

    def parse_default(self, key: str) -> ipaddress.IPv6Address:
        if not self.has_default():
            raise AttributeError(f'No default configured for "{key}".')
        value = self._make_default()
        if isinstance(value, ipaddress.IPv6Address):
            self._run_validators(key, value)
            return value
        if isinstance(value, str):
            return self.parse(key, value)
        raise EnvParseError(key, repr(value), 'IPv6', 'default must be IPv6Address or string')

class EnvEmail(_EnvType[str]):
    """
    Email-address string variable.

    Validation uses a pragmatic pattern suitable for application
    configuration input (not full RFC-level mailbox parsing).
    """

    _EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')

    def __init__(
        self,
        *,
        default: str | Any = _UNSET,
        default_factory: Callable[[], str] | None = None,
        strict: bool | None = None,
        validators: Sequence[Callable[[str, str], None]] | None = None,
    ) -> None:
        super().__init__(
            default=default,
            default_factory=default_factory,
            strict=strict,
            validators=validators,
        )

    def _coerce(self, key: str, raw: str) -> str:
        value = raw if self._strict else raw.strip()
        if self._strict and raw != raw.strip():
            raise EnvParseError(key, raw, 'email', 'strict mode forbids surrounding whitespace')
        if not self._EMAIL_RE.fullmatch(value):
            raise EnvParseError(key, raw, 'email', 'must be a valid email address')
        return value

    def parse_default(self, key: str) -> str:
        if not self.has_default():
            raise AttributeError(f'No default configured for "{key}".')
        value = self._make_default()
        if not isinstance(value, str):
            raise EnvParseError(key, repr(value), 'email', 'default must be a string')
        return self.parse(key, value)

class EnvUUID(_EnvType[UUID]):
    """
    UUID variable parsed into :class:`uuid.UUID`.

    Use ``versions`` to restrict accepted UUID versions
    (for example ``versions={4}``).
    """

    def __init__(
        self,
        *,
        default: UUID | str | Any = _UNSET,
        default_factory: Callable[[], UUID] | None = None,
        strict: bool | None = None,
        versions: Collection[int] | None = None,
        validators: Sequence[Callable[[str, UUID], None]] | None = None,
    ) -> None:
        super().__init__(
            default=default,
            default_factory=default_factory,
            strict=strict,
            validators=validators,
        )
        self._versions = versions

    def _coerce(self, key: str, raw: str) -> UUID:
        text = raw if self._strict else raw.strip()
        if self._strict and raw != raw.strip():
            raise EnvParseError(key, raw, 'UUID', 'strict mode forbids surrounding whitespace')
        try:
            parsed = UUID(text)
        except ValueError as exc:
            raise EnvParseError(key, raw, 'UUID', str(exc)) from exc
        if self._versions is not None and parsed.version not in self._versions:
            raise EnvValidationError(
                key,
                str(parsed),
                f'UUID version {parsed.version} not in allowed versions {list(self._versions)!r}',
            )
        return parsed

    def parse_default(self, key: str) -> UUID:
        if not self.has_default():
            raise AttributeError(f'No default configured for "{key}".')
        value = self._make_default()
        if isinstance(value, UUID):
            if self._versions is not None and value.version not in self._versions:
                raise EnvValidationError(
                    key,
                    str(value),
                    f'UUID version {value.version} not in allowed versions {list(self._versions)!r}',
                )
            self._run_validators(key, value)
            return value
        if isinstance(value, str):
            return self.parse(key, value)
        raise EnvParseError(key, repr(value), 'UUID', 'default must be UUID or string')

class EnvLiteral(_EnvType[Any]):
    """
    Variable that must match one of a fixed set of literal values.

    String literals are matched directly (optionally case-insensitive);
    non-string literals are matched via JSON decoding (for example ``true``,
    ``42``, ``null``).
    """

    def __init__(
        self,
        literals: Sequence[Any],
        *,
        default: Any = _UNSET,
        default_factory: Callable[[], Any] | None = None,
        strict: bool | None = None,
        case_sensitive: bool = False,
        validators: Sequence[Callable[[str, Any], None]] | None = None,
    ) -> None:
        if not literals:
            raise ValueError('EnvLiteral requires at least one literal value.')
        super().__init__(
            default=default,
            default_factory=default_factory,
            strict=strict,
            validators=validators,
        )
        self._literals = tuple(literals)
        self._case_sensitive = case_sensitive
        self._string_lookup: dict[str, Any] = {}
        self._non_string_literals: list[Any] = []
        for lit in self._literals:
            if isinstance(lit, str):
                key = lit if self._case_sensitive else lit.lower()
                self._string_lookup[key] = lit
            else:
                self._non_string_literals.append(lit)

    def _coerce(self, key: str, raw: str) -> Any:
        text = raw if self._strict else raw.strip()
        if self._strict and raw != raw.strip():
            raise EnvParseError(key, raw, 'Literal', 'strict mode forbids surrounding whitespace')
        lookup_key = text if self._case_sensitive else text.lower()
        if lookup_key in self._string_lookup:
            return self._string_lookup[lookup_key]
        try:
            parsed_json = json.loads(text)
        except json.JSONDecodeError:
            parsed_json = _UNSET
        if parsed_json is not _UNSET and parsed_json in self._non_string_literals:
            return parsed_json
        raise EnvParseError(key, raw, 'Literal', f'valid literals are {list(self._literals)!r}')

    def parse_default(self, key: str) -> Any:
        if not self.has_default():
            raise AttributeError(f'No default configured for "{key}".')
        value = self._make_default()
        if value in self._literals:
            self._run_validators(key, value)
            return value
        if isinstance(value, str):
            return self.parse(key, value)
        raise EnvParseError(key, repr(value), 'Literal', f'valid literals are {list(self._literals)!r}')

class EnvOptional(_EnvType[T | None]):
    """
    Optional wrapper around another spec.

    This is most useful for schema composition and explicit ``None`` defaults.
    Missing values resolve to ``default`` (``None`` by default), while present
    values are parsed by the wrapped ``inner`` spec.
    """

    def __init__(
        self,
        inner: _EnvType[T],
        *,
        default: T | None | Any = None,
        default_factory: Callable[[], T | None] | None = None,
        strict: bool | None = None,
        validators: Sequence[Callable[[str, T | None], None]] | None = None,
    ) -> None:
        super().__init__(
            default=default,
            default_factory=default_factory,
            strict=strict,
            validators=validators,
        )
        self._inner = inner

    def _effective_inner(self) -> _EnvType[T]:
        if self._strict and not self._inner.has_explicit_strict():
            return self._inner.with_strict(True)
        return self._inner

    def _coerce(self, key: str, raw: str) -> T | None:
        return self._effective_inner().parse(key, raw)

    def parse_default(self, key: str) -> T | None:
        if not self.has_default():
            raise AttributeError(f'No default configured for "{key}".')
        value = self._make_default()
        if value is None:
            self._run_validators(key, None)
            return None
        if isinstance(value, str):
            return self.parse(key, value)
        # Delegate non-string defaults through wrapped spec validation/coercion.
        encoded = json.dumps(value) if isinstance(value, (dict, list, bool, int, float, type(None))) else str(value)
        parsed = self._effective_inner().parse(key, encoded)
        self._run_validators(key, parsed)
        return parsed

class EnvUnion(_EnvType[Any]):
    """
    Union spec that tries multiple specs in order.

    The first spec that successfully parses the raw value wins. If none match,
    parsing fails with a combined error message.
    """

    def __init__(
        self,
        specs: Sequence[_EnvType[Any]],
        *,
        default: Any = _UNSET,
        default_factory: Callable[[], Any] | None = None,
        strict: bool | None = None,
        validators: Sequence[Callable[[str, Any], None]] | None = None,
    ) -> None:
        if not specs:
            raise ValueError('EnvUnion requires at least one spec.')
        super().__init__(
            default=default,
            default_factory=default_factory,
            strict=strict,
            validators=validators,
        )
        self._specs = list(specs)

    def _effective_specs(self) -> list[_EnvType[Any]]:
        if not self._strict:
            return self._specs
        return [spec.with_strict(True) if not spec.has_explicit_strict() else spec for spec in self._specs]

    def _coerce(self, key: str, raw: str) -> Any:
        errors: list[str] = []
        for spec in self._effective_specs():
            try:
                return spec.parse(key, raw)
            except Exception as exc:  # noqa: BLE001
                errors.append(str(exc))
        raise EnvParseError(key, raw, 'Union', f'no union member matched: {errors}')

    def has_default(self) -> bool:
        if super().has_default():
            return True
        return any(spec.has_default() for spec in self._effective_specs())

    def parse_default(self, key: str) -> Any:
        if super().has_default():
            return super().parse_default(key)
        for spec in self._effective_specs():
            if spec.has_default():
                return spec.parse_default(key)
        raise AttributeError(f'No default configured for "{key}".')

class EnvMapping(_EnvType[dict[str, Any]]):
    """
    JSON-object spec with per-field typed validation.

    ``fields`` maps object keys to specs. Missing keys may use per-field
    defaults. Set ``allow_extra=True`` to keep unknown keys instead of failing.
    """

    def __init__(
        self,
        fields: Mapping[str, _EnvType[Any]],
        *,
        default: Mapping[str, Any] | str | Any = _UNSET,
        default_factory: Callable[[], Mapping[str, Any]] | None = None,
        strict: bool | None = None,
        allow_extra: bool = False,
        validators: Sequence[Callable[[str, dict[str, Any]], None]] | None = None,
    ) -> None:
        super().__init__(
            default=default,
            default_factory=cast(Callable[[], dict[str, Any]] | None, default_factory),
            strict=strict,
            validators=validators,
        )
        self._fields = dict(fields)
        self._allow_extra = allow_extra

    def _coerce(self, key: str, raw: str) -> dict[str, Any]:
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise EnvParseError(key, raw, 'JSON object', str(exc)) from exc
        if not isinstance(obj, dict):
            raise EnvParseError(key, raw, 'mapping', 'expected a JSON object')
        return self._parse_obj(key, obj)

    def _parse_obj(self, key: str, obj: Mapping[str, Any]) -> dict[str, Any]:
        parsed: dict[str, Any] = {}
        for field_name, spec in self._fields.items():
            field_key = f'{key}.{field_name}'
            if field_name not in obj:
                if spec.has_default():
                    parsed[field_name] = spec.parse_default(field_key)
                    continue
                raise EnvValidationError(field_key, '<missing>', 'missing required mapping field')
            raw_value = _value_to_raw(obj[field_name])
            parsed[field_name] = spec.parse(field_key, raw_value)
        if not self._allow_extra:
            extras = sorted(set(obj.keys()) - set(self._fields.keys()))
            if extras:
                raise EnvValidationError(key, repr(obj), f'unexpected mapping keys: {extras!r}')
        return parsed

    def parse_default(self, key: str) -> dict[str, Any]:
        if not self.has_default():
            raise AttributeError(f'No default configured for "{key}".')
        value = self._make_default()
        if isinstance(value, str):
            return self.parse(key, value)
        if not isinstance(value, Mapping):
            raise EnvParseError(key, repr(value), 'mapping', 'default must be a mapping or JSON string')
        parsed = self._parse_obj(key, value)
        self._run_validators(key, parsed)
        return parsed

class EnvListOfSchema(_EnvType[list[dict[str, Any]]]):
    """
    JSON-array spec where each item is validated as a typed mapping.

    This is the list equivalent of :class:`EnvMapping`, using the same
    ``fields`` and ``allow_extra`` semantics for each object item.
    """

    def __init__(
        self,
        fields: Mapping[str, _EnvType[Any]],
        *,
        default: Sequence[Mapping[str, Any]] | str | Any = _UNSET,
        default_factory: Callable[[], Sequence[Mapping[str, Any]]] | None = None,
        strict: bool | None = None,
        allow_extra: bool = False,
        validators: Sequence[Callable[[str, list[dict[str, Any]]], None]] | None = None,
    ) -> None:
        super().__init__(
            default=default,
            default_factory=cast(Callable[[], list[dict[str, Any]]] | None, default_factory),
            strict=strict,
            validators=validators,
        )
        self._mapping_spec = EnvMapping(fields, strict=strict, allow_extra=allow_extra)

    def _coerce(self, key: str, raw: str) -> list[dict[str, Any]]:
        try:
            items = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise EnvParseError(key, raw, 'JSON array', str(exc)) from exc
        if not isinstance(items, list):
            raise EnvParseError(key, raw, 'list', 'expected a JSON array')
        parsed: list[dict[str, Any]] = []
        for idx, item in enumerate(items):
            if not isinstance(item, Mapping):
                raise EnvValidationError(
                    f'{key}[{idx}]', repr(item), 'list item must be a JSON object'
                )
            parsed.append(self._mapping_spec._parse_obj(f'{key}[{idx}]', item))
        return parsed

    def parse_default(self, key: str) -> list[dict[str, Any]]:
        if not self.has_default():
            raise AttributeError(f'No default configured for "{key}".')
        value = self._make_default()
        if isinstance(value, str):
            return self.parse(key, value)
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
            raise EnvParseError(key, repr(value), 'list', 'default must be a list or JSON array string')
        parsed: list[dict[str, Any]] = []
        for idx, item in enumerate(value):
            if not isinstance(item, Mapping):
                raise EnvValidationError(
                    f'{key}[{idx}]', repr(item), 'default list item must be a mapping'
                )
            parsed.append(self._mapping_spec._parse_obj(f'{key}[{idx}]', item))
        self._run_validators(key, parsed)
        return parsed

def _value_to_raw(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list, bool, int, float, type(None))):
        return json.dumps(value)
    return str(value)
