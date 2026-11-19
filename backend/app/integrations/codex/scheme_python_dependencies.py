"""Read Python dependency declarations without importing repository code."""
import ast
from collections import defaultdict
import configparser
from pathlib import PurePosixPath
import re
import sys


_STDLIB = sys.stdlib_module_names | set(sys.builtin_module_names)
_REQUIREMENT = re.compile(r'([A-Za-z0-9][A-Za-z0-9._-]*)(\[[A-Za-z0-9_.,-]+\])?\s*([<>=!~@;].*)?')
_SETUP_LIMITATION = 'Some Python setup dependency declarations could not be resolved statically.'


def local_python_modules(paths):
    """Index importable names by directory, including namespace packages."""
    modules = defaultdict(set)
    for relative in paths:
        path = PurePosixPath(relative)
        if path.suffix != '.py':
            continue
        if path.stem != '__init__' and path.stem.isidentifier():
            modules[path.parent].add(path.stem)
        for parent in path.parents:
            if parent.name.isidentifier():
                modules[parent.parent].add(parent.name)
    return modules


def python_imports(value, path, local_modules, limitations):
    """Return absolute import names and identify recognizable dynamic gaps."""
    try:
        tree = ast.parse(value)
    except (SyntaxError, ValueError, RecursionError):
        limitations.add('Imports could not be parsed in some Python source files.')
        return []

    path = PurePosixPath(path)
    local = set(local_modules.get(path.parent, ())) | set(local_modules.get(PurePosixPath('.'), ()))
    for parent in path.parents:
        local.update(local_modules.get(parent / 'src', ()))
    ignored = _STDLIB | local
    nodes = list(ast.walk(tree))
    importlib_names = {'importlib'}
    import_functions = {'__import__'}
    names = set()
    for node in nodes:
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split('.')[0])
                if alias.name == 'importlib' and alias.asname:
                    importlib_names.add(alias.asname)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split('.')[0])
            if node.module == 'importlib':
                import_functions.update(alias.asname or alias.name
                                        for alias in node.names if alias.name == 'import_module')
    for node in nodes:
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        if isinstance(function, ast.Name) and function.id in import_functions:
            if function.id == '__import__':
                level = (node.args[4] if len(node.args) > 4 else
                         next((keyword.value for keyword in node.keywords if keyword.arg == 'level'), None))
                if isinstance(level, ast.Constant) and isinstance(level.value, int) and level.value > 0:
                    continue
                if level is not None and not (isinstance(level, ast.Constant) and level.value == 0):
                    limitations.add('Some Python imports are resolved dynamically at runtime.')
                    continue
        elif not (isinstance(function, ast.Attribute) and function.attr == 'import_module'
                  and isinstance(function.value, ast.Name) and function.value.id in importlib_names):
            continue
        argument = (node.args[0] if node.args else
                    next((keyword.value for keyword in node.keywords if keyword.arg == 'name'), None))
        if (isinstance(argument, ast.Constant) and isinstance(argument.value, str)
                and all(part.isidentifier() for part in argument.value.lstrip('.').split('.'))):
            if not argument.value.startswith('.'):
                names.add(argument.value.split('.')[0])
        else:
            limitations.add('Some Python imports are resolved dynamically at runtime.')
    return [(name, '', 'import:python') for name in sorted(names - ignored)]


def _requirement_entries(lines, category, limitations):
    for line in lines:
        if isinstance(line, str):
            line = line.strip().split(' #', 1)[0]
            if not line or line.startswith('#'):
                continue
            match = _REQUIREMENT.fullmatch(line)
            if match:
                name, extras, constraint = match.groups()
                yield name, (extras or '') + (constraint or ''), category
                continue
        limitations.add(_SETUP_LIMITATION)


def _setup_cfg_dependencies(value, limitations):
    config = configparser.ConfigParser(interpolation=None)
    config.read_string(value)
    for field, category in (('install_requires', 'runtime'), ('setup_requires', 'build'),
                            ('tests_require', 'development')):
        if config.has_option('options', field):
            yield from _requirement_entries(config['options'][field].splitlines(), category, limitations)
    if config.has_section('options.extras_require'):
        for name, requirements in config.items('options.extras_require'):
            yield from _requirement_entries(requirements.splitlines(), f'optional:{name}', limitations)


def _literal_requirements(node, variables, limitations):
    node = variables.get(node.id) if isinstance(node, ast.Name) else node
    if isinstance(node, (ast.List, ast.Tuple)):
        return [item.value if isinstance(item, ast.Constant) else None for item in node.elts]
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value.splitlines()
    limitations.add(_SETUP_LIMITATION)
    return []


def _setup_py_dependencies(value, limitations):
    tree = ast.parse(value)
    variables = {}
    setup_names = {'setup'}
    modules = {'setuptools', 'distutils.core'}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            variables[node.targets[0].id] = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            variables[node.target.id] = node.value
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module in modules:
            setup_names.update(alias.asname or alias.name for alias in node.names if alias.name == 'setup')
        elif isinstance(node, ast.Import):
            modules.update(alias.asname for alias in node.names if alias.name in modules and alias.asname)

    field_categories = {'install_requires': 'runtime', 'setup_requires': 'build', 'tests_require': 'development'}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        if isinstance(function, ast.Attribute) and function.attr == 'setup':
            if not (isinstance(function.value, ast.Name) and function.value.id in modules
                    or isinstance(function.value, ast.Attribute) and function.value.attr == 'core'
                    and isinstance(function.value.value, ast.Name) and function.value.value.id == 'distutils'):
                continue
        elif not isinstance(function, ast.Name) or function.id not in setup_names:
            continue
        for keyword in node.keywords:
            field = keyword.arg
            if field in field_categories:
                lines = _literal_requirements(keyword.value, variables, limitations)
                yield from _requirement_entries(lines, field_categories[field], limitations)
            elif field == 'extras_require':
                groups = variables.get(keyword.value.id) if isinstance(keyword.value, ast.Name) else keyword.value
                if not isinstance(groups, ast.Dict):
                    limitations.add(_SETUP_LIMITATION)
                    continue
                for name, group in zip(groups.keys, groups.values):
                    if isinstance(name, ast.Constant) and isinstance(name.value, str):
                        lines = _literal_requirements(group, variables, limitations)
                        yield from _requirement_entries(lines, f'optional:{name.value}', limitations)
                    else:
                        limitations.add(_SETUP_LIMITATION)
            elif field is None:
                limitations.add(_SETUP_LIMITATION)


def setup_dependencies(value, path, limitations):
    """Collect literal declarations in setup.py and setup.cfg."""
    try:
        if PurePosixPath(path).name == 'setup.cfg':
            return list(_setup_cfg_dependencies(value, limitations))
        return list(_setup_py_dependencies(value, limitations))
    except (ValueError, SyntaxError, RecursionError, configparser.Error):
        limitations.add(_SETUP_LIMITATION)
        return []
