"""Overlap-only candidates cannot block or bypass validated primary extraction."""
from copy import deepcopy
import json

import pytest
from sqlalchemy.orm import Session

from backend.app.db import initialize
from backend.app.integrations.codex import knowledge
from backend.app.integrations.codex.protocol import CodexError
from backend.app.models import Project


@pytest.fixture
def batch():
    sources = [{'id': name, 'role': 'assistant', 'text': text,
                'revision': name, 'conversation_id': 'discussion', 'order': order}
               for order, (name, text) in enumerate([
                   ('old', 'Use a local database.'), ('new', 'Add a backup command.')])]
    window = {'sources': sources, 'primary_ids': ['new'], 'context_ids': ['old']}
    def item(index):
        source = sources[index]
        return {'statement': source['text'], 'category': 'PROPOSAL',
                'design_status': 'PROPOSED', 'scope': 'Storage', 'rationale': '',
                'alternatives': '', 'limitations': '', 'agreement_basis': 'none',
                'revises_statement': '', 'conflicting_statements': [],
                'evidence': [{'source_id': source['id'], 'role': source['role'],
                              'excerpt': source['text']}]}
    return window, item(0), item(1)


def validate(window, *items):
    return knowledge.validate_extraction(json.dumps({'items': items}), window, 1, 'version')


def test_overlap_only_does_not_discard_primary_items(batch):
    window, old, new = batch
    result = validate(window, old, new)
    assert [item['statement'] for item in result] == [new['statement']]
    assert result[0]['evidence'][0]['source_id'] == 'new'
    assert validate(window, old) == []
    assert validate(window, {**old, 'evidence': old['evidence'] + new['evidence']})


@pytest.mark.parametrize('failure', ['foreign', 'role', 'quote', 'agreement'])
def test_context_only_items_still_require_valid_evidence(batch, failure):
    window, old, new = batch
    old = deepcopy(old)
    if failure == 'foreign':
        old['evidence'][0]['source_id'] = 'another-project'
    elif failure == 'role':
        old['evidence'][0]['role'] = 'user'
    elif failure == 'quote':
        old['evidence'][0]['excerpt'] = 'Invented quotation.'
    else:
        old.update(design_status='AGREED', agreement_basis='direct_user')
    with pytest.raises(CodexError):
        validate(window, old, new)


def test_overlap_filter_is_identical_for_generated_and_cached_batches(tmp_path, batch):
    engine, _ = initialize(tmp_path)
    try:
        with Session(engine) as db:
            project = Project(name='Overlap test')
            db.add(project)
            db.commit()
            project_id = project.id
        window, old, new = batch
        class Manager:
            calls = 0
            def generate_extraction(self, prompt, schema):
                self.calls += 1
                return json.dumps({'items': [old, new]})
        manager = Manager()
        configuration = knowledge.extraction_configuration(manager)
        fresh, cached = knowledge.extract_batch(engine, project_id, manager, window, configuration)
        assert not cached
        replay, cached = knowledge.extract_batch(engine, project_id, manager, window, configuration)
        assert cached and replay == fresh
        assert [item['statement'] for item in fresh] == [new['statement']]
        assert manager.calls == 1
    finally:
        engine.dispose()
