"""Local ProjectHub entry point; configuration is applied before app startup."""
import argparse
import sys

from .config import ConfigError, create_config, load_settings


def main(argv=None):
    parser = argparse.ArgumentParser(prog='projecthub')
    parser.add_argument('command', nargs='?', choices=('start', 'config'), default='start')
    args = parser.parse_args(argv)
    try:
        if args.command == 'config':
            print(create_config())
            return 0
        settings = load_settings()
        settings.apply_environment()
    except (ConfigError, OSError) as exc:
        message = str(exc) if isinstance(exc, ConfigError) else 'Could not create the local configuration file.'
        print(f'projecthub: {message}', file=sys.stderr)
        return 1
    import uvicorn
    print(f'ProjectHub: http://127.0.0.1:{settings.port}', flush=True)
    uvicorn.run('backend.app.main:create_app', factory=True, host='127.0.0.1', port=settings.port)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
