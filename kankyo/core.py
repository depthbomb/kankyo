from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast, Iterator, Mapping, TypeVar

from .exceptions import EnvMissingError
from .types import _EnvType, _UNSET, EnvStr

T = TypeVar('T')

_COMMENT_RE = re.compile(r'^\s*#')
_ASSIGN_RE = re.compile(
    r'''
    ^\s*
    (?:export\s+)?          # optional `export` prefix
    (?P<key>[A-Za-z_][A-Za-z0-9_]*)   # variable name
    \s*=\s*                 # equals sign (whitespace allowed)
    (?P<value>.*)           # everything after the equals
    $
    ''',
    re.VERBOSE,
)

_QUOTED_VALUE_RE = re.compile(
    r'''
    ^
    (?P<quote>['"])         # opening quote
    (?P<inner>.*?)          # content (non-greedy)
    (?P=quote)              # matching closing quote
    \s*(?:\#.*)?            # optional trailing comment
    $
    ''',
    re.VERBOSE | re.DOTALL,
)

_DOUBLE_QUOTE_ESCAPES: dict[str, str] = {
    'n': '\n',
    't': '\t',
    'r': '\r',
    '"': '"',
    '\\': '\\',
}
_DOUBLE_QUOTE_ESCAPE_RE = re.compile(r'\\([ntr"\\])')
_VAR_REF_RE = re.compile(r'\$\{([A-Za-z_][A-Za-z0-9_]*)\}')
_DEFAULT_STR_SPEC = EnvStr()

def _unescape_double_quoted(value: str) -> str:
    return _DOUBLE_QUOTE_ESCAPE_RE.sub(
        lambda match: _DOUBLE_QUOTE_ESCAPES[match.group(1)], value
    )

def _expand_env_vars(
    data: dict[str, str],
    *,
    strict_missing: bool,
) -> dict[str, str]:
    expanded: dict[str, str] = {}
    stack: list[str] = []

    def resolve(key: str) -> str:
        if key in expanded:
            return expanded[key]
        if key in stack:
            cycle = ' -> '.join([*stack, key])
            raise ValueError(f'cyclic env var expansion detected: {cycle}')
        stack.append(key)
        raw = data[key]

        def replace(match: re.Match[str]) -> str:
            ref = match.group(1)
            if ref in data:
                return resolve(ref)
            if strict_missing:
                raise KeyError(f'unresolved reference "{ref}"')
            return match.group(0)

        value = _VAR_REF_RE.sub(replace, raw)
        stack.pop()
        expanded[key] = value
        return value

    for key in data:
        resolve(key)

    return expanded

@dataclass(frozen=True)
class EnvTraceEntry:
    """Single assignment record for an environment key.

    Attributes
    ----------
    source:
        Source layer name (for example ``.env``, ``os.environ``, ``extra``).
    value:
        Raw value provided by that source before merge/override.
    """

    source: str
    value: str

@dataclass(frozen=True)
class EnvTrace:
    """Resolved provenance data returned by :meth:`Env.trace`.

    Attributes
    ----------
    key:
        Environment variable name.
    value:
        Current effective value after all merges and optional expansion.
    raw_value:
        Winning source value before any post-merge transformations.
    winner:
        Source name that supplied the final winning value.
    history:
        Full assignment history in source application order.
    """

    key: str
    value: str
    raw_value: str
    winner: str
    history: tuple[EnvTraceEntry, ...]


