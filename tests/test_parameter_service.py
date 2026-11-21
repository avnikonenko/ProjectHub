"""Independent Parameters persistence and validation; semantic AI is mocked."""
from copy import deepcopy
import json

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db import initialize
from backend.app.models import GenerationRevision, Project, ProjectBrief, ProjectKnowledge, ProjectScheme
from backend.app.integrations.codex import parameters, progress
from backend.app.integrations.codex.brief import BriefBusy, generation_lock
from backend.app.integrations.codex.generation_metrics import begin_model_request
from backend.app.integrations.codex.protocol import CodexError


def evidence():
    return {'parameters': [{
        'id': 'parameter:context', 'name': 'MAX_CONTEXT', 'component': 'Configuration',
        'path': 'app/config.py', 'symbol': 'MAX_CONTEXT', 'line_start': 3, 'line_end': 3,
        'default_value': '40000', 'default_kind': 'literal', 'source_ids': ['code:context'],
    }, {
        'id': 'parameter:workers', 'name': 'workers', 'component': 'Execution',
        'path': 'app/main.py', 'symbol': 'parse_args', 'line_start': 20, 'line_end': 20,
        'default_value': 'cpu_count()', 'default_kind': 'expression', 'source_ids': ['code:workers'],
    }], 'sources': [
        {'id': 'code:context', 'label': 'Context budget', 'path': 'app/config.py',
         'text': 'MAX_CONTEXT = 40000; beyond this context some input is omitted.',
         'provenance': 'parsed code declaration', 'content_hash': 'context', 'revision': 'r1'},
        {'id': 'code:workers', 'label': 'Worker expression', 'path': 'app/main.py',
         'text': 'workers default=cpu_count()', 'provenance': 'parsed code declaration',
         'content_hash': 'workers', 'revision': 'r1'},
    ], 'coverage': {'partial': False, 'limitations': []}, 'fingerprint': 'original-revision',
    'diagnostics': {'files_inspected': 2}}


def explanation(identifier='parameter:context', refs=None):
    return {'id': identifier, 'purpose': 'Bound the retained project context.', 'purpose_basis': 'documented',
        'rationale': '', 'rationale_basis': 'unknown',
        'trade_off': 'Input beyond this bound is omitted.', 'trade_off_basis': 'documented',
        'units': 'characters', 'source_ids': ['code:context'] if refs is None else refs}


def test_model_can_explain_but_not_replace_default_facts_or_remove_unexplained_parameters():
    original = evidence()
    before = deepcopy(original)
    items, count = parameters.validate_output(json.dumps({'items': [explanation()]}), original)
    assert count == 1 and len(items) == 2
    assert items[0]['default_value'] == '40000' and items[0]['default_kind'] == 'literal'
    assert items[0]['source_ids'] == ['code:context']
    assert items[0]['explanation_source_ids'] == ['code:context']
    assert items[1]['default_value'] == 'cpu_count()' and items[1]['default_kind'] == 'expression'
    assert items[1]['purpose_basis'] == items[1]['rationale_basis'] == items[1]['trade_off_basis'] == 'unknown'
    assert original == before


def used_evidence():
    value = evidence()
    value['sources'].append({
        'id': 'code:context-use', 'kind': 'implementation_usage',
        'label': 'app/reader.py:27', 'path': 'app/reader.py', 'symbol': 'read',
        'line_start': 27, 'line_end': 28, 'revision': 'r2',
        'text': 'if len(selected_text) > MAX_CONTEXT:\n    return selected_text[:MAX_CONTEXT]',
    })
    value['parameters'][0]['usage_source_ids'] = ['code:context-use']
    return value


def finding():
    return {**explanation(refs=['code:context', 'code:context-use']),
        'title': 'Selected context is truncated at 40,000 characters',
        'behavior': 'The reader returns only the first MAX_CONTEXT characters when selected text exceeds the bound.',
        'behavior_basis': 'documented', 'finding_kind': 'limit'}


