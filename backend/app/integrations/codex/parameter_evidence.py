"""Bounded, static declarations for Parameters & Trade-offs; never executes a repo.

Only sanitized declaration fragments leave the inspector. In particular, allowing
config/settings filenames does not allow their original file bodies into AI input.
"""
import ast
from collections import defaultdict
import hashlib
import io
import json
import math
import os
from pathlib import PurePosixPath
import re
import time
import tomllib
import tokenize

from sqlalchemy.orm import Session

from ...project_evidence import (
    EvidenceReadError, _DEFAULT_EXCLUSIONS, _digest, _exclusions, _git, _repository_root,
)
from ...services import require_project
from .scheme_evidence import (
    _InspectionStopped, _entrypoint_declarations, _entrypoint_targets,
    _historical_path, _read, _safe_relative,
)

MAX_PARAMETERS = 100
MAX_SOURCE_CHARS = 40_000
MAX_READ_BYTES = 2 * 1024 * 1024
MAX_FILE_BYTES = 65_536
MAX_INSPECTION_SECONDS = 5.0
MAX_PACKAGE_CHARS = 140_000
_VERSION = 3
_SETTING_WORDS = re.compile(r'(?:default|limit|budget|timeout|worker|thread|batch|chunk|context|threshold|cutoff|interval|rate|buffer|cache|size|count|retr(?:y|ies)|profile|precision|tolerance|seed|replica|sample|frame|occupancy|temperature|step|window|minimum|maximum|(?:^|_)min(?:_|$)|(?:^|_)max(?:_|$))', re.I)
_PRIVATE_NAME = re.compile(r'(?:password|passwd|credential|secret|api[_-]?key|private[_-]?key|bearer|authorization|authentication|(?:^|[._-])auth(?:[._-]|$)|access[_-]?token|refresh[_-]?token)', re.I)
_PRIVATE_TEXT = re.compile(r'''(?:[a-z][a-z0-9+.-]*://|-----BEGIN|\b(?:Bearer|Basic)\s+\S+|\bsk-[A-Za-z0-9_-]{10,}|\b(?:password|passwd|credential|secret|api[_-]?key|authorization|token)["']?\s*[:=])''', re.I)
_BEHAVIOR_WORDS = re.compile(r'(?:file|source|context|sample|record|input|evidence|batch|chunk|budget|limit|size|count|cache|thread|worker|unsupported|exclude|filter|fallback|format|backend)', re.I)
_AUXILIARY = {'tests', 'test', 'examples', 'example', 'benchmarks', 'benchmark', 'migrations', 'migration', 'vendor', 'generated'}


class _Budget:
    def __init__(self):
        self.started = time.monotonic()
        self.deadline = self.started + MAX_INSPECTION_SECONDS
        self.bytes_read = 0
        self.prefix_read_files = 0

    def check(self):
        if time.monotonic() >= self.deadline:
            raise _InspectionStopped('time_budget')
        if self.bytes_read >= MAX_READ_BYTES:
            raise _InspectionStopped('byte_budget')


def _secret_name(name):
    if _PRIVATE_NAME.search(name):
        return True
    words = set(re.findall(r'[a-z]+', name.lower()))
    return bool(words & {'token', 'tokens'} and not words & {
        'max', 'min', 'budget', 'count', 'limit', 'length', 'len', 'window', 'size'})


def _safe_text(value, limit=600):
    return (isinstance(value, str) and len(value) <= limit
            and not any(ord(char) < 32 and char not in '\n\t' for char in value)
            and not _PRIVATE_TEXT.search(value)
            and not value.lstrip().startswith(('/', '~/', '~\\'))
            and not re.match(r'^[A-Za-z]:[\\/]', value.lstrip())
            and not re.search(r'/(?:home|Users|root|private)/[^\s\'"<>]+', value)
            and not re.search(r'''["'](?:/|~[/\\]|[A-Za-z]:[\\/])''', value))


def _safe_expression(node):
    for part in ast.walk(node):
        if isinstance(part, ast.Name) and _secret_name(part.id):
            return False
        if isinstance(part, ast.Attribute) and _secret_name(part.attr):
            return False
        if (isinstance(part, ast.Subscript) and isinstance(part.slice, ast.Constant)
                and isinstance(part.slice.value, str) and _secret_name(part.slice.value)):
            return False
        if isinstance(part, ast.Constant) and isinstance(part.value, str) and not _safe_text(part.value, 400):
            return False
        if isinstance(part, ast.Call) and isinstance(part.func, ast.Attribute) and part.func.attr in {'getenv', 'get'}:
            if part.args and isinstance(part.args[0], ast.Constant) and isinstance(part.args[0].value, str) and _secret_name(part.args[0].value):
                return False
        if (isinstance(part, ast.Call) and isinstance(part.func, ast.Name)
                and part.func.id in {'getattr', 'hasattr'} and len(part.args) > 1
                and isinstance(part.args[1], ast.Constant) and isinstance(part.args[1].value, str)
                and _secret_name(part.args[1].value)):
            return False
        if (isinstance(part, ast.MatchMapping) and any(isinstance(key, ast.Constant)
                and isinstance(key.value, str) and _secret_name(key.value) for key in part.keys)):
            return False
        if isinstance(part, ast.MatchClass) and any(_secret_name(name) for name in part.kwd_attrs):
            return False
    return True


def _segment_reader(value):
    """AST columns are UTF-8 byte offsets; split the bounded file only once."""
    lines = [line.encode('utf-8') for line in value.splitlines(keepends=True)]

    def read(node):
        start, end = getattr(node, 'lineno', None), getattr(node, 'end_lineno', None)
        if start is None or end is None or not (1 <= start <= end <= len(lines)):
            return None
        left, right = node.col_offset, node.end_col_offset
        if start == end:
            return lines[start - 1][left:right].decode('utf-8')
        return b''.join([lines[start - 1][left:], *lines[start:end - 1], lines[end - 1][:right]]).decode('utf-8')
    return read


