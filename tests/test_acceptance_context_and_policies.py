"""Reference-backed contract tests with mocked extraction, not model accuracy."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from backend.app.db import initialize
from backend.app.models import Project
from backend.app.integrations.codex import knowledge
from backend.app.integrations.codex.knowledge_sources import build_windows


REFERENCE = json.loads((Path(__file__).parent / 'fixtures/knowledge/acceptance_context_and_policies.json').read_text())


def original_sources(case):
    return [{**source, 'conversation_id': case['id'], 'source_key': case['id'],
             'revision': hashlib.sha256(source['text'].encode()).hexdigest(), 'order': index}
            for index, source in enumerate(case['sources'])]


def extracted_item(case):
    expected = case['expected']
    source = next(source for source in case['sources'] if source['id'] == expected['source_id'])
    return {**{key: expected[key] for key in ('statement', 'category', 'design_status', 'scope', 'agreement_basis')},
            'rationale': '', 'alternatives': '', 'limitations': '', 'revises_statement': '',
            'conflicting_statements': [], 'evidence': [
                {'source_id': source['id'], 'role': source['role'], 'excerpt': expected['excerpt']}]}


@pytest.mark.parametrize('case', REFERENCE['cases'], ids=lambda case: case['id'])
def test_mocked_classification_and_exact_evidence_survive_cache_replay(tmp_path, case):
    """Whole message context is sent; cached output retains supplied annotations."""
    engine, _ = initialize(tmp_path)
    with Session(engine) as db:
        db.add(Project(name='Acceptance contract')); db.commit()
    sources = original_sources(case)
    window, = build_windows({'sources': sources})
    original_window = deepcopy(window)
    response = extracted_item(case)

    class MockExtraction:
        calls = 0

        def generate_extraction(self, prompt, schema):
            self.calls += 1
            payload = json.loads(prompt[len(knowledge.PROMPT):])
            # In particular, a table row must not be sent without the user's
            # question or explicit adoption instruction surrounding that row.
            assert [source['text'] for source in payload['sources']] == [source['text'] for source in sources]
            return json.dumps({'items': [response]})

    manager = MockExtraction()
    configuration = knowledge.extraction_configuration(manager)
    first, cached = knowledge.extract_batch(engine, 1, manager, window, configuration)
    assert not cached
    replay, cached = knowledge.extract_batch(engine, 1, manager, window, configuration)
    assert cached and replay == first and manager.calls == 1
    item, = replay
    assert item['statement'] == case['expected']['statement']
    assert item['design_status'] == case['expected']['design_status']
    assert item['agreement_basis'] == case['expected']['agreement_basis']
    assert item['evidence'][0]['excerpt'] == case['expected']['excerpt']
    assert item['rationale'] == item['alternatives'] == item['limitations'] == ''
    assert window == original_window
    engine.dispose()


def test_question_context_and_explicit_adoption_are_not_merged():
    items, sources = [], []
    for case in REFERENCE['cases'][:2]:
        refs = original_sources(case)
        window, = build_windows({'sources': refs})
        items.extend(knowledge.validate_extraction(json.dumps({'items': [extracted_item(case)]}),
                                                  window, 1, 'reference-config'))
        sources.extend(refs)
    bundle = {'sources': sources, 'manifest': {'entries': []}}
    reconciled = knowledge.reconcile(None, items, bundle, 'reference-config')
    assert len(reconciled) == 2
    assert {item['design_status'] for item in reconciled} == {'PROPOSED', 'AGREED'}
    assert len({item['id'] for item in reconciled}) == 2
    assert all(not item['supersedes'] and not item['superseded_by'] for item in reconciled)


def test_original_excerpt_annotations_do_not_claim_human_confirmation():
    assert REFERENCE['annotation_status'] == 'agent_source_checked_pending_human_confirmation'
    originals = [case for case in REFERENCE['cases'] if case['origin'] == 'original_excerpt']
    assert {case['id'] for case in originals} == {
        'polite_preference_with_separate_concern', 'tentative_component_separation',
        'independent_reproducibility_policy'}
    assert all(len(case['original_source_revision']) == 64 for case in originals)
    assert all(case['expected']['excerpt'] in case['sources'][0]['text'] for case in originals)
    preference = next(case for case in originals if case['id'] == 'polite_preference_with_separate_concern')
    assert preference['expected']['category'] == 'REQUIREMENT'
    assert preference['expected']['design_status'] == 'AGREED'
    assert preference['expected']['agreement_basis'] == 'direct_user'
    assert all(case['expected']['design_status'] == 'PROPOSED'
               for case in originals if case is not preference)
