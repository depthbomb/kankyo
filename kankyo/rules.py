from __future__ import annotations

from uuid import UUID
from io import StringIO
from pathlib import Path
from math import isfinite
from csv import Error, reader
from datetime import timedelta
from kankyo.secret import Secret
from dataclasses import dataclass
from urllib.parse import urlsplit
from re import error as PatternError
from kankyo.errors import SchemaError
from re import compile as compile_pattern
from decimal import Decimal, InvalidOperation
from json import dumps, loads, JSONDecodeError
from ipaddress import IPv4Address, IPv6Address, ip_address as parse_ip

@dataclass(frozen=True, slots=True)
class Rule:
    """An immutable parsing rule. Use the factory functions to create rules."""

    kind: str
    minimum: int | float | Decimal | None = None
    maximum: int | float | Decimal | None = None
    min_length: int | None = None
    max_length: int | None = None
    pattern: str | None = None
    strip: bool = False
    choices: tuple[str, ...] = ()
    schemes: tuple[str, ...] = ()
    item: Rule | None = None
    separator: str = ','
    unique: bool = False

    def __post_init__(self) -> None:
        kinds = {'string', 'integer', 'number', 'boolean', 'decimal', 'duration', 'path', 'url',
                 'uuid', 'ip_address', 'one_of', 'list', 'secret'}
        if self.kind not in kinds:
            raise SchemaError(f'Unknown rule kind: {self.kind}')

        if any(not isinstance(flag, bool) for flag in (self.strip, self.unique)):
            raise SchemaError('strip and unique must be booleans')

        for bound in (self.minimum, self.maximum):
            if bound is not None:
                if self.kind not in {'integer', 'number', 'decimal'}:
                    raise SchemaError(f'{self.kind} does not support numeric bounds')
                if isinstance(bound, bool) or not isinstance(bound, (int, float, Decimal)):
                    raise SchemaError('Numeric bounds must be finite numbers')
                if not Decimal(str(bound)).is_finite():
                    raise SchemaError('Numeric bounds must be finite numbers')

        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise SchemaError('minimum must not exceed maximum')

        for length in (self.min_length, self.max_length):
            if length is not None:
                if self.kind not in {'string', 'secret', 'list'}:
                    raise SchemaError(f'{self.kind} does not support length bounds')
                if type(length) is not int or length < 0:
                    raise SchemaError('Length bounds must be non-negative integers')

        if self.min_length is not None and self.max_length is not None and self.min_length > self.max_length:
            raise SchemaError('min_length must not exceed max_length')

        if self.pattern is not None:
            if self.kind != 'string' or not isinstance(self.pattern, str):
                raise SchemaError('Only string rules support a string pattern')
            try:
                compile_pattern(self.pattern)
            except (PatternError, ValueError, TypeError) as exc:
                raise SchemaError('Invalid regular expression') from exc

        if self.strip and self.kind not in {'string', 'secret'}:
            raise SchemaError('Only string and secret rules support strip')

        if not isinstance(self.choices, tuple) or not isinstance(self.schemes, tuple):
            raise SchemaError('Choices and schemes must be tuples')

        if self.kind == 'one_of':
            if not self.choices or any(not isinstance(choice, str) for choice in self.choices):
                raise SchemaError('one_of requires at least one string choice')
            if len(set(self.choices)) != len(self.choices):
                raise SchemaError('one_of choices must be unique')
        elif self.choices:
            raise SchemaError('Only one_of rules support choices')

        if self.schemes:
            if self.kind != 'url' or any(not isinstance(scheme, str) or
                    not compile_pattern(r'[a-z][a-z0-9+.-]*').fullmatch(scheme) for scheme in self.schemes):
                raise SchemaError('URL schemes must be lowercase scheme names')

        if self.kind == 'list':
            if not isinstance(self.item, Rule):
                raise SchemaError('list_of requires an item rule')
            if not isinstance(self.separator, str) or len(self.separator) != 1 or self.separator in {'\n', '\r', '"', '\0'}:
                raise SchemaError('List separator must be one character other than a quote, newline, or NUL')
        elif self.item is not None or self.unique or self.separator != ',':
            raise SchemaError('Item rules, separators, and unique apply only to lists')

    def parse(self, value: object) -> object:
        """Parse a raw string or a Python default, without including values in errors."""
        try:
            parsed = self._parse(value)
            self._validate(parsed)
        except (ValueError, TypeError, OverflowError, InvalidOperation):
            raise ValueError(f'expected {self.describe()}') from None

        return parsed

    def describe(self) -> str:
        parts = [self.kind if self.kind != 'one_of' else 'one of the declared choices']
        if self.minimum is not None:
            parts.append(f'>= {self.minimum}')
        if self.maximum is not None:
            parts.append(f'<= {self.maximum}')
        if self.min_length is not None:
            parts.append(f'length >= {self.min_length}')
        if self.max_length is not None:
            parts.append(f'length <= {self.max_length}')
        if self.pattern is not None:
            parts.append('matching the declared pattern')
        if self.schemes:
            parts.append('with an allowed scheme')
        if self.item is not None:
            parts.append(f'of {self.item.describe()}')
        if self.unique:
            parts.append('with unique items')

        return ' '.join(parts)

    def encode_default(self, value: object) -> str:
        parsed = self.parse(value)
        if self.kind == 'path':
            return str(value)
        if self.kind == 'list':
            return dumps(self._default_items(value), ensure_ascii=True)

        return _text_value(parsed)

    def _default_items(self, value: object) -> list[object]:
        # Keep paths unexpanded so generation does not bake in the developer's home directory.
        assert self.item is not None
        items: list[object] = []
        for item in _list_items(value, self.separator):
            if self.item.kind == 'list':
                items.append(self.item._default_items(item))
            elif self.item.kind == 'path':
                items.append(str(item))
            else:
                items.append(_json_value(self.item.parse(item)))

        return items

    def _parse(self, value: object) -> object:
        if self.kind == 'list':
            return self._parse_list(value)

        if self.kind == 'secret' and isinstance(value, Secret):
            value = value.reveal()

        if self.kind in {'string', 'secret', 'one_of', 'url'}:
            if not isinstance(value, str):
                raise ValueError
            text = value.strip() if self.strip else value
            if self.kind == 'secret':
                return Secret(text)
            if self.kind == 'one_of' and text not in self.choices:
                raise ValueError
            if self.kind == 'url':
                self._validate_url(text)

            return text

        if self.kind == 'boolean':
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                normalized = value.strip().lower()
                if normalized in {'true', '1', 'yes', 'on'}:
                    return True
                if normalized in {'false', '0', 'no', 'off'}:
                    return False

            raise ValueError

        if isinstance(value, bool):
            raise ValueError

        if self.kind == 'integer':
            if isinstance(value, int):
                return value
            if isinstance(value, str) and compile_pattern(r'[+-]?[0-9]+').fullmatch(value.strip()):
                return int(value)

            raise ValueError

        if self.kind in {'number', 'decimal'}:
            if not isinstance(value, (str, int, float, Decimal)):
                raise ValueError
            number_value = Decimal(str(value).strip())
            if not number_value.is_finite():
                raise ValueError
            if self.kind == 'decimal':
                return number_value

            result = float(number_value)
            if not isfinite(result):
                raise ValueError

            return result

        if self.kind == 'duration':
            return _parse_duration(value)

        if self.kind == 'path':
            if isinstance(value, (str, Path)) and str(value) and '\0' not in str(value):
                return Path(value).expanduser()

            raise ValueError

        if self.kind == 'uuid':
            if isinstance(value, UUID):
                return value
            if isinstance(value, str):
                return UUID(value.strip())

            raise ValueError

        if isinstance(value, (IPv4Address, IPv6Address)):
            return value
        if isinstance(value, str):
            return parse_ip(value.strip())

        raise ValueError

    def _parse_list(self, value: object) -> list[object]:
        assert self.item is not None
        return [self.item.parse(item) for item in _list_items(value, self.separator)]

    def _validate(self, value: object) -> None:
        if isinstance(value, (int, float, Decimal)):
            if self.minimum is not None and value < self.minimum:
                raise ValueError
            if self.maximum is not None and value > self.maximum:
                raise ValueError

        measured = value.reveal() if isinstance(value, Secret) else value
        if isinstance(measured, (str, list)):
            if self.min_length is not None and len(measured) < self.min_length:
                raise ValueError
            if self.max_length is not None and len(measured) > self.max_length:
                raise ValueError

        if isinstance(value, str) and self.pattern is not None and not compile_pattern(self.pattern).fullmatch(value):
            raise ValueError

        if isinstance(value, list) and self.unique:
            for index, item in enumerate(value):
                if item in value[:index]:
                    raise ValueError

    def _validate_url(self, value: str) -> None:
        if any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError

        parsed = urlsplit(value)
        if not parsed.scheme or not parsed.hostname or (self.schemes and parsed.scheme not in self.schemes):
            raise ValueError

        # Accessing port also checks invalid and out-of-range port numbers.
        parsed.port

