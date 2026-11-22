"""Structured Scheme graphs and persistence; Codex calls are mocked here."""
from copy import deepcopy
import json

import pytest
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db import initialize
from backend.app.integrations.codex import scheme
from backend.app.integrations.codex.generation_metrics import begin_model_request
from backend.app.integrations.codex.protocol import CodexError
from backend.app.models import GenerationRevision, Project, ProjectKnowledge, ProjectScheme


def evidence():
    return {
        'sources': [
            {'id': 'file:api.py', 'path': 'api.py', 'label': 'API source', 'text': 'def serve(): pass',
             'provenance': 'observed in code', 'content_hash': 'abc'},
            {'id': 'discussion:1', 'label': 'Future design', 'text': 'Replace backend A with B',
             'provenance': 'mentioned in Codex conversation', 'content_hash': 'def'},
        ],
        'dependencies': [{'id': 'dep:fastapi', 'name': 'fastapi', 'constraint': '>=1',
                          'category': 'runtime', 'source_ids': ['file:api.py'], 'purpose': ''}],
        'tree': [{'path': 'api.py', 'type': 'file'}],
        'coverage': {'partial': True, 'limitations': ['Bounded code excerpts'], 'files_inspected': 1,
                     'conversation_excerpts': 1},
        'fingerprint': 'baseline', 'diagnostics': {'cache_hits': 1},
    }


def node(identifier='api', status='observed', refs=None):
    return {'id': identifier, 'label': 'API', 'description': 'Application interface', 'type': 'service',
            'status': status, 'source_ids': refs or ['file:api.py']}


def output():
    return {
        'architecture': {
            'nodes': [node(), node('replacement', 'proposed', ['discussion:1'])],
            'edges': [{'source': 'api', 'target': 'replacement', 'label': 'planned replacement',
                       'type': 'replaces', 'status': 'uncertain',
                       'source_ids': ['file:api.py', 'discussion:1']}],
            'explanation': 'Code shows the API; discussion proposes a replacement. The relation is uncertain.',
        },
        'workflow': {'nodes': [], 'edges': [], 'explanation': 'Execution order is not established by this evidence.'},
        'dependencies': [{'id': 'dep:fastapi', 'purpose': 'Serves the application API.',
                          'source_ids': ['file:api.py']}],
    }


def test_graph_validates_conflicting_evidence_and_empty_workflow():
    architecture, workflow, dependencies = scheme.validate_output(json.dumps(output()), evidence())
    assert architecture['nodes'][1]['status'] == 'proposed'
    assert architecture['edges'][0]['status'] == 'uncertain'
    assert architecture['edges'][0]['source_ids'] == ['file:api.py', 'discussion:1']
    assert workflow['mermaid'] == ''
    assert dependencies[0]['name'] == 'fastapi'
    assert dependencies[0]['constraint'] == '>=1'
    assert dependencies[0]['purpose_source_ids'] == ['file:api.py']


@pytest.mark.parametrize('change,match', [
    (lambda result: result['architecture']['nodes'].append(deepcopy(result['architecture']['nodes'][0])), 'duplicate node'),
    (lambda result: result['architecture']['nodes'][0].update(id=' api '), 'node ID'),
    (lambda result: result['architecture']['edges'][0].update(target='missing'), 'missing endpoint'),
    (lambda result: result['architecture']['nodes'][0].update(source_ids=['project:foreign']), 'foreign evidence'),
    (lambda result: result['architecture']['nodes'][0].update(paths=['../../outside']), 'invalid structure'),
    (lambda result: result['architecture']['nodes'][0].update(status='validated'), 'classification'),
    (lambda result: result['architecture']['nodes'][0].update(type='output'), 'classification'),
    (lambda result: result['workflow'].update(explanation=''), 'invalid text'),
    (lambda result: result['dependencies'][0].update(id='invented'), 'invented'),
    (lambda result: result['architecture']['edges'].append(deepcopy(result['architecture']['edges'][0])), 'duplicate relationships'),
    (lambda result: result['architecture'].update(mermaid='flowchart LR; dangerous'), 'invalid structure'),
])
def test_invalid_graph_is_rejected(change, match):
    result = output()
    change(result)
    with pytest.raises(CodexError, match=match):
        scheme.validate_output(json.dumps(result), evidence())


