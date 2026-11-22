import ast

from backend.app.integrations.codex.scheme_evidence import _EvidenceScope, _predicate_shape
from backend.app.integrations.codex.scheme_python_flow import python_flow_declarations


def _extract(source, instruction):
    return python_flow_declarations(ast.parse(source), _EvidenceScope(instruction).score,
                                    predicate_shape=_predicate_shape)


def test_flow_preserves_identifier_handoffs_and_rejecting_guard_lines():
    rows = dict(_extract("""\
class Handler:
    async def cancel_job(self, project_id, body, started_at):
        key = (project_id, body.kind)
        with run_lock:
            if snapshot.get('started_at') != started_at:
                raise StaleAttempt('diagnostic literal')
            return await progress.cancel(project_id, body.kind, body.started_at)
""", 'Explain cancellation'))
    assert rows[3] == 'Python flow Handler.cancel_job: key = (project_id, body.kind)'
    assert rows[5] == (
        'Python flow Handler.cancel_job under with run_lock: if snapshot.get(...) != started_at -> raise StaleAttempt'
    )
    assert rows[7] == (
        'Python flow Handler.cancel_job under with run_lock: '
        'return await progress.cancel(project_id, body.kind, body.started_at)'
    )


def test_flow_includes_save_callbacks_without_forwarding_literals_or_default_calls():
    rows = _extract("""\
@wrap(cancel_in_decorator('decorator literal'))
def complete(save, default=cancel_in_default('default literal')):
    with run_lock:
        if events[key].is_set():
            raise GenerationCancelled('exception literal')
        result = save('positional literal', name='keyword literal')
    return result

def persist(project_id, db):
    progress.complete(project_id, db.commit, db=db)
""", 'Show how a cancelled generation preserves the saved result')
    assert set(rows) == {
        (4, 'Python flow complete under with run_lock: if events[key].is_set(...) -> raise GenerationCancelled'),
        (6, 'Python flow complete under with run_lock: result = save(<literal>, name=<literal>)'),
        (10, 'Python flow persist: progress.complete(project_id, db.commit, db=db)'),
    }
