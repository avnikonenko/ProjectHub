"""Exact local source handling for imported conversation consumers."""
from copy import deepcopy

import pytest
from sqlalchemy.orm import Session

from backend.app.db import initialize
from backend.app.integrations.codex import knowledge_sources, scheme_knowledge
from backend.app.integrations.codex.brief import note_evidence
from backend.app.integrations.codex.history_search import search_history
from backend.app.models import Note, Project
from backend.app.project_evidence import collect_project_evidence


def transcript(messages, completeness='partial'):
    content, ranges = '', []
    for identifier, role, message in messages:
        content += f'{role.title()}:\n'
        start = len(content)
        content += message
        ranges.append({'id': identifier, 'role': role, 'start': start, 'end': len(content)})
        content += '\n\n'
    return content, {'provider': 'claude', 'completeness': completeness, 'source_status': 'none',
                     'messages': ranges, 'fingerprint': 'fixture'}


@pytest.fixture
def imported(tmp_path):
    engine, _ = initialize(tmp_path)
    messages = [('message-1', 'user', 'Primary unicorn 🦄.\nChatGPT: an embedded speaker cue.'),
                ('message-2', 'user', 'A second unicorn message.'),
                ('message-3', 'assistant', 'The unicorn decision was accepted.')]
    content, metadata = transcript(messages)
    metadata['messages'][0]['timestamp'] = '2026-11-20T10:00:00Z'
    with Session(engine) as db:
        db.add_all([Project(id=1, name='Selected project'), Project(id=2, name='Other project')])
        db.flush()
        db.add_all([Note(id=1, project_id=1, title='Claude transcript', kind='document', content=content,
                         reference='', conversation=metadata),
                    Note(id=2, project_id=2, title='Foreign transcript', kind='document', content=content,
                         reference='local.json', conversation=metadata),
                    Note(id=3, project_id=1, title='Ordinary unicorn document', kind='document', content='unicorn')])
        db.commit()
    yield engine, messages
    engine.dispose()


def test_knowledge_uses_exact_imported_roles_offsets_and_stable_message_ids(imported):
    engine, messages = imported
    first = knowledge_sources.load_sources(engine, 1)
    sources = first['sources']
    assert [source['role'] for source in sources] == ['user', 'user', 'assistant']
    assert [source['text'] for source in sources] == [message[2] for message in messages]
    assert {source['note_id'] for source in sources} == {1}
    assert sources[0]['timestamp'] == '2026-11-20T10:00:00Z'
    assert first['source_inventory']['imported_chats_total'] == 1
    assert 'imported transcript is partial' in ' '.join(first['coverage']['limitations'])
    assert first['manifest']['entries'][0]['transcript_completeness'] == 'partial'
    with Session(engine) as db:
        note = db.get(Note, 1)
        for source in sources:
            assert note.content[source['offset_start']:source['offset_end']] == source['text']
        note.content, note.conversation = transcript([('preamble', 'assistant', 'Earlier text'), *messages], 'unknown')
        db.commit()
    second = knowledge_sources.load_sources(engine, 1, previous_manifest=first['manifest'])
    assert second['manifest']['fingerprint'] != first['manifest']['fingerprint']
    for previous, current in zip(sources, second['sources'][1:]):
        assert current['id'] == previous['id']
        assert current['revision'] == previous['revision']
        assert current['offset_start'] > previous['offset_start']
    assert 'completeness of the imported transcript is not established' in ' '.join(second['coverage']['limitations'])


def test_history_overview_and_qa_selection_include_only_owned_transcripts(imported, monkeypatch):
    engine, _ = imported
    with Session(engine) as db:
        hits = search_history(db, 1, 'unicorn', include_shared=True)['results']
        assert [(hit['kind'], hit['note_id']) for hit in hits] == [('chatgpt', 1)]
        assert search_history(db, 1, 'unicorn')['results'] == []
        shared = [item['sources'][0] for item in note_evidence(db, 1, shared_only=True)]
        ordinary = [item['sources'][0] for item in note_evidence(db, 1, exclude_shared=True)]
        assert {source['note_id'] for source in shared} == {1}
        assert 'Imported transcript is partial.' in shared[0]['text']
        assert shared[0]['share'] is True
        assert {source['note_id'] for source in ordinary} == {3}
    monkeypatch.setattr(knowledge_sources, 'MAX_SOURCE_CHARS', 40)
    assert knowledge_sources.needs_large_history_intake(engine, 1)
    result = collect_project_evidence(engine, 1)
    assert any(source.get('note_id') == 1 and source.get('share') for source in result['sources'])


