"""Zip pages use project-scoped saved records and local evidence links."""
import io
from zipfile import ZipFile

from sqlalchemy.orm import Session

from backend.app.db import initialize
from backend.app.models import CodexSession, Note, Project, Task
from backend.app.project_export_markdown import write_project_markdown


def render(db, project, saved, paths=None):
    output = io.BytesIO()
    with ZipFile(output, 'w') as archive:
        write_project_markdown(archive, db, project, saved, paths or {})
    return ZipFile(output)


def test_markdown_export_has_readable_empty_views(tmp_path):
    engine, _ = initialize(tmp_path)
    with Session(engine) as db:
        project = Project(name='Empty project')
        db.add(project)
        db.commit()
        with render(db, project, {}) as result:
            assert set(result.namelist()) == {
                'index.md', 'overview.md', 'knowledge.md', 'ai-qa.md', 'scheme.md',
                'implementation-choices.md', 'todos.md', 'notes.md', 'sources/index.md',
            }
            assert b'[Project index](../index.md)' in result.read('sources/index.md')
            assert b'No published Knowledge snapshot.' in result.read('knowledge.md')
            assert b'No saved generated Scheme.' in result.read('scheme.md')
            assert b'No saved TODOs.' in result.read('todos.md')
    engine.dispose()


def test_markdown_export_renders_saved_views_sources_and_diagrams(tmp_path):
    engine, _ = initialize(tmp_path)
    imported = 'sources/conversations/local #8.txt'
    related = 'sources/codex/17.txt'
    copy = 'sources/originals/source copy.md'
    link = 'https://example.test/share/archived'
    paths = {'notes': {8: imported}, 'sessions': {17: related}, 'copies': {
        8: [{'path': copy, 'filename': 'Original copy', 'content_hash': 'abc'}]}}
    long_note = 'x' * 65535 + 'é\r\n[Unsafe](javascript:alert(0))\n'
    with Session(engine) as db:
        db.add_all([
            Project(id=1, name='Project Eight', description='Purpose <script>', goals='Known goals',
                    architecture_mermaid='flowchart LR\nA --> B\n'),
            Project(id=2, name='Foreign project'),
        ])
        db.flush()
        db.add_all([
            Note(id=8, project_id=1, title='Archived conversation', kind='document', reference=link, content='Saved transcript'),
            Note(id=9, project_id=1, title='Long note', content=long_note),
            Note(id=12, project_id=2, title='Foreign note', content='Should not be included'),
            Task(project_id=1, title='Test workflow', status='in_progress', priority='high', session_id=17),
            Task(project_id=2, title='Foreign task'),
            CodexSession(id=17, project_id=1, thread_id='test-session', title='Review session'),
        ])
        db.commit()
        transcript = {'id': 'original', 'label': 'Recorded answer', 'note_id': 8, 'reference': link, 'text': 'Original <reply>'}
        session = {'id': 'turn', 'label': 'Review session', 'session_id': 17, 'text': 'Session excerpt'}
        saved = {
            'brief': {'project_id': 1, 'generated_at': '2026-11-20', 'sources': [transcript],
                      'sections': {'overview': [{'text': 'Reported observation', 'classification': 'reported',
                                                 'source_ids': ['original']}]}},
            'knowledge': {'project_id': 1, 'generated_at': '2026-11-21', 'publication_id': 'publication-one',
                          'items': [{'id': 'item-a', 'statement': 'Adopted scope', 'design_status': 'AGREED',
                                     'scope': 'Project', 'rationale': 'Retain details', 'supersedes': ['item-b'],
                                     'reconciliation_state': 'unresolved', 'reconciliation_note': 'Scope needs review.',
                                     'evidence': [{'source_id': 'original', 'role': 'user', 'excerpt': 'I agree.'}]},
                                    {'id': 'item-b', 'statement': 'Earlier choice'},
                                    {'id': 'foreign', 'project_id': 2, 'statement': 'Foreign claim'}],
                          'sources': [transcript], 'coverage': {'processed': ['this'], 'limitations': ['Partial transcript']}},
            'qa': {'items': [{'question': 'What changed?', 'answer': 'The result.',
                              'source_ids': ['turn'], 'variants': [{'answer': 'Earlier variant.'}]}], 'sources': [session]},
            'scheme': {'architecture': {'nodes': [{'id': 'entry', 'label': 'Entry', 'type': 'component',
                                                   'status': 'observed', 'source_ids': ['turn']}],
                                        'edges': [], 'explanation': 'Flow'},
                       'dependencies': [{'name': 'Package', 'source_ids': ['turn']}], 'sources': [session]},
            'parameters': {'items': [{'id': 'limit', 'title': 'Read limit', 'default_value': '12', 'path': 'core.py',
                                      'line_start': 8, 'trade_off': 'Partial context', 'trade_off_basis': 'inferred',
                                      'usage_source_ids': ['original']}], 'sources': [transcript]},
        }
        with render(db, db.get(Project, 1), saved, paths) as result:
            overview = result.read('overview.md').decode()
            assert '**Reported** · Reported observation [1](#source-1)' in overview
            assert 'Purpose &lt;script&gt;' in overview
            assert 'sources/conversations/local%20%238.txt' in overview
            assert f'[Original source]({link})' in overview
            knowledge = result.read('knowledge.md').decode()
            assert 'publication-one' in knowledge and 'Retain details' in knowledge
            assert '**Design status** · AGREED' in knowledge
            assert '**Reconciliation** · unresolved' in knowledge
            assert '**Context review** · Scope needs review.' in knowledge
            assert '[Item 2](#item-2)' in knowledge and '> I agree.' in knowledge
            assert 'Partial transcript' in knowledge and 'Foreign claim' not in knowledge
            qa = result.read('ai-qa.md').decode()
            assert 'Earlier variant.' in qa and related in qa
            scheme = result.read('scheme.md').decode()
            assert 'diagrams/architecture.mmd' in scheme and 'Package' in scheme
            assert result.read('diagrams/architecture.mmd').startswith(b'flowchart TD')
            assert result.read('diagrams/saved-architecture.mmd') == b'flowchart LR\nA --> B\n'
            parameters = result.read('implementation-choices.md').decode()
            assert 'core.py' in parameters and 'Trade-off (inferred)' in parameters
            assert 'Usage sources** · [1](#source-1)' in parameters
            todos = result.read('todos.md').decode()
            assert 'Test workflow' in todos and 'Foreign task' not in todos and related in todos
            notes = result.read('notes.md').decode()
            assert ('    ' + 'x' * 65535 + 'é\n    [Unsafe]') in notes
            assert 'Saved transcript' in notes and 'Foreign note' not in notes
            assert 'Saved transcript\n' not in notes
            sources = result.read('sources/index.md').decode()
            assert '[Archived conversation](conversations/local%20%238.txt)' in sources
            assert '[Original copy](originals/source%20copy.md) · SHA-256 abc' in sources
            assert '[Review session](codex/17.txt)' in sources
    engine.dispose()