def test_implementation_finding_requires_its_definition_and_usage_and_keeps_locations():
    original = used_evidence()
    parameters._evidence(original, 1)
    items, count = parameters.validate_output(json.dumps({'items': [finding()]}), original)
    assert count == 1
    assert items[0]['finding_kind'] == 'limit'
    assert items[0]['behavior_basis'] == 'documented'
    assert items[0]['default_value'] == '40000'
    assert items[0]['path'] == 'app/config.py'
    assert items[0]['source_ids'] == ['code:context']
    assert items[0]['usage_source_ids'] == ['code:context-use']
    assert items[0]['explanation_source_ids'] == ['code:context', 'code:context-use']
    assert items[1]['finding_kind'] == 'parameter' and items[1]['behavior_basis'] == 'unknown'


@pytest.mark.parametrize('change,match', [
    (lambda item: item.update(source_ids=['code:context']), 'usage site'),
    (lambda item: item.update(source_ids=['code:context-use']), 'code declaration'),
    (lambda item: item.update(behavior='', behavior_basis='unknown'), 'inconsistent implementation'),
    (lambda item: item.update(behavior_basis='unknown'), 'inconsistent implementation'),
    (lambda item: item.update(title=''), 'inconsistent implementation'),
    (lambda item: item.update(finding_kind='AGREED'), 'inconsistent implementation'),
    (lambda item: item.update(default_value='24'), 'invalid structure'),
])
def test_unsupported_behavior_cannot_be_promoted_to_code_backed_finding(change, match):
    output = finding()
    change(output)
    with pytest.raises(CodexError, match=match):
        parameters.validate_output(json.dumps({'items': [output]}), used_evidence())


def test_declaration_alone_is_not_evidence_of_enforcement_or_a_consequence():
    output = {**explanation(), 'title': 'Context default', 'behavior': '',
              'behavior_basis': 'unknown', 'finding_kind': 'parameter'}
    items, _ = parameters.validate_output(json.dumps({'items': [output]}), evidence())
    assert items[0]['finding_kind'] == 'parameter' and not items[0]['behavior']
    output.update(behavior='The reader truncates all input.', behavior_basis='inferred')
    with pytest.raises(CodexError, match='usage site'):
        parameters.validate_output(json.dumps({'items': [output]}), evidence())


@pytest.mark.parametrize('change', [
    lambda value: value['parameters'][0].update(usage_source_ids=['foreign']),
    lambda value: value['parameters'][0].update(usage_source_ids=['code:context']),
    lambda value: value['sources'][-1].update(project_id=2),
])
def test_usage_evidence_cannot_reference_missing_declaration_only_or_foreign_sources(change):
    value = used_evidence()
    change(value)
    with pytest.raises(CodexError):
        parameters._evidence(value, 1)


def test_inline_behavior_can_use_its_code_as_both_definition_and_usage():
    value = used_evidence()
    item = value['parameters'][0]
    item.update(declaration_kind='implementation_behavior', default_kind='unspecified', default_value=None,
                source_ids=['code:context-use'], path='app/reader.py')
    parameters._evidence(value, 1)
    output = finding(); output['source_ids'] = ['code:context-use']
    rows, _ = parameters.validate_output(json.dumps({'items': [output]}), value)
    assert rows[0]['default_value'] is None
    assert rows[0]['finding_kind'] == 'limit'


def test_machine_citation_only_groups_are_removed_without_changing_code_brackets_or_refs():
    output = finding()
    output['behavior'] = 'The reader applies records[:12] [code:context; code:context-use].'
    output['title'] = 'Selected context [code:context]'
    output['purpose'] = 'Bound input [code:context, code:context-use].'
    output['trade_off'] = 'Keep [unknown:ref] and [code:context; ordinary words] as supplied.'
    rows, _ = parameters.validate_output(json.dumps({'items': [output]}), used_evidence())
    assert rows[0]['behavior'] == 'The reader applies records[:12].'
    assert rows[0]['title'] == 'Selected context'
    assert rows[0]['purpose'] == 'Bound input.'
    assert rows[0]['trade_off'] == output['trade_off']
    assert rows[0]['explanation_source_ids'] == output['source_ids']


