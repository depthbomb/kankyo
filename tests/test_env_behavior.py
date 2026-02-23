from __future__ import annotations

import ipaddress
from datetime import timedelta
from decimal import Decimal
from enum import Enum
from uuid import UUID

import pytest

from kankyo import (Env, EnvBool, EnvComputed, EnvDecimal, EnvEmail, EnvEnum, EnvFloat, EnvInt, EnvIPv4, EnvIPv6, EnvJson, EnvList, EnvListOfSchema, EnvLiteral, EnvMapping, EnvNested, EnvOptional, EnvPath, EnvSchema, EnvSecret, EnvStr, EnvTimedelta, EnvUnion, EnvUrl, EnvUUID, EnvVar)
from kankyo.exceptions import EnvMissingError, EnvParseError, EnvValidationError

def test_missing_default_is_coerced_and_typed() -> None:
    env = Env(eager=True)
    value = env.get("KANKYO_TEST_MISSING_INT", EnvInt(default="8080"))
    assert value == 8080
    assert isinstance(value, int)


def test_missing_default_is_validated(tmp_path) -> None:
    env = Env(eager=True)
    missing_path = tmp_path / "missing-file-for-kankyo-test"
    spec = EnvPath(default=missing_path, must_exist=True)
    with pytest.raises(EnvValidationError):
        env.get("KANKYO_TEST_MISSING_PATH", spec)


def test_mutable_defaults_are_not_shared_between_calls() -> None:
    env = Env(eager=True)

    json_spec = EnvJson(default={})
    first_json = env.get("KANKYO_TEST_MISSING_JSON", json_spec)
    first_json["x"] = 1
    second_json = env.get("KANKYO_TEST_MISSING_JSON", json_spec)
    assert first_json is not second_json
    assert second_json == {}

    list_spec = EnvList(default=["1"], subtype=EnvInt())
    first_list = env.get("KANKYO_TEST_MISSING_LIST", list_spec)
    first_list.append(2)
    second_list = env.get("KANKYO_TEST_MISSING_LIST", list_spec)
    assert first_list is not second_list
    assert second_list == [1]


def test_default_factory_returns_fresh_values() -> None:
    env = Env(eager=True)
    spec = EnvJson(default_factory=dict)
    first = env.get("KANKYO_TEST_MISSING_FACTORY", spec)
    second = env.get("KANKYO_TEST_MISSING_FACTORY", spec)
    assert first == {}
    assert second == {}
    assert first is not second


def test_patch_works_with_lazy_env_and_reload() -> None:
    env = Env(eager=False, extra={"KANKYO_TEST_PATCH": "1"})
    with env.patch({"KANKYO_TEST_PATCH": "2"}):
        assert env.get_raw("KANKYO_TEST_PATCH") == "2"
        env.reload()
        assert env.get_raw("KANKYO_TEST_PATCH") == "2"
    assert env.get_raw("KANKYO_TEST_PATCH") == "1"