def _value(node, source, segment=None):
    if node is None:
        return None, 'unspecified'
    value = (segment(node) if segment else ast.get_source_segment(source, node)) or ast.unparse(node)
    if not _safe_text(value, 400) or not _safe_expression(node):
        return None
    try:
        literal = ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        return value, 'expression'
    if not isinstance(literal, (str, int, float, bool, type(None))):
        return None
    if isinstance(literal, str) and not _safe_text(literal, 400):
        return None
    if isinstance(literal, float) and not math.isfinite(literal):
        return None
    return value, 'literal'


def _comments(lines, line):
    selected = []
    for index in range(line - 2, -1, -1):
        text = lines[index].strip()
        # An earlier comment belongs to the intervening declaration, not this
        # parameter. Blank lines also end the immediately preceding block.
        if not text.startswith('#'):
            break
        # Never retain a credential continuation just because its identifying
        # line is outside the two-line display bound or was individually omitted.
        if not _safe_text(text, 300):
            return ''
        if len(selected) < 2 and sum(map(len, selected)) + len(text) + len(selected) <= 500:
            selected.append(text)
    return '\n'.join(reversed(selected))


def _snippet(node, value, *, maximum=1200, segment=None):
    """Exact bounded source lines with comments removed; never copy unsafe bodies."""
    if not _safe_expression(node):
        return None
    start, end = node.lineno, getattr(node, 'end_lineno', node.lineno)
    if end - start > 9:
        return None
    text = segment(node) if segment else ast.get_source_segment(value, node)
    if not text or len(text) > maximum:
        return None
    # Strip comments as tokens, including continuation comments which are not
    # independently attributable documentation. Code tokens stay verbatim.
    lines = text.splitlines()
    try:
        for token in tokenize.generate_tokens(io.StringIO(text).readline):
            if token.type == tokenize.COMMENT:
                line, column = token.start
                lines[line - 1] = lines[line - 1][:column].rstrip()
    except (tokenize.TokenError, IndentationError):
        return None
    text = '\n'.join(lines)
    if not _safe_text(text, maximum):
        return None
    return dict(text=text, line_start=start, line_end=end)


