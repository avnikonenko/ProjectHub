"""Bounded structural evidence for repository files named in a focused instruction."""
from bisect import bisect_right
from collections.abc import Callable
from pathlib import PurePosixPath
import re


_DOCKERFILE = re.compile(r'(?:dockerfile|containerfile)(?:[.-][\w-]+)*')
_MAKEFILES = {'makefile', 'gnumakefile'}
_DOCKER_DIRECTIVES = frozenset(
    'ADD ARG CMD COPY ENTRYPOINT ENV EXPOSE FROM HEALTHCHECK LABEL MAINTAINER ONBUILD RUN '
    'SHELL STOPSIGNAL USER VOLUME WORKDIR'.split())
_DOCKER_FROM = re.compile(
    r'(?:--platform=\S+\s+)?(?P<image>[a-zA-Z0-9][a-zA-Z0-9._/-]*)(?::[a-zA-Z0-9._-]+)?'
    r'(?:@sha256:[a-fA-F0-9]{64})?(?:\s+AS\s+(?P<stage>[a-zA-Z][\w-]*))?\s*', re.I)
_DOCKER_COPY_STAGE = re.compile(r'^--from=([a-zA-Z][\w-]*)(?:\s|$)')
_HEREDOC = re.compile(r"""<<-?\s*(['"]?)([A-Za-z_][\w-]*)\1""")
_MAKE_RULE = re.compile(r'([\w./% -]+):(?!=)(.*)')
_MAKE_TARGET = re.compile(r'[\w.%/-]+')
_MAKE_ASSIGNMENT = re.compile(r'\s*(?:(?:private|override|export|unexport)\s+)*[^\s:]+\s*[:?+!]*=')
_MAKE_DEFINE = re.compile(r'\s*(?:(?:override|export)\s+)*define\s+')
_BUILD_COMMANDS = frozenset(
    'cargo cmake docker gcc go make mvn ninja node npm npx pip pip3 pnpm podman pytest '
    'python python3 ruby rustc tsc uv yarn'.split())
_YAML_KEY = re.compile(r'(?P<indent> *)(?P<list_item>- +)?(?P<key>[A-Za-z_][\w.-]*):(?=\s|$)(?P<value>.*)')
_SQL_ID = r'(?:[A-Za-z_][\w$]*|"[A-Za-z_][\w$]*"|`[A-Za-z_][\w$]*`|\[[A-Za-z_][\w$]*\])'
_SQL_NAME = rf'{_SQL_ID}(?:\s*\.\s*{_SQL_ID})*'
_SQL_OBJECT = re.compile(
    rf'\s*(?P<verb>CREATE(?:\s+(?:OR\s+REPLACE|TEMPORARY|TEMP|UNIQUE))?|ALTER)\s+'
    rf'(?P<object>TABLE|VIEW|INDEX)\s+(?:IF\s+(?:NOT\s+)?EXISTS\s+)?(?P<name>{_SQL_NAME})', re.I)
_SQL_RELATION = re.compile(rf'\b(?P<verb>REFERENCES|FROM|JOIN|ON)\s+(?P<name>{_SQL_NAME})', re.I)
_SQL_COLUMN = re.compile(rf'\s*(?P<name>{_SQL_ID})(?=\s|,|\)|$)(?:\s+(?P<type>[A-Za-z][\w]*))?')
_SQL_CONSTRAINTS = {'CHECK', 'CONSTRAINT', 'FOREIGN', 'PRIMARY', 'UNIQUE'}
_SQL_TYPES = frozenset(
    'BIGINT BLOB BOOLEAN CHAR DATE DATETIME DECIMAL FLOAT INT INTEGER JSON JSONB NCHAR '
    'NUMERIC REAL SERIAL SMALLINT TEXT TIMESTAMP TIMESTAMPTZ UUID VARCHAR'.split())
_SQL_MASK = re.compile(r"""--|\#|/\*|(?P<dollar>\$(?:[A-Za-z_][\w]*)?\$)|['"`\[]""")
_SQL_COMMENT = re.compile(r'/\*|\*/')
_SQL_QUOTED = {
    "'": re.compile(r"'(?:''|\\(?:.|\Z)|[^'\\])*(?:'|\Z)", re.S),
    '"': re.compile(r'"(?:""|\\(?:.|\Z)|[^"\\])*(?:"|\Z)', re.S),
    '`': re.compile(r'`(?:``|\\(?:.|\Z)|[^`\\])*(?:`|\Z)', re.S),
    '[': re.compile(r'\[[^\]]*(?:\]|\Z)'),
}


