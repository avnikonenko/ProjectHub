"""Completed invalid model responses get one strict corrective attempt."""
from contextlib import contextmanager
from copy import deepcopy
import json

import pytest

from backend.app.integrations.codex import qa
from backend.app.integrations.codex.protocol import CodexError


def entry(**changes):
    return {'question': 'Which storage?', 'answer': 'SQLite.', 'source_ids': ['1'], **changes}


def response(items):
    return json.dumps({'items': items})


class Replies:
    def __init__(self, *values):
        self.values = list(values)
        self.requests = []
        self.children = 0

    @contextmanager
    def independent_generation(self):
        self.children += 1
        child = self.children
        parent = self

        class Child:
            def generate_brief(self, prompt, schema):
                parent.requests.append((child, prompt, deepcopy(schema)))
                value = parent.values.pop(0)
                if isinstance(value, Exception):
                    raise value
                return value

        yield Child()


@pytest.mark.parametrize('invalid,maximum', [
    ('not JSON', 1), ('[]', 1), ('{"selected_ids":{}}', 1),
    ('{"selected_ids":[],"comment":"extra root field"}', 1),
    ('{"selected_ids":["q1","q2"]}', 1), ('{"selected_ids":["q1","q1"]}', 3),
    ('{"selected_ids":[1]}', 1), ('{"selected_ids":["foreign"]}', 1),
    (response([entry()]), 1),
])
def test_invalid_consolidation_is_corrected_without_discarding_or_changing_candidates(invalid, maximum):
    candidates = [{'question': 'Which storage?', 'answer': 'SQLite.', 'source_ids': ['original:one']}]
    before = deepcopy(candidates)
    manager = Replies(invalid, '{"selected_ids":["q1"]}')
    result = qa._consolidate_group(manager, candidates, maximum)
    assert result == candidates and candidates == before
    assert manager.children == 2 and len(manager.requests) == 2
    first, second = manager.requests
    assert first[0] != second[0] and first[2] == second[2]
    assert 'Correct the previous attempt' in second[1]
    assert f'at most {maximum} entries' in second[1]
    assert json.loads(first[1].split('\n', 1)[1]) == json.loads(second[1].split('\n', 1)[1])


@pytest.mark.parametrize('invalid', [
    'not JSON', '[]', '{"items":{}}',
    response([entry(answer='x' * 801)]), response([entry(answer='[99]')]),
])
def test_related_draft_output_errors_get_one_correction_with_full_evidence(invalid):
    evidence = [{'text': 'The user chose SQLite.', 'sources': [
        {'id': 'original:one', 'text': 'The user chose SQLite.'}]}]
    before = deepcopy(evidence)
    manager = Replies(invalid, response([entry()]))
    result = qa.generate_items(manager, evidence)
    assert result[0][0]['source_ids'] == ['original:one']
    assert evidence == before and manager.children == 2 and len(manager.requests) == 2
    assert json.loads(manager.requests[0][1].split('\n', 1)[1]) == json.loads(manager.requests[1][1].split('\n', 1)[1])


@pytest.mark.parametrize('answer,expected', [
    ('Use values [1, 2] in order.', 'Use values [1, 2] in order.'),
    ('The configured values are [1, 2]. [99][100]', 'The configured values are [1, 2].'),
    ('Use items[1] and output [2, 3]', 'Use items[1] and output [2, 3]'),
    ('[99]', '[99]'),
    ('The selected vector follows. [1, 2]', 'The selected vector follows. [1, 2]'),
])
def test_draft_preserves_numeric_literals_while_removing_detached_references(answer, expected):
    evidence = [{'text': 'Use the recorded values.', 'sources': [
        {'id': 'original:one', 'text': 'Use [99]. The selected vector follows. [1, 2]'}]}]
    manager = Replies(response([entry(answer=answer)]))
    item, sources = qa.generate_items(manager, evidence)[0]
    assert item['answer'] == expected
    assert item['source_ids'] == list(sources) == ['original:one']
    assert len(manager.requests) == 1