_DURATION_PART = compile_pattern(r'([0-9]+(?:\.[0-9]+)?)\s*(us|ms|s|m|h|d)')
_DURATION_UNITS = {'us': Decimal('0.000001'), 'ms': Decimal('0.001'), 's': Decimal(1),
                   'm': Decimal(60), 'h': Decimal(3600), 'd': Decimal(86400)}

def _list_items(value: object, separator: str) -> list[object] | tuple[object, ...]:
    if isinstance(value, str):
        if value.lstrip().startswith('['):
            try:
                value = loads(value, parse_float=Decimal)
            except JSONDecodeError:
                raise ValueError from None
        elif not value.strip():
            value = []
        else:
            try:
                rows = list(reader(StringIO(value), delimiter=separator, skipinitialspace=True, strict=True))
            except Error:
                raise ValueError from None
            if len(rows) != 1:
                raise ValueError

            value = rows[0]

    if not isinstance(value, (list, tuple)):
        raise ValueError

    return value

def _parse_duration(value: object) -> timedelta:
    if isinstance(value, timedelta):
        return value

    if isinstance(value, (int, float, Decimal)):
        seconds = Decimal(str(value))
    elif isinstance(value, str):
        text = value.strip()
        try:
            seconds = Decimal(text)
        except InvalidOperation:
            sign = -1 if text.startswith('-') else 1
            text = text[1:] if text.startswith(('-', '+')) else text
            seconds = Decimal(0)
            position = 0
            for match in _DURATION_PART.finditer(text):
                if text[position:match.start()].strip():
                    raise ValueError
                seconds += Decimal(match[1]) * _DURATION_UNITS[match[2]]
                position = match.end()

            if position == 0 or text[position:].strip():
                raise ValueError

            seconds *= sign
    else:
        raise ValueError

    if not seconds.is_finite():
        raise ValueError

    return timedelta(microseconds=int(seconds * 1_000_000))