def named_file_kind(relative: str) -> str | None:
    name = PurePosixPath(relative).name.casefold()
    if _DOCKERFILE.fullmatch(name) or name.endswith(('.dockerfile', '.containerfile')):
        return 'dockerfile'
    if name in _MAKEFILES or name.startswith(('makefile.', 'gnumakefile.')) or name.endswith('.mk'):
        return 'makefile'
    if name.endswith('.sql'):
        return 'sql'
    if name.endswith(('.yaml', '.yml')):
        return 'yaml'
    return None


def _dockerfile(text):
    heredocs = []
    stages = set()
    continued = False
    escape = '\\'
    for line, value in enumerate(text.splitlines(), 1):
        value = value.strip()
        if heredocs:
            if value == heredocs[0]:
                heredocs.pop(0)
            continue
        if line == 1 and re.fullmatch(r'#\s*escape\s*=\s*`', value, re.I):
            escape = '`'
        if not value or value.startswith('#'):
            continue
        if continued:
            continued = value.endswith(escape)
            continue
        directive, *rest = value.split(None, 1)
        directive = directive.upper()
        if directive not in _DOCKER_DIRECTIVES:
            continue
        arguments = rest[0] if rest else ''
        continued = value.endswith(escape)
        if directive in {'ADD', 'COPY', 'RUN'}:
            heredocs = [match[2] for match in _HEREDOC.finditer(arguments)]
        if directive == 'FROM' and (parsed := _DOCKER_FROM.fullmatch(arguments)):
            stage = parsed['stage']
            if stage:
                stages.add(stage.casefold())
            detail = parsed['image'] + (f' AS {stage}' if stage else '')
            yield line, f'FROM {detail}'
        elif directive == 'COPY' and (parsed := _DOCKER_COPY_STAGE.match(arguments)) and parsed[1].casefold() in stages:
            yield line, f'COPY from stage {parsed[1]}'
        else:
            command = arguments.split(None, 1)[0] if arguments else ''
            detail = f' {command}' if directive == 'RUN' and command in _BUILD_COMMANDS else ''
            yield line, f'{directive}{detail}'


def _makefile(text):
    target = ''
    continued = False
    define_depth = 0
    for line, value in enumerate(text.splitlines(), 1):
        if _MAKE_DEFINE.match(value):
            define_depth += 1
            target = ''
            continue
        if define_depth:
            if value.strip().split('#', 1)[0].rstrip() == 'endef':
                define_depth -= 1
            continue
        if continued:
            continued = value.rstrip().endswith('\\')
            continue
        continued = value.rstrip().endswith('\\')
        if value.startswith('\t') and target and value.strip() and not value.lstrip().startswith('#'):
            command = value.strip().lstrip('@+-').split(None, 1)
            detail = f' {command[0]}' if command and command[0] in _BUILD_COMMANDS else ''
            yield line, f'Make recipe for {target}{detail}'
        elif parsed := _MAKE_RULE.fullmatch(value):
            target = parsed[1].strip()
            if _MAKE_ASSIGNMENT.match(parsed[2]):
                target = ''
            if not target:
                continue
            # Inline recipes and comments have no part in the dependency list.
            body = re.split(r'[;#]', parsed[2], maxsplit=1)[0]
            prerequisites = [part for part in body.split() if _MAKE_TARGET.fullmatch(part)][:12]
            detail = f' depends on {", ".join(prerequisites)}' if prerequisites else ''
            yield line, f'Make rule {target}{detail}'
        elif value.strip() and not value.lstrip().startswith('#'):
            target = ''


def _yaml(text):
    levels = []
    scalar_indent = None
    for line, value in enumerate(text.splitlines(), 1):
        stripped = value.strip()
        if not stripped or stripped.startswith('#'):
            continue
        indent = len(value) - len(value.lstrip(' '))
        if scalar_indent is not None and indent > scalar_indent:
            continue
        scalar_indent = None
        key = _YAML_KEY.fullmatch(value)
        if not key:
            continue
        indent += len(key['list_item'] or '')
        while levels and levels[-1][0] >= indent:
            levels.pop()
        levels.append((indent, key['key']))
        path = '.'.join(name for _, name in levels)
        yield line, f'YAML key {path}'
        argument = key['value'].strip()
        if argument and not argument.startswith('#'):
            # A nonempty value cannot introduce child mapping keys. Its
            # indented lines may be block, quoted, or folded scalar content.
            scalar_indent = indent


