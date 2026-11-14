"""Structural transport output still passes full local bounds before cache writes."""
from copy import deepcopy
import json

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db import initialize
from backend.app.models import Project, ProjectHistoryBatch
from backend.app.integrations.codex import knowledge
from backend.app.integrations.codex.protocol import CodexError


@pytest.fixture
def database(tmp_path):
    engine, _ = initialize(tmp_path)
    with Session(engine) as db:
        db.add(Project(name='Bounds')); db.commit()
    yield engine
    engine.dispose()


def data():
    source = {'id': 'source', 'role': 'assistant', 'text': 'Consider bounded operations.',
              'revision': 'original', 'conversation_id': 'one', 'source_key': 'one', 'order': 1}
    value = {'statement': 'Consider bounded operations.', 'category': 'PROPOSAL',
             'design_status': 'PROPOSED', 'scope': 'project', 'rationale': '', 'alternatives': '',
             'limitations': '', 'agreement_basis': 'none', 'revises_statement': '',
             'conflicting_statements': [], 'evidence': [{'source_id': source['id'],
                  'role': 'assistant', 'excerpt': source['text']}]}
    window = {'sources': [source], 'primary_ids': [source['id']], 'context_ids': []}
    return window, {'items': [value]}


class Manager:
    extraction_generation_mode = 'structural-schema'
    def __init__(self, raw):
        self.raw = json.dumps(raw)
        self.calls = 0
    def generate_extraction(self, prompt, schema):
        self.calls += 1
        # The helper receives the complete original contract, including numeric bounds.
        assert schema['properties']['items']['maxItems'] == knowledge.MAX_ITEMS
        fields = schema['properties']['items']['items']['properties']
        assert fields['statement']['maxLength'] == 600
        assert fields['evidence']['maxItems'] == 6
        assert fields['evidence']['items']['properties']['source_id']['maxLength'] == 240
        return self.raw
    def generate_brief(self, *_):
        pytest.fail('Extraction bypassed its dedicated transport helper')


def modify(case, window, raw):
    value = raw['items'][0]
    if case == 'items': raw['items'] = [deepcopy(value) for _ in range(knowledge.MAX_ITEMS + 1)]
    elif case == 'references': value['evidence'] *= 7
    elif case == 'statement': value['statement'] = 'x' * 601
    elif case == 'empty_statement': value['statement'] = ''
    elif case == 'scope': value['scope'] = 'x' * 181
    elif case == 'rationale': value['rationale'] = 'x' * 801
    elif case == 'excerpt':
        value['evidence'][0]['excerpt'] = window['sources'][0]['text'] = 'x' * 1601
    elif case == 'source_id':
        identifier = 'x' * 241
        window['sources'][0]['id'] = identifier
        window['primary_ids'] = [identifier]
        value['evidence'][0]['source_id'] = identifier
    else: raise AssertionError(case)


@pytest.mark.parametrize('case', ['items', 'references', 'statement', 'empty_statement',
                                 'scope', 'rationale', 'excerpt', 'source_id'])
def test_local_bounds_reject_structural_helper_output_without_caching(database, case):
    window, raw = data(); modify(case, window, raw)
    manager = Manager(raw)
    with pytest.raises(CodexError):
        knowledge.extract_batch(database, 1, manager, window, knowledge.extraction_configuration(manager))
    # Only quotation failures permit one source-ID encoding retry. The helper
    # deliberately returns the same invalid literal again; it must not be cached.
    assert manager.calls == (2 if case == 'excerpt' else 1)
    with Session(database) as db:
        assert db.scalars(select(ProjectHistoryBatch)).all() == []


def test_successful_structural_response_records_mode_and_keeps_original_cache_identity(database):
    window, raw = data(); manager = Manager(raw)
    config = knowledge.extraction_configuration(manager)
    values, cached = knowledge.extract_batch(database, 1, manager, window, config)
    assert not cached and len(values) == 1
    with Session(database) as db:
        entry = db.scalars(select(ProjectHistoryBatch)).one()
        assert entry.evidence[0]['generation_mode'] == 'structural-schema'
        # Legacy envelopes have no mode metadata; they remain independently valid.
        entry.evidence = [{'raw': entry.evidence[0]['raw']}]
        db.commit()
    manager.raw = 'This must never be read on cache replay.'
    replay, cached = knowledge.extract_batch(database, 1, manager, window, config)
    assert cached and replay == values and manager.calls == 1
