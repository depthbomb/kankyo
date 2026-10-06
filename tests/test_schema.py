from traceback import format_exception

import pytest

from kankyo import Schema, ConfigError, SchemaError, boolean, env, integer, secret, string

def test_required_optional_and_defaults():
    schema = Schema(
        port=env('PORT', integer(), default=8080),
        debug=env('DEBUG', boolean(), optional=True),
        host=env('HOST', string(), optional=True, default='localhost'),
    )
    assert schema.load({}) == {'port': 8080, 'debug': None, 'host': 'localhost'}
    assert schema.load({'PORT': '80', 'DEBUG': 'false', 'HOST': ''}) == {'port': 80, 'debug': False, 'host': ''}

def test_load_reports_all_failures_and_no_raw_values():
    schema = Schema(
        host=env('HOST', string()), port=env('PORT', integer()),
        token=env('TOKEN', secret(max_length=2)),
    )
    with pytest.raises(ConfigError) as error:
        schema.load({'PORT': 'private-port', 'TOKEN': 'private-token'})
    assert [issue.field for issue in error.value.issues] == ['host', 'port', 'token']
    assert error.value.issues[0].reason == 'required variable is missing'
    trace = ''.join(format_exception(error.value))
    assert 'private-port' not in str(error.value)
    assert 'private-token' not in str(error.value)
    assert error.value.__context__ is None
    assert 'expected secret' in trace

def test_alias_order_and_primary_presence():
    schema = Schema(value=env('VALUE', integer(), aliases=('FIRST_ALIAS', 'SECOND_ALIAS'), default=3))
    assert schema.load({'SECOND_ALIAS': '1', 'FIRST_ALIAS': '2'}) == {'value': 2}
    assert schema.load({'SECOND_ALIAS': '1', 'FIRST_ALIAS': '2', 'VALUE': '0'}) == {'value': 0}
    with pytest.raises(ConfigError):
        schema.load({'VALUE': '', 'FIRST_ALIAS': '2'})
    with pytest.raises(ConfigError) as error:
        schema.load({'FIRST_ALIAS': 'invalid'})
    assert error.value.issues[0].variable == 'FIRST_ALIAS'

def test_mapping_load_rejects_non_string_values_even_when_optional():
    with pytest.raises(ConfigError, match='must be strings'):
        Schema(value=env('VALUE', string(), optional=True)).load({'VALUE': None})

def test_unrelated_values_are_ignored():
    assert Schema(value=env('VALUE', string(), default='')).load({'UNKNOWN': 'anything'}) == {'value': ''}

@pytest.mark.parametrize('name', ['class', '_private', 'with-dash', '123', 'café', '__dict__'])
def test_invalid_python_field_names(name):
    with pytest.raises(SchemaError):
        Schema(**{name: env('VALID', string())})

@pytest.mark.parametrize('name', ['', 'WITH-DASH', '1BAD', 'A=B', 'A\nB'])
def test_invalid_environment_names(name):
    with pytest.raises(SchemaError):
        env(name, string())

def test_invalid_aliases():
    for aliases in [('VALUE',), ('ALIAS', 'ALIAS'), ('NOT VALID',), ['ALIAS']]:
        with pytest.raises(SchemaError):
            env('VALUE', string(), aliases=aliases)

def test_schema_fields_are_read_only():
    schema = Schema(value=env('VALUE', string()))
    with pytest.raises(TypeError):
        schema.fields['new'] = env('NEW', string())

def test_empty_schema():
    assert Schema().load({}) == {}
