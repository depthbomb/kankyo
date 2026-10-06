from sys import executable, modules, path as sys_path
from pathlib import Path
from decimal import Decimal
from datetime import timedelta
from subprocess import run
from dataclasses import FrozenInstanceError
from importlib.metadata import version
from importlib.util import module_from_spec, spec_from_file_location

import pytest

from kankyo import (
    ConfigError, GenerationError, Schema, env, boolean, decimal, duration, integer,
    ip_address, list_of, number, one_of, path, secret, string, url, uuid,
)
from kankyo.cli import main
from kankyo.generate import generate, load_schema, render

def import_client(file):
    spec = spec_from_file_location(file.stem, file)
    module = module_from_spec(spec)
    modules[file.stem] = module
    try:
        spec.loader.exec_module(module)
    finally:
        modules.pop(file.stem, None)
    return module

@pytest.fixture
def schema_file(tmp_path):
    file = tmp_path / 'env_schema.py'
    file.write_text(
        "from kankyo import Schema, env, integer, boolean\n"
        "schema = Schema(port=env('PORT', integer(minimum=1), default=8080), "
        "debug=env('DEBUG', boolean(), optional=True))\n", encoding='utf-8')
    return file

def test_generated_client_runs_without_schema_file(schema_file):
    generated = generate(schema_file)
    schema_file.unlink()
    client = import_client(generated)
    config = client.load_from({'PORT': '80'})
    assert config.port == 80
    assert config.debug is None
    assert client.load_from({}).port == 8080
    with pytest.raises(FrozenInstanceError):
        config.port = 1
    with pytest.raises(ConfigError):
        client.load_from({'PORT': 'bad', 'DEBUG': 'bad'})

def test_generated_load_applies_all_layers(schema_file, monkeypatch):
    root = schema_file.parent
    for index, name in enumerate(['.env', '.env.test', '.env.local', '.env.test.local']):
        (root / name).write_text(f'PORT={index + 1}', encoding='utf-8')
    monkeypatch.delenv('PORT', raising=False)
    client = import_client(generate(schema_file))
    assert client.load(root=root, environment='test').port == 4
    monkeypatch.setenv('PORT', '5')
    assert client.load(root=root, environment='test').port == 5
    assert client.load(root=root, environment='test', overrides={'PORT': '6'}).port == 6
    assert client.load_from({}).port == 8080

def test_render_all_types_and_defaults(tmp_path):
    schema = Schema(
        text=env('TEXT', string(pattern=r'[a-z]+'), default='hello'),
        count=env('COUNT', integer(minimum=1, maximum=10), default=3),
        fraction=env('FRACTION', number(minimum=0.1), default=0.5),
        flag=env('FLAG', boolean(), optional=True, default=True),
        price=env('PRICE', decimal(minimum=Decimal('0.1')), default=Decimal('1.12345678901234567890')),
        timeout=env('TIMEOUT', duration(), default=timedelta(microseconds=1)),
        file=env('FILE', path(), default=Path('data')),
        endpoint=env('ENDPOINT', url(schemes=('https',)), default='https://example.com'),
        identifier=env('ID', uuid(), default='123e4567-e89b-12d3-a456-426614174000'),
        ip=env('IP', ip_address(), default='::1'),
        mode=env('MODE', one_of('a', 'b'), default='a'),
        token=env('TOKEN', secret(), default='hidden'),
        nested=env('NESTED', list_of(list_of(integer())), default=[[1, 2]]),
        modes=env('MODES', list_of(one_of('a', 'b')), default=['a']),
        fallback=env('VALUE', integer(), aliases=('ALIAS',), optional=True),
    )
    file = tmp_path / 'typed_config.py'
    file.write_text(render(schema), encoding='utf-8')
    client = import_client(file)
    config = client.load_from({'ALIAS': '4'})
    expected = schema.load({'ALIAS': '4'})
    for name, value in expected.items():
        assert getattr(config, name) == value
    assert 'hidden' not in repr(config)
    source = file.read_text(encoding='utf-8')
    assert 'flag: bool\n' in source
    assert "modes: list[Literal['a', 'b']]" in source
    assert 'Any' not in source

    valid = tmp_path / 'valid.py'
    valid.write_text(
        'from decimal import Decimal\nfrom datetime import timedelta\nfrom pathlib import Path\n'
        'from uuid import UUID\nfrom ipaddress import IPv4Address, IPv6Address\n'
        'from typing import assert_type, Literal\nfrom kankyo import Secret\n'
        'from typed_config import load_from\ncfg = load_from({})\n'
        'assert_type(cfg.text, str)\nassert_type(cfg.count, int)\nassert_type(cfg.fraction, float)\n'
        'assert_type(cfg.flag, bool)\nassert_type(cfg.price, Decimal)\nassert_type(cfg.timeout, timedelta)\n'
        'assert_type(cfg.file, Path)\nassert_type(cfg.endpoint, str)\nassert_type(cfg.identifier, UUID)\n'
        'assert_type(cfg.ip, IPv4Address | IPv6Address)\nassert_type(cfg.mode, Literal["a", "b"])\n'
        'assert_type(cfg.token, Secret)\nassert_type(cfg.nested, list[list[int]])\n'
        'assert_type(cfg.modes, list[Literal["a", "b"]])\nassert_type(cfg.fallback, int | None)\n',
        encoding='utf-8')
    checked = run([executable, '-m', 'mypy', '--strict', '--follow-imports=normal', str(valid)],
                  cwd=tmp_path, capture_output=True, text=True)
    assert checked.returncode == 0, checked.stdout + checked.stderr

    invalid = tmp_path / 'invalid.py'
    invalid.write_text(
        'from typed_config import load_from\ncfg = load_from({})\n'
        'wrong: str = cfg.count\nrequired: int = cfg.fallback\n'
        'cfg.count = 2\ncfg.unknown\ncfg.modes.append("wrong")\n', encoding='utf-8')
    checked = run([executable, '-m', 'mypy', '--strict', str(invalid)], cwd=tmp_path, capture_output=True, text=True)
    assert checked.returncode == 1, checked.stdout + checked.stderr
    assert checked.stdout.count('error:') == 5, checked.stdout