def _parse_env_file(path: Path) -> dict[str, str]:
    """
    Parse a ``.env`` file and return a ``{key: value}`` dict.

    Handles:
    - ``KEY=value`` and ``export KEY=value``
    - Single- and double-quoted values (quotes stripped, no escape processing
      for single-quoted; ``\\n`` / ``\\t`` etc. interpreted for double-quoted)
    - Inline ``# comments`` outside of quotes
    - Blank lines and full-line comments are silently skipped
    - Multi-line values are **not** supported (POSIX convention)
    """
    result: dict[str, str] = {}
    try:
        text = path.read_text(encoding='utf-8')
    except FileNotFoundError:
        return result
    except OSError as exc:
        raise OSError(f'Cannot read {path}: {exc}') from exc

    for raw_line in text.splitlines():
        line = raw_line.rstrip('\n')

        # Skip blanks and comments
        if not line.strip() or _COMMENT_RE.match(line):
            continue

        m = _ASSIGN_RE.match(line)
        if not m:
            continue  # silently skip malformed lines (like bash does)

        key = m.group('key')
        raw_value = m.group('value').strip()

        qm = _QUOTED_VALUE_RE.match(raw_value)
        if qm:
            quote = qm.group('quote')
            value = qm.group('inner')
            if quote == '"':
                # Interpret common escape sequences
                value = _unescape_double_quoted(value)
        else:
            # Unquoted: strip trailing inline comment
            value = re.sub(r'\s+#.*$', '', raw_value).strip()

        result[key] = value
    return result

