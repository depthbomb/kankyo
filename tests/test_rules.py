from uuid import UUID
from pathlib import Path
from decimal import Decimal
from datetime import timedelta
from ipaddress import IPv4Address, IPv6Address

import pytest

from kankyo import (
    Rule, Schema, SchemaError, Secret, boolean, decimal, duration, env, integer, ip_address,
    list_of, number, one_of, path, secret, string, url, uuid,
)

@pytest.mark.parametrize(('rule', 'raw', 'expected'), [
    (string(), ' hello ', ' hello '),
    (string(strip=True, min_length=1, max_length=5, pattern='[a-z]+'), ' hello ', 'hello'),
    (integer(minimum=1, maximum=10), ' 10 ', 10),
    (integer(), '+7', 7),
    (integer(), str(2 ** 100), 2 ** 100),
    (number(minimum=0.1, maximum=2.5), '1e-1', 0.1),
    (decimal(minimum=Decimal('0.1')), '0.100000000000000000001', Decimal('0.100000000000000000001')),
    (boolean(), 'YES', True),
    (boolean(), 'false', False),
    (boolean(), ' 0 ', False),
    (boolean(), 'on', True),
    (duration(), '1h 30m 2.5s', timedelta(hours=1, minutes=30, seconds=2.5)),
    (duration(), '500ms', timedelta(milliseconds=500)),
    (duration(), '1us', timedelta(microseconds=1)),
    (duration(), '-2m', timedelta(minutes=-2)),
    (duration(), '2.5', timedelta(seconds=2.5)),
    (duration(), '1d', timedelta(days=1)),
    (path(), 'data/file.txt', Path('data/file.txt')),
    (url(schemes=('https',)), 'https://example.com:443/path', 'https://example.com:443/path'),
    (uuid(), '123e4567-e89b-12d3-a456-426614174000', UUID('123e4567-e89b-12d3-a456-426614174000')),
    (ip_address(), '127.0.0.1', IPv4Address('127.0.0.1')),
    (ip_address(), '::1', IPv6Address('::1')),
    (one_of('debug', 'info'), 'info', 'info'),
    (list_of(integer()), '1, 2,3', [1, 2, 3]),
    (list_of(integer()), '[1,"2",3]', [1, 2, 3]),
    (list_of(string()), '"a,b",c', ['a,b', 'c']),
    (list_of(integer(), separator=';'), '1;2', [1, 2]),
    (list_of(integer()), '', []),
    (list_of(list_of(integer())), '[[1,2],[3]]', [[1, 2], [3]]),
    (list_of(decimal()), '[1.12345678901234567890]', [Decimal('1.12345678901234567890')]),
])
def test_parsing(rule, raw, expected):
    result = rule.parse(raw)
    assert result == expected
    assert type(result) is type(expected)

@pytest.mark.parametrize(('rule', 'raw'), [
    (string(min_length=1), ''), (string(max_length=2), 'abc'), (string(pattern='[a-z]+'), '123'),
    (string(), 1), (integer(), '1.5'), (integer(), True), (integer(), 1.0),
    (integer(), '1_000'), (integer(), '0xff'), (integer(minimum=2), '1'), (integer(maximum=1), '2'),
    (number(), 'nan'), (number(), 'inf'), (number(), '1e10000'), (number(), True),
    (decimal(), 'NaN'), (decimal(), 'Infinity'), (boolean(), 'perhaps'), (boolean(), 1),
    (duration(), '1h garbage'), (duration(), 'trash1h'), (duration(), ''), (duration(), 'inf'),
    (duration(), True), (duration(), object()), (duration(), '1e100'), (path(), ''), (path(), '\0'),
    (duration(), '-+1h'), (duration(), '--1h'), (duration(), '+-1h'),
    (url(), 'relative/path'), (url(), 'http://'), (url(), 'https://example.com:70000'),
    (url(), 'https://example.com:bad'), (url(), 'https://exa mple.com'),
    (url(schemes=('https',)), 'http://example.com'), (url(), 'https://[broken'),
    (uuid(), 'bad'), (uuid(), 123), (ip_address(), '999.0.0.1'), (ip_address(), 1),
    (one_of('a', 'b'), 'A'), (list_of(integer()), '1,no'), (list_of(integer()), '[true]'),
    (list_of(string()), '[null]'), (list_of(string()), '[{}]'), (list_of(integer()), '[1'),
    (list_of(integer()), '1\n2'), (list_of(string()), '"unclosed'),
    (list_of(integer(), min_length=1), ''), (list_of(integer(), max_length=1), '1,2'),
    (list_of(integer(), unique=True), '1,01'), (list_of(integer()), 123),
    (list_of(list_of(integer()), unique=True), '[[1],[1]]'),
])
def test_invalid_values(rule, raw):
    with pytest.raises(ValueError, match='expected'):
        rule.parse(raw)