def test_citation_markup_alone_does_not_establish_behavior():
    output = finding(); output['behavior'] = '[code:context-use]'
    with pytest.raises(CodexError, match='inconsistent implementation'):
        parameters.validate_output(json.dumps({'items': [output]}), used_evidence())


@pytest.mark.parametrize('change,match', [
    (lambda value: value.update(default_value='999'), 'invalid structure'),
    (lambda value: value.update(id='invented'), 'invented'),
    (lambda value: value.update(id=' parameter:context '), 'invented'),
    (lambda value: value.update(source_ids=['foreign']), 'foreign evidence'),
    (lambda value: value.update(source_ids=['code:workers']), 'code declaration'),
    (lambda value: value.update(source_ids=[]), 'code declaration'),
    (lambda value: value.update(rationale='It is faster', rationale_basis='inferred'), 'inconsistent'),
    (lambda value: value.update(rationale='It is faster', rationale_basis='unknown'), 'inconsistent'),
    (lambda value: value.update(trade_off='', trade_off_basis='documented'), 'inconsistent'),
    (lambda value: value.update(purpose='x' * 501), 'invalid text'),
    (lambda value: value.update(purpose='bad\x00text'), 'invalid text'),
])
def test_invalid_explanations_are_rejected(change, match):
    value = explanation()
    change(value)
    with pytest.raises(CodexError, match=match):
        parameters.validate_output(json.dumps({'items': [value]}), evidence())


def test_duplicate_ids_and_invalid_structure_are_rejected():
    for raw in ('{bad', '[]', '{"items":[],"extra":true}'):
        with pytest.raises(CodexError):
            parameters.validate_output(raw, evidence())
    with pytest.raises(CodexError, match='duplicate'):
        parameters.validate_output(json.dumps({'items': [explanation(), explanation()]}), evidence())


@pytest.fixture
def engine(tmp_path):
    engine, _ = initialize(tmp_path)
    with Session(engine) as db:
        db.add_all([Project(id=1, name='One'), Project(id=2, name='Two')])
        db.flush()
        db.add_all([ProjectBrief(project_id=1, generated_at='general', sections={'overview': []}, sources=[], coverage=[]),
                    ProjectKnowledge(project_id=1, snapshot={'preserve': 'knowledge'}),
                    ProjectScheme(project_id=1, snapshot={'preserve': 'scheme'})])
        db.commit()
    try:
        yield engine
    finally:
        engine.dispose()


class Manager:
    generation_metadata = {'account': 'test', 'model': 'mock', 'effort': 'medium'}
    calls = 0
    raw = json.dumps({'items': [explanation()]})

    def generate_brief(self, prompt, schema):
        self.calls += 1
        usage = begin_model_request('parameters-thread')
        usage.bind_turn('parameters-turn')
        usage.record({'threadId': 'parameters-thread', 'turnId': 'parameters-turn',
                      'tokenUsage': {'total': {'totalTokens': 70}}})
        assert 'code/configuration' in prompt and 'textbook caveats' in prompt
        assert 'undocumented rationale must be empty' in prompt
        assert 'Do not execute or evaluate project code' in prompt
        assert schema['properties']['items']['items']['properties']['id']['enum'] == ['parameter:context', 'parameter:workers']
        return self.raw


def saved_state(engine):
    with Session(engine) as db:
        return (parameters.read_parameters(db, 1),
                db.get(ProjectBrief, 1).generated_at,
                db.get(ProjectKnowledge, 1).snapshot,
                db.get(ProjectScheme, 1).snapshot,
                list(db.scalars(select(GenerationRevision.kind))))