class Env:
    """
    Loads, merges, and provides typed access to environment variables.

    Parameters
    ----------
    environment:
        Optional environment name (e.g. ``'production'``, ``'test'``).
        Controls which extra ``.env.<environment>`` and
        ``.env.<environment>.local`` files are loaded.
    root:
        Directory to search for ``.env`` files (defaults to ``Path.cwd()``).
    override_os_env:
        When ``True``, values from ``.env`` files can *override* values
        already present in ``os.environ``.  The default (``False``) gives
        ``os.environ`` the highest priority, which matches the behaviour of
        most CLI tools.
    extra:
        Extra ``{key: value}`` pairs injected *after* ``os.environ``,
        primarily useful for testing.
    eager:
        Immediately parse all ``.env`` files on construction (default
        ``True``).  Set to ``False`` if you want lazy loading.
    expand_vars:
        When ``True``, expand ``${OTHER_KEY}`` references after all sources
        are merged.
    strict:
        Global strict mode. If enabled, specs that did not explicitly set
        ``strict=...`` inherit strict behavior.
    strict_expansion:
        Controls whether unresolved ``${...}`` references raise an error during
        expansion. Defaults to the value of ``strict`` when omitted.

    Loading order (highest priority last → wins)
    --------------------------------------------
    1. ``.env``
    2. ``.env.<environment>``
    3. ``.env.local``
    4. ``.env.<environment>.local``
    5. ``os.environ``
    6. ``extra`` (for test injection)

    Example
    -------
    ::

        env = Env(environment='production', root=Path('/app'))
        port = env.get('PORT', EnvInt(ge=1024, le=65535, default=8080))
        host = env.get('HOST', EnvStr(default='0.0.0.0'))
    """

    def __init__(
        self,
        *,
        environment: str | None = None,
        root: str | Path | None = None,
        override_os_env: bool = False,
        extra: Mapping[str, str] | None = None,
        eager: bool = True,
        expand_vars: bool = False,
        strict: bool = False,
        strict_expansion: bool | None = None,
    ) -> None:
        self._environment = environment
        self._root = Path(root) if root else Path.cwd()
        self._override_os_env = override_os_env
        self._extra = dict(extra) if extra else {}
        self._expand_vars = expand_vars
        self._strict = strict
        self._strict_expansion = strict if strict_expansion is None else strict_expansion
        self._data: dict[str, str] = {}
        self._trace: dict[str, list[tuple[str, str]]] = {}
        self._loaded = False
        self._revision = 0

        if eager:
            self._load()

    def _candidate_files(self) -> list[Path]:
        """Return .env file paths in ascending priority order."""
        env = self._environment
        files = [
            self._root / '.env',
        ]
        if env:
            files.append(self._root / f'.env.{env}')
        files.append(self._root / '.env.local')
        if env:
            files.append(self._root / f'.env.{env}.local')
        return files

    def _source_layers(self) -> list[tuple[str, dict[str, str]]]:
        layers: list[tuple[str, dict[str, str]]] = []
        for path in self._candidate_files():
            layers.append((str(path.name), _parse_env_file(path)))

        if self._override_os_env:
            layers.insert(0, ('os.environ', dict(os.environ)))
        else:
            layers.append(('os.environ', dict(os.environ)))

        layers.append(('extra', dict(self._extra)))
        return layers

    def _load(self) -> None:
        """Merge all sources into ``self._data`` respecting priority."""
        merged: dict[str, str] = {}
        trace: dict[str, list[tuple[str, str]]] = {}
        for source, values in self._source_layers():
            for key, value in values.items():
                merged[key] = value
                trace.setdefault(key, []).append((source, value))

        if self._expand_vars:
            try:
                merged = _expand_env_vars(
                    merged,
                    strict_missing=self._strict_expansion,
                )
            except (ValueError, KeyError) as exc:
                raise ValueError(f'Failed to expand environment variables: {exc}') from exc

        self._data = merged
        self._trace = trace
        self._loaded = True
        self._revision += 1

    def reload(self) -> None:
        """Re-read all source files and rebuild the internal store."""
        self._load()

    def _resolve_spec(self, spec: _EnvType[T] | None) -> _EnvType[T]:
        resolved = cast(_EnvType[T], _DEFAULT_STR_SPEC) if spec is None else spec
        if self._strict and not resolved.has_explicit_strict():
            return resolved.with_strict(True)
        return resolved

    def get(self, key: str, spec: _EnvType[T] | None = None) -> T:
        """
        Retrieve *key* as a typed value governed by *spec*.

        Parameters
        ----------
        key:
            Environment variable name (case-sensitive).
        spec:
            An ``_EnvType`` instance.  Defaults to ``EnvStr()``.

        Returns
        -------
        The coerced, validated value.

        Raises
        ------
        EnvMissingError
            If *key* is absent and *spec* has no default.
        EnvParseError
            If the raw string cannot be converted to the target type.
        EnvValidationError
            If the converted value fails a validation constraint.
        """
        if not self._loaded:
            self._load()

        effective_spec = self._resolve_spec(spec)

        raw = self._data.get(key)

        if raw is None:
            if effective_spec.has_default():
                return effective_spec.parse_default(key)
            raise EnvMissingError(key)

        return effective_spec.parse(key, raw)

    def require(self, key: str, spec: _EnvType[T] | None = None) -> T:
        """
        Like ``get`` but always raises ``EnvMissingError`` when the key is
        absent, even if *spec* has a default.  Useful for documenting that a
        variable is truly required in a given context.
        """
        if not self._loaded:
            self._load()

        raw = self._data.get(key)
        if raw is None:
            raise EnvMissingError(key)

        effective_spec = self._resolve_spec(spec)

        return effective_spec.parse(key, raw)

    def get_raw(self, key: str, default: str | None = None) -> str | None:
        """Return the raw (unparsed) string value, or *default* if absent."""
        if not self._loaded:
            self._load()
        return self._data.get(key, default)

    def is_set(self, key: str) -> bool:
        """Return ``True`` if *key* is present in any source."""
        if not self._loaded:
            self._load()
        return key in self._data

    def get_many(self, specs: dict[str, _EnvType[Any]]) -> dict[str, Any]:
        """
        Resolve multiple variables in one call, collecting *all* errors
        before raising so callers see every problem at once.

        Parameters
        ----------
        specs:
            ``{key: spec}`` mapping.

        Returns
        -------
        ``{key: value}`` dict.

        Raises
        ------
        ExceptionGroup (Python 3.11+) or ``EnvSchemaError`` (earlier)
            Containing one sub-exception per failed variable.
        """
        results: dict[str, Any] = {}
        errors: list[Exception] = []

        for key, spec in specs.items():
            try:
                results[key] = self.get(key, spec)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        if errors:
            _raise_group(errors)

        return results

    def snapshot(self) -> dict[str, str]:
        """Return a shallow copy of the merged raw data store."""
        if not self._loaded:
            self._load()
        return dict(self._data)

    def trace(self, key: str) -> EnvTrace | None:
        """
        Return source provenance for *key*.

        Includes winning source, current value, raw winning value, and all
        source/value assignments in priority order.
        """
        if not self._loaded:
            self._load()
        history_raw = self._trace.get(key)
        if not history_raw:
            return None
        history = tuple(EnvTraceEntry(source=src, value=val) for src, val in history_raw)
        winner_source, raw_value = history_raw[-1]
        return EnvTrace(
            key=key,
            value=self._data[key],
            raw_value=raw_value,
            winner=winner_source,
            history=history,
        )

    def __enter__(self) -> 'Env':
        return self

    def __exit__(self, *_: Any) -> None:
        pass  # nothing to clean up by default

    def __contains__(self, key: object) -> bool:
        return self.is_set(str(key))

    def __iter__(self) -> Iterator[str]:
        if not self._loaded:
            self._load()
        return iter(self._data)

    def __len__(self) -> int:
        if not self._loaded:
            self._load()
        return len(self._data)

    def __repr__(self) -> str:
        env_label = f', environment={self._environment!r}' if self._environment else ''
        return (
            f'Env(root={self._root!r}{env_label}, '
            f"variables={len(self._data) if self._loaded else 'not yet loaded'})"
        )

    def patch(self, overrides: dict[str, str]) -> '_PatchContext':
        """
        Return a context manager that temporarily injects *overrides* into this
        ``Env`` instance.  Original values are restored on exit.

            with env.patch({'DEBUG': 'true', 'PORT': '9000'}):
                assert env.get('PORT', EnvInt()) == 9000
        """
        return _PatchContext(self, overrides)