def test_deterministic_mermaid_escapes_labels_and_uses_fixed_syntax():
    result = output()
    result['architecture']['nodes'][0]['label'] = '"]\nclick x "javascript:alert(1)"<script>%%{init: {}}'
    graph = scheme.validate_output(json.dumps(result), evidence())[0]
    mermaid = graph['mermaid']
    assert 'javascript:' not in mermaid
    assert '<script>' not in mermaid
    assert '%%{' not in mermaid
    assert '" ]' not in mermaid
    assert '#34;' in mermaid and '#10;' in mermaid
    assert '(proposed)' not in mermaid  # Parentheses are escaped too.
    assert 'proposed' in mermaid and 'uncertain' in mermaid and '-.->' in mermaid
    graph['nodes'].reverse()
    assert scheme.render_mermaid(graph) == mermaid


def test_workflow_decisions_render_as_diamonds():
    result = output()
    stage = {**node('choose'), 'type': 'decision', 'label': 'Which backend?'}
    result['workflow']['nodes'] = [stage]
    graph = scheme.validate_output(json.dumps(result), evidence())[1]
    assert '{"Which backend#63; #40;observed#41;"}' in graph['mermaid']


@pytest.mark.parametrize('focus, guidance', [('balanced', 'Use code for implemented relationships'),
    ('code', 'Prioritize inspected entry points'), ('sessions', 'Prioritize decisions, rationale')])
def test_generation_calls_once_and_preserves_saved_snapshot_on_failure(tmp_path, monkeypatch, focus, guidance):
    engine, _ = initialize(tmp_path)
    with Session(engine) as db:
        db.add_all([Project(name='One'), Project(name='Two')])
        db.commit()
    gathered = []

    def collect(engine, project_id, _limits):
        gathered.append(project_id)
        inspected = evidence()
        inspected['dependency_inventory'] = {
            'dependencies': [*inspected['dependencies'], {'id': 'dep:local-only', 'name': 'local-only',
                'constraint': '', 'category': 'import:python', 'source_ids': ['dependency:local-only'], 'purpose': ''}],
            'sources': [{'id': 'dependency:local-only', 'label': 'local.py', 'text': 'import local_only',
                         'provenance': 'Python import'}],
            'coverage': {'files_total': 2, 'files_scanned': 2, 'limitations': []},
        }
        return inspected

    monkeypatch.setattr(scheme, 'collect_evidence', collect)

    class Manager:
        calls = 0
        raw = json.dumps(output())
        generation_metadata = {'account': 'test', 'model': 'mock', 'effort': 'low'}

        def generate_brief(self, prompt, schema):
            self.calls += 1
            usage = begin_model_request('scheme-thread')
            usage.bind_turn('scheme-turn')
            usage.record({'threadId': 'scheme-thread', 'turnId': 'scheme-turn',
                          'tokenUsage': {'total': {'totalTokens': 100}}})
            if self.calls == 1:
                assert prompt.startswith(guidance)
            assert 'primary end-to-end' in prompt
            assert 'Do not substitute the workflow for generating this Scheme' in prompt
            # Prompt contract only: semantic diagram quality is evaluated with real generation.
            assert 'project-capability level' in prompt
            assert 'domain-language labels supported by the evidence' in prompt
            assert 'represent their purpose rather than giving each command a node' in prompt
            assert 'Preserve documented domain purposes of analysis and visualization' in prompt
            assert 'archived, backup, example, or experimental files' in prompt
            assert 'static' in prompt and 'runtime correctness' in prompt
            assert 'Preserve conflicts' in prompt
            assert 'Do not use tools' in prompt
            assert 'local-only' not in prompt
            assert schema['properties']['architecture']['properties']['nodes']['maxItems'] == 20
            assert schema['properties']['architecture']['properties']['nodes']['items']['properties']['source_ids']['items']['enum'] == ['file:api.py', 'discussion:1']
            return self.raw

    manager = Manager()
    saved = scheme.refresh_scheme(engine, 1, manager, focus=focus)
    assert saved['focus'] == focus
    assert gathered == [1] and manager.calls == 1
    assert saved['diagnostics']['ai_requests'] == 1
    assert saved['diagnostics']['model_requests'] == 1
    assert saved['diagnostics']['token_usage']['total_tokens'] == 100
    assert saved['sources'] == evidence()['sources']
    assert saved['coverage']['partial']
    assert saved['generation'] == manager.generation_metadata
    saved_inventory = saved['dependency_inventory']['dependencies']
    assert [item['name'] for item in saved_inventory] == ['fastapi', 'local-only']
    assert saved_inventory[0]['purpose'] == 'Serves the application API.'
    with Session(engine) as db:
        assert scheme.read_scheme(db, 1) == saved
        assert scheme.read_scheme(db, 2) is None
    manager.raw = '{broken'
    with pytest.raises(CodexError, match='invalid JSON'):
        scheme.refresh_scheme(engine, 1, manager)
    invalid = output()
    invalid['architecture']['explanation'] = 'Invalid Unicode \ud800'
    manager.raw = json.dumps(invalid)
    failed_focus = 'code' if focus != 'code' else 'sessions'
    with pytest.raises(CodexError, match='invalid text'):
        scheme.refresh_scheme(engine, 1, manager, focus=failed_focus)
    assert manager.calls == 3  # No automatic repair/retry.
    with Session(engine) as db:
        assert scheme.read_scheme(db, 1) == saved
        assert scheme.progress.read(1, 'scheme', engine)['latest_attempt']['settings']['scheme_focus'] == failed_focus
        revisions = list(db.scalars(select(GenerationRevision)))
        assert len(revisions) == 1 and revisions[0].kind == 'scheme'
        assert revisions[0].snapshot == saved
    engine.dispose()