def _python(value, *, partial=False, external=None):
    try:
        tree = ast.parse(value)
    except SyntaxError:
        if not partial:
            raise
        # A bounded read may stop inside a function or multiline expression.
        # Retain only a verifiably complete leading module, without inventing
        # closing delimiters, bodies or values for the truncated declaration.
        original_lines = value.splitlines(True)
        boundaries = [index for index, line in enumerate(original_lines)
                      if line.strip() and not line[0].isspace() and not line.startswith(('#', '@'))]
        for end in reversed(boundaries[-12:]):
            try:
                tree = ast.parse(''.join(original_lines[:end]))
                break
            except SyntaxError:
                continue
        else:
            raise
    lines = value.splitlines()
    segment = _segment_reader(value)
    candidates = []
    scopes, conditions, bindings, class_scopes = {}, {}, {}, set()

    class Context(ast.NodeVisitor):
        scope = []
        qualifiers = []

        def text(self, node):
            text = segment(node) or ast.unparse(node)
            return text if _safe_text(text, 250) and _safe_expression(node) else '[condition omitted for safety]'

        def branch(self, qualifier, children):
            self.qualifiers.append(qualifier)
            for child in children:
                self.visit(child)
            self.qualifiers.pop()

        def visit(self, node):
            scopes[id(node)] = tuple(self.scope)
            conditions[id(node)] = tuple(self.qualifiers)
            return super().visit(node)

        def visit_FunctionDef(self, node):
            self.scope.append(node.name)
            names = {arg.arg for arg in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]}
            names.update(arg.arg for arg in (node.args.vararg, node.args.kwarg) if arg)
            class Local(ast.NodeVisitor):
                global_names = set()
                def visit_Name(self, child):
                    if isinstance(child.ctx, ast.Store):
                        names.add(child.id)
                def visit_Global(self, child):
                    self.global_names.update(child.names)
                def visit_FunctionDef(self, child):
                    names.add(child.name)
                visit_AsyncFunctionDef = visit_FunctionDef
                def visit_ClassDef(self, child):
                    names.add(child.name)
                def visit_Import(self, child):
                    names.update(alias.asname or alias.name.split('.')[0] for alias in child.names)
                def visit_ImportFrom(self, child):
                    names.update(alias.asname or alias.name for alias in child.names)
            local = Local()
            for child in node.body:
                local.visit(child)
            bindings[tuple(self.scope)] = names - local.global_names
            self.generic_visit(node)
            self.scope.pop()

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_ClassDef(self, node):
            self.scope.append(node.name)
            class_scopes.add(tuple(self.scope))
            self.generic_visit(node)
            self.scope.pop()

        def visit_If(self, node):
            test = self.text(node.test)
            self.visit(node.test)
            for prefix, children in (('if ', node.body), ('else branch of if ', node.orelse)):
                self.branch(prefix + test, children)

        def visit_Try(self, node):
            self.branch('try branch', node.body)
            for handler in node.handlers:
                exception = self.text(handler.type) if handler.type is not None else 'any exception'
                self.branch('except branch: ' + exception, handler.body)
            self.branch('else branch of try', node.orelse)
            self.branch('finally branch of try', node.finalbody)

        visit_TryStar = visit_Try

        def visit_For(self, node):
            self.visit(node.target); self.visit(node.iter)
            self.branch('for ' + self.text(node.target) + ' in ' + self.text(node.iter), node.body)
            self.branch('else branch of for', node.orelse)

        visit_AsyncFor = visit_For

        def visit_While(self, node):
            self.visit(node.test)
            self.branch('while ' + self.text(node.test), node.body)
            self.branch('else branch of while ' + self.text(node.test), node.orelse)

        def visit_With(self, node):
            for item in node.items:
                self.visit(item.context_expr)
            self.branch('with ' + ', '.join(self.text(item.context_expr) for item in node.items), node.body)

        visit_AsyncWith = visit_With

        def visit_Match(self, node):
            self.visit(node.subject)
            for case in node.cases:
                qualifier = 'match ' + self.text(node.subject) + ': case ' + self.text(case.pattern)
                if case.guard is not None:
                    qualifier += ' if ' + self.text(case.guard)
                self.branch(qualifier, [case.pattern, *([case.guard] if case.guard is not None else []), *case.body])

    Context().visit(tree)
    guard_index = defaultdict(list)
    for guard in ast.walk(tree):
        if isinstance(guard, ast.If):
            referenced = {part.id for part in ast.walk(guard.test) if isinstance(part, ast.Name)}
            scope = scopes.get(id(guard), ())
            for name in referenced:
                key = (scope, name)
                if len(guard_index[key]) < 2:
                    guard_index[key].append(guard)
                # A global constant's uses are relevant only where no enclosing
                # function binds that name. Same-named local options stay apart.
                shadowed = any(name in bindings.get(scope[:depth], set()) or scope[:depth] in class_scopes
                               for depth in range(1, len(scope) + 1))
                if scope and not shadowed and len(guard_index[((), name)]) < 2:
                    guard_index[((), name)].append(guard)
            for part in ast.walk(guard.test):
                if (isinstance(part, ast.Attribute) and isinstance(part.value, ast.Name)
                        and part.value.id in {'args', 'options'} and len(guard_index[(scope, '--' + part.attr)]) < 2):
                    guard_index[(scope, '--' + part.attr)].append(guard)

    def add(name, symbol, node, default, kind=None, help_text='', declaration_kind='code_default'):
        if not isinstance(name, str) or not name or len(name) > 160 or len(symbol) > 300 or _secret_name(name):
            return
        parsed = _value(default, value, segment)
        if parsed is None:
            return
        default_value, default_kind = parsed
        if kind is not None:
            default_kind = kind
        line = node.lineno
        context = _comments(lines, line)
        if help_text and _safe_text(help_text, 500):
            context = '\n'.join(part for part in (context, 'Declared help: ' + help_text) if part)
        # Keep a few directly related guards; they establish code-specific
        # consequences without copying arbitrary bodies, arguments or secrets.
        guards = []
        leaf = name.lstrip('-').replace('-', '_').rsplit('.', 1)[-1]
        guard_key = '--' + leaf if name.startswith('-') else leaf
        for guard in guard_index[(scopes.get(id(node), ()), guard_key)]:
            text = segment(guard.test) or ast.unparse(guard.test)
            if _safe_text(text, 250) and _safe_expression(guard.test):
                actions = ', '.join(type(part).__name__ for part in guard.body[:2])
                guards.append(f'L{guard.lineno}: if {text}; body syntax: {actions}')
            if len(guards) == 2:
                break
        context = '\n'.join(part for part in (context, *guards) if part)
        condition = '; '.join(conditions.get(id(node), ()))
        if len(condition) > 800:
            condition = 'Conditional declaration; full condition exceeds the inspection bound.'
        candidates.append(dict(name=name, symbol=symbol, line_start=line,
            line_end=max(getattr(node, 'end_lineno', line), getattr(default, 'end_lineno', line)), default_value=default_value,
            default_kind=default_kind, declaration_kind=declaration_kind, condition=condition, context=context,
            _scope=scopes.get(id(node), ()), declaration_snippet=_snippet(node, value, segment=segment), usages=[]))

    class Visitor(ast.NodeVisitor):
        scope = []
        setting_class = []
        class_function_depth = []
        function_depth = 0

        def visit_ClassDef(self, node):
            self.scope.append(node.name)
            self.class_function_depth.append(self.function_depth)
            self.setting_class.append(bool(re.search(r'(?:config|settings|options|parameters)$', node.name, re.I)
                or any((isinstance(d, ast.Name) and d.id == 'dataclass')
                       or (isinstance(d, ast.Call) and isinstance(d.func, ast.Name) and d.func.id == 'dataclass') for d in node.decorator_list)))
            self.generic_visit(node)
            self.setting_class.pop(); self.class_function_depth.pop(); self.scope.pop()

        def visit_FunctionDef(self, node):
            self.scope.append(node.name)
            arguments = [*node.args.posonlyargs, *node.args.args]
            for argument, default in zip(arguments[-len(node.args.defaults):] if node.args.defaults else [], node.args.defaults):
                if _SETTING_WORDS.search(argument.arg):
                    add(argument.arg, '.'.join(self.scope), argument, default)
            for argument, default in zip(node.args.kwonlyargs, node.args.kw_defaults):
                if _SETTING_WORDS.search(argument.arg):
                    add(argument.arg, '.'.join(self.scope), argument, default,
                        kind='required' if default is None else None)
            self.function_depth += 1
            self.generic_visit(node)
            self.function_depth -= 1
            self.scope.pop()

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Assign(self, node):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id.isupper() and _SETTING_WORDS.search(target.id):
                    add(target.id, '.'.join([*self.scope, target.id]), node, node.value)
            self.generic_visit(node)

        def visit_AnnAssign(self, node):
            if isinstance(node.target, ast.Name) and (
                    self.setting_class and self.setting_class[-1] and self.function_depth == self.class_function_depth[-1] or
                    node.target.id.isupper() and _SETTING_WORDS.search(node.target.id)):
                add(node.target.id, '.'.join([*self.scope, node.target.id]), node, node.value,
                    kind='required' if node.value is None else None)
            self.generic_visit(node)

        def visit_Call(self, node):
            if isinstance(node.func, ast.Attribute) and node.func.attr == 'add_argument':
                flags = [arg.value for arg in node.args if isinstance(arg, ast.Constant) and isinstance(arg.value, str)]
                if flags:
                    name = next((flag for flag in flags if flag.startswith('--')), flags[0])
                    options = {entry.arg: entry.value for entry in node.keywords if entry.arg is not None}
                    required = isinstance(options.get('required'), ast.Constant) and options['required'].value is True
                    help_node = options.get('help')
                    help_text = help_node.value if isinstance(help_node, ast.Constant) and isinstance(help_node.value, str) else ''
                    add(name, '.'.join(self.scope) or '<module>', node, options.get('default'),
                        kind='required' if required and 'default' not in options else None, help_text=help_text)
            elif isinstance(node.func, ast.Attribute) and node.func.attr == 'set_defaults':
                for option in node.keywords:
                    if option.arg:
                        add(option.arg, '.'.join(self.scope) or '<module>', node, option.value)
            elif isinstance(node.func, ast.Attribute) and node.func.attr in {'getenv', 'get'}:
                function = ast.unparse(node.func)
                if function in {'os.getenv', 'os.environ.get', 'environ.get'} and node.args:
                    key = node.args[0]
                    if isinstance(key, ast.Constant) and isinstance(key.value, str):
                        fallback = node.args[1] if len(node.args) > 1 else next((k.value for k in node.keywords if k.arg == 'default'), None)
                        add(key.value, '.'.join(self.scope) or '<module>', node, fallback,
                            declaration_kind='environment_fallback')
            self.generic_visit(node)

    Visitor().visit(tree)
    parents = {id(child): parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    usage_nodes, seen_usage = [], set()
    for node in ast.walk(tree):
        selected = None
        if isinstance(node, ast.If):
            selected = node
        elif isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Slice):
            selected = node
        elif isinstance(node, ast.Call) and (
                isinstance(node.func, ast.Name) and node.func.id == 'islice'
                or isinstance(node.func, ast.Attribute) and node.func.attr == 'islice'
                or any(keyword.arg and _SETTING_WORDS.search(keyword.arg) for keyword in node.keywords)):
            selected = node
        if selected is None:
            continue
        if not isinstance(selected, ast.If):
            while id(selected) in parents and not isinstance(selected, ast.stmt):
                parent = parents[id(selected)]
                if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    break
                selected = parent
        if id(selected) in seen_usage or not isinstance(selected, ast.stmt):
            continue
        seen_usage.add(id(selected))
        snippet = _snippet(selected, value, segment=segment)
        if snippet is None:
            continue
        # Only loaded references establish a use, never another assignment with
        # the same spelling. Preserve a symbol and exact independently cited range.
        references = {part.id for part in ast.walk(node.test if isinstance(node, ast.If) else node)
                      if isinstance(part, ast.Name) and isinstance(part.ctx, ast.Load)}
        scope = scopes.get(id(node), ())
        snippet['symbol'] = '.'.join(scope) or '<module>'
        usage_nodes.append((node, scope, references, snippet))

    def visible(declaration, scope, name):
        declared = declaration['_scope']
        if declared == scope:
            return True
        if declared or not scope:
            return False
        return not any(name in bindings.get(scope[:depth], set()) or scope[:depth] in class_scopes
                       for depth in range(1, len(scope) + 1))

    linked_usage = set()
    for declaration in candidates:
        name = declaration['name']
        for node, scope, references, snippet in usage_nodes:
            if (name in references and visible(declaration, scope, name)
                    and not (declaration['line_start'] <= snippet['line_start'] <= declaration['line_end'])):
                declaration['usages'].append(dict(snippet))
                linked_usage.add(id(node))
                if len(declaration['usages']) == 3:
                    break

    # Direct module-level imports are resolvable by the caller's safe repository
    # inventory. Ambiguous or shadowed aliases are deliberately not linked.
    if external is not None:
        imports = defaultdict(list)
        for node in tree.body:
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name != '*':
                        imports[alias.asname or alias.name].append((node.level, node.module or '', alias.name))
        rebound = set()
        for node in ast.walk(tree):
            if scopes.get(id(node), ()) != ():
                continue
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                rebound.add(node.id)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                rebound.add(node.name)
            elif isinstance(node, ast.Import):
                rebound.update(alias.asname or alias.name.split('.')[0] for alias in node.names)
            elif isinstance(node, ast.ExceptHandler) and node.name:
                rebound.add(node.name)
        for _, scope, references, snippet in usage_nodes:
            for name in references:
                if name not in rebound and len(imports.get(name, [])) == 1 and not any(
                        scope[:depth] in class_scopes or name in bindings.get(scope[:depth], set()) for depth in range(1, len(scope) + 1)):
                    level, module, imported = imports[name][0]
                    external.append(dict(level=level, module=module, name=imported, usage=dict(snippet)))

    # Inline hardcoded boundaries have no separate constant declaration. They
    # still belong in a code-backed choices view. Do not duplicate a named use.
    for node, scope, references, snippet in usage_nodes:
        text = snippet['text']
        literal_bound = any(isinstance(part, ast.Constant) and type(part.value) is int and part.value > 0
                            for part in ast.walk(node.test if isinstance(node, ast.If) else node))
        terminating = isinstance(node, ast.If) and any(isinstance(part, (ast.Break, ast.Continue, ast.Return, ast.Raise))
                                                      for part in node.body)
        sliced = isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Slice)
        bounded_call = isinstance(node, ast.Call)
        filtering = terminating and any(isinstance(part, ast.Call) and (
            isinstance(part.func, ast.Attribute) and part.func.attr in {'endswith', 'startswith', 'match', 'fullmatch', 'search'}
            or isinstance(part.func, ast.Name) and re.search(r'exclude|ignore|filter|supported', part.func.id, re.I))
            for part in ast.walk(node.test))
        if (id(node) in linked_usage or not (filtering or literal_bound and (terminating or sliced or bounded_call))
                or not (filtering or _BEHAVIOR_WORDS.search(text + snippet['symbol']))):
            continue
        name = ('Filtering policy' if filtering else 'Bounded selection' if sliced else 'Processing bound' if bounded_call else 'Conditional limit') + f' at line {snippet["line_start"]}'
        candidates.append(dict(name=name, symbol=snippet['symbol'], line_start=snippet['line_start'],
            line_end=snippet['line_end'], default_value=None, default_kind='unspecified',
            declaration_kind='implementation_behavior', condition='; '.join(conditions.get(id(node), ())),
            context='', declaration_snippet=dict(snippet), usages=[dict(snippet)], _scope=scope))
    for node in ast.walk(tree):
        snippet = None
        name = ''
        if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call):
            exception = ast.unparse(node.exc.func)
            if exception.endswith('NotImplementedError') or any(
                    isinstance(part, ast.Constant) and isinstance(part.value, str)
                    and re.search(r'(?:unsupported|not supported|not implemented)', part.value, re.I)
                    for part in ast.walk(node.exc)):
                snippet, name = _snippet(node, value, segment=segment), 'Unsupported path'
        elif isinstance(node, (ast.Try, ast.TryStar)) and node.handlers:
            # A fallback is a recorded exception branch, not proof that the
            # alternative always succeeds. Copy only a safe bounded branch.
            for handler in node.handlers:
                if any(isinstance(part, (ast.Return, ast.Assign)) and any(isinstance(child, ast.Call) for child in ast.walk(part))
                       for part in handler.body):
                    snippet = _snippet(handler, value, segment=segment)
                    if snippet:
                        name = 'Exception fallback'
                        break
        if snippet and _BEHAVIOR_WORDS.search(snippet['text'] + '.'.join(scopes.get(id(node), ()))):
            scope = scopes.get(id(node), ())
            candidates.append(dict(name=name + f' at line {snippet["line_start"]}',
                symbol='.'.join(scope) or '<module>', line_start=snippet['line_start'], line_end=snippet['line_end'],
                default_value=None, default_kind='unspecified', declaration_kind='implementation_behavior',
                condition='; '.join(conditions.get(id(node), ())), context='', declaration_snippet=dict(snippet),
                usages=[dict(snippet)], _scope=scope))
    for declaration in candidates:
        declaration.pop('_scope', None)
    return candidates


