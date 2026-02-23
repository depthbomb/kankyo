class EnvError(Exception):
    """Base class for all kankyo errors."""

class EnvMissingError(EnvError):
    """Raised when a required environment variable is not set."""

    def __init__(self, key: str) -> None:
        self.key = key
        super().__init__(f'Required environment variable "{key}" is not set.')

class EnvValidationError(EnvError):
    """Raised when a variable's value fails a validation rule."""

    def __init__(self, key: str, value: str, reason: str) -> None:
        self.key = key
        self.value = value
        self.reason = reason
        super().__init__(f'Validation failed for "{key}" (value={value!r}): {reason}')

class EnvParseError(EnvError):
    """Raised when a variable's raw string cannot be coerced to the target type."""

    def __init__(self, key: str, value: str, target_type: str, detail: str = '') -> None:
        self.key = key
        self.value = value
        self.target_type = target_type
        msg = f'Cannot parse "{key}" (value={value!r}) as {target_type}'
        if detail:
            msg += f': {detail}'
        super().__init__(msg)


class EnvSchemaError(EnvError):
    """Raised when an EnvSchema definition contains an error."""