class _PatchContext:
    def __init__(self, env: Env, overrides: dict[str, str]) -> None:
        self._env = env
        self._overrides = overrides
        self._saved_extra: dict[str, str | object] = {}
        self._saved_data: dict[str, str | object] = {}
        self._saved_trace: dict[str, list[tuple[str, str]] | object] = {}
        self._entered_revision = env._revision

    def __enter__(self) -> Env:
        for key, val in self._overrides.items():
            self._saved_extra[key] = self._env._extra.get(key, _UNSET)
            self._env._extra[key] = val
            if self._env._loaded:
                self._saved_data[key] = self._env._data.get(key, _UNSET)
                self._env._data[key] = val
                current_trace = self._env._trace.get(key)
                self._saved_trace[key] = list(current_trace) if current_trace is not None else _UNSET
                updated_trace = list(current_trace) if current_trace is not None else []
                if updated_trace and updated_trace[-1][0] == 'extra':
                    updated_trace[-1] = ('extra', val)
                else:
                    updated_trace.append(('extra', val))
                self._env._trace[key] = updated_trace
        return self._env

    def __exit__(self, *_: Any) -> None:
        for key, old_val in self._saved_extra.items():
            if old_val is _UNSET:
                self._env._extra.pop(key, None)
            else:
                self._env._extra[key] = old_val  # type: ignore[assignment]
        if self._env._loaded:
            if self._env._revision != self._entered_revision:
                self._env._load()
                return
            for key, old_val in self._saved_data.items():
                if old_val is _UNSET:
                    self._env._data.pop(key, None)
                else:
                    self._env._data[key] = old_val  # type: ignore[assignment]
            for key, old_trace in self._saved_trace.items():
                if old_trace is _UNSET:
                    self._env._trace.pop(key, None)
                else:
                    self._env._trace[key] = old_trace  # type: ignore[assignment]

def _raise_group(errors: list[Exception]) -> None:
    if not errors:
        return

    if sys.version_info >= (3, 11):
        raise ExceptionGroup(  # type: ignore[name-defined]  # noqa: F821
            'Multiple environment variable errors', errors
        )

    # Fallback: flatten into a single EnvSchemaError message
    lines = '\n'.join(f'  • {e}' for e in errors)
    raise EnvSchemaError(f'Multiple environment variable errors:\n{lines}') from errors[0]
