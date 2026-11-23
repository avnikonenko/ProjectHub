"""Read bounded argparse choices without importing or executing repository code."""

import ast
import re


_MAX_ROWS = 24
_MAX_CHOICES = 12
_FLAG = re.compile(r'--[a-zA-Z][a-zA-Z0-9_-]{0,39}')
_CHOICE = re.compile(r'[a-zA-Z][a-zA-Z0-9_-]{0,23}')
_PRIVATE_NAME = re.compile(
    r'password|passwd|credential|secret|token|bearer|authoriz|authentic|'
    r'(?:^|[-_])(?:auth|key|pin)(?:[-_]|$)|api[-_]?key|private[-_]?key',
    re.I,
)
_PRIVATE_VALUE = re.compile(r'^(?:sk[-_]|gh[oprsu]_|xox[baprs]-|AKIA)', re.I)
_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)


def _literal(node):
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else ''


def _mentions_private_name(value):
    return bool(_PRIVATE_NAME.search(re.sub(r'(?<=[a-z0-9])(?=[A-Z])', '_', value)))


def _declaration(call, bindings):
    func = call.func
    if (not isinstance(func, ast.Attribute) or not isinstance(func.value, ast.Name)
            or func.attr != 'add_argument' or bindings.get(func.value.id) != 'parser'):
        return ''
    aliases = [_literal(node) for node in call.args]
    kwargs = {keyword.arg: keyword.value for keyword in call.keywords}
    flag = next((alias for alias in aliases if _FLAG.fullmatch(alias)), '')
    if (not flag or None in kwargs or any(not alias or _mentions_private_name(alias) for alias in aliases)
            or 'dest' in kwargs and (not _literal(kwargs['dest']) or _mentions_private_name(_literal(kwargs['dest'])))):
        return ''
    choices = kwargs.get('choices')
    if not isinstance(choices, (ast.List, ast.Tuple)) or not 1 <= len(choices.elts) <= _MAX_CHOICES:
        return ''
    values = [_literal(node) for node in choices.elts]
    if not all(_CHOICE.fullmatch(value) and not _PRIVATE_VALUE.search(value) for value in values):
        return ''
    result = f'CLI {flag} choices: {", ".join(dict.fromkeys(values))}'
    default = _literal(kwargs.get('default'))
    return result + f' (default: {default})' if default in values else result


def _parser_binding(value, bindings):
    if not isinstance(value, ast.Call):
        return ''
    func = value.func
    if isinstance(func, ast.Name) and bindings.get(func.id) == 'factory':
        return 'parser'
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        receiver = bindings.get(func.value.id)
        if (receiver, func.attr) in {('argparse', 'ArgumentParser'), ('parser', 'add_argument_group'),
                                    ('parser', 'add_mutually_exclusive_group')}:
            return 'parser'
    return ''


def _local_names(node):
    """Collect local writes conservatively for inherited bindings and unsupported statements."""
    names = set()
    for part in ast.walk(node):
        if isinstance(part, ast.Name) and isinstance(part.ctx, (ast.Store, ast.Del)):
            names.add(part.id)
        elif isinstance(part, ast.arg):
            names.add(part.arg)
        elif isinstance(part, ast.alias):
            names.add(part.asname or part.name.partition('.')[0])
        elif isinstance(part, (*_FUNCTIONS, ast.ClassDef)):
            names.add(part.name)
    return names


def cli_choice_rows(parsed):
    """Return line-numbered, literal CLI declarations on proven parser/group receivers."""
    rows = []

    def inspect(body, bindings, *, module=False):
        for statement in body:
            if len(rows) == _MAX_ROWS:
                return
            if isinstance(statement, ast.Import):
                for alias in statement.names:
                    name = alias.asname or alias.name.partition('.')[0]
                    bindings.pop(name, None)
                    if alias.name == 'argparse':
                        bindings[name] = 'argparse'
            elif isinstance(statement, ast.ImportFrom):
                for alias in statement.names:
                    name = alias.asname or alias.name
                    bindings.pop(name, None)
                    if statement.level == 0 and statement.module == 'argparse' and alias.name == 'ArgumentParser':
                        bindings[name] = 'factory'
            elif isinstance(statement, ast.Assign) and len(statement.targets) == 1 and isinstance(statement.targets[0], ast.Name):
                name = statement.targets[0].id
                if kind := _parser_binding(statement.value, bindings):
                    bindings[name] = kind
                else:
                    bindings.pop(name, None)
            elif isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call):
                if summary := _declaration(statement.value, bindings):
                    rows.append((statement.lineno, summary))
            elif module and isinstance(statement, _FUNCTIONS):
                bindings.pop(statement.name, None)
                writes = _local_names(statement)
                inspect(statement.body, {key: value for key, value in bindings.items() if key not in writes})
            else:
                for name in _local_names(statement):
                    bindings.pop(name, None)

    if isinstance(parsed, ast.Module):
        inspect(parsed.body, {}, module=True)
    return rows