def test_double_quoted_escape_preserves_escaped_quote(tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text('KANKYO_TEST_QUOTE="a\\\"b"\n', encoding="utf-8")
    env = Env(root=tmp_path, eager=True)
    assert env.get_raw("KANKYO_TEST_QUOTE") == 'a"b'


def test_url_require_tld_raises_validation_not_type_error() -> None:
    env = Env(extra={"KANKYO_TEST_URL": "http://user:pass@"}, eager=True)
    with pytest.raises(EnvValidationError):
        env.get("KANKYO_TEST_URL", EnvUrl(require_tld=True))


def test_secret_validation_error_redacts_value() -> None:
    env = Env(extra={"KANKYO_TEST_SECRET": "supersecret"}, eager=True)
    with pytest.raises(EnvValidationError) as exc:
        env.get("KANKYO_TEST_SECRET", EnvSecret(max_length=3))
    message = str(exc.value)
    assert "supersecret" not in message
    assert "********" in message


def test_secret_default_error_redacts_value() -> None:
    env = Env(eager=True)
    with pytest.raises(EnvValidationError) as exc:
        env.get("KANKYO_TEST_SECRET_MISSING", EnvSecret(default="toolong", max_length=3))
    message = str(exc.value)
    assert "toolong" not in message
    assert "********" in message


def test_secret_validator_reason_is_redacted() -> None:
    def leak_validator(key, value):
        raise EnvValidationError(key, value, f"bad secret: {value}")

    env = Env(extra={"KANKYO_TEST_SECRET_VALIDATOR": "supersecret"}, eager=True)
    with pytest.raises(EnvValidationError) as exc:
        env.get("KANKYO_TEST_SECRET_VALIDATOR", EnvSecret(validators=[leak_validator]))
    message = str(exc.value)
    assert "supersecret" not in message
    assert "bad secret" not in message
    assert "secret validation failed" in message


def test_secret_non_env_validator_exception_is_redacted() -> None:
    def leak_value_error(key, value):
        raise ValueError(f"leaked: {value}")

    env = Env(extra={"KANKYO_TEST_SECRET_VALUE_ERROR": "supersecret"}, eager=True)
    with pytest.raises(EnvValidationError) as exc:
        env.get("KANKYO_TEST_SECRET_VALUE_ERROR", EnvSecret(validators=[leak_value_error]))
    message = str(exc.value)
    assert "supersecret" not in message
    assert "leaked:" not in message
    assert "secret validation failed" in message


def test_enum_lookup_supports_value_and_name_case_insensitive() -> None:
    class Mode(str, Enum):
        DEV = "dev"
        PROD = "prod"

    env = Env(extra={"KANKYO_TEST_ENUM_VALUE": "PROD", "KANKYO_TEST_ENUM_NAME": "dev"}, eager=True)
    spec = EnvEnum(Mode)
    assert env.get("KANKYO_TEST_ENUM_VALUE", spec) is Mode.PROD
    assert env.get("KANKYO_TEST_ENUM_NAME", spec) is Mode.DEV


def test_invalid_non_string_defaults_raise_parse_errors() -> None:
    env = Env(eager=True)

    with pytest.raises(EnvParseError):
        env.get("KANKYO_TEST_BAD_INT_DEFAULT", EnvInt(default=1.2))
    with pytest.raises(EnvParseError):
        env.get("KANKYO_TEST_BAD_BOOL_DEFAULT", EnvBool(default=1))
    with pytest.raises(EnvParseError):
        env.get("KANKYO_TEST_BAD_PATH_DEFAULT", EnvPath(default=123))
    with pytest.raises(EnvParseError):
        env.get("KANKYO_TEST_BAD_ENUM_DEFAULT", EnvEnum(ModeEnum, default=42))


def test_float_default_accepts_numeric_value() -> None:
    env = Env(eager=True)
    value = env.get("KANKYO_TEST_FLOAT_DEFAULT", EnvFloat(default=1))
    assert value == 1.0
    assert isinstance(value, float)


def test_path_default_factory_invalid_type_raises_parse_error() -> None:
    env = Env(eager=True)
    with pytest.raises(EnvParseError):
        env.get("KANKYO_TEST_BAD_PATH_FACTORY", EnvPath(default_factory=lambda: 123))


def test_patch_exit_skips_reload_when_revision_unchanged(monkeypatch) -> None:
    env = Env(extra={"KANKYO_TEST_PATCH_FAST": "1"}, eager=True)
    calls = 0
    original_load = env._load

    def wrapped_load() -> None:
        nonlocal calls
        calls += 1
        original_load()

    monkeypatch.setattr(env, "_load", wrapped_load)

    with env.patch({"KANKYO_TEST_PATCH_FAST": "2"}):
        assert env.get_raw("KANKYO_TEST_PATCH_FAST") == "2"

    assert env.get_raw("KANKYO_TEST_PATCH_FAST") == "1"
    assert calls == 0


class ModeEnum(str, Enum):
    DEV = "dev"
    PROD = "prod"


def test_env_optional_missing_and_present() -> None:
    env_missing = Env(eager=True)
    assert env_missing.get("OPT", EnvOptional(EnvInt())) is None

    env_present = Env(extra={"OPT": "42"}, eager=True)
    assert env_present.get("OPT", EnvOptional(EnvInt())) == 42


def test_env_union_tries_specs_in_order() -> None:
    spec = EnvUnion([EnvInt(), EnvLiteral(["auto"])])
    env_auto = Env(extra={"MODE": "auto"}, eager=True)
    env_num = Env(extra={"MODE": "7"}, eager=True)
    assert env_auto.get("MODE", spec) == "auto"
    assert env_num.get("MODE", spec) == 7


def test_env_mapping_validates_structured_json() -> None:
    spec = EnvMapping(
        {
            "host": EnvStr(),
            "port": EnvInt(ge=1),
            "ssl": EnvBool(default=False),
        }
    )
    env = Env(extra={"DB": '{"host":"localhost","port":"5432"}'}, eager=True)
    value = env.get("DB", spec)
    assert value == {"host": "localhost", "port": 5432, "ssl": False}


def test_env_list_of_schema_validates_json_array_of_objects() -> None:
    spec = EnvListOfSchema(
        {
            "name": EnvStr(min_length=1),
            "port": EnvInt(ge=1),
        }
    )
    env = Env(
        extra={"BACKENDS": '[{"name":"a","port":"8000"},{"name":"b","port":"9000"}]'},
        eager=True,
    )
    value = env.get("BACKENDS", spec)
    assert value == [{"name": "a", "port": 8000}, {"name": "b", "port": 9000}]


def test_schema_nested_and_computed_fields() -> None:
    class DBConfig(EnvSchema):
        host: str = EnvVar("HOST", EnvStr())
        port: int = EnvVar("PORT", EnvInt())

    class AppConfig(EnvSchema):
        db: DBConfig = EnvNested(DBConfig, prefix="DB")
        database_url: str = EnvComputed(lambda cfg: f"postgres://{cfg.db.host}:{cfg.db.port}")

    env = Env(extra={"DB__HOST": "localhost", "DB__PORT": "5432"}, eager=True)
    cfg = AppConfig(env)
    assert cfg.db.host == "localhost"
    assert cfg.db.port == 5432
    assert cfg.database_url == "postgres://localhost:5432"
    assert cfg.as_dict()["database_url"] == "postgres://localhost:5432"


def test_trace_reports_winner_and_history(tmp_path) -> None:
    (tmp_path / ".env").write_text("X=1\n", encoding="utf-8")
    (tmp_path / ".env.local").write_text("X=2\n", encoding="utf-8")
    env = Env(root=tmp_path, extra={"X": "3"}, eager=True)
    trace = env.trace("X")
    assert trace is not None
    assert trace.key == "X"
    assert trace.value == "3"
    assert trace.raw_value == "3"
    assert trace.winner == "extra"
    assert [entry.source for entry in trace.history][-1] == "extra"


def test_trace_returns_none_for_unknown_key() -> None:
    env = Env(eager=True)
    assert env.trace("KANKYO_TEST_UNKNOWN_TRACE") is None


def test_variable_expansion_resolves_dependencies() -> None:
    env = Env(
        extra={
            "HOST": "localhost",
            "PORT": "5432",
            "URL": "postgres://${HOST}:${PORT}/app",
        },
        expand_vars=True,
        eager=True,
    )
    assert env.get_raw("URL") == "postgres://localhost:5432/app"


def test_variable_expansion_cycle_raises() -> None:
    with pytest.raises(ValueError):
        Env(extra={"A": "${B}", "B": "${A}"}, expand_vars=True, eager=True)


def test_variable_expansion_strict_missing_raises() -> None:
    with pytest.raises(ValueError):
        Env(extra={"A": "${MISSING}"}, expand_vars=True, strict=True, eager=True)


def test_variable_expansion_missing_ref_left_when_not_strict() -> None:
    env = Env(extra={"A": "${MISSING}"}, expand_vars=True, strict=False, eager=True)
    assert env.get_raw("A") == "${MISSING}"


def test_strict_mode_requires_default_factory_for_mutables() -> None:
    with pytest.raises(ValueError):
        EnvJson(default={}, strict=True)
    with pytest.raises(ValueError):
        EnvList(default=["a"], strict=True)


def test_strict_mode_forbids_implicit_default_coercion() -> None:
    env = Env(eager=True)
    with pytest.raises(EnvParseError):
        env.get("X", EnvFloat(default=1, strict=True))
    with pytest.raises(ValueError):
        EnvList(default=[1], strict=True, subtype=EnvInt())

def test_env_level_strict_applies_when_spec_not_explicit() -> None:
    env = Env(extra={"PORT": " 8080 "}, strict=True, eager=True)
    with pytest.raises(EnvParseError):
        env.get("PORT", EnvInt())


def test_explicit_spec_strict_false_overrides_env_strict() -> None:
    env = Env(extra={"PORT": " 8080 "}, strict=True, eager=True)
    assert env.get("PORT", EnvInt(strict=False)) == 8080


def test_new_type_decimal() -> None:
    env = Env(extra={"PRICE": "12.50"}, eager=True)
    value = env.get("PRICE", EnvDecimal(ge=Decimal("0")))
    assert value == Decimal("12.50")


def test_new_type_timedelta() -> None:
    env = Env(extra={"TTL": "1h30m"}, eager=True)
    value = env.get("TTL", EnvTimedelta())
    assert value == timedelta(hours=1, minutes=30)


def test_new_type_ipv4_ipv6() -> None:
    env = Env(extra={"IP4": "192.168.1.1", "IP6": "2001:db8::1"}, eager=True)
    assert env.get("IP4", EnvIPv4()) == ipaddress.IPv4Address("192.168.1.1")
    assert env.get("IP6", EnvIPv6()) == ipaddress.IPv6Address("2001:db8::1")


def test_new_type_email() -> None:
    valid_env = Env(extra={"EMAIL": "user@example.com"}, eager=True)
    assert valid_env.get("EMAIL", EnvEmail()) == "user@example.com"
    invalid_env = Env(extra={"EMAIL": "not-an-email"}, eager=True)
    with pytest.raises(EnvParseError):
        invalid_env.get("EMAIL", EnvEmail())


def test_new_type_uuid() -> None:
    raw = "123e4567-e89b-12d3-a456-426614174000"
    env = Env(extra={"UUID": raw}, eager=True)
    value = env.get("UUID", EnvUUID())
    assert value == UUID(raw)


def test_new_type_literal() -> None:
    env = Env(extra={"MODE": "prod", "COUNT": "2"}, eager=True)
    assert env.get("MODE", EnvLiteral(["dev", "prod"])) == "prod"
    assert env.get("COUNT", EnvLiteral([1, 2, 3])) == 2


def test_env_defaults_still_work_for_missing_key() -> None:
    env = Env(eager=True)
    with pytest.raises(EnvMissingError):
        env.require("KANKYO_MISSING_REQUIRED")
