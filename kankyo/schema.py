from __future__ import annotations

from re import fullmatch
from kankyo.rules import Rule
from keyword import iskeyword
from dataclasses import dataclass
from types import MappingProxyType
from collections.abc import Mapping
from kankyo.errors import Issue, ConfigError, SchemaError

@dataclass(frozen=True, slots=True)
class Field:
    """An environment binding. Create one with env()."""

    variable: str
    rule: Rule
    optional: bool = False
    aliases: tuple[str, ...] = ()
    description: str = ''
    default: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.rule, Rule):
            raise SchemaError('Environment fields require a parsing rule')
        if not isinstance(self.aliases, tuple):
            raise SchemaError('Aliases must be a tuple of environment variable names')
        for name in (self.variable, *self.aliases):
            if not isinstance(name, str) or not fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', name):
                raise SchemaError('Environment variable names must be valid ASCII identifiers')

        if len(set((self.variable, *self.aliases))) != 1 + len(self.aliases):
            raise SchemaError(f'Duplicate aliases for {self.variable}')
        if not isinstance(self.optional, bool) or not isinstance(self.description, str):
            raise SchemaError('optional must be a bool and description must be a string')
        if self.default is not None:
            if not isinstance(self.default, str):
                raise SchemaError('Field defaults must be encoded strings; use env() for Python defaults')
            try:
                self.rule.parse(self.default)
            except ValueError:
                raise SchemaError(f'Invalid default for {self.variable}: {self.rule.describe()}') from None

class Schema:
    """Named Python fields bound to environment variables and parsing rules."""

    fields: Mapping[str, Field]

    def __init__(self, /, **fields: Field) -> None:
        for name, field in fields.items():
            if not valid_identifier(name):
                raise SchemaError(f'Invalid Python field name: {name!r}')
            if not isinstance(field, Field):
                raise SchemaError(f'{name} must be an env() field')

        self.fields = MappingProxyType(dict(fields))

    def load(self, source: Mapping[str, str]) -> dict[str, object]:
        """Validate all fields before returning any values."""
        values: dict[str, object] = {}
        issues: list[Issue] = []
        for name, field in self.fields.items():
            variable = field.variable
            for candidate in (field.variable, *field.aliases):
                if candidate in source:
                    variable = candidate
                    raw = source[candidate]
                    if not isinstance(raw, str):
                        issues.append(Issue(name, variable, 'environment values must be strings'))
                        break
                    try:
                        values[name] = field.rule.parse(raw)
                    except ValueError as exc:
                        issues.append(Issue(name, variable, str(exc)))
                    break
            else:
                if field.default is not None:
                    values[name] = field.rule.parse(field.default)
                elif field.optional:
                    values[name] = None
                else:
                    issues.append(Issue(name, variable, 'required variable is missing'))

        if issues:
            raise ConfigError(tuple(issues))

        return values

_MISSING = object()

def valid_identifier(name: str) -> bool:
    return name.isascii() and name.isidentifier() and not name.startswith('_') and not iskeyword(name)

def env(variable: str, rule: Rule, *, default: object = _MISSING, optional: bool = False,
        aliases: tuple[str, ...] = (), description: str = '') -> Field:
    """Bind a field to a variable. Missing values are errors unless configured otherwise."""
    if not isinstance(rule, Rule):
        raise SchemaError('env() requires a parsing rule')

    raw_default = None
    if default is not _MISSING:
        try:
            raw_default = rule.encode_default(default)
        except ValueError:
            raise SchemaError(f'Invalid default for {variable}: {rule.describe()}') from None

    return Field(variable, rule, optional=optional, aliases=aliases, description=description, default=raw_default)
