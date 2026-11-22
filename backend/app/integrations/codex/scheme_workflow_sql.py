"""Extract SQL operation shapes from static Python source without retaining values."""

import ast
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


def workflow_sql_rows(value):
    """Return line-numbered SQL query shapes supported by visible literals."""
    try:
        parsed = value if isinstance(value, ast.Module) else ast.parse(value)
        rows = {}
        for parent in ast.walk(parsed):
            if isinstance(parent, (ast.JoinedStr, ast.BinOp)):
                continue
            for node in ast.iter_child_nodes(parent):
                if not isinstance(node, (ast.Constant, ast.JoinedStr, ast.BinOp)):
                    continue
                if (isinstance(parent, ast.Expr) and isinstance(node, ast.Constant)):
                    continue  # Ignore bare strings, including docstrings.
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