def _strip_comment(value):
    quote = None
    escaped = False
    for index, char in enumerate(value):
        if escaped:
            escaped = False
        elif char == '\\':
            escaped = True
        elif quote:
            if char == quote:
                quote = None
        elif char in {'"', "'"}:
            quote = char
        elif char == '#':
            return value[:index].rstrip()
    return value.strip()


def _config(value, relative, limitations):
    suffix = PurePosixPath(relative).suffix.lower()
    lines = value.splitlines()
    found = []

    def add(path, raw, number, decoded=None):
        name = '.'.join(path)
        if (not name or len(name) > 160 or _secret_name(name) or not _safe_text(raw, 400)
                or isinstance(decoded, str) and not _safe_text(decoded, 400)):
            return
        # Config paths are not runtime precedence or observed run settings.
        found.append(dict(name=name, symbol=name, line_start=number, line_end=number,
            default_value=raw, default_kind='literal', declaration_kind='configuration_value',
            condition='', context=_comments(lines, number)))

    if suffix in {'.yaml', '.yml'}:
        stack, skipped_indent = [], None
        for number, line in enumerate(lines, 1):
            if not line.strip() or line.lstrip().startswith('#'):
                continue
            current_indent = len(line) - len(line.lstrip(' '))
            if skipped_indent is not None and current_indent > skipped_indent:
                continue
            skipped_indent = None
            match = re.fullmatch(r'( *)([A-Za-z_][A-Za-z0-9_.-]*):\s*(.*)', line)
            if not match:
                limitations.add('Only simple YAML mapping/scalar declarations are supported; unsupported constructs were omitted.')
                skipped_indent = current_indent
                continue
            indent, key, raw = len(match[1]), match[2], _strip_comment(match[3])
            while stack and stack[-1][0] >= indent:
                stack.pop()
            path = [entry[1] for entry in stack] + [key]
            if not raw:
                stack.append((indent, key)); continue
            if raw.startswith(('!', '&', '*', '[', '{', '|', '>')) or '\t' in line:
                limitations.add('Only simple YAML mapping/scalar declarations are supported; unsupported constructs were omitted.')
                skipped_indent = indent
                continue
            try:
                if raw.startswith(("'", '"')) and (len(raw) < 2 or raw[-1] != raw[0]):
                    raise ValueError('Unclosed YAML scalar')
                if raw.startswith("'") and "'" in raw[1:-1]:
                    raise ValueError('Unsupported YAML escape')
                decoded = json.loads(raw) if raw.startswith('"') else raw.strip("'")
            except ValueError:
                limitations.add('Some quoted YAML scalars use unsupported syntax and were omitted.')
                continue
            add(path, raw, number, decoded)
        return found

    if suffix == '.toml':
        # Validate the document, then locate assignments in source order. A
        # flattened parsed tree can reorder reopened tables and misattribute
        # same-named keys to the wrong component/line.
        tomllib.loads(value)
        table, unsupported = [], False
        for number, line in enumerate(lines, 1):
            stripped = _strip_comment(line).strip()
            if not stripped:
                continue
            if stripped.startswith('['):
                header = re.fullmatch(r'\[([A-Za-z0-9_.-]+)\]', stripped)
                unsupported = header is None
                if header:
                    table = header[1].split('.')
                else:
                    limitations.add('Some TOML tables or multiline declarations use unsupported syntax and were omitted.')
                continue
            if unsupported:
                continue
            assignment = re.fullmatch(r'([A-Za-z0-9_.-]+)\s*=\s*(.+)', stripped)
            if not assignment:
                limitations.add('Some TOML tables or multiline declarations use unsupported syntax and were omitted.')
                continue
            try:
                decoded = tomllib.loads('value=' + assignment[2])['value']
            except ValueError:
                limitations.add('Some TOML tables or multiline declarations use unsupported syntax and were omitted.')
                continue
            if isinstance(decoded, (str, int, float, bool, type(None))):
                add([*table, *assignment[1].split('.')], assignment[2], number, decoded)
        return found

    # JSON is parsed with source spans, not global key-name searches. Arrays of
    # examples must not steal a root setting's value; escaped/duplicate keys
    # retain their actual value and lexical occurrence.
    decoder = json.JSONDecoder()
    json.loads(value)  # Validate the complete bounded document first.

    def whitespace(position):
        while position < len(value) and value[position].isspace():
            position += 1
        return position

    def walk(position, path, key_position=0):
        position = whitespace(position)
        if value[position] == '{':
            position = whitespace(position + 1)
            if value[position] == '}':
                return position + 1
            while True:
                start = position
                key, position = decoder.raw_decode(value, position)
                if not isinstance(key, str):
                    raise ValueError('JSON object key is not text')
                position = whitespace(position)
                if value[position] != ':':
                    raise ValueError('JSON object separator missing')
                position = whitespace(walk(position + 1, [*path, key], start))
                if value[position] == '}':
                    return position + 1
                if value[position] != ',':
                    raise ValueError('JSON object separator missing')
                position = whitespace(position + 1)
        decoded, end = decoder.raw_decode(value, position)
        if value[position] == '[':
            limitations.add('Configuration arrays are not interpreted as scalar parameter defaults.')
        elif path and isinstance(decoded, (str, int, float, bool, type(None))):
            add(path, value[position:end], value.count('\n', 0, key_position) + 1, decoded)
        return end
    walk(0, [])
    return found