def test_economy_scheme_uses_overviews_as_guides_to_original_citations(tmp_path, monkeypatch):
    from backend.app.generation_profiles import get_generation_profile
    from backend.app.integrations.codex import archive
    from backend.app.integrations.codex.knowledge_profiles import use_knowledge_profile

    engine, _ = initialize(tmp_path)
    with Session(engine) as db:
        db.add(Project(name='One'))
        db.commit()
    inspected = evidence()
    inspected['sources'][1]['provenance'] = 'Codex conversation'
    monkeypatch.setattr(scheme, 'collect_evidence', lambda *_: inspected)
    original = {'id': 'codex:original', 'label': 'Old discussion', 'text': 'Try backend B.', 'session_id': 7}
    monkeypatch.setattr(archive, 'conversation_overviews', lambda *_: (
        [{'text': 'The user proposed backend B.', 'sources': [original]}], ['One cached overview.']))

    class Manager:
        def generate_brief(self, prompt, schema):
            payload = json.loads(prompt.split('\n', 1)[1])
            assert payload['conversation_overviews'] == [
                {'text': 'The user proposed backend B.', 'source_ids': ['codex:original']}]
            assert [source['id'] for source in payload['sources']] == ['file:api.py', 'codex:original']
            assert payload['sources'][-1]['text'] == original['text']
            assert payload['dependencies'] == inspected['dependencies']
            return json.dumps(output()).replace('discussion:1', 'codex:original')

    with use_knowledge_profile(get_generation_profile('economy')):
        saved = scheme.refresh_scheme(engine, 1, Manager())
    assert saved['coverage']['overview_freshness_unknown'] is True
    assert saved['fingerprint'] == inspected['fingerprint']
    assert saved['architecture']['nodes'][1]['source_ids'] == ['codex:original']
    engine.dispose()


