"""Local ProjectHub entry point; configuration is applied before app startup."""
import argparse
import os
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
        startup_environment = dict(os.environ)
        settings = load_settings()
        settings.apply_environment()
    except (ConfigError, OSError) as exc:
        message = str(exc) if isinstance(exc, ConfigError) else 'Could not create the local configuration file.'
        print(f'projecthub: {message}', file=sys.stderr)
        return 1
    import uvicorn
    print(f'ProjectHub: http://127.0.0.1:{settings.port}', flush=True)
    from .main import create_app
    from .restart import RestartController
    def validate_restart():
        from fastapi import HTTPException
        try:
            updated = load_settings(startup_environment)
        except ConfigError as exc:
            raise HTTPException(422, str(exc)) from exc
        except OSError as exc:
            raise HTTPException(503, 'Could not read the local ProjectHub configuration.') from exc
        if updated.port != settings.port:
            raise HTTPException(409, 'Restart from the terminal to apply a changed server port.')

    controller = RestartController(validate_restart=validate_restart)
    config = uvicorn.Config(lambda: create_app(restart_controller=controller),
                            factory=True, host='127.0.0.1', port=settings.port)
    server = uvicorn.Server(config)
    controller.bind(lambda: setattr(server, 'should_exit', True))
    try:
        server.run()
    except BaseException:
        controller.close(cancel=True)
        raise
    if not getattr(server, 'started', True):
        controller.close(cancel=True)
        return 3
    if controller.requested:
        try:
            os.execve(sys.executable, [sys.executable, '-m', 'backend.app.cli', 'start'],
                      startup_environment)
        except OSError:
            print('projecthub: Could not restart ProjectHub. Start it again with the projecthub command.',
                  file=sys.stderr)
            return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
