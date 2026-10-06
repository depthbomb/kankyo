from __future__ import annotations

import sys
from argparse import ArgumentParser
from collections.abc import Sequence
from kankyo.generate import generate
from kankyo.errors import KankyoError
from importlib.metadata import version

def main(argv: Sequence[str] | None = None) -> int:
    parser = ArgumentParser(prog='kankyo', description='Generate a typed Python client from an environment schema.')
    parser.add_argument('--version', action='version', version=f'kankyo {version("kankyo")}')
    commands = parser.add_subparsers(dest='command', required=True)
    command = commands.add_parser('generate', help='generate a client from a trusted Python schema file')
    command.add_argument('schema', help='path to a Python file containing a Schema instance')
    command.add_argument('-o', '--output', help='output file (default: config_gen.py next to the schema)')
    command.add_argument('--name', help='Schema variable to use when the file defines several')
    command.add_argument('--class-name', default='Config', help='generated dataclass name (default: Config)')
    command.add_argument('--check', action='store_true', help='fail if the generated client is missing or stale')
    command.add_argument('--force', action='store_true', help='allow replacing an existing handwritten output file')
    arguments = parser.parse_args(argv)
    try:
        target = generate(arguments.schema, output=arguments.output, name=arguments.name,
                          class_name=arguments.class_name, check=arguments.check, force=arguments.force)
    except (KankyoError, OSError, UnicodeError) as exc:
        print(f'kankyo: {exc}', file=sys.stderr)
        return 1

    print(f'{"Checked" if arguments.check else "Generated"} {target}')
    return 0
