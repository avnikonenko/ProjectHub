from backend.app.integrations.codex.scheme_python_dependencies import (
    local_python_modules,
    python_imports,
    setup_dependencies,
)


def test_python_imports_detect_nested_and_literal_dynamic_external_modules():
    modules = local_python_modules([
        'src/application/__init__.py', 'src/application/actions.py',
        'scripts/workflow.py', 'scripts/workflow_helper.py', 'tests/crem.py',
    ])
    limitations = set()
    dependencies = python_imports("""
import os, importlib as il
from application.actions import say
from importlib import import_module as load
from . import runtime
import workflow_helper
def explain():
    from crem.crem import grow_mol
    il.import_module("numpy.typing")
    load(name="rdkit.Chem")
    __import__("nonlocal_module")
    __import__("relative_module", level=1)
    load(".relative_module", "application")
    load(plugin_name)
""", 'scripts/workflow.py', modules, limitations)

    assert dependencies == [(name, '', 'import:python') for name in ['crem', 'nonlocal_module', 'numpy', 'rdkit']]
    assert limitations == {'Some Python imports are resolved dynamically at runtime.'}


def test_python_import_parse_failure_reports_incomplete_scan():
    limitations = set()
    assert python_imports('from crem import (\n', 'broken.py', {}, limitations) == []
    assert limitations == {'Imports could not be parsed in some Python source files.'}


def test_setup_py_declarations_keep_known_requirements_with_dynamic_fields():
    limitations = set()
    dependencies = setup_dependencies("""
from setuptools import setup as package
requirements = ["crem>=0.2", optional_at_runtime, "rdkit[draw]; python_version >= '3.11'"]
extra = {"test": ["pytest>=8"], "dynamic": load_requirements()}
package(install_requires=requirements, extras_require=extra, setup_requires=["setuptools"])
""", 'setup.py', limitations)

    assert dependencies == [
        ('crem', '>=0.2', 'runtime'),
        ('rdkit', "[draw]; python_version >= '3.11'", 'runtime'),
        ('pytest', '>=8', 'optional:test'),
        ('setuptools', '', 'build'),
    ]
    assert limitations


def test_setup_cfg_declarations_support_multiline_dependencies_and_extras():
    limitations = set()
    dependencies = setup_dependencies("""
[metadata]
name = crem-project
[options]
install_requires =
    crem>=0.2
    scipy
[options.extras_require]
docs =
    sphinx>=8
""", 'nested/setup.cfg', limitations)

    assert dependencies == [
        ('crem', '>=0.2', 'runtime'),
        ('scipy', '', 'runtime'),
        ('sphinx', '>=8', 'optional:docs'),
    ]
    assert not limitations