def test_cached_raw_draft_preserves_numeric_literals_without_another_call(tmp_path):
    from sqlalchemy.orm import Session
    from backend.app.db import initialize
    from backend.app.models import Project

    engine, _ = initialize(tmp_path)
    try:
        with Session(engine) as db:
            db.add(Project(id=1, name='Q&A fixture'))
            db.commit()
        answer = 'Use values [1, 2] in order.'
        evidence = [{'text': answer, 'sources': [{'id': 'original:one', 'text': answer}]}]
        first = qa.generate_items(Replies(response([entry(answer=answer)])), evidence, engine=engine, project_id=1)
        offline = Replies()
        replay = qa.generate_items(offline, evidence, engine=engine, project_id=1)
        assert replay == first
        assert replay[0][0]['answer'] == answer
        assert offline.requests == []
    finally:
        engine.dispose()


def test_invalid_consolidation_correction_is_not_silently_accepted_or_retried_again():
    candidates = [{'question': 'Which storage?', 'answer': 'SQLite.', 'source_ids': ['original:one']}]
    manager = Replies('{"selected_ids":{}}', '{"selected_ids":["foreign"]}')
    with pytest.raises(qa.InvalidQACitation, match='foreign'):
        qa._consolidate_group(manager, candidates, 1)
    assert len(manager.requests) == 2


def test_consolidation_transport_failure_is_not_retried_as_invalid_model_output():
    manager = Replies(CodexError('Connection unavailable'))
    with pytest.raises(CodexError, match='Connection unavailable'):
        qa._consolidate_group(manager, [
            {'question': 'Which storage?', 'answer': 'SQLite.', 'source_ids': ['original:one']}], 1)
    assert len(manager.requests) == 1


def test_selection_overflow_is_corrected_without_truncation_and_valid_ids_are_cached(tmp_path):
    from sqlalchemy.orm import Session
    from backend.app.db import initialize
    from backend.app.models import Project

    engine, _ = initialize(tmp_path)
    with Session(engine) as db:
        db.add(Project(id=1, name='Q&A fixture'))
        db.commit()
    candidates = [{'question': f'Question {i}?', 'answer': 'SQLite.', 'source_ids': ['original:one']}
                  for i in range(27)]
    overflow = json.dumps({'selected_ids': [f'q{i}' for i in range(1, 28)]})
    valid = json.dumps({'selected_ids': [f'q{i}' for i in range(1, 26)]})
    try:
        failed = Replies(overflow, '{"selected_ids":["foreign"]}')
        with pytest.raises(qa.InvalidQACitation, match='foreign'):
            qa._consolidate_group(failed, candidates, 25)
        manager = Replies(overflow, valid)
        assert qa._consolidate_group(manager, candidates, 25, engine=engine, project_id=1) == candidates[:25]
        assert len(manager.requests) == 2
        invalid = Replies(overflow, '{"selected_ids":["foreign"]}')
        assert qa._consolidate_group(invalid, candidates, 24, engine=engine, project_id=1) == candidates[:24]
        assert len(invalid.requests) == 2
        assert qa._consolidate_group(manager, candidates, 25, engine=engine, project_id=1) == candidates[:25]
        assert len(manager.requests) == 2
    finally:
        engine.dispose()


def test_local_fallback_merges_matching_answers_and_preserves_other_sourced_answers():
    candidates = [
        {'question': 'Which storage?', 'answer': 'SQLite.', 'source_ids': ['conversation:a']},
        {'question': 'Which editor?', 'answer': 'Any editor.', 'source_ids': ['conversation:b']},
        {'question': 'Which storage?', 'answer': 'PostgreSQL.', 'source_ids': ['conversation:c']},
        {'question': 'Which storage?', 'answer': 'SQLite.', 'source_ids': ['conversation:d']},
    ]
    before = deepcopy(candidates)
    assert qa._select_qa_candidates(candidates, 1) == [{
        'question': 'Which storage?', 'answer': 'SQLite.', 'source_ids': ['conversation:a', 'conversation:d'],
        'variants': [{'answer': 'PostgreSQL.', 'source_ids': ['conversation:c']}],
    }]
    assert candidates == before
