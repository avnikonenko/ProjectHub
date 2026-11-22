import ast

from backend.app.integrations.codex.scheme_evidence import _predicate_shape
from backend.app.integrations.codex.scheme_python_flow import python_entrypoint_trace


def _trace(source, entrypoints=()):
    return python_entrypoint_trace(ast.parse(source), entrypoints, predicate_shape=_predicate_shape)


def test_main_guard_traces_pipeline_handoffs_and_omits_unreached_calls():
    rows, aliases = _trace("""\
import backend
from checkpoints import state
import unused

class Pipeline:
    def run(self, args):
        if args.resume:
            self.restore(args)
        else:
            self.create(args)

    def restore(self, args):
        state.load_checkpoint(args.checkpoint)

    def create(self, args):
        dispatch(args)

def dispatch(args):
    if args.engine == 'private setting':
        backend.launch('private argument')

def main(args):
    if False:
        unused.inactive()
    def deferred():
        unused.unexecuted()
    pipeline = Pipeline()
    pipeline.run(args)

def another_command():
    unused.unreached()

if '__main__' == __name__:
    raise SystemExit(main(config))
""")
    summaries = '\n'.join(label for _, label in rows)
    assert 'main: pipeline.run(args)' in summaries
    assert 'Pipeline.run under if args.resume: self.restore(args)' in summaries
    assert 'Pipeline.run under else of if args.resume: self.create(args)' in summaries
    assert 'Pipeline.restore: state.load_checkpoint(args.checkpoint)' in summaries
    assert 'dispatch under if args.engine == <literal>: backend.launch(<literal>)' in summaries
    assert 'private' not in summaries
    assert 'unused' not in aliases
    assert {'backend', 'state'} <= aliases


def test_package_entrypoint_does_not_follow_a_shadowed_module_function():
    source = """\
def run():
    unreachable.invoke()

def module_entrypoint(run):
    run()
    handler.start_job()

if __name__ != '__main__':
    run()
"""
    assert _trace(source) == ([], set())
    rows, aliases = _trace(source, ('module_entrypoint',))
    assert any('module_entrypoint: handler.start_job()' in label for _, label in rows)
    assert 'unreachable' not in aliases
