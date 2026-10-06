from dataclasses import dataclass

class KankyoError(Exception):
    """Base class for schema, source, generation, and configuration errors."""

class SchemaError(KankyoError, ValueError):
    """The schema cannot produce a valid client."""

class GenerationError(KankyoError):
    """The client could not be generated."""

class SourceError(KankyoError):
    """An environment file could not be read or parsed."""

@dataclass(frozen=True)
class Issue:
    field: str
    variable: str
    reason: str

    def __str__(self) -> str:
        return f'{self.field} ({self.variable}): {self.reason}'

class ConfigError(KankyoError, ValueError):
    """All configuration problems found during a load, without raw values."""

    issues: tuple[Issue, ...]

    def __init__(self, issues: tuple[Issue, ...]) -> None:
        self.issues = issues
        super().__init__('Invalid environment configuration:\n' + '\n'.join(f'  {issue}' for issue in issues))
