"""Published Knowledge is bounded context, never a replacement for original evidence."""
import json

import pytest
from sqlalchemy import event, text
from sqlalchemy.orm import Session

from backend.app.models import GenerationRevision, ProjectKnowledge
from backend.app.integrations.codex import scheme_knowledge as knowledge
from backend.tests.integrations.test_scheme_evidence import setup


def item(identifier='k1', status='AGREED', source='chatgpt:note:1:m:1:p:1', quote='Use a pipeline.'):
    return {'id': identifier, 'project_id': 1, 'statement': 'Use a preparation pipeline.',
            'category': 'DECISION', 'design_status': status, 'scope': 'Project',
            'rationale': 'Preserve chemical integrity.', 'source_state': 'valid',
            'conflicts_with': ['k2'], 'supersedes': [], 'superseded_by': [],
            'evidence': [{'source_id': source, 'role': 'user', 'excerpt': quote, 'revision': 'saved-rev'}]}


def publish(engine, *, publication='pub1', items=None, sources=None):
    snapshot = {'project_id': 1, 'publication_id': publication, 'generated_at': 'then',
                'extraction_version': 'v1', 'fingerprint': 'originals',
                'items': items if items is not None else [item()],
                'sources': sources if sources is not None else [
                    {'id': 'chatgpt:note:1:m:1:p:1', 'note_id': 1, 'label': 'Original design'}]}
    with Session(engine) as db:
        row = db.get(ProjectKnowledge, 1)
        if row is None:
            db.add(ProjectKnowledge(project_id=1, snapshot=snapshot))
        else:
            row.snapshot = snapshot
        db.commit()
    return snapshot


def collect(engine):
    with Session(engine) as db:
        return knowledge.collect_published_knowledge(db, 1)


def test_missing_published_knowledge_does_not_use_preview_or_foreign_snapshot(tmp_path):
    engine = setup(tmp_path)
    try:
        with Session(engine) as db:
            db.add(ProjectKnowledge(project_id=2, snapshot={'project_id': 2, 'items': [item()]}))
            db.add(GenerationRevision(project_id=1, kind='knowledge_preview', generated_at='then',
                                      snapshot={'result': {'knowledge': {'items': [item()]}}}))
            db.commit()
        result = collect(engine)
        assert result['sources'] == []
        assert result['metadata']['status'] == 'absent'
    finally:
        engine.dispose()


def test_published_items_keep_separate_original_quotes_and_historical_metadata(tmp_path):
    engine = setup(tmp_path)
    try:
        original = publish(engine)
        with Session(engine) as db:
            db.execute(text("UPDATE notes SET reference=:reference WHERE project_id=1 AND id=1"),
                       {'reference': 'https://chatgpt.com/share/11111111-1111-1111-1111-111111111111'})
            db.commit()
        result = collect(engine)
        assert result['metadata']['publication_id'] == 'pub1'
        assert result['metadata']['included_items'] == 1
        derived = next(source for source in result['sources'] if source['provenance'] == 'published Knowledge interpretation')
        primary = next(source for source in result['sources'] if source['provenance'] == 'published Knowledge original evidence')
        assert derived['supporting_source_ids'] == [primary['id']]
        interpretation = json.loads(derived['text'])
        assert interpretation['design_status'] == 'AGREED'
        assert interpretation['source_state'] == 'valid'
        assert interpretation['rationale'] == 'Preserve chemical integrity.'
        assert interpretation['conflicts_with'] == ['k2']
        assert primary['text'] == 'Use a pipeline.'
        assert primary['original_source_id'] == 'chatgpt:note:1:m:1:p:1'
        assert primary['note_id'] == 1 and primary['share'] is True
        with Session(engine) as db:
            assert db.get(ProjectKnowledge, 1).snapshot == original
    finally:
        engine.dispose()


def test_publication_identity_and_origin_changes_update_marker_without_reclassifying(tmp_path):
    engine = setup(tmp_path)
    try:
        publish(engine)
        first = collect(engine)
        publish(engine, publication='pub2')
        second = collect(engine)
        assert first['marker'] != second['marker']
        with Session(engine) as db:
            db.execute(text("UPDATE notes SET content='Changed source',updated_at='later' WHERE project_id=1 AND id=1"))
            db.commit()
        third = collect(engine)
        assert third['marker'] != second['marker']
        derived = next(source for source in third['sources'] if source['provenance'] == 'published Knowledge interpretation')
        interpretation = json.loads(derived['text'])
        assert interpretation['design_status'] == 'AGREED'
        assert interpretation['source_state'] == 'valid'
        assert interpretation['current_evidence_availability'] == 'stale'
    finally:
        engine.dispose()


