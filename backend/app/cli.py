"""Local ProjectHub entry point; configuration is applied before app startup."""
import argparse
from contextlib import ExitStack
import os
from pathlib import Path
import sys

from .config import ConfigError, create_config, load_settings
from .resource_paths import bundled_resources_available, runtime_resource


_AGENT_COMMANDS = ('projects', 'context', 'read', 'search', 'note', 'notes', 'todo', 'session')


def _install_skill():
    try:
        source = (runtime_resource('skills', 'skills') / 'projecthub-notes').resolve()
        if not (source / 'SKILL.md').is_file():
            print('projecthub: The ProjectHub notes skill is missing from this installation.', file=sys.stderr)
            return 1
        target = Path.home() / '.agents' / 'skills' / 'projecthub-notes'
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            target.symlink_to(source, target_is_directory=True)
        except FileExistsError:
            if not target.is_symlink() or target.resolve() != source:
                print(f'projecthub: Refusing to replace existing skill: {target}', file=sys.stderr)
                return 1
    except (OSError, RuntimeError):
        print('projecthub: Could not install the ProjectHub notes skill.', file=sys.stderr)
        return 1
    print(f'Installed Codex skill: {target}')
    print('Open a new Codex session with ProjectHub running to use the skill.')
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in _AGENT_COMMANDS:
        from scripts.notes_cli import main as agent_main

        return agent_main(argv)
    parser = argparse.ArgumentParser(
        prog='projecthub', description='Start or configure your local ProjectHub workspace.',
        epilog='Project commands: projects, context, read, search, note, todo, session. '
               'Use projecthub COMMAND --help for details.',
    )
    parser.add_argument('command', nargs='?', choices=('start', 'config', 'install-skill', 'help'), default='start',
                        help='start the app (default), show the configuration path, install the optional Codex skill, or show help')
    args = parser.parse_args(argv)
    if args.command == 'help':
        parser.print_help()
        return 0
    if args.command == 'install-skill':
        return _install_skill()
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
    from .main import create_app
    from .restart import RestartController, own_data_directory
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
    config = uvicorn.Config(lambda: create_app(data_dir=settings.data_dir, restart_controller=controller,
                                              data_directory_owned=True),
                            factory=True, host='127.0.0.1', port=settings.port)
    server = uvicorn.Server(config)
    controller.bind(lambda: setattr(server, 'should_exit', True))
    with ExitStack() as resources:
        try:
            resources.enter_context(own_data_directory(settings.data_dir))
        except BlockingIOError:
            print('projecthub: Another server is using this data directory.', file=sys.stderr)
            return 3
        except OSError:
            print('projecthub: Could not lock the local data directory.', file=sys.stderr)
            return 1
        try:
            # Uvicorn normally binds after startup. Reserve the port before
            # building the app, which performs persistent recovery at startup.
            listener = resources.enter_context(config.bind_socket())
        except SystemExit as exc:
            return exc.code if isinstance(exc.code, int) else 3
        listener.set_inheritable(False)
        print(f'ProjectHub: http://127.0.0.1:{settings.port}', flush=True)
        try:
            server.run(sockets=[listener])
        except BaseException:
            controller.close(cancel=True)
            raise
    if not getattr(server, 'started', True):
        controller.close(cancel=True)
        return 3
    if controller.requested:
        try:
            safe_path = ['-P'] if bundled_resources_available() else []
            os.execve(sys.executable, [sys.executable, *safe_path, '-m', 'backend.app.cli', 'start'],
                      startup_environment)
        except OSError:
            print('projecthub: Could not restart ProjectHub. Start it again with the projecthub command.',
                  file=sys.stderr)
            return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
