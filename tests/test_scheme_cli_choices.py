import ast

from backend.app.integrations.codex.scheme_cli_choices import cli_choice_rows


def test_argparse_groups_report_literal_choices_and_declared_default():
    tree = ast.parse("""import argparse as ap
from argparse import ArgumentParser as Parser
def main():
    parser = ap.ArgumentParser()
    group = parser.add_argument_group('private group heading')
    group.add_argument('-p', '--program', choices=('vina', 'qvina', 'vina-gpu', 'gnina'),
                       default='vina', help='hidden help text')
    feature = Parser()
    feature.add_argument('--mode', choices=['automatic', 'manual'], default=read_env())
""")
    assert cli_choice_rows(tree) == [
        (6, 'CLI --program choices: vina, qvina, vina-gpu, gnina (default: vina)'),
        (9, 'CLI --mode choices: automatic, manual'),
    ]


def test_choice_rows_exclude_sensitive_names_values_and_inferred_defaults():
    tree = ast.parse("""import argparse
parser = argparse.ArgumentParser()
parser.add_argument('--apiKey', choices=['privateText'])
parser.add_argument('--mode', dest='secretValue', choices=['privateText'])
parser.add_argument('--public', '--auth', choices=['privateText'])
parser.add_argument('--url', choices=['https://example.org'])
parser.add_argument('--path', choices=['companion.dat'])
parser.add_argument('--mode', choices=['automatic', 'sk-12345678'])
parser.add_argument('--mode', choices=['automatic', dynamic()])
parser.add_argument('--backend', choices=['vina'], default='example-secret')
""")
    assert cli_choice_rows(tree) == [(10, 'CLI --backend choices: vina')]


def test_choice_rows_require_a_local_argparse_receiver():
    tree = ast.parse("""import argparse
parser = argparse.ArgumentParser()
def local(argparse):
    local_parser = argparse.ArgumentParser()
    local_parser.add_argument('--wrong', choices=['value'])
def shadow(parser):
    parser.add_argument('--wrong', choices=['value'])
parser = unknown_factory()
parser.add_argument('--wrong', choices=['value'])
fake.add_argument('--wrong', choices=['value'])
""")
    assert cli_choice_rows(tree) == []