def test_foreign_item_source_assignment_or_absent_origin_cannot_supply_context(tmp_path):
    engine = setup(tmp_path)
    try:
        foreign_item = item('foreign-item'); foreign_item['project_id'] = 2
        foreign_note = item('foreign-note', source='chatgpt:note:2:m:1:p:1', quote='FOREIGN NOTE')
        absent = item('absent', source='chatgpt:note:999:m:1:p:1')
        publish(engine, items=[item(), foreign_item, foreign_note, absent], sources=[
            {'id': 'chatgpt:note:1:m:1:p:1', 'note_id': 1},
            {'id': 'chatgpt:note:2:m:1:p:1', 'note_id': 2},
        ])
        result = collect(engine)
        assert result['metadata']['included_items'] == 1
        assert result['metadata']['omitted_items'] == 3
        assert 'FOREIGN NOTE' not in json.dumps(result)
    finally:
        engine.dispose()


def test_budget_keeps_complete_interpretation_support_groups_and_status_variety(tmp_path, monkeypatch):
    engine = setup(tmp_path)
    try:
        values = [item(f'k{index}', status=status) for index in range(20)
                  for status in ('AGREED', 'PROPOSED', 'UNRESOLVED')]
        for index, value in enumerate(values):
            value['id'] = f'k{index}'
            value['rationale'] = 'Reason ' * 80
        publish(engine, items=values)
        monkeypatch.setattr(knowledge, 'BUDGET_CHARS', 3500)
        result = collect(engine)
        assert sum(len(source['text']) for source in result['sources']) <= 3500
        sources = {source['id']: source for source in result['sources']}
        statuses = set()
        for source in result['sources']:
            if source['provenance'] == 'published Knowledge interpretation':
                statuses.add(json.loads(source['text'])['design_status'])
                assert all(identifier in sources for identifier in source['supporting_source_ids'])
        assert statuses == {'AGREED', 'PROPOSED', 'UNRESOLVED'}
        assert result['metadata']['omitted_items'] > 0
        assert result['limitations']
    finally:
        engine.dispose()


def test_session_evidence_only_links_owned_existing_turns(tmp_path):
    engine = setup(tmp_path)
    try:
        evidence_item = item(source='codex:1:turn:original:m:1:p:1', quote='Use a pipeline.')
        publish(engine, items=[evidence_item], sources=[
            {'id': evidence_item['evidence'][0]['source_id'], 'session_id': 1, 'turn_id': 'a'}])
        with Session(engine) as db:
            db.execute(text('UPDATE codex_history_turns SET messages=:messages WHERE project_id=1 AND session_id=1'),
                       {'messages': json.dumps([{'role': 'user', 'text': 'Use a pipeline.'}])})
            db.commit()
        result = collect(engine)
        primary = next(source for source in result['sources'] if source['provenance'] == 'published Knowledge original evidence')
        assert primary['session_id'] == 1 and primary['turn_id'] == 'a'
        assert primary['evidence_availability'] == 'available'
        with Session(engine) as db:
            db.execute(text('DELETE FROM codex_history_turns WHERE project_id=1 AND session_id=1'))
            db.commit()
        changed = collect(engine)
        primary = next(source for source in changed['sources'] if source['provenance'] == 'published Knowledge original evidence')
        assert 'turn_id' not in primary
        assert primary['evidence_availability'] == 'unavailable'
        assert changed['marker'] != result['marker']
    finally:
        engine.dispose()


def test_large_snapshot_projection_does_not_deserialize_unrelated_bodies(tmp_path):
    import gc
    import tracemalloc

    engine = setup(tmp_path)
    try:
        snapshot = publish(engine, items=[item(f'k{index}') for index in range(3000)])
        snapshot['unused_body'] = 'x' * (20 * 1024 * 1024)
        with Session(engine) as db:
            db.get(ProjectKnowledge, 1).snapshot = snapshot
            db.commit()
        del snapshot
        gc.collect()
        tracemalloc.start()
        try:
            result = collect(engine)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        assert result['metadata']['available_items'] == 3000
        assert result['metadata']['included_items'] > 0
        assert sum(len(source['text']) for source in result['sources']) <= knowledge.BUDGET_CHARS
        assert peak < 4 * 1024 * 1024
    finally:
        engine.dispose()


def test_six_reference_item_is_retained_with_disclosed_partial_evidence(tmp_path):
    engine = setup(tmp_path)
    try:
        value = item()
        value['evidence'] = [
            {'source_id': f'chatgpt:note:1:m:{index + 1}:p:1', 'role': role,
             'excerpt': 'Use a pipeline.', 'revision': 'saved'}
            for index, role in enumerate(('assistant', 'assistant', 'user', 'assistant', 'user', 'user'))]
        publish(engine, items=[value], sources=[{'id': ref['source_id'], 'note_id': 1}
                                               for ref in value['evidence']])
        result = collect(engine)
        assert result['metadata']['included_items'] == 1
        assert result['metadata']['partial_evidence_items'] == 1
        derived = next(source for source in result['sources'] if source['provenance'] == 'published Knowledge interpretation')
        data = json.loads(derived['text'])
        assert data['evidence_refs_available'] == 6
        assert data['evidence_refs_selected'] == 4 and data['evidence_partial'] is True
        originals = {source['id']: source for source in result['sources']}
        assert {originals[identifier]['role'] for identifier in derived['supporting_source_ids']} == {'user', 'assistant'}
        assert len(derived['supporting_source_ids']) <= 4
    finally:
        engine.dispose()