def _text_value(value: object) -> str:
    if isinstance(value, Secret):
        return value.reveal()
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, timedelta):
        microseconds = (value.days * 86400 + value.seconds) * 1_000_000 + value.microseconds
        return str(Decimal(microseconds) / 1_000_000)

    return str(value)

def _json_value(value: object) -> object:
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, (str, int, float, bool)):
        return value

    return _text_value(value)

def string(*, strip: bool = False, min_length: int | None = None, max_length: int | None = None,
           pattern: str | None = None) -> Rule:
    return Rule('string', strip=strip, min_length=min_length, max_length=max_length, pattern=pattern)

def integer(*, minimum: int | None = None, maximum: int | None = None) -> Rule:
    return Rule('integer', minimum=minimum, maximum=maximum)

def number(*, minimum: float | None = None, maximum: float | None = None) -> Rule:
    return Rule('number', minimum=minimum, maximum=maximum)

def boolean() -> Rule:
    return Rule('boolean')

def decimal(*, minimum: Decimal | None = None, maximum: Decimal | None = None) -> Rule:
    return Rule('decimal', minimum=minimum, maximum=maximum)

def duration() -> Rule:
    """Parse seconds or compound durations such as '1h 30m'."""
    return Rule('duration')

def path() -> Rule:
    return Rule('path')

def url(*, schemes: tuple[str, ...] = ()) -> Rule:
    return Rule('url', schemes=schemes)

def uuid() -> Rule:
    return Rule('uuid')

def ip_address() -> Rule:
    return Rule('ip_address')

def one_of(*choices: str) -> Rule:
    return Rule('one_of', choices=choices)

def list_of(item: Rule, *, separator: str = ',', min_length: int | None = None,
            max_length: int | None = None, unique: bool = False) -> Rule:
    return Rule('list', item=item, separator=separator, min_length=min_length, max_length=max_length, unique=unique)

def secret(*, strip: bool = False, min_length: int | None = None, max_length: int | None = None) -> Rule:
    return Rule('secret', strip=strip, min_length=min_length, max_length=max_length)
