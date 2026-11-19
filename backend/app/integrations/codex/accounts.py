"""Discover Codex homes without opening authentication files."""
import os
import re
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session
from ...models import CodexProfile

from .protocol import CodexError


def account_homes():
    homes = {'default': Path.home() / '.codex'}
    current = os.environ.get('CODEX_HOME')
    if current:
        homes['current'] = Path(current).expanduser()
    root = Path.home() / '.codex-manager' / 'profiles'
    if root.is_dir():
        for profile in sorted(root.iterdir()):
            if re.fullmatch(r'[A-Za-z0-9_-]+', profile.name) and (profile / 'home').is_dir():
                homes['profile:' + profile.name] = profile / 'home'
    return homes


def account_home(key, engine=None):
    if key is None:
        return None
    if engine is not None and re.fullmatch(r'registered:[1-9][0-9]*', key):
        with Session(engine) as db:
            row = db.get(CodexProfile, int(key.split(':')[1]))
            home = Path(row.directory) if row and row.provider == 'codex' else None
    else:
        home = account_homes().get(key)
    if home is None or not home.is_dir():
        raise CodexError('The selected Codex account profile is unavailable.')
    return str(home.resolve())


def launch_home():
    return Path(os.environ.get('CODEX_HOME', Path.home() / '.codex')).expanduser().resolve()


def profile_entries(engine=None):
    entries = [(key, {'default': 'Default Codex', 'current': 'Launch profile'}.get(
        key, key.removeprefix('profile:')), path) for key, path in account_homes().items()]
    if engine is not None:
        with Session(engine) as db:
            entries.extend((f'registered:{row.id}', row.name, Path(row.directory))
                           for row in db.scalars(select(CodexProfile).where(CodexProfile.provider == 'codex')
                                                 .order_by(CodexProfile.id)))
    return entries


def claude_profile_entries(engine):
    with Session(engine) as db:
        return [(f'registered:{row.id}', row.name, Path(row.directory)) for row in db.scalars(
            select(CodexProfile).where(CodexProfile.provider == 'claude').order_by(CodexProfile.id))]


def claude_profile(engine, key):
    for identifier, label, path in claude_profile_entries(engine):
        if identifier == key and path.is_dir():
            return label, str(path.resolve())
    raise CodexError('The selected Claude Code profile is unavailable.')


def claude_launch_home():
    return Path(os.environ.get('CLAUDE_CONFIG_DIR', Path.home() / '.claude')).expanduser().resolve()


def account_options(engine=None, selected=None):
    entries = profile_entries(engine)
    # Preserve an existing selection's identity while hiding other aliases.
    entries.sort(key=lambda item: (item[0] != selected, not item[0].startswith('registered:')))
    names = {path.resolve(): label for key, label, path in entries
             if key.startswith('registered:') and path.is_dir()}
    seen = set() if selected is not None else {launch_home()}
    options = []
    for key, label, path in entries:
        if not path.is_dir():
            continue
        canonical = path.resolve()
        if canonical in seen or (canonical == launch_home() and key != selected):
            continue
        seen.add(canonical)
        options.append({'id': key, 'label': names.get(canonical, label)})
    if selected and not any(option['id'] == selected for option in options):
        options.insert(0, {'id': selected, 'label': 'Unavailable profile'})
    return options


def launch_label(engine=None):
    home = launch_home()
    entries = profile_entries(engine)
    entries.sort(key=lambda item: (not item[0].startswith('registered:'),
                                   not item[0].startswith('profile:')))
    for key, label, path in entries:
        if (key.startswith(('registered:', 'profile:')) and path.is_dir()
                and path.resolve() == home):
            return label
    return 'ProjectHub launch account'


def validate_profile_directory(value):
    path = Path(value).expanduser()
    if (not path.is_absolute() or any(ord(c) < 32 for c in str(path))
            or not path.is_dir() or path.resolve() == Path('/')):
        raise CodexError('Choose an existing absolute Codex directory.')
    return str(path.resolve())
