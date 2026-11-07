"""Discover Codex homes without opening authentication files."""
import os
import re
from pathlib import Path

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


def account_home(key):
    if key is None:
        return None
    home = account_homes().get(key)
    if home is None or not home.is_dir():
        raise CodexError('The selected Codex account profile is unavailable.')
    return str(home.resolve())


def account_options():
    return [{'id': key, 'label': {'default': 'Default Codex account', 'current': 'ProjectHub launch account'}.get(
        key, key.removeprefix('profile:'))} for key in account_homes()]
