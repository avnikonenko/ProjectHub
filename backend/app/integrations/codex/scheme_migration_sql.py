"""Extract bounded SQL structure from the Python syntax of Alembic upgrades."""

import ast
import re

from .scheme_named_files import _mask_sql, _SQL_NAME


_IDENTIFIER = re.compile(r'[A-Za-z_][A-Za-z0-9_]{0,127}')
_UNKNOWN_SLOT = '<dynamic>'
_MAX_STEPS = 384
_MAX_SQL_CHARS = 65_536
_IDENTIFIER_END = r'(?=\s|[();,]|$)'
_VIRTUAL_TABLE = re.compile(
    rf'\s*CREATE\s+VIRTUAL\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?P<name>{_SQL_NAME})'
    rf'\s+USING\s+(?P<module>{_SQL_NAME}){_IDENTIFIER_END}', re.I)
_TABLE = re.compile(
    rf'\s*CREATE\s+(?:TEMP(?:ORARY)?\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?'
    rf'(?P<name>{_SQL_NAME}){_IDENTIFIER_END}', re.I)
_TRIGGER = re.compile(
    rf'\s*CREATE\s+(?:TEMP(?:ORARY)?\s+)?TRIGGER\s+(?:IF\s+NOT\s+EXISTS\s+)?'
    rf'(?P<name>{_SQL_NAME})\s+(?:(?P<timing>BEFORE|AFTER|INSTEAD\s+OF)\s+)?'
    rf'(?P<event>INSERT|UPDATE|DELETE)\s+ON\s+(?P<table>{_SQL_NAME}){_IDENTIFIER_END}', re.I)
_BODY_START = re.compile(r'\bBEGIN\s+', re.I)
_BODY_ACTION = re.compile(
    rf'\s*(?:(?P<insert>INSERT)(?:\s+OR\s+(?:ROLLBACK|ABORT|REPLACE|FAIL|IGNORE))?\s+INTO'
    rf'|(?P<delete>DELETE)\s+FROM|(?P<update>UPDATE))\s+(?P<table>{_SQL_NAME}){_IDENTIFIER_END}', re.I)
_ACTIONS = {'insert': 'INSERT INTO', 'delete': 'DELETE FROM', 'update': 'UPDATE'}


def _qualified_name(name):
    return ''.join(name.split())


def _trigger_event(match):
    timing = ' '.join((match['timing'] or '').upper().split())
    return ' '.join(part for part in (timing, match['event'].upper()) if part)


def _summarize(sql):
    masked = _mask_sql(sql)
    if match := _VIRTUAL_TABLE.match(masked):
        name, module = map(_qualified_name, (match['name'], match['module']))
        return f'CREATE VIRTUAL TABLE {name} USING {module}'
    if match := _TABLE.match(masked):
        return f"CREATE TABLE {_qualified_name(match['name'])}"
    match = _TRIGGER.match(masked)
    if not match:
        return None
    summary = (f"CREATE TRIGGER {_qualified_name(match['name'])} "
               f"{_trigger_event(match)} ON {_qualified_name(match['table'])}")
    if start := _BODY_START.search(masked, match.end()):
        actions = {}
        for statement in masked[start.end():].split(';'):
            if statement.strip().upper() == 'END':
                break
            action = _BODY_ACTION.match(statement)
            if action:
                key = next(name for name in _ACTIONS if action[name])
                actions.setdefault(f"{_ACTIONS[key]} {_qualified_name(action['table'])}", None)
                if len(actions) >= 4:
                    break
        if actions:
            summary += ': ' + ', '.join(actions)
    return summary


def _string_template(node, bindings):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if not isinstance(node, ast.JoinedStr):
        return None
    pieces = []
    for piece in node.values:
        if isinstance(piece, ast.Constant) and isinstance(piece.value, str):
            pieces.append(piece.value)
        elif (isinstance(piece, ast.FormattedValue) and isinstance(piece.value, ast.Name)
              and piece.conversion == -1 and piece.format_spec is None):
            pieces.append(bindings.get(piece.value.id, _UNKNOWN_SLOT))
        else:
            pieces.append(_UNKNOWN_SLOT)
    return ''.join(pieces)


