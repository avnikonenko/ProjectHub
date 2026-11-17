"""Exercise both skill installers using isolated homes."""
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from backend.app import cli


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts' / 'install-skill.sh'


def test_wheel_install_skill_is_idempotent_and_preserves_existing_links(tmp_path, monkeypatch, capsys):
    home = tmp_path / 'home'
    source = tmp_path / 'bundled data' / 'skills' / 'projecthub-notes'
    source.mkdir(parents=True)
    (source / 'SKILL.md').write_text('bundled skill', encoding='utf-8')
    monkeypatch.setenv('HOME', str(home))
    monkeypatch.setattr(cli, 'runtime_resource', lambda *_: source.parent)
    monkeypatch.chdir(tmp_path)

    target = home / '.agents/skills/projecthub-notes'
    assert cli.main(['install-skill']) == 0
    inode = target.lstat().st_ino
    assert target.resolve() == source
    assert cli.main(['install-skill']) == 0
    assert target.lstat().st_ino == inode

    target.unlink()
    other = tmp_path / 'existing skill'
    other.mkdir()
    target.symlink_to(other)
    assert cli.main(['install-skill']) == 1
    assert target.resolve() == other
    assert 'Refusing to replace existing skill' in capsys.readouterr().err


def run_installer(home, *args, script=SCRIPT, cwd=None):
    return subprocess.run(
        ['bash', str(script), *args],
        env={**os.environ, 'HOME': str(home)},
        cwd=cwd or home,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )


def test_install_creates_skill_link_from_unrelated_working_directory(tmp_path):
    home = tmp_path / 'user home'
    home.mkdir()

    result = run_installer(home)

    assert result.returncode == 0, result.stderr
    target = home / '.agents' / 'skills' / 'projecthub-notes'
    assert target.is_symlink()
    assert target.resolve() == ROOT / 'skills' / 'projecthub-notes'
    assert (target / 'SKILL.md').read_bytes() == (ROOT / 'skills/projecthub-notes/SKILL.md').read_bytes()


def test_repeat_install_is_successful_without_replacing_existing_link(tmp_path):
    first = run_installer(tmp_path)
    assert first.returncode == 0, first.stderr
    target = tmp_path / '.agents/skills/projecthub-notes'
    before = (target.lstat().st_ino, target.readlink())

    second = run_installer(tmp_path)

    assert second.returncode == 0, second.stderr
    assert (target.lstat().st_ino, target.readlink()) == before


@pytest.mark.parametrize('kind', ['directory', 'file', 'different_link', 'dangling_link'])
def test_install_preserves_conflicting_skill(tmp_path, kind):
    target = tmp_path / '.agents/skills/projecthub-notes'
    target.parent.mkdir(parents=True)
    if kind == 'directory':
        target.mkdir()
        (target / 'SKILL.md').write_text('custom existing skill')
    elif kind == 'file':
        target.write_text('custom existing skill')
    elif kind == 'different_link':
        other = tmp_path / 'other skill'
        other.mkdir()
        (other / 'SKILL.md').write_text('custom existing skill')
        target.symlink_to(other)
    else:
        target.symlink_to(tmp_path / 'missing skill')
    before = target.lstat().st_ino
    previous_link = target.readlink() if target.is_symlink() else None

    result = run_installer(tmp_path)

    assert result.returncode != 0
    assert target.lstat().st_ino == before
    if previous_link is not None:
        assert target.readlink() == previous_link
    if kind in ('directory', 'different_link'):
        assert (target / 'SKILL.md').read_text() == 'custom existing skill'
    elif kind == 'file':
        assert target.read_text() == 'custom existing skill'


def test_install_rejects_unknown_arguments(tmp_path):
    result = run_installer(tmp_path, '--unknown-option')

    assert result.returncode != 0
    assert not (tmp_path / '.agents/skills/projecthub-notes').is_symlink()


def test_install_resolves_checkout_with_spaces(tmp_path):
    checkout = tmp_path / 'ProjectHub checkout'
    (checkout / 'scripts').mkdir(parents=True)
    (checkout / 'skills/projecthub-notes').mkdir(parents=True)
    script = checkout / 'scripts/install-skill.sh'
    shutil.copyfile(SCRIPT, script)
    (checkout / 'skills/projecthub-notes/SKILL.md').write_text('test skill')
    home = tmp_path / 'another home'
    home.mkdir()

    result = run_installer(home, script=script, cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    target = home / '.agents/skills/projecthub-notes'
    assert target.is_symlink()
    assert target.resolve() == checkout / 'skills/projecthub-notes'
    assert (target / 'SKILL.md').read_text() == 'test skill'


@pytest.fixture
def installer_checkout(tmp_path):
    """Use real install orchestration with inert setup and launcher commands."""
    checkout = tmp_path / 'installer checkout'
    (checkout / 'scripts').mkdir(parents=True)
    (checkout / '.venv/bin').mkdir(parents=True)
    shutil.copyfile(ROOT / 'scripts/install.sh', checkout / 'scripts/install.sh')
    log = checkout / 'calls.log'
    for name in ('setup', 'install-local', 'install-skill'):
        (checkout / f'scripts/{name}.sh').write_text(
            f'#!/usr/bin/env bash\nprintf "%s\\n" "{name}" >> calls.log\n'
        )
    python = checkout / '.venv/bin/python'
    python.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "config" >> calls.log\n')
    python.chmod(0o755)
    return checkout, log


@pytest.mark.parametrize(('args', 'expected'), [
    ((), ['setup', 'config', 'install-local']),
    (('--skip-skill',), ['setup', 'config', 'install-local']),
    (('--install-skill',), ['setup', 'config', 'install-local', 'install-skill']),
    (('--help',), []),
])
def test_main_installer_optional_skill_flags(installer_checkout, tmp_path, args, expected):
    checkout, log = installer_checkout

    # DEVNULL is deliberately not a TTY: unattended installs must never prompt.
    result = subprocess.run(
        ['bash', str(checkout / 'scripts/install.sh'), *args],
        stdin=subprocess.DEVNULL,
        env={**os.environ, 'HOME': str(tmp_path)},
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    assert (log.read_text().splitlines() if log.exists() else []) == expected


def test_main_installer_invalid_option_does_not_run_setup(installer_checkout, tmp_path):
    checkout, log = installer_checkout

    result = run_installer(tmp_path, '--unknown-option', script=checkout / 'scripts/install.sh')

    assert result.returncode != 0
    assert not log.exists()


def test_main_installer_reports_skill_install_failure(installer_checkout, tmp_path):
    checkout, log = installer_checkout
    (checkout / 'scripts/install-skill.sh').write_text('exit 17\n')

    result = run_installer(tmp_path, '--install-skill', script=checkout / 'scripts/install.sh')

    assert result.returncode == 17
    assert log.read_text().splitlines() == ['setup', 'config', 'install-local']