def test_economy_scheme_preserves_local_archive_warning_at_package_limit(tmp_path, monkeypatch):
    from backend.app.generation_profiles import get_generation_profile
    from backend.app.integrations.codex import archive
    from backend.app.integrations.codex.knowledge_profiles import use_knowledge_profile

    engine, _ = initialize(tmp_path)
    with Session(engine) as db:
        db.add(Project(name='One'))
        db.commit()
    inspected = evidence()
    limit = scheme.DEFAULT_SCHEME_PACKAGE_CHARS
    inspected['coverage'].update(partial=False, inspection_policy={'max_package_chars': limit})
    package = {key: inspected[key] for key in ('sources', 'dependencies', 'tree', 'coverage')}
    inspected['sources'][0]['text'] += 'x' * (limit - len(json.dumps(package, ensure_ascii=False)))
    assert len(json.dumps(package, ensure_ascii=False)) == limit
    monkeypatch.setattr(scheme, 'collect_evidence', lambda *_: inspected)
    warning = 'Conversation summary coverage is partial: 1 batch omitted after citation validation failed.'
    original = {'id': 'codex:original', 'label': 'Old discussion', 'text': 'Try backend B.', 'session_id': 7}
    monkeypatch.setattr(archive, 'conversation_overviews', lambda *_: (
        [{'text': 'The user proposed backend B.', 'sources': [original]}], ['Batch counts.', warning]))

    class Manager:
        def generate_brief(self, prompt, schema):
            serialized = prompt.split('\n', 1)[1]
            assert len(serialized) == limit
            assert json.loads(serialized) == package
            return json.dumps(output())

    with use_knowledge_profile(get_generation_profile('economy')):
        saved = scheme.refresh_scheme(engine, 1, Manager())
    assert saved['coverage']['partial'] is True
    assert saved['coverage']['conversation_overview_points'] == 0
    assert warning in saved['coverage']['limitations']
    assert warning not in inspected['coverage']['limitations']
    with Session(engine) as db:
        assert scheme.read_scheme(db, 1)['coverage'] == saved['coverage']
    engine.dispose()


def test_bounds_and_missing_evidence_do_not_call_codex(tmp_path, monkeypatch):
    engine, _ = initialize(tmp_path)
    with Session(engine) as db:
        db.add(Project(name='One'))
        db.commit()
    class Manager:
        def generate_brief(self, *args):
            pytest.fail('No Codex call should happen.')
    item = evidence()
    monkeypatch.setattr(scheme, 'collect_evidence', lambda *args: item)
    item['sources'] = []
    with pytest.raises(CodexError, match='No project evidence'):
        scheme.refresh_scheme(engine, 1, Manager())
    item['sources'] = evidence()['sources']
    item['sources'][0]['text'] = 'x' * scheme.MAX_INPUT_CHARS
    with pytest.raises(CodexError, match='input limit'):
        scheme.refresh_scheme(engine, 1, Manager())
    # Serialized evidence above the selected cap must fail even when the
    # maximum prompt size still has room for it.
    item['coverage']['inspection_policy'] = {'max_package_chars': 180_000}
    item['sources'][0]['text'] = 'x' * 180_001
    with pytest.raises(CodexError, match='input limit'):
        scheme.refresh_scheme(engine, 1, Manager())
    engine.dispose()


def knowledge_evidence():
    result = evidence()
    originals = ['knowledge:original:proposal', 'knowledge:original:acceptance']
    result['sources'].extend([
        {'id': originals[0], 'label': 'Original proposal', 'text': 'Use the replacement backend.',
         'provenance': 'published Knowledge original evidence', 'content_hash': 'proposal', 'role': 'assistant'},
        {'id': originals[1], 'label': 'Original acceptance', 'text': 'Yes, adopt that backend.',
         'provenance': 'published Knowledge original evidence', 'content_hash': 'acceptance', 'role': 'user'},
        {'id': 'knowledge:item:choice', 'label': 'Accepted design choice',
         'text': 'AGREED: Use the replacement backend. Implementation is not established.',
         'provenance': 'published Knowledge interpretation', 'content_hash': 'choice',
         'supporting_source_ids': originals},
    ])
    result['knowledge_context'] = {'publication_id': 'published-one', 'included_items': 1,
                                   'available_items': 1, 'omitted_items': 0}
    return result