def test_partial_evidence_cannot_conceal_foreign_reference(tmp_path):
    engine = setup(tmp_path)
    try:
        value = item()
        value['evidence'] = [{'source_id': f'chatgpt:note:1:m:{index}:p:1', 'role': 'user',
                              'excerpt': 'Use a pipeline.'} for index in range(6)]
        value['evidence'][4] = {'source_id': 'chatgpt:note:2:m:1:p:1', 'role': 'user',
                                'excerpt': 'FOREIGN NOTE'}
        publish(engine, items=[value], sources=[{'id': ref['source_id'], 'note_id':
            2 if 'note:2:' in ref['source_id'] else 1} for ref in value['evidence']])
        result = collect(engine)
        assert result['metadata']['included_items'] == 0
        assert 'FOREIGN NOTE' not in json.dumps(result)
    finally:
        engine.dispose()


@pytest.mark.parametrize('replacement', [
    [], 'a scalar', {'items': {}, 'sources': []}, {'items': [], 'sources': {}},
    {'items': [], 'sources': [], 'publication_id': {}},
    {'items': [], 'sources': [], 'publication_id': 123},
    {'items': [], 'sources': [], 'publication_id': 'p' * 241},
    {'items': [], 'sources': [], 'generated_at': {}},
    {'items': [], 'sources': [], 'generated_at': 'x' * 101},
    {'items': [], 'sources': [], 'extraction_version': []},
    {'items': [], 'sources': [], 'fingerprint': 123},
    {'items': [], 'sources': [], 'project_id': True},
])
def test_malformed_snapshot_or_identity_metadata_is_unavailable_not_coerced(tmp_path, replacement):
    engine = setup(tmp_path)
    try:
        with Session(engine) as db:
            db.add(ProjectKnowledge(project_id=1, snapshot=replacement))
            db.commit()
        result = collect(engine)
        assert result['metadata']['status'] == 'unavailable'
        assert result['metadata']['publication_id'] is None
        assert result['sources'] == []
        assert result['limitations']
    finally:
        engine.dispose()


def test_legacy_missing_optional_identity_metadata_remains_supported(tmp_path):
    engine = setup(tmp_path)
    try:
        snapshot = publish(engine)
        for key in ('publication_id', 'generated_at', 'extraction_version', 'fingerprint'):
            snapshot.pop(key)
        with Session(engine) as db:
            db.get(ProjectKnowledge, 1).snapshot = snapshot
            db.commit()
        result = collect(engine)
        assert result['metadata']['status'] == 'published'
        assert result['metadata']['publication_id'] is None
        assert result['metadata']['included_items'] == 1
    finally:
        engine.dispose()


@pytest.mark.parametrize('revision', [{}, [], 123, True, 'x' * 241])
def test_invalid_evidence_revision_cannot_reach_scheme_source_metadata(tmp_path, revision):
    engine = setup(tmp_path)
    try:
        value = item()
        value['evidence'][0]['revision'] = revision
        publish(engine, items=[value])
        result = collect(engine)
        assert result['metadata']['included_items'] == 0
        assert result['sources'] == []
    finally:
        engine.dispose()


@pytest.mark.parametrize('action', ['replace', 'delete'])
def test_publication_replacement_during_read_discards_mixed_context(tmp_path, action):
    engine = setup(tmp_path)
    fired = False
    replacement = publish(engine, publication='old-publication')
    replacement = {**replacement, 'publication_id': 'new-publication', 'items': [item('new-item')]}

    def replace_before_item_read(connection, cursor, statement, parameters, context, executemany):
        nonlocal fired
        if not fired and statement.startswith('WITH items AS'):
            fired = True
            with engine.begin() as concurrent:
                if action == 'replace':
                    concurrent.execute(text('UPDATE project_knowledge SET snapshot=:snapshot WHERE project_id=1'),
                                       {'snapshot': json.dumps(replacement)})
                else:
                    concurrent.execute(text('DELETE FROM project_knowledge WHERE project_id=1'))

    event.listen(engine, 'before_cursor_execute', replace_before_item_read)
    try:
        result = collect(engine)
        assert fired
        assert result['sources'] == []
        assert result['metadata']['status'] == 'unavailable'
        assert result['metadata']['included_items'] == 0
        assert result['metadata']['input_chars'] == 0
        assert any('changed during inspection' in reason for reason in result['limitations'])
    finally:
        event.remove(engine, 'before_cursor_execute', replace_before_item_read)
        engine.dispose()