def test_markdown_export_preserves_project_scoped_excerpt_provenance(tmp_path):
    engine, _ = initialize(tmp_path)
    paths = {
        'notes': {8: 'sources/conversations/8.txt', 88: 'sources/conversations/88.txt'},
        'sessions': {17: 'sources/codex/17.txt', 99: 'sources/codex/99.txt'},
    }
    with Session(engine) as db:
        db.add_all([Project(id=1, name='Source project'), Project(id=2, name='Other project')])
        db.flush()
        db.add_all([
            Note(id=8, project_id=1, kind='document', title='Imported conversation', content='Source'),
            Note(id=10, project_id=1, title='Older transcript', content='Source'),
            Note(id=11, project_id=1, title='Desired transcript reference', content='Excerpt',
                 reference='#history?project=1&note=8&q=source+query'),
            Note(id=12, project_id=1, title='Desired Codex reference', content='Excerpt',
                 reference='#history?project=1&session=17&turn=turn%2Babc'),
            Note(id=13, project_id=1, title='No transcript copy', content='Excerpt',
                 reference='#history?project=1&session=18&turn=historical-turn'),
            Note(id=14, project_id=1, title='Foreign project reference', content='Excerpt',
                 reference='#history?project=2&session=17&turn=foreign-project-turn'),
            Note(id=15, project_id=1, title='Duplicate session reference', content='Excerpt',
                 reference='#history?project=1&session=17&session=99&turn=ambiguous-turn'),
            Note(id=16, project_id=1, title='Foreign session reference', content='Excerpt',
                 reference='#history?project=1&session=99&turn=foreign-session-turn'),
            Note(id=19, project_id=1, title='Foreign note reference', content='Excerpt',
                 reference='#history?project=1&note=88'),
            Note(id=20, project_id=1, title='Retained transcript note reference', content='Excerpt',
                 reference='#history?project=1&note=10'),
            Note(id=88, project_id=2, title='Foreign transcript', content='Source'),
            CodexSession(id=17, project_id=1, thread_id='session-17', title='Current session'),
            CodexSession(id=18, project_id=1, thread_id='session-18', title='Unarchived session'),
            CodexSession(id=99, project_id=2, thread_id='session-99', title='Foreign session'),
        ])
        db.commit()
        with render(db, db.get(Project, 1), {}, paths) as result:
            notes = result.read('notes.md').decode()
            assert '[Source transcript](sources/conversations/8.txt)' in notes
            assert '[Source note 10](#note-10)' in notes
            assert '[Source Codex session](sources/codex/17.txt)' in notes
            assert '**Source turn** · turn+abc' in notes
            assert '**Source turn** · historical-turn' in notes
            for invalid in ('foreign-project-turn', 'ambiguous-turn', 'foreign-session-turn',
                            'conversations/88.txt', 'codex/99.txt', 'source query'):
                assert invalid not in notes
    engine.dispose()