@pytest.mark.parametrize('collection', ['nodes', 'edges', 'dependencies'])
def test_knowledge_citations_require_original_evidence_in_each_claim(collection):
    result = output()
    target = (result['dependencies'] if collection == 'dependencies'
              else result['architecture'][collection])[0]
    target['source_ids'] = ['knowledge:item:choice']
    with pytest.raises(CodexError, match='original evidence alongside Knowledge'):
        scheme.validate_output(json.dumps(result), knowledge_evidence())
    target['source_ids'].extend(['knowledge:original:proposal', 'knowledge:original:acceptance'])
    architecture, _, dependencies = scheme.validate_output(json.dumps(result), knowledge_evidence())
    refs = (dependencies[0]['purpose_source_ids'] if collection == 'dependencies'
            else architecture[collection][0]['source_ids'])
    assert refs == target['source_ids']


@pytest.mark.parametrize('supporting', [None, [], ['missing'], ['knowledge:item:choice'],
                                      ['knowledge:original:proposal'] * 2])
def test_knowledge_interpretations_without_valid_original_support_are_rejected(supporting):
    context = knowledge_evidence()
    context['sources'][-1]['supporting_source_ids'] = supporting
    result = output()
    result['architecture']['nodes'][1]['source_ids'] = ['knowledge:item:choice', 'knowledge:original:proposal']
    with pytest.raises(CodexError, match='Knowledge without valid original evidence'):
        scheme.validate_output(json.dumps(result), context)


def test_knowledge_support_respects_six_reference_limit():
    context = knowledge_evidence()
    derived = context['sources'][-1]
    supporting = [f'original:{index}' for index in range(5)]
    context['sources'].extend({'id': identifier, 'provenance': 'published Knowledge original evidence'}
                              for identifier in supporting)
    derived['supporting_source_ids'] = supporting
    result = output()
    target = result['architecture']['nodes'][1]
    target['source_ids'] = ['knowledge:item:choice', *supporting]
    assert len(scheme.validate_output(json.dumps(result), context)[0]['nodes'][1]['source_ids']) == 6
    target['source_ids'].append('file:api.py')
    with pytest.raises(CodexError, match='1–6'):
        scheme.validate_output(json.dumps(result), context)


def test_knowledge_publication_is_in_prompt_and_saved_with_scheme_atomically(tmp_path, monkeypatch):
    engine, _ = initialize(tmp_path)
    with Session(engine) as db:
        db.add(Project(name='One'))
        db.commit()
    context = knowledge_evidence()
    omissions = {'total': 1, 'files': [{'path': 'local-only.py', 'reason': 'source_output_budget'}]}
    context['coverage']['file_omissions'] = omissions
    monkeypatch.setattr(scheme, 'collect_evidence', lambda *args: context)

    class Manager:
        calls = 0
        raw = json.dumps(output())
        generation_metadata = {'model': 'mock'}

        def generate_brief(self, prompt, schema):
            self.calls += 1
            assert json.dumps(context['knowledge_context']['publication_id']) in prompt
            assert 'local-only.py' not in prompt
            assert 'also include ALL its supporting_source_ids' in prompt
            assert 'AGREED does not establish implementation' in prompt
            assert 'When evidence_selection_partial is true' in prompt
            assert 'do not assume omitted references establish a claim' in prompt
            assert 'historical evidence, not currently verified source material' in prompt
            assert 'Respect source_state and evidence-availability limitations' in prompt
            assert 'Rejected or superseded knowledge is historical context' in prompt
            assert 'unpublished previews are not included' in prompt
            return self.raw

    manager = Manager()
    saved = scheme.refresh_scheme(engine, 1, manager)
    assert saved['knowledge_context'] == context['knowledge_context']
    assert saved['coverage']['file_omissions'] == omissions
    assert manager.calls == 1
    context['knowledge_context']['publication_id'] = 'published-two'
    manager.raw = '{broken'
    with pytest.raises(CodexError, match='invalid JSON'):
        scheme.refresh_scheme(engine, 1, manager)
    with Session(engine) as db:
        assert scheme.read_scheme(db, 1)['knowledge_context']['publication_id'] == 'published-one'
        revisions = list(db.scalars(select(GenerationRevision)))
        assert len(revisions) == 1
        assert revisions[0].snapshot['knowledge_context']['publication_id'] == 'published-one'
    engine.dispose()