def _candidate(relative):
    path = PurePosixPath(relative)
    if _historical_path(relative) or any(part.lower() in _AUXILIARY or part.startswith('.') for part in path.parts[:-1]):
        return False
    if path.name.startswith(('test_', 'conftest')):
        return False
    if path.name == 'pyproject.toml':
        return True
    if path.suffix == '.py':
        return True
    return path.suffix.lower() in {'.toml', '.json', '.yaml', '.yml'} and (
        any(token in path.name.lower() for token in ('config', 'settings', 'options', 'parameters'))
        or any(part.lower() in {'config', 'configs', 'configuration'} for part in path.parts[:-1]))


def collect_parameter_evidence(engine, project_id):
    started = time.monotonic()
    with Session(engine) as db:
        project = require_project(db, project_id)
        repository_path = project.repository_path
    limitations = set()
    result = {'parameters': [], 'sources': [], 'coverage': {'partial': False, 'limitations': [],
        'freshness_unknown': False,
        'supported_formats': ['Python declarations', 'simple JSON configuration', 'simple TOML configuration', 'simple YAML mappings'],
        'unsupported_language_files': None,
        'repository_revision': None, 'files_attempted': 0, 'files_read': 0,
        'eligible_files': 0, 'parameters_found': 0, 'parameters_omitted': 0}, 'diagnostics': {}}
    coverage = result['coverage']
    if not repository_path:
        limitations.add('No repository is registered; code-declared parameters are unavailable.')
        coverage['freshness_unknown'] = True
    root_fd = None
    budget = _Budget()
    markers, selected = [], []
    file_facts, imported_usages = {}, []
    try:
        if not repository_path:
            raise EvidenceReadError('No repository is registered; code-declared parameters are unavailable.')
        root = _repository_root(repository_path, budget.deadline)
        configured = _exclusions()
        # Permit repository config.toml declarations, retaining explicitly
        # configured exclusions and every credential/directory exclusion.
        patterns = tuple(pattern for index, pattern in enumerate(configured)
                         if not (index < len(_DEFAULT_EXCLUSIONS) and pattern == 'config.toml'))
        paths, _ = _git(root, 'ls-files', '-z', '--cached', '--others', '--exclude-standard', deadline=budget.deadline)
        ignored, _ = _git(root, 'ls-files', '-z', '--cached', '--ignored', '--exclude-standard', deadline=budget.deadline)
        ignored = {os.fsdecode(path) for path in ignored.split(b'\0') if path}
        listed = sorted({os.fsdecode(path) for path in paths.split(b'\0') if path})
        safe_paths = [path for path in listed if path not in ignored and _safe_relative(path, patterns)]
        candidates = [path for path in safe_paths if _candidate(path)]
        unsupported = sum(PurePosixPath(path).suffix.lower() in {
            '.js', '.jsx', '.mjs', '.cjs', '.ts', '.tsx', '.go', '.rs', '.java', '.kt',
            '.c', '.h', '.cpp', '.cs', '.r', '.jl', '.vue', '.svelte'}
            for path in safe_paths if not _historical_path(path))
        coverage['unsupported_language_files'] = unsupported
        if unsupported:
            limitations.add(f'Only Python code and supported simple configuration formats are inspected for parameter declarations; {unsupported} other-language files were not analyzed.')
        candidates.sort(key=lambda path: (
            0 if PurePosixPath(path).name in {'setup.py', 'pyproject.toml'} else
            1 if any(word in path.lower() for word in ('config', 'settings', 'options', 'parameters')) else
            3 if any(part.lower() in {'scripts', 'script', 'tools', 'tool'} for part in PurePosixPath(path).parts[:-1]) else 2,
            len(PurePosixPath(path).parts), path))
        coverage['eligible_files'] = len(candidates)
        revision, _ = _git(root, 'rev-parse', '--verify', 'HEAD', allowed=(0, 128), deadline=budget.deadline)
        coverage['repository_revision'] = revision.decode('ascii', errors='replace').strip() or None
        root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        occurrences = defaultdict(int)
        entrypoint_paths = set()
        available_paths = set(candidates)
        for relative in candidates:
            budget.check()
            coverage['files_attempted'] += 1
            read_complete = False
            try:
                prefix_before = budget.prefix_read_files
                value = _read(root_fd, relative, allow_prefix=relative.endswith('.py'), budget=budget)
                coverage['files_read'] += 1
                read_complete = True
                partial = budget.prefix_read_files > prefix_before
                file_hash = hashlib.sha256(value.encode()).hexdigest()
                markers.append((relative, file_hash))
                packaging = _entrypoint_declarations(value, relative)
                entrypoint_paths.update(_entrypoint_targets(packaging, relative, available_paths))
                if PurePosixPath(relative).name == 'pyproject.toml':
                    continue  # Packaging metadata selects entrypoints, not runtime defaults.
                external = []
                declarations = _python(value, partial=partial, external=external) if relative.endswith('.py') else _config(value, relative, limitations)
                file_facts[relative] = dict(revision=file_hash, partial=partial)
                imported_usages.extend((relative, entry) for entry in external[:100])
                for declaration in declarations:
                    coverage['parameters_found'] += 1
                    location = (relative, declaration['symbol'], declaration['name'], declaration['line_start'])
                    ordinal = occurrences[location]
                    occurrences[location] += 1
                    identity = _digest([*location, ordinal])[:32]
                    identifier = 'parameter:' + identity
                    source_id = 'parameter-source:' + identity
                    source_text = (f"L{declaration['line_start']}: {declaration['symbol']}\n"
                        f"Parameter: {declaration['name']}\nDeclaration kind: {declaration['declaration_kind']}\n"
                        f"Declared {'configuration value' if declaration['declaration_kind'] == 'configuration_value' else 'default'} ({declaration['default_kind']}): "
                        f"{declaration['default_value'] if declaration['default_value'] is not None else 'No declared default'}")
                    if declaration.get('declaration_snippet'):
                        fragment = declaration['declaration_snippet']
                        source_text += '\nInspected code:\n' + fragment['text']
                    if declaration['context']:
                        source_text += '\n' + declaration['context']
                    if declaration['condition']:
                        source_text += '\nDeclared condition: ' + declaration['condition']
                    parameter = {key: declaration[key] for key in ('name', 'symbol', 'line_start', 'line_end', 'default_value', 'default_kind', 'declaration_kind', 'condition')}
                    parameter.update(id=identifier, component=PurePosixPath(relative).parent.as_posix(), path=relative, source_ids=[source_id])
                    source = dict(id=source_id, label=f"{relative}:{declaration['line_start']}", text=source_text,
                        provenance='parsed code/configuration declaration', content_hash=hashlib.sha256(source_text.encode()).hexdigest(),
                        path=relative, revision=file_hash, symbol=declaration['symbol'],
                        kind='implementation_usage' if declaration['declaration_kind'] == 'implementation_behavior' else 'implementation_declaration',
                        line_start=declaration['line_start'], line_end=declaration['line_end'],
                        partial=partial, revision_kind='inspected_prefix_hash' if partial else 'file_content_hash')
                    # Prefer actual setting declarations over incidental helper
                    # argument defaults without hardcoding project features.
                    priority = (-5 if declaration.get('usages') and declaration['name'].isupper()
                        and declaration['declaration_kind'] != 'implementation_behavior' else
                        -4 if declaration['declaration_kind'] == 'implementation_behavior' else
                        -3 if declaration.get('usages') else -1 if relative in entrypoint_paths else 0 if declaration['name'].isupper()
                        and declaration['declaration_kind'] != 'environment_fallback'
                        and re.search(r'(?:context|budget|source|input|worker|thread|timeout|threshold|cutoff)', declaration['name'], re.I) else
                        1 if declaration['declaration_kind'] == 'configuration_value'
                        or declaration['name'].isupper() or declaration['name'].startswith('-') or '.' in declaration['symbol'] else 2,
                        relative, declaration['line_start'], declaration['name'], ordinal)
                    usage_sources = []
                    for usage in declaration.get('usages', [])[:3]:
                        usage_id = 'implementation-use:' + _digest([relative, usage['line_start'], usage['line_end'], usage['text']])[:32]
                        if declaration['declaration_kind'] == 'implementation_behavior':
                            usage_id = source_id
                            usage_source = source
                        else:
                            usage_source = dict(id=usage_id, label=f"{relative}:{usage['line_start']}", text=usage['text'],
                                kind='implementation_usage', provenance='inspected static usage; not executed',
                                content_hash=hashlib.sha256(usage['text'].encode()).hexdigest(), path=relative,
                                revision=file_hash, symbol=usage['symbol'], line_start=usage['line_start'], line_end=usage['line_end'],
                                partial=partial, revision_kind='inspected_prefix_hash' if partial else 'file_content_hash')
                        if usage_id not in [entry['id'] for entry in usage_sources]:
                            usage_sources.append(usage_source)
                    parameter['usage_source_ids'] = [entry['id'] for entry in usage_sources]
                    selected.append((priority, parameter, source, usage_sources))
            except OSError:
                coverage['freshness_unknown'] = True
                limitations.add('Some files were unsafe, unavailable, malformed, binary or beyond the per-file read bound; their parameters were not established.')
            except (ValueError, TypeError, RecursionError, SyntaxError):
                if not read_complete:
                    coverage['freshness_unknown'] = True
                limitations.add('Some files were malformed, binary or beyond the supported static syntax; their parameters were not established.')
        if budget.prefix_read_files:
            limitations.add('Some Python files were inspected only through a 64 KiB prefix; later declarations may be omitted.')
    except _InspectionStopped as error:
        coverage['freshness_unknown'] = True
        coverage['inspection_stopped'] = error.reason
        limitations.add('The parameter inspection byte/time budget was reached; coverage is incomplete.')
    except (EvidenceReadError, OSError):
        coverage['freshness_unknown'] = True
        limitations.add('The registered repository is unavailable or unsafe; no additional parameter declarations were established.')
    finally:
        if root_fd is not None:
            os.close(root_fd)
    # Resolve only direct imports to one safely inspected module and one
    # module-level declaration. Never link by spelling alone across files.
    unresolved = 0
    for importing_path, entry in imported_usages:
        path = PurePosixPath(importing_path)
        module = PurePosixPath(*entry['module'].split('.')) if entry['module'] else PurePosixPath()
        if entry['level']:
            if entry['level'] > len(path.parent.parts):
                unresolved += 1
                continue
            base = path.parent
            for _ in range(entry['level'] - 1):
                base = base.parent
            module = base / module
        possibilities = {str(module) + '.py', (module / '__init__.py').as_posix()} & file_facts.keys()
        if len(possibilities) != 1:
            unresolved += 1
            continue
        target_path = next(iter(possibilities))
        matches = [record for record in selected if record[1]['path'] == target_path
                   and record[1]['name'] == entry['name'] and record[1]['symbol'] == entry['name']]
        if len(matches) != 1:
            unresolved += 1
            continue
        record = matches[0]
        usage = entry['usage']
        if len(record[3]) >= 6:
            continue
        fact = file_facts[importing_path]
        identifier = 'implementation-use:' + _digest([importing_path, usage['line_start'], usage['line_end'], usage['text']])[:32]
        if identifier in record[1]['usage_source_ids']:
            continue
        record[3].append(dict(id=identifier, label=f"{importing_path}:{usage['line_start']}", text=usage['text'],
            kind='implementation_usage', provenance='direct static import usage; not executed',
            content_hash=hashlib.sha256(usage['text'].encode()).hexdigest(), path=importing_path,
            revision=fact['revision'], symbol=usage['symbol'], line_start=usage['line_start'], line_end=usage['line_end'],
            partial=fact['partial'], revision_kind='inspected_prefix_hash' if fact['partial'] else 'file_content_hash'))
        record[1]['usage_source_ids'].append(identifier)
    limitations.add('Usage inspection is bounded to supported Python syntax and direct unambiguous from-imports; dynamic imports, configuration precedence and general call/data flow are not established.')
    if unresolved:
        limitations.add(f'{unresolved} imported usage candidates could not be resolved unambiguously within the inspected files.')
    # Select only after imports are linked: a declaration used in a later file
    # must compete as applied evidence rather than being discarded as unused.
    selected.sort(key=lambda record: (
        not bool(record[1]['usage_source_ids']),
        -5 if record[1]['usage_source_ids'] and record[1]['name'].isupper()
            and record[1]['declaration_kind'] != 'implementation_behavior' else record[0][0],
        record[0][1:]))
    selected = selected[:MAX_PARAMETERS]
    used, retained = 0, set()
    for _, parameter, source, usage_sources in selected:
        bundle = {entry['id']: entry for entry in [source, *usage_sources] if entry['id'] not in retained}
        cost = sum(len(entry['text']) for entry in bundle.values())
        if used + cost > MAX_SOURCE_CHARS:
            continue
        result['parameters'].append(parameter); result['sources'].extend(bundle.values())
        retained.update(bundle)
        used += cost
    coverage['parameters_omitted'] = coverage['parameters_found'] - len(result['parameters'])
    if coverage['parameters_omitted']:
        limitations.add('Parameter/text limits omitted some declarations; defaults and conditions were not merged or rewritten.')
    coverage['limits'] = dict(max_parameters=MAX_PARAMETERS, max_source_chars=MAX_SOURCE_CHARS,
        max_read_bytes=MAX_READ_BYTES, max_file_bytes=MAX_FILE_BYTES, max_seconds=MAX_INSPECTION_SECONDS)
    coverage.update(partial=bool(limitations), limitations=sorted(limitations))
    # Keep facts and their evidence together under serialized metadata limits too.
    while len(json.dumps(result, ensure_ascii=False)) > MAX_PACKAGE_CHARS and result['parameters']:
        result['parameters'].pop(); coverage['parameters_omitted'] += 1
        kept = {identifier for item in result['parameters'] for identifier in [*item['source_ids'], *item.get('usage_source_ids', [])]}
        result['sources'] = [source for source in result['sources'] if source['id'] in kept]
        coverage['partial'] = True
        coverage['limitations'] = sorted({*coverage['limitations'], 'The serialized parameter evidence budget omitted complete declarations.'})
    result['fingerprint'] = _digest({'version': _VERSION, 'project_id': project_id, 'repository': repository_path,
        'markers': markers, 'revision': coverage['repository_revision'], 'parameters': result['parameters'],
        'sources': result['sources'], 'coverage': coverage})
    result['diagnostics'] = dict(duration_seconds=round(time.monotonic() - started, 6),
        bytes_read=budget.bytes_read, input_chars=sum(len(source['text']) for source in result['sources']), ai_requests=0)
    return result