def _bound_rows(node):
    if not isinstance(node.iter, (ast.List, ast.Tuple)):
        return
    if any(isinstance(child, (ast.Break, ast.Continue, ast.Return, ast.Raise)) for child in ast.walk(node)):
        return  # Literal rows do not establish which iterations reach a statement.
    targets = node.target.elts if isinstance(node.target, (ast.List, ast.Tuple)) else [node.target]
    if not all(isinstance(target, ast.Name) for target in targets):
        return
    names = [target.id for target in targets]
    for row in node.iter.elts[:64]:
        values = row.elts if isinstance(row, (ast.List, ast.Tuple)) else [row]
        if len(values) != len(names):
            continue
        yield {
            name: value.value if isinstance(value, ast.Constant) and isinstance(value.value, str)
            and _IDENTIFIER.fullmatch(value.value) else _UNKNOWN_SLOT
            for name, value in zip(names, values)
        }


def _assigned_names(node):
    names = set()
    for item in ast.walk(node):
        if isinstance(item, ast.Name) and isinstance(item.ctx, (ast.Store, ast.Del)):
            names.add(item.id)
        elif isinstance(item, ast.arg):
            names.add(item.arg)
        elif isinstance(item, (ast.Import, ast.ImportFrom)):
            names.update(alias.asname or alias.name.partition('.')[0] for alias in item.names)
        elif isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(item.name)
    return names


def _operator_aliases(statements):
    aliases = set()
    for statement in statements:
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            aliases.discard(statement.name)
        else:
            aliases.difference_update(_assigned_names(statement))
        if isinstance(statement, ast.ImportFrom) and statement.module == 'alembic' and not statement.level:
            aliases.update(alias.asname or alias.name for alias in statement.names if alias.name == 'op')
    return aliases


def migration_sql_declarations(tree: ast.AST) -> list[tuple[int, str]]:
    """Summarize direct Alembic execute calls and literal-loop templates without evaluation."""
    if not isinstance(tree, ast.Module):
        return []
    aliases = _operator_aliases(tree.body)
    declarations = {}
    remaining = _MAX_STEPS

    def walk(statements, bindings, available):
        nonlocal remaining
        for statement in statements:
            remaining -= 1
            if remaining < 0 or isinstance(statement, (ast.Break, ast.Continue, ast.Return, ast.Raise)):
                break
            if isinstance(statement, ast.For):
                for row in _bound_rows(statement):
                    walk(statement.body, {**bindings, **row}, available)
                    if remaining < 0:
                        break
            elif isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call):
                call = statement.value
                name = call.func
                if (isinstance(name, ast.Attribute) and name.attr == 'execute'
                        and isinstance(name.value, ast.Name) and name.value.id in available and call.args):
                    sql = _string_template(call.args[0], bindings)
                    if sql and len(sql) <= _MAX_SQL_CHARS and (summary := _summarize(sql)):
                        declarations.setdefault((statement.lineno, summary), None)
            # Only the iterator's literal values are known. Unknown assignments
            # must not leave a stale binding available to a later f-string.
            for name in _assigned_names(statement) & bindings.keys():
                bindings[name] = _UNKNOWN_SLOT

    for statement in tree.body:
        if isinstance(statement, ast.FunctionDef) and statement.name == 'upgrade':
            walk(statement.body, {}, aliases - _assigned_names(statement))
            break
    return list(declarations)


def compact_migration_sql(declarations: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """Share repeated trigger actions across tables without losing names."""
    groups = {}
    for line, summary in declarations:
        head, _, actions = summary.partition(': ')
        if match := _TRIGGER.fullmatch(head):
            member = f"{match['name']} ON {match['table']}"
            groups.setdefault((line, _trigger_event(match), actions), []).append((summary, member))
        else:
            groups[line, summary, None] = []

    compact = []
    for (line, event, actions), members in groups.items():
        if not members:
            compact.append((line, event))
            continue
        if len(members) == 1:
            compact.append((line, members[0][0]))
            continue
        prefix = f'CREATE TRIGGERS {event}: '
        suffix = f'; {actions}' if actions else ''
        chunk = []
        for summary, member in members:
            if len(prefix) + len(suffix) + len(', '.join([*chunk, member])) > 320:
                if chunk:
                    compact.append((line, prefix + ', '.join(chunk) + suffix))
                    chunk = []
                if len(prefix) + len(suffix) + len(member) > 320:
                    compact.append((line, summary))
                    continue
            chunk.append(member)
        if chunk:
            compact.append((line, prefix + ', '.join(chunk) + suffix))
    return compact