def _sql_comment_end(text, start):
    depth = 1
    for marker in _SQL_COMMENT.finditer(text, start + 2):
        depth += 1 if marker[0] == '/*' else -1
        if not depth:
            return marker.end()
    return len(text)


def _mask_sql(text):
    parts = []
    index = 0
    while token := _SQL_MASK.search(text, index):
        start = token.start()
        parts.append(text[index:start])
        if token[0] in {'--', '#'}:
            end = text.find('\n', token.end())
            end = len(text) if end < 0 else end
        elif token[0] == '/*':
            end = _sql_comment_end(text, start)
        elif delimiter := token['dollar']:
            end = text.find(delimiter, token.end())
            end = len(text) if end < 0 else end + len(delimiter)
        else:
            end = _SQL_QUOTED[token[0]].match(text, start).end()
        value = text[start:end]
        # Keep simple quoted identifiers for parsing named DDL positions only.
        keep = token[0] in {'"', '`', '['} and re.fullmatch(_SQL_ID, value)
        parts.append(value if keep else re.sub(r'[^\n]', ' ', value))
        index = end
    return ''.join([*parts, text[index:]])


def _sql_columns(statement, start):
    if start >= len(statement) or statement[start] != '(':
        return
    depth, field = 1, start + 1
    for index in range(field, len(statement)):
        value = statement[index]
        if value == '(':
            depth += 1
        elif value == ')':
            depth -= 1
        if not depth or value == ',' and depth == 1:
            column = _SQL_COLUMN.match(statement, field, index)
            if column and column['name'].upper() not in _SQL_CONSTRAINTS:
                data_type = (column['type'] or '').upper()
                yield column.start('name'), column['name'] + (f' {data_type}' if data_type in _SQL_TYPES else '')
            field = index + 1
            if not depth:
                break


def _sql(text):
    masked = _mask_sql(text)
    newlines = [match.start() for match in re.finditer('\n', masked)]
    start = 0
    for statement in masked.split(';'):
        obj = _SQL_OBJECT.match(statement)
        if obj:
            operation, kind = obj['verb'].split()[0].upper(), obj['object'].upper()
            name = obj['name']
            label = f'{operation} {kind} {name}'
            yield bisect_right(newlines, start + obj.start('verb')) + 1, label
            if operation == 'CREATE' and kind == 'TABLE':
                body = re.match(r'\s*', statement[obj.end():])
                for offset, column in _sql_columns(statement, obj.end() + body.end()):
                    yield bisect_right(newlines, start + offset) + 1, f'{label} column {column}'
            relevant = {'TABLE': {'REFERENCES'}, 'VIEW': {'FROM', 'JOIN'}, 'INDEX': {'ON'}}[kind]
            for relation in _SQL_RELATION.finditer(statement, obj.end()):
                relation_type = relation['verb'].upper()
                if relation_type in relevant:
                    yield (bisect_right(newlines, start + relation.start()) + 1,
                           f'{label} {relation_type} {relation["name"]}')
        start += len(statement) + 1


_EXTRACTORS = {'dockerfile': _dockerfile, 'makefile': _makefile, 'sql': _sql, 'yaml': _yaml}


def named_file_excerpt(text: str, relative: str, *, limit: int, priority: Callable[[str], int]) -> str:
    """Return line-numbered evidence of recognized declarations within the excerpt budget."""
    extract = _EXTRACTORS.get(named_file_kind(relative))
    if extract is None or limit < 1:
        return ''
    chosen, size = [], 0
    for line, declaration in sorted(extract(text), key=lambda item: -priority(item[1])):
        formatted = f'L{line}: {declaration}'
        if len(formatted) > min(320, limit - size):
            continue
        chosen.append((line, formatted))
        size += len(formatted) + 1
    return '\n'.join(formatted for _, formatted in sorted(chosen, key=lambda item: item[0]))