@pytest.mark.parametrize('create', [
    lambda: integer(minimum=2, maximum=1), lambda: number(minimum=float('nan')),
    lambda: integer(minimum=True), lambda: string(min_length=-1), lambda: string(min_length=True),
    lambda: string(min_length=3, max_length=2), lambda: string(pattern='['),
    lambda: one_of(), lambda: one_of('a', 'a'), lambda: url(schemes=('HTTPS',)),
    lambda: list_of(string(), separator=''), lambda: list_of(string(), separator='ab'),
    lambda: list_of(string(), separator='\n'), lambda: list_of('string'),
    lambda: Rule('unknown'), lambda: Rule('boolean', minimum=0),
    lambda: Rule('integer', max_length=5), lambda: Rule('secret', pattern='.'),
    lambda: Rule('integer', strip=True), lambda: Rule('integer', unique=True),
    lambda: Rule('integer', choices=('one',)), lambda: Rule('url', schemes=['https']),
    lambda: string(pattern=b'pattern'),
])
def test_bad_rules_fail_at_definition(create):
    with pytest.raises(SchemaError):
        create()

@pytest.mark.parametrize(('rule', 'default'), [
    (integer(), '123'), (number(), 2), (boolean(), False), (decimal(), Decimal('0.12345678901234567890')),
    (duration(), timedelta(days=-1, microseconds=1)), (path(), Path('relative/file')),
    (uuid(), UUID('123e4567-e89b-12d3-a456-426614174000')), (ip_address(), IPv6Address('::1')),
    (list_of(decimal()), [Decimal('1.12345678901234567890')]),
    (list_of(duration()), [timedelta(microseconds=1)]), (list_of(secret()), ['private']),
    (list_of(list_of(integer())), [[1, 2], [3]]), (secret(), Secret('private')),
])
def test_defaults_round_trip(rule, default):
    schema = Schema(value=env('VALUE', rule, default=default))
    assert schema.load({})['value'] == rule.parse(default)

def test_invalid_default_fails_immediately_without_leaking():
    with pytest.raises(SchemaError) as error:
        env('TOKEN', secret(max_length=2), default='very-private')
    assert 'very-private' not in str(error.value)

def test_defaults_are_snapshotted_and_loads_are_independent():
    default = [[1], [2]]
    schema = Schema(values=env('VALUES', list_of(list_of(integer())), default=default))
    default[0].append(3)
    first = schema.load({})['values']
    first[0].append(4)
    assert schema.load({})['values'] == [[1], [2]]

def test_secret_requires_explicit_reveal():
    token = secret().parse('private-value')
    assert isinstance(token, Secret)
    assert str(token) == '********'
    assert repr(token) == 'Secret(********)'
    assert f'{token}' == '********'
    assert token.reveal() == 'private-value'

@pytest.mark.parametrize('default', [['~/data'], '["~/data"]', '~/data'])
def test_path_defaults_remain_portable(default, monkeypatch):
    rule = list_of(path())
    field = env('PATHS', rule, default=default)
    assert field.default == '["~/data"]'
    monkeypatch.setattr(Path, 'expanduser', lambda self: Path(str(self).replace('~', 'other-home')))
    assert Schema(paths=field).load({})['paths'] == [Path('other-home/data')]
