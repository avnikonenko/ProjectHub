"""Extract SQL operation shapes from static Python source without retaining values."""

import ast
from collections import defaultdict
import re

from .scheme_named_files import _SQL_NAME, _mask_sql


_MAX_SQL_CHARS = 65_536
_MAX_ROWS = 32
_QUERY = re.compile(r'\s*SELECT\b', re.I)
_READ_RELATION = re.compile(rf'\b(?:FROM|JOIN)\s+({_SQL_NAME})(?![\w$.])', re.I)
_MATCH = re.compile(rf'(?<![\w$:])({_SQL_NAME})\s+MATCH\b', re.I)
_INSERT = re.compile(
    rf'\s*INSERT(?:\s+OR\s+(?:ABORT|FAIL|IGNORE|REPLACE|ROLLBACK))?\s+INTO\s+({_SQL_NAME})(?=\s|\(|;|$)',
    re.I,
)
_SQL_START = re.compile(r'\s*(?:SELECT|INSERT)\b', re.I)
_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
_EXECUTORS = {'execute', 'executemany', 'executescript', 'scalar', 'scalars'}


def _fragments(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        yield node.value
    elif isinstance(node, ast.JoinedStr):
        for part in node.values:
            yield from _fragments(part)
    elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        yield from _fragments(node.left)
        yield from _fragments(node.right)
    else:
        yield '\x00'


def _identifier(value):
    return '.'.join(part.strip().strip('"`[]').casefold() for part in value.split('.'))


def _sql_rows(sql):
    masked = _mask_sql(sql)
    if _QUERY.match(masked):
        relations = {_identifier(match[1]) for match in _READ_RELATION.finditer(masked)}
        for match in _MATCH.finditer(masked):
            name = _identifier(match[1])
            if len(name) <= 160 and name in relations:
                yield f'SELECT FROM {name} WHERE {name} MATCH'
    elif match := _INSERT.match(masked):
        name = _identifier(match[1])
        if len(name) <= 160:
            yield f'INSERT INTO {name}'


def _scoped_nodes(scope):
    pending = list(reversed(scope.body))
    while pending:
        node = pending.pop()
        yield node
        if not isinstance(node, _SCOPES):
            pending.extend(reversed(list(ast.iter_child_nodes(node))))


def _assignments(scope, nodes):
    values = defaultdict(list)
    direct = set(scope.body)
    if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
        args = scope.args
        for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs, args.vararg, args.kwarg):
            if arg:
                values[arg.arg] = []
    for node in nodes:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)) or node.value is None:
            continue
        for target in node.targets if isinstance(node, ast.Assign) else (node.target,):
            if isinstance(target, ast.Name):
                values[target.id].append((node in direct, node.value))
    return values


def _reaching_assignments(assignments, before):
    for direct, value in reversed(assignments):
        if before is not None and (value.lineno, value.col_offset) >= before:
            continue
        yield value
        if direct:
            break


def _query_nodes(root, bindings, globals_, scope):
    pending = [(root, (root.lineno, root.col_offset), isinstance(scope, ast.Module))]
    seen = set()
    while pending and len(seen) < 128:
        node, before, module_scope = pending.pop()
        key = node, before, module_scope
        if key in seen:
            continue
        seen.add(key)
        if isinstance(node, ast.Name):
            local = not module_scope and node.id in bindings
            candidates = bindings[node.id] if local else globals_.get(node.id, ())
            pending.extend((value, (value.lineno, value.col_offset), not local)
                           for value in _reaching_assignments(candidates, before if local or module_scope else None))
        elif isinstance(node, ast.Call):
            function = node.func
            if ((isinstance(function, ast.Name) and function.id == 'text')
                    or (isinstance(function, ast.Attribute) and function.attr == 'text')) and node.args:
                pending.append((node.args[0], before, module_scope))
        elif isinstance(node, (ast.Constant, ast.JoinedStr, ast.BinOp)):
            yield node
            if isinstance(node, (ast.JoinedStr, ast.BinOp)):
                pending.extend((child, before, module_scope) for child in reversed(list(ast.iter_child_nodes(node)))
                               if not isinstance(child, ast.Constant))
        elif isinstance(node, ast.FormattedValue):
            pending.append((node.value, before, module_scope))


def workflow_sql_rows(value):
    """Return line-numbered SQL shapes referenced by database executor calls."""
    try:
        parsed = value if isinstance(value, ast.Module) else ast.parse(value)
        rows = {}
        globals_ = _assignments(parsed, _scoped_nodes(parsed))
        for scope in [parsed, *(node for node in ast.walk(parsed) if isinstance(node, _SCOPES[:-1]))]:
            nodes = list(_scoped_nodes(scope))
            bindings = globals_ if scope is parsed else _assignments(scope, nodes)
            roots = (node.args[0] for node in nodes if isinstance(node, ast.Call) and node.args
                     and isinstance(node.func, ast.Attribute) and node.func.attr in _EXECUTORS)
            for node in (item for root in roots for item in _query_nodes(root, bindings, globals_, scope)):
                pieces = _fragments(node)
                first = next(pieces, '')
                if not _SQL_START.match(first):
                    continue
                sql = [first[:_MAX_SQL_CHARS]]
                size = len(sql[0])
                for piece in pieces:
                    if size >= _MAX_SQL_CHARS:
                        break
                    sql.append(piece[:_MAX_SQL_CHARS - size])
                    size += len(sql[-1])
                for row in _sql_rows(''.join(sql)):
                    rows.setdefault(row, node.lineno)
                    if len(rows) >= _MAX_ROWS:
                        break
                if len(rows) >= _MAX_ROWS:
                    return sorted((line, row) for row, line in rows.items())
        return sorted((line, row) for row, line in rows.items())
    except (SyntaxError, ValueError, RecursionError):
        return []
