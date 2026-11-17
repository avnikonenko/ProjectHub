"""Resolve runtime assets for a source checkout or a wheel installation."""
from pathlib import Path


_APP = Path(__file__).resolve().parent
_DISTRIBUTION = _APP / '_distribution'
_CHECKOUT = _APP.parents[1]


def bundled_resources_available() -> bool:
    return _DISTRIBUTION.is_dir()


def runtime_resource(name: str, *checkout_parts: str) -> Path:
    if bundled_resources_available():
        return _DISTRIBUTION / name
    return _CHECKOUT.joinpath(*checkout_parts)
