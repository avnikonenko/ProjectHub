"""Section-role fixtures and transport regressions, not mocked semantic proof.

The fictional fixture also supports a real model evaluation: see its manual
rubric. These tests check empty-section validity, original citation restoration
and unchanged Knowledge when a curated response separates the section roles.
"""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from backend.app.integrations.codex import knowledge_brief
from backend.app.integrations.codex.brief import OUTPUT_SCHEMA, SECTION_NAMES, validate_sections
from backend.app.integrations.codex.protocol import CodexError

FIXTURE = Path(__file__).parent / 'fixtures' / 'overview' / 'section_roles.json'


def section_role_inputs(include_activity=True):
    fixture = json.loads(FIXTURE.read_text())
    records = [record for record in fixture['records'] if include_activity or record['id'] != 'activity']
    originals = [dict(id='original:' + record['id'], label='Fictional discussion',
                      text=record['statement'], role=record['role'],
                      conversation_id='fictional:framescope', order=index, revision='fixture-v1')
                 for index, record in enumerate(records)]
    candidate = {'project_id': fixture['project']['id'], 'coverage': {'limitations': []},
                 'sources': originals, 'items': []}
    for record, original in zip(records, originals):
        candidate['items'].append({
            'id': record['id'], 'project_id': fixture['project']['id'],
            **{key: record[key] for key in ('statement', 'category', 'design_status', 'agreement_basis', 'scope')},
            'source_state': 'valid', 'superseded_by': [],
            'evidence': [{'source_id': original['id'], 'role': record['role'],
                          'excerpt': original['text'], 'conversation_id': original['conversation_id'],
                          'order': original['order'], 'revision': original['revision']}],
        })
    return candidate, {'project': fixture['project']}, fixture['repository'], fixture['expectations']


def point(text, *source_ids):
    return {'text': text, 'classification': 'reported', 'source_ids': list(source_ids)}


def test_stable_requirements_can_produce_a_brief_without_invented_current_work():
    candidate, bundle, repository, _ = section_role_inputs(include_activity=False)
    sections = {name: [] for name in SECTION_NAMES}
    sections['overview'] = [point(bundle['project']['description'], 'project:metadata')]
    sections['architecture'] = [point(candidate['items'][0]['statement'], 'original:architecture')]
    raw = json.dumps({'sections': sections, 'project_map': {'nodes': [], 'edges': []}})
    parsed, _ = validate_sections(raw, [*candidate['sources'], {'id': 'project:metadata'}])
    assert parsed['current_status'] == []
    fields = OUTPUT_SCHEMA['properties']['sections']
    assert 'current_status' in fields['required']
    assert fields['properties']['current_status'].get('minItems', 0) == 0
    assert fields['properties']['overview']['minItems'] == fields['properties']['architecture']['minItems'] == 1
    for missing in ('overview', 'architecture'):
        invalid = deepcopy(sections)
        invalid[missing] = []
        with pytest.raises(CodexError, match='overview or reported architecture'):
            validate_sections(json.dumps({'sections': invalid, 'project_map': {'nodes': [], 'edges': []}}),
                              [*candidate['sources'], {'id': 'project:metadata'}])


@pytest.mark.parametrize('include_activity', [True, False])
def test_curated_section_roles_restore_original_citations_without_changing_knowledge(include_activity):
    candidate, bundle, repository, expected = section_role_inputs(include_activity)
    before = deepcopy(candidate)
    class CuratedResponse:
        def generate_brief(self, prompt, schema):
            payload = json.loads(prompt.rsplit('\n', 1)[1])
            aliases = {source['item']['statement']: source['id']
                       for source in payload['sources'] if 'item' in source}
            metadata = next(source['id'] for source in payload['sources']
                            if source.get('label') == 'User-maintained project metadata')
            code = next(source['id'] for source in payload['sources']
                        if source.get('kind') == 'inspected_repository')
            sections = {name: [] for name in SECTION_NAMES}
            sections['overview'] = [point(bundle['project']['description'], metadata)]
            architecture = candidate['items'][0]
            sections['architecture'] = [point(architecture['statement'], aliases[architecture['statement']], code)]
            activity = next((record for record in candidate['items'] if record['id'] == 'activity'), None)
            if activity:
                sections['current_status'] = [point(activity['statement'], aliases[activity['statement']])]
            return json.dumps({'sections': sections, 'project_map': {'nodes': [], 'edges': []}})
    sections, _, cited = knowledge_brief._synthesize(CuratedResponse(), candidate, bundle, repository)
    assert set(sections['architecture'][0]['source_ids']) == set(expected['architecture_source_ids'])
    assert [ref for entry in sections['current_status'] for ref in entry['source_ids']] == (
        expected['current_activity_source_ids'] if include_activity else [])
    assert not set(expected['stable_requirement_source_ids']) & {
        ref for entry in sections['current_status'] for ref in entry['source_ids']}
    assert candidate == before
    assert all(not source['id'].startswith('k') for source in cited)


def test_overview_presentation_changes_reuse_valid_knowledge_extraction(tmp_path, monkeypatch):
    from sqlalchemy.orm import Session
    from backend.app.db import initialize
    from backend.app.models import Project
    from backend.app.integrations.codex import knowledge

    candidate, _, _, _ = section_role_inputs()
    source = candidate['sources'][0]
    window = {'sources': [source], 'primary_ids': [source['id']], 'context_ids': []}
    raw_item = {'statement': source['text'], 'category': 'FINDING', 'design_status': 'UNKNOWN',
                'agreement_basis': 'none', 'scope': 'System architecture', 'rationale': '',
                'alternatives': '', 'limitations': '', 'revises_statement': '',
                'conflicting_statements': [], 'evidence': [{'source_id': source['id'],
                    'role': source['role'], 'excerpt': source['text']}]}
    class Extractor:
        calls = 0
        def generate_extraction(self, prompt, schema):
            self.calls += 1
            assert self.calls == 1, 'Changing Overview rules must not regenerate valid Knowledge.'
            return json.dumps({'items': [raw_item]})
    manager = Extractor()
    engine, _ = initialize(tmp_path / 'data')
    try:
        with Session(engine) as db:
            db.add(Project(id=1, name='Fictional')); db.commit()
        config = knowledge.extraction_configuration(manager)
        first, cached = knowledge.extract_batch(engine, 1, manager, window, config)
        assert not cached
        monkeypatch.setattr(knowledge_brief, 'SECTION_ROLE_GUIDANCE', 'Changed Overview presentation policy.')
        fields = OUTPUT_SCHEMA['properties']['sections']['properties']
        monkeypatch.setitem(fields['current_status'], 'minItems', 0)
        replay, cached = knowledge.extract_batch(engine, 1, manager, window,
                                                 knowledge.extraction_configuration(manager))
        assert cached and replay == first and manager.calls == 1
    finally:
        engine.dispose()
