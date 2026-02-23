from __future__ import annotations

from typing import Any, Callable, cast, Generic, Protocol, TypeVar

from .exceptions import EnvSchemaError
from .types import _EnvType

T = TypeVar('T')
S = TypeVar('S', bound='EnvSchema')

class EnvVar(Generic[T]):
    """
    Descriptor that binds a variable *key* to a typed *spec*.

    Parameters
    ----------
    key:
        The environment variable name to look up (e.g. ``'DATABASE_URL'``).
    spec:
        An ``_EnvType`` instance (``EnvStr``, ``EnvInt``, …) that controls
        coercion and validation.  If omitted, ``EnvStr()`` is used.
    description:
        Optional human-readable explanation shown in validation error messages
        and schema introspection.

    When used as a *class-level attribute* inside an ``EnvSchema`` subclass,
    this descriptor stores its parsed value per-instance so that
    ``MySchema(...).port`` returns an ``int``, not an ``EnvVar``.
    """

    def __init__(
        self,
        key: str,
        spec: _EnvType[T] | None = None,
        *,
        description: str = '',
    ) -> None:
        if not key:
            raise EnvSchemaError('EnvVar key must be a non-empty string.')
        self.key = key
        self.spec: _EnvType[T] = spec if spec is not None else cast(_EnvType[T], _default_spec())
        self.description = description
        self._attr_name: str = ''

    def __set_name__(self, owner: type, name: str) -> None:
        self._attr_name = name

    # Return the cached value from the instance dict when accessed on an instance; return *self* when accessed on the
    # class.
    def __get__(self, obj: Any, objtype: type | None = None) -> 'T | EnvVar[T]':
        if obj is None:
            return self
        try:
            return cast(T, obj.__dict__[self._attr_name])
        except KeyError:
            raise AttributeError(
                f'"{type(obj).__name__}.{self._attr_name}" has not been resolved yet. '
                'Did you forget to call EnvSchema.__init__?'
            ) from None

    def __set__(self, obj: Any, value: T) -> None:
        obj.__dict__[self._attr_name] = value

    def __repr__(self) -> str:
        return (
            f'EnvVar(key={self.key!r}, spec={self.spec!r}'
            + (f', description={self.description!r}' if self.description else '')
            + ')'
        )

class EnvNested(Generic[S]):
    """
    Descriptor for nested schemas with prefixed environment keys.

    Parameters
    ----------
    schema_cls:
        ``EnvSchema`` subclass used to resolve the nested config object.
    prefix:
        Prefix prepended to nested keys (for example ``DB``).
    separator:
        Delimiter between prefix and child key (default ``"__"``).
    description:
        Optional human-readable description for introspection.

    Example
    -------
    ``db: DBConfig = EnvNested(DBConfig, prefix='DB')`` maps ``host`` to
    ``DB__HOST`` when ``separator="__"``.
    """

    def __init__(
        self,
        schema_cls: type[S],
        *,
        prefix: str,
        separator: str = '__',
        description: str = '',
    ) -> None:
        if not prefix:
            raise EnvSchemaError('EnvNested prefix must be a non-empty string.')
        self.schema_cls = schema_cls
        self.prefix = prefix
        self.separator = separator
        self.description = description
        self._attr_name: str = ''

    def __set_name__(self, owner: type, name: str) -> None:
        self._attr_name = name

    def __get__(self, obj: Any, objtype: type | None = None) -> 'S | EnvNested[S]':
        if obj is None:
            return self
        try:
            return cast(S, obj.__dict__[self._attr_name])
        except KeyError:
            raise AttributeError(
                f'"{type(obj).__name__}.{self._attr_name}" has not been resolved yet.'
            ) from None

    def __set__(self, obj: Any, value: S) -> None:
        obj.__dict__[self._attr_name] = value

    def __repr__(self) -> str:
        return (
            f'EnvNested(schema_cls={self.schema_cls.__name__}, '
            f'prefix={self.prefix!r}, separator={self.separator!r})'
        )

class EnvComputed(Generic[T]):
    """
    Descriptor for computed fields derived from resolved schema state.

    The compute callable runs after all ``EnvVar`` and ``EnvNested`` fields
    have been resolved, so it can safely reference those attributes.
    """

    def __init__(
        self,
        compute: Callable[[Any], T],
        *,
        description: str = '',
    ) -> None:
        self.compute = compute
        self.description = description
        self._attr_name: str = ''

    def __set_name__(self, owner: type, name: str) -> None:
        self._attr_name = name

    def __get__(self, obj: Any, objtype: type | None = None) -> 'T | EnvComputed[T]':
        if obj is None:
            return self
        try:
            return cast(T, obj.__dict__[self._attr_name])
        except KeyError:
            raise AttributeError(
                f'"{type(obj).__name__}.{self._attr_name}" has not been resolved yet.'
            ) from None

    def __set__(self, obj: Any, value: T) -> None:
        obj.__dict__[self._attr_name] = value

    def __repr__(self) -> str:
        name = getattr(self.compute, '__name__', type(self.compute).__name__)
        return (
            'EnvComputed('
            + (f'description={self.description!r}' if self.description else name)
            + ')'
        )

def _default_spec() -> _EnvType[str]:
    from .types import EnvStr
    return EnvStr()

