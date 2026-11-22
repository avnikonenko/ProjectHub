"""Read declared HTTP routes and local navigation fragments without running Python."""

import ast
import re


_HTTP_METHODS = frozenset({'delete', 'get', 'head', 'options', 'patch', 'post', 'put', 'trace'})
_ROUTE_SEGMENT = re.compile(r'(?:[a-zA-Z0-9._~-]+|\{[a-zA-Z_]\w*(?::[a-zA-Z_]\w*)?\})+')
_NAVIGATION_FRAGMENT = re.compile(r'#[a-zA-Z][a-zA-Z0-9_-]{0,63}\??')
_MAX_DECLARATIONS = 128


def _string(node):
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else ''


def _route_methods(call):
    if not isinstance(call.func, ast.Attribute) or not isinstance(call.func.value, ast.Name):
        return ()
    receiver = call.func.value.id
    if receiver not in {'app', 'api', 'bp', 'router', 'blueprint'} and not receiver.endswith(
            ('_app', '_router', '_blueprint')):
        return ()
    method = call.func.attr
    if method in _HTTP_METHODS:
        return (method.upper(),)
    if method in {'route', 'api_route'}:
        declared = next((item.value for item in call.keywords if item.arg == 'methods'), None)
        if isinstance(declared, (ast.List, ast.Tuple, ast.Set)):
            return tuple(dict.fromkeys(name.upper() for item in declared.elts
                                       if (name := _string(item).lower()) in _HTTP_METHODS))
    return ()


def _route_path(call):
    path = _string(call.args[0]) if call.args else next(
        (_string(item.value) for item in call.keywords if item.arg in {'path', 'rule'}), '')
    if path == '/':
        return path
    if not path.startswith('/') or len(path) > 180:
        return ''
    segments = path[1:].removesuffix('/').split('/')
    return path if all(segment not in {'.', '..'} and _ROUTE_SEGMENT.fullmatch(segment)
                       for segment in segments) else ''


def _href_fragment(node):
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        node = node.left
    if isinstance(node, ast.JoinedStr) and node.values:
        node = node.values[0]
    fragment = _string(node)
    return fragment if _NAVIGATION_FRAGMENT.fullmatch(fragment) else ''


def python_route_declarations(tree: ast.AST) -> list[tuple[int, str]]:
    """Collect bounded lexical evidence for route decorators and local hrefs."""
    pending = [(tree, '')]
    declarations = {}
    while pending and len(declarations) < _MAX_DECLARATIONS:
        node, handler = pending.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            handler = node.name
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call) or not (methods := _route_methods(decorator)):
                    continue
                if path := _route_path(decorator):
                    for method in methods:
                        declarations.setdefault((decorator.lineno, f'HTTP route {method} {path} -> {handler}'), None)
        elif handler and isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if _string(key) == 'href' and (fragment := _href_fragment(value)):
                    declarations.setdefault((key.lineno, f'{handler} builds navigation link {fragment}'), None)
        pending.extend((child, handler) for child in reversed(list(ast.iter_child_nodes(node))))
    return list(declarations)[:_MAX_DECLARATIONS]
