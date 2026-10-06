from os import environ

import pytest

from kankyo import SourceError, load_environment, parse_dotenv

def test_loading_priority_at_every_layer(tmp_path, monkeypatch):
    monkeypatch.delenv('KANKYO_PRIORITY', raising=False)
    filenames = ['.env', '.env.production', '.env.local', '.env.production.local']
    for index, filename in enumerate(filenames):
        (tmp_path / filename).write_text(f'KANKYO_PRIORITY={index}\n', encoding='utf-8')
        assert load_environment(root=tmp_path, environment='production')['KANKYO_PRIORITY'] == str(index)

    monkeypatch.setenv('KANKYO_PRIORITY', 'process')
    assert load_environment(root=tmp_path, environment='production')['KANKYO_PRIORITY'] == 'process'
    assert load_environment(root=tmp_path, environment='production', overrides={'KANKYO_PRIORITY': 'override'})[
        'KANKYO_PRIORITY'] == 'override'
    assert environ['KANKYO_PRIORITY'] == 'process'

def test_environment_layers_are_only_loaded_when_selected(tmp_path, monkeypatch):
    monkeypatch.delenv('KANKYO_VALUE', raising=False)
    (tmp_path / '.env').write_text('KANKYO_VALUE=base', encoding='utf-8')
    (tmp_path / '.env.test').write_text('KANKYO_VALUE=test', encoding='utf-8')
    assert load_environment(root=tmp_path)['KANKYO_VALUE'] == 'base'
    assert load_environment(root=tmp_path, environment='test')['KANKYO_VALUE'] == 'test'

def test_missing_files_and_empty_overrides(tmp_path, monkeypatch):
    monkeypatch.setenv('KANKYO_VALUE', 'process')
    assert load_environment(root=tmp_path, overrides={'KANKYO_VALUE': ''})['KANKYO_VALUE'] == ''

def test_default_root_is_current_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv('KANKYO_VALUE', raising=False)
    (tmp_path / '.env').write_text('KANKYO_VALUE=local', encoding='utf-8')
    assert load_environment()['KANKYO_VALUE'] == 'local'

def test_dotenv_quotes_comments_escapes_and_export():
    text = '''\ufeff# comment
export NAME = example # inline
EMPTY=
HASH=abc#def
SINGLE='literal\\n \\\"'
DOUBLE="snow 雪\\nline\\t\\\"quoted\\\"\\\\path"
UNKNOWN="\\q"
REFERENCE=${NAME}
NAME=last
'''
    assert parse_dotenv(text) == {
        'NAME': 'last', 'EMPTY': '', 'HASH': 'abc#def', 'SINGLE': 'literal\\n \\\"',
        'DOUBLE': 'snow 雪\nline\t"quoted"\\path', 'UNKNOWN': '\\q', 'REFERENCE': '${NAME}',
    }

def test_multiline_values():
    assert parse_dotenv('A="line1\nline2" # comment\nB=ok') == {'A': 'line1\nline2', 'B': 'ok'}
    assert parse_dotenv("A='line1\nline2'\r\nB=ok\r\n") == {'A': 'line1\nline2', 'B': 'ok'}

@pytest.mark.parametrize('text', ['BAD', '1BAD=x', 'A="unclosed', 'A="ok" trailing', 'A=bad\0value'])
def test_invalid_dotenv_reports_location_without_raw_content(text):
    with pytest.raises(SourceError) as error:
        parse_dotenv('# comment\n' + text, source='settings.env')
    assert str(error.value).startswith('settings.env:2:')
    assert text not in str(error.value)

def test_error_line_after_multiline_value():
    with pytest.raises(SourceError, match=':4:'):
        parse_dotenv('A="one\ntwo\nthree"\nBAD')

@pytest.mark.parametrize('name', ['', '../test', 'one/two', 'one\\two'])
def test_environment_name_cannot_escape_root(name):
    with pytest.raises(SourceError):
        load_environment(environment=name)

def test_malformed_files_fail_even_if_process_overrides(tmp_path, monkeypatch):
    monkeypatch.setenv('KANKYO_VALUE', 'process')
    (tmp_path / '.env').write_text('KANKYO_VALUE="unclosed', encoding='utf-8')
    with pytest.raises(SourceError):
        load_environment(root=tmp_path)

def test_unreadable_utf8_and_directories_fail(tmp_path):
    file = tmp_path / '.env'
    file.write_bytes(b'VALUE=\xff')
    with pytest.raises(SourceError, match='Cannot read'):
        load_environment(root=tmp_path)
    file.unlink()
    file.mkdir()
    with pytest.raises(SourceError, match='Cannot read'):
        load_environment(root=tmp_path)