def test_knowledge_followup_checks_publication_at_start_and_commit(tmp_path, monkeypatch):
    engine, _ = initialize(tmp_path)
    old_scheme = {'generated_at': 'earlier'}
    with Session(engine) as db:
        db.add(Project(id=1, name='One'))
        db.flush()
        db.add(ProjectKnowledge(project_id=1, snapshot={'publication_id': 'published-one'}))
        db.add(ProjectScheme(project_id=1, snapshot=old_scheme))
        db.commit()
    context = knowledge_evidence()
    monkeypatch.setattr(scheme, 'collect_evidence', lambda *args: context)

    class Manager:
        calls = 0
        generation_metadata = {'model': 'mock'}

        def generate_brief(self, prompt, schema):
            self.calls += 1
            if self.calls == 1:
                with Session(engine) as db:
                    db.get(ProjectKnowledge, 1).snapshot = {'publication_id': 'published-two'}
                    db.commit()
            return json.dumps(output())

    manager = Manager()
    with pytest.raises(CodexError, match='Knowledge changed'):
        scheme.refresh_scheme(engine, 1, manager, expected_publication_id='other-publication')
    assert manager.calls == 0
    with pytest.raises(CodexError, match='Knowledge changed'):
        scheme.refresh_scheme(engine, 1, manager, expected_publication_id='published-one')
    with Session(engine) as db:
        assert scheme.read_scheme(db, 1) == old_scheme
        assert db.scalar(select(GenerationRevision)) is None
    context['knowledge_context']['publication_id'] = 'published-two'
    saved = scheme.refresh_scheme(engine, 1, manager, expected_publication_id='published-two')
    assert saved['knowledge_context']['publication_id'] == 'published-two'
    engine.dispose()


def test_legacy_scheme_unicode_is_readable_without_altering_persisted_result(tmp_path):
    engine, _ = initialize(tmp_path)
    legacy = {'architecture': {'explanation': 'Displayable text with \ud800'},
              'coverage': {'file_omissions': {'files': [{'path': 'path-with-\udcff.py'}, {'path': 'valid.py'}]}}}
    with Session(engine) as db:
        db.add(Project(id=1, name='One'))
        db.flush()
        db.add(ProjectScheme(project_id=1, snapshot=legacy))
        db.commit()
        shown = scheme.read_scheme(db, 1)
        assert shown['architecture']['explanation'].endswith('\ufffd')
        assert shown['coverage']['file_omissions']['files'] == [{'path': 'valid.py'}]
        assert json.loads(JSONResponse(shown).body) == shown
        assert db.get(ProjectScheme, 1).snapshot == legacy
    engine.dispose()


@pytest.mark.parametrize('repair_works', [True, False])
def test_index_handoff_gets_one_targeted_retry(tmp_path, monkeypatch, repair_works):
    engine, _ = initialize(tmp_path)
    with Session(engine) as db:
        db.add(Project(name='Search'))
        db.commit()
    inspected = evidence()
    inspected['sources'] += [
        {'id': 'migration', 'path': 'migrations/index.py', 'provenance': 'observed in code',
         'label': 'Index migration',
         'text': 'L1: CREATE VIRTUAL TABLE doc_fts USING fts5\n'
                 'L2: CREATE TRIGGER index_doc AFTER INSERT ON docs: INSERT INTO doc_fts'},
        {'id': 'query', 'path': 'query.py', 'provenance': 'observed in code',
         'label': 'Runtime query', 'text': 'L8: SELECT FROM doc_fts WHERE doc_fts MATCH'},
    ]
    monkeypatch.setattr(scheme, 'collect_evidence', lambda *_: deepcopy(inspected))
    prompts = []

    class Manager:
        def generate_brief(self, prompt, schema):
            prompts.append(prompt)
            graph = output()
            if len(prompts) > 1 and repair_works:
                graph['workflow']['nodes'] = [
                    {**node('find', refs=['migration', 'query']), 'type': 'operation'},
                ]
            return json.dumps(graph)

    saved = scheme.refresh_scheme(engine, 1, Manager())
    assert len(prompts) == saved['diagnostics']['ai_requests'] == 2
    assert '["migration", "query"]' in prompts[-1]
    warning = any('did not fully cite' in value for value in saved['coverage']['limitations'])
    assert warning is not repair_works
    engine.dispose()
