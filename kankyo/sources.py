from __future__ import annotations

from os import environ
from pathlib import Path
from re import fullmatch
from collections.abc import Mapping
from kankyo.errors import SourceError

def _quoted_value(text: str, position: int, quote: str, source: str, line: int) -> tuple[str, int]:
    value: list[str] = []
    escapes = {'n': '\n', 'r': '\r', 't': '\t', '\\': '\\', '"': '"'}
    while position < len(text):
        character = text[position]
        position += 1
        if character == quote:
            return ''.join(value), position

        if character == '\\' and quote == '"' and position < len(text):
            escaped = text[position]
            position += 1
            value.append(escapes.get(escaped, '\\' + escaped))
        else:
            value.append(character)

    raise SourceError(f'{source}:{line}: unterminated quoted value')

def parse_dotenv(text: str, *, source: str = '<string>') -> dict[str, str]:
    """Parse dotenv text without expansion, execution, or changes to os.environ."""
    text = text.removeprefix('\ufeff').replace('\r\n', '\n').replace('\r', '\n')
    values: dict[str, str] = {}
    position = 0
    line = 1
    while position < len(text):
        start = position
        end = text.find('\n', position)
        if end == -1:
            end = len(text)

        content = text[position:end].strip()
        if not content or content.startswith('#'):
            position = min(end + 1, len(text))
            line += 1
            continue

        assignment = text[position:end].lstrip()
        if assignment.startswith('export ') or assignment.startswith('export\t'):
            assignment = assignment[6:].lstrip()

        key, separator, raw = assignment.partition('=')
        key = key.strip()
        if not separator or not fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', key):
            raise SourceError(f'{source}:{line}: expected NAME=value')

        raw = raw.lstrip()
        if raw.startswith(('"', "'")):
            quote_position = end - len(raw)
            value, position = _quoted_value(text, quote_position + 1, raw[0], source, line)
            end = text.find('\n', position)
            if end == -1:
                end = len(text)
            tail = text[position:end].strip()
            if tail and not tail.startswith('#'):
                raise SourceError(f'{source}:{line}: unexpected text after quoted value')
        else:
            value = raw
            for index, character in enumerate(raw):
                if character == '#' and (index == 0 or raw[index - 1].isspace()):
                    value = raw[:index]
                    break

            value = value.rstrip()

        if '\0' in value:
            raise SourceError(f'{source}:{line}: NUL is not allowed in environment values')

        values[key] = value
        position = min(end + 1, len(text))
        line += text[start:position].count('\n')

    return values

def load_environment(*, root: str | Path = '.', environment: str | None = None,
                     overrides: Mapping[str, str] | None = None) -> dict[str, str]:
    """Merge dotenv layers, the process environment, and explicit overrides."""
    if environment is not None and not fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', environment):
        raise SourceError('environment must be a non-empty name without path separators')

    filenames = ['.env']
    if environment is not None:
        filenames.append(f'.env.{environment}')
    filenames.append('.env.local')
    if environment is not None:
        filenames.append(f'.env.{environment}.local')

    values: dict[str, str] = {}
    for filename in filenames:
        file = Path(root) / filename
        try:
            text = file.read_text(encoding='utf-8-sig')
        except FileNotFoundError:
            continue
        except (OSError, UnicodeError):
            raise SourceError(f'Cannot read environment file: {file}') from None

        values.update(parse_dotenv(text, source=str(file)))

    values.update(environ)
    if overrides is not None:
        values.update(overrides)

    return values
