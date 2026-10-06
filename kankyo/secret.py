from dataclasses import field, dataclass

@dataclass(frozen=True, slots=True)
class Secret:
    """A value that stays redacted until explicitly revealed."""

    _value: str = field(repr=False)

    def __str__(self) -> str:
        return '********'

    def __repr__(self) -> str:
        return 'Secret(********)'

    def reveal(self) -> str:
        return self._value