def test_generation_is_deterministic_and_check_never_writes(schema_file):
    output = generate(schema_file)
    content = output.read_bytes()
    timestamp = output.stat().st_mtime_ns
    assert generate(schema_file, check=True) == output
    generate(schema_file)
    assert output.stat().st_mtime_ns == timestamp
    assert output.read_bytes() == content
    schema_file.write_text(schema_file.read_text(encoding='utf-8').replace('8080', '9090'), encoding='utf-8')
    with pytest.raises(GenerationError, match='out of date'):
        generate(schema_file, check=True)
    assert output.read_bytes() == content
    missing = output.parent / 'missing' / 'config.py'
    with pytest.raises(GenerationError):
        generate(schema_file, output=missing, check=True)
    assert not missing.parent.exists()

def test_handwritten_output_requires_explicit_force(schema_file):
    output = schema_file.parent / 'config_gen.py'
    output.write_text('handwritten', encoding='utf-8')
    with pytest.raises(GenerationError, match='--force'):
        generate(schema_file)
    assert output.read_text(encoding='utf-8') == 'handwritten'
    generate(schema_file, force=True)
    assert import_client(output).load_from({}).port == 8080
    with pytest.raises(GenerationError, match='schema file'):
        generate(schema_file, output=schema_file, force=True)

def test_atomic_write_keeps_existing_client_on_failure(schema_file, monkeypatch):
    output = generate(schema_file)
    original = output.read_bytes()
    schema_file.write_text(schema_file.read_text(encoding='utf-8').replace('8080', '9090'), encoding='utf-8')
    def fail_replace(*args):
        raise OSError('simulated write failure')
    monkeypatch.setattr('kankyo.generate.replace', fail_replace)
    before = set(output.parent.iterdir())
    with pytest.raises(OSError):
        generate(schema_file)
    assert set(output.parent.iterdir()) == before
    assert output.read_bytes() == original

def test_schema_selection_and_execution_errors(schema_file):
    schema_file.write_text('from kankyo import Schema\na = Schema()\nb = Schema()\n', encoding='utf-8')
    with pytest.raises(GenerationError, match='--name'):
        load_schema(schema_file)
    assert isinstance(load_schema(schema_file, name='a'), Schema)
    with pytest.raises(GenerationError, match='not a Schema'):
        load_schema(schema_file, name='missing')
    schema_file.write_text('raise RuntimeError("private-value")\n', encoding='utf-8')
    previous = sys_path[:]
    with pytest.raises(GenerationError) as error:
        load_schema(schema_file)
    assert 'RuntimeError' in str(error.value)
    assert 'private-value' not in str(error.value)
    assert previous == sys_path

def test_invalid_schema_does_not_change_existing_client(schema_file):
    output = generate(schema_file)
    original = output.read_bytes()
    schema_file.write_text(
        "from kankyo import Schema, env, integer\nschema = Schema(port=env('PORT', integer(), default='bad'))\n",
        encoding='utf-8')
    with pytest.raises(GenerationError, match='Invalid default'):
        generate(schema_file)
    assert output.read_bytes() == original

@pytest.mark.parametrize('class_name', ['load', 'str', 'Path', 'class', '__Config', 'bad-name', 'source', 'values'])
def test_invalid_client_names(class_name):
    with pytest.raises(GenerationError):
        render(Schema(), class_name=class_name)

def test_empty_schema_and_custom_class_name(tmp_path):
    file = tmp_path / 'empty.py'
    file.write_text(render(Schema(), class_name='Settings'), encoding='utf-8')
    client = import_client(file)
    assert isinstance(client.load_from({}), client.Settings)

def test_fields_can_use_dataclass_and_loader_parameter_names(tmp_path):
    schema = Schema(**{name: env(name.upper(), string(), default=name) for name in ('self', 'source', 'values', 'fields')})
    file = tmp_path / 'names.py'
    file.write_text(render(schema), encoding='utf-8')
    config = import_client(file).load_from({})
    assert config.self == 'self'
    assert config.source == 'source'
    assert config.values == 'values'
    assert config.fields == 'fields'

def test_escaped_choices_defaults_and_descriptions_are_code_safe(tmp_path):
    choice = "quoted '\n雪\\value"
    schema = Schema(value=env('VALUE', one_of(choice), default=choice, description='First\nSecond\n    pass\0'))
    file = tmp_path / 'escaped.py'
    file.write_text(render(schema), encoding='utf-8')
    assert import_client(file).load_from({}).value == choice

def test_cli(schema_file, capsys):
    assert main(['generate', str(schema_file)]) == 0
    assert main(['generate', str(schema_file), '--check']) == 0
    assert main(['generate', str(schema_file), '--output', str(schema_file)]) == 1
    assert 'must not overwrite' in capsys.readouterr().err
    completed = run([executable, '-m', 'kankyo', '--version'], capture_output=True, text=True)
    assert completed.returncode == 0
    assert f'kankyo {version("kankyo")}' in completed.stdout

def test_checked_in_example_is_current():
    examples = Path(__file__).resolve().parents[1] / 'examples'
    generate(examples / 'env_schema.py', check=True)