def test_scheme_revalidates_quote_within_exact_imported_message_and_role(imported):
    engine, _ = imported
    source = knowledge_sources.load_sources(engine, 1)['sources'][0]
    ref = {'source_id': source['id'], 'excerpt': 'Primary unicorn', 'role': 'user'}
    with Session(engine) as db:
        origin = scheme_knowledge._origin(db, 1, ref, source)
        assert origin[:2] == ({'note_id': 1, 'note_created_at': db.get(Note, 1).created_at, 'share': True}, 'available')
        assert scheme_knowledge._origin(db, 2, ref, source) is None
        note = db.get(Note, 1)
        metadata = deepcopy(note.conversation)
        metadata['messages'][0]['role'] = 'assistant'
        note.conversation = metadata
        db.commit()
        assert scheme_knowledge._origin(db, 1, ref, source)[1] == 'stale'


def test_corrupt_message_ranges_do_not_fall_back_to_inferred_roles(imported):
    engine, _ = imported
    with Session(engine) as db:
        note = db.get(Note, 1)
        metadata = deepcopy(note.conversation)
        metadata['messages'][0]['end'] = len(note.content) + 1
        note.conversation = metadata
        db.commit()
    bundle = knowledge_sources.load_sources(engine, 1)
    assert bundle['sources'] == []
    assert bundle['coverage']['unavailable'] == ['chatgpt:note:1']


def test_older_import_with_nul_is_unavailable_without_editing_its_transcript(imported):
    engine, messages = imported
    source = knowledge_sources.load_sources(engine, 1)['sources'][-1]
    ref = {'source_id': source['id'], 'excerpt': messages[-1][2], 'role': 'assistant'}
    first_id, first_role, first_text = messages[0]
    content, metadata = transcript([(first_id, first_role, first_text + '\x00suffix'), *messages[1:]])
    with Session(engine) as db:
        note = db.get(Note, 1)
        note.content, note.conversation = content, metadata
        db.commit()
        origin = scheme_knowledge._origin(db, 1, ref, source)
        assert origin[:2] == ({'note_id': 1, 'note_created_at': note.created_at, 'share': True}, 'unavailable')
        assert origin[2]['quote_present'] is False
    bundle = knowledge_sources.load_sources(engine, 1)
    assert bundle['sources'] == []
    assert bundle['coverage']['unavailable'] == ['chatgpt:note:1']
    assert bundle['manifest']['entries'][0]['reason'] == 'invalid_transcript_extent'
    with Session(engine) as db:
        assert db.get(Note, 1).content == content


@pytest.mark.parametrize('balanced', [False, True])
def test_large_import_admits_exact_messages_within_budget(imported, monkeypatch, balanced):
    engine, messages = imported
    oversized = '🦄 context\n' * 60_000
    with Session(engine) as db:
        note = db.get(Note, 1)
        note.content, note.conversation = transcript([messages[0], ('huge', 'assistant', oversized), *messages[1:]])
        content = note.content
        db.commit()

    complete = knowledge_sources.load_sources(engine, 1, balanced=balanced)
    assert ''.join(source['text'] for source in complete['sources'] if source['message_id'] == 'huge') == oversized
    assert complete['coverage']['unprocessed'] == []
    assert complete['coverage']['excluded'] == []

    monkeypatch.setattr(knowledge_sources, 'MAX_SOURCE_CHARS', 200)
    monkeypatch.setattr(knowledge_sources, '_BALANCED_TEXT_HEADROOM', 0)
    limited = knowledge_sources.load_sources(engine, 1, balanced=balanced, previous_manifest=complete['manifest'])
    assert limited['coverage']['processed'] == limited['coverage']['unprocessed'] == ['chatgpt:note:1']
    assert limited['coverage']['excluded'] == []
    assert limited['source_inventory']['omission_reasons']['global_text_budget_messages'] == 1
    assert limited['manifest']['entries'][0]['partial'] is True
    assert [source['message_id'] for source in limited['sources']] == [message[0] for message in messages]
    windows = knowledge_sources.build_windows(limited)
    assert [[source['message_id'] for source in window['sources']] for window in windows] == [
        ['message-1'], ['message-2', 'message-3']]
    for source in limited['sources']:
        original = next(entry for entry in complete['sources'] if entry['id'] == source['id'])
        assert (source['role'], source['text'], source['revision']) == (original['role'], original['text'], original['revision'])
        assert source['text'] == content[source['offset_start']:source['offset_end']]