def test_one_generation_saves_all_declared_values_independently_and_failure_preserves_it(engine, monkeypatch):
    calls = []
    monkeypatch.setattr(parameters, 'collect_parameter_evidence', lambda eng, pid: (calls.append(pid), evidence())[1])
    manager = Manager()
    before = saved_state(engine)
    saved = parameters.refresh_parameters(engine, 1, manager)
    assert saved['project_id'] == 1
    assert saved['view_version'] == 2
    assert progress.read(1, 'parameters')['state'] == 'completed'
    assert progress.current_cancel_event() is None
    assert manager.calls == 1 and calls == [1, 1]
    assert saved['generation'] == manager.generation_metadata
    assert saved['diagnostics']['parameter_count'] == 2 and saved['diagnostics']['explained_parameters'] == 1
    assert saved['diagnostics']['model_requests'] == 1
    assert saved['diagnostics']['token_usage']['total_tokens'] == 70
    assert saved['coverage']['partial']
    state = saved_state(engine)
    assert state[0] == saved and state[1:4] == before[1:4]
    assert state[-1] == ['parameters']
    with Session(engine) as db:
        assert parameters.read_parameters(db, 2) is None
        with pytest.raises(HTTPException) as exc:
            parameters.read_parameters(db, 999)
        assert exc.value.status_code == 404
    manager.raw = '{broken'
    with pytest.raises(CodexError, match='invalid JSON'):
        parameters.refresh_parameters(engine, 1, manager)
    assert saved_state(engine) == state
    assert progress.read(1, 'parameters')['state'] == 'failed'


def test_changed_source_prevents_publication(engine, monkeypatch):
    calls = 0

    def inspect(*args):
        nonlocal calls
        calls += 1
        value = evidence()
        if calls == 2:
            value['fingerprint'] = 'changed'
        return value

    monkeypatch.setattr(parameters, 'collect_parameter_evidence', inspect)
    before = saved_state(engine)
    manager = Manager()
    with pytest.raises(CodexError, match='changed during generation'):
        parameters.refresh_parameters(engine, 1, manager)
    assert manager.calls == 1 and saved_state(engine) == before


def test_cancellation_before_commit_preserves_saved_artifacts(engine, monkeypatch):
    monkeypatch.setattr(parameters, 'collect_parameter_evidence', lambda *args: evidence())
    before = saved_state(engine)
    original = parameters.save_revision

    def cancel_before_commit(db, project_id, kind, snapshot):
        original(db, project_id, kind, snapshot)
        active = progress.read(project_id, kind)
        progress.cancel(project_id, kind, active['started_at'])

    monkeypatch.setattr(parameters, 'save_revision', cancel_before_commit)
    with pytest.raises(progress.GenerationCancelled):
        parameters.refresh_parameters(engine, 1, Manager())
    assert saved_state(engine) == before
    assert progress.read(1, 'parameters')['state'] == 'cancelled'


def test_missing_declarations_oversized_context_and_foreign_sources_make_no_ai_request(engine, monkeypatch):
    manager = Manager()
    value = evidence()
    monkeypatch.setattr(parameters, 'collect_parameter_evidence', lambda *args: value)
    value['parameters'] = []
    with pytest.raises(CodexError, match='No supported parameter'):
        parameters.refresh_parameters(engine, 1, manager)
    value = evidence()
    value['sources'][0]['text'] = 'x' * parameters.MAX_INPUT_CHARS
    with pytest.raises(CodexError, match='input limit'):
        parameters.refresh_parameters(engine, 1, manager)
    value = evidence()
    value['sources'][0]['project_id'] = 2
    with pytest.raises(CodexError, match='foreign evidence'):
        parameters.refresh_parameters(engine, 1, manager)
    assert manager.calls == 0


def test_shared_generation_lock_prevents_parallel_writers(engine):
    generation_lock.acquire()
    try:
        with pytest.raises(BriefBusy):
            parameters.refresh_parameters(engine, 1, Manager())
    finally:
        generation_lock.release()