class _EnvSchemaMeta(type):
    """Collect ``EnvVar`` fields declared on the class body."""

    def __new__(
        mcs,
        name: str,
        bases: tuple[type, ...],
        namespace: dict[str, Any],
    ) -> '_EnvSchemaMeta':
        fields: dict[str, EnvVar[Any]] = {}
        nested_fields: dict[str, EnvNested[Any]] = {}
        computed_fields: dict[str, EnvComputed[Any]] = {}

        # Inherit fields from base schemas
        for base in reversed(bases):
            if hasattr(base, '__env_fields__'):
                fields.update(base.__env_fields__)
            if hasattr(base, '__env_nested_fields__'):
                nested_fields.update(base.__env_nested_fields__)
            if hasattr(base, '__env_computed_fields__'):
                computed_fields.update(base.__env_computed_fields__)

        # Collect from this class
        for attr, val in namespace.items():
            if isinstance(val, EnvVar):
                if not val._attr_name:
                    val._attr_name = attr
                fields[attr] = val
            elif isinstance(val, EnvNested):
                if not val._attr_name:
                    val._attr_name = attr
                nested_fields[attr] = val
            elif isinstance(val, EnvComputed):
                if not val._attr_name:
                    val._attr_name = attr
                computed_fields[attr] = val

        namespace['__env_fields__'] = fields
        namespace['__env_nested_fields__'] = nested_fields
        namespace['__env_computed_fields__'] = computed_fields
        return super().__new__(mcs, name, bases, namespace)

class _EnvReader(Protocol):
    def get(self, key: str, spec: _EnvType[Any] | None = None) -> Any: ...
    def require(self, key: str, spec: _EnvType[Any] | None = None) -> Any: ...
    def get_raw(self, key: str, default: str | None = None) -> str | None: ...
    def is_set(self, key: str) -> bool: ...

class _PrefixedEnvProxy:
    def __init__(self, env: _EnvReader, prefix: str, separator: str) -> None:
        self._env = env
        self._prefix = prefix
        self._separator = separator

    def _k(self, key: str) -> str:
        return f'{self._prefix}{self._separator}{key}'

    def get(self, key: str, spec: _EnvType[Any] | None = None) -> Any:
        return self._env.get(self._k(key), spec)

    def require(self, key: str, spec: _EnvType[Any] | None = None) -> Any:
        return self._env.require(self._k(key), spec)

    def get_raw(self, key: str, default: str | None = None) -> str | None:
        return self._env.get_raw(self._k(key), default)

    def is_set(self, key: str) -> bool:
        return self._env.is_set(self._k(key))

class EnvSchema(metaclass=_EnvSchemaMeta):
    """
    Base class for declarative environment-variable schemas.

    Sub-class this and declare class-level descriptors:
    - ``EnvVar`` for direct key bindings
    - ``EnvNested`` for prefixed nested schemas
    - ``EnvComputed`` for derived fields

    Pass an ``Env``-compatible object to the constructor to resolve and
    validate eagerly. Resolution order is direct fields, then nested schemas,
    then computed fields.

    Example
    -------
    ::

        from kankyo import Env, EnvSchema, EnvVar, EnvStr, EnvInt, EnvBool

        class Config(EnvSchema):
            app_name: str = EnvVar('APP_NAME', EnvStr(default='myapp'))
            port: int     = EnvVar('PORT',     EnvInt(ge=1024, le=65535, default=8080))
            debug: bool   = EnvVar('DEBUG',    EnvBool(default=False))

        env = Env(environment='production')
        cfg = Config(env)
        print(cfg.port)    # 8080
        print(cfg.debug)   # False
    """

    #: Populated by the metaclass; maps attr name → EnvVar descriptor.
    __env_fields__: dict[str, EnvVar[Any]]
    __env_nested_fields__: dict[str, EnvNested[Any]]
    __env_computed_fields__: dict[str, EnvComputed[Any]]

    def __init__(self, env: _EnvReader) -> None:
        errors: list[str] = []
        for attr, env_field in self.__env_fields__.items():
            try:
                value = env.get(env_field.key, env_field.spec)
                object.__setattr__(self, attr, value)
            except Exception as exc:  # noqa: BLE001
                errors.append(f'  • {env_field.key}: {exc}')

        for attr, nested_field in self.__env_nested_fields__.items():
            try:
                proxy = _PrefixedEnvProxy(env, nested_field.prefix, nested_field.separator)
                value = nested_field.schema_cls(proxy)
                object.__setattr__(self, attr, value)
            except Exception as exc:  # noqa: BLE001
                errors.append(f'  • {nested_field.prefix}{nested_field.separator}*: {exc}')

        for attr, computed_field in self.__env_computed_fields__.items():
            try:
                value = computed_field.compute(self)
                object.__setattr__(self, attr, value)
            except Exception as exc:  # noqa: BLE001
                errors.append(f'  • computed "{attr}": {exc}')

        if errors:
            lines = '\n'.join(errors)
            raise EnvSchemaError(
                f'{type(self).__name__} failed to load environment variables:\n{lines}'
            )

    def __repr__(self) -> str:
        parts = []
        for attr in self._all_schema_attrs():
            try:
                parts.append(f'{attr}={getattr(self, attr)!r}')
            except AttributeError:
                parts.append(f'{attr}=<unresolved>')
        fields = ', '.join(parts)
        return f'{type(self).__name__}({fields})'

    def as_dict(self) -> dict[str, Any]:
        """Return all resolved variable values keyed by *attribute name*."""
        return {attr: getattr(self, attr) for attr in self._all_schema_attrs()}

    @classmethod
    def _all_schema_attrs(cls) -> list[str]:
        return [
            *cls.__env_fields__.keys(),
            *cls.__env_nested_fields__.keys(),
            *cls.__env_computed_fields__.keys(),
        ]

# Re-export so callers can ``from kankyo.schema import Env`` without circular imports
# (actual definition lives in kankyo.core)
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    pass
