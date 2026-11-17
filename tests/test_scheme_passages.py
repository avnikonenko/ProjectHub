"""Whole original Scheme passages survive bounded selection; no model calls."""
from pathlib import Path
import json

from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.app.integrations.codex.scheme_passages import GAP, select_passages
from backend.app.integrations.codex import scheme_evidence as evidence
from backend.app.models import CodexSession, Note
from backend.tests.integrations.test_scheme_evidence import setup

WORKFLOW = Path(__file__).parent / 'fixtures' / 'synthetic_library_workflow.txt'


def test_synthetic_inventory_sequence_is_complete_after_old_prefix_boundary():
    original = WORKFLOW.read_text()
    assert '4. Record assembly' not in original[:1200]
    selected = select_passages(original)
    assert selected['text'] == original and not selected['omitted']
    stages = ['Spreadsheet intake', 'Field classification', 'Catalog planning',
              'Record assembly', 'Validation and reporting', 'Catalog output']
    assert [selected['text'].index(stage) for stage in stages] == sorted(original.index(stage) for stage in stages)
    assert 'review notes and rejected rows' in selected['text'] and 'source worksheet identity' in selected['text']


def test_relevant_late_diagram_retains_exact_spans_and_assistant_provenance():
    background = 'An unrelated earlier discussion.\n\n' * 250
    original = 'User:\nPlease explain the workflow.\n\nChatGPT:\n' + background + WORKFLOW.read_text().split('ChatGPT:\n', 1)[1]
    selected = select_passages(original, limit=3500)
    assert selected['omitted'] and len(selected['text']) <= 3500
    assert 'Validation and reporting' in selected['text'] and 'Catalog output' in selected['text']
    assert selected['text'] == GAP.join(original[s['start']:s['end']] for s in selected['spans'])
    diagram = next(span for span in selected['spans'] if 'Validation and reporting' in original[span['start']:span['end']])
    assert any(message['role'] == 'assistant' for message in diagram['messages'])
    assert not any(message['role'] == 'user' for message in diagram['messages'])


def test_duplicate_paragraphs_do_not_displace_unique_late_sections():
    duplicate = 'Workflow component structure.\n\n'
    original = duplicate * 300 + 'Validation & Reporting\n\nPreserve provenance and report changes.\n'
    selected = select_passages(original, limit=350)
    assert selected['text'].count('Workflow component structure.') <= 2
    assert 'Preserve provenance and report changes.' in selected['text']
    assert len(selected['text']) <= 350


def test_oversized_paragraph_is_omitted_instead_of_being_cut_into_a_false_complete_stage():
    original = 'Architecture ' + 'large paragraph ' * 1000 + '\n\nValidation stage.\n'
    selected = select_passages(original, limit=200)
    assert selected['text'] == 'Validation stage.\n'
    assert selected['omitted']


def test_scheme_record_evidence_preserves_full_diagram_and_invalidates_late_changes(tmp_path):
    engine = setup(tmp_path)
    original = WORKFLOW.read_text()
    with Session(engine) as db:
        note = db.query(Note).filter_by(project_id=1).first()
        note.content = original
        db.commit()
        identity = note.id
    first = evidence.collect_evidence(engine, 1)
    source = next(s for s in first['sources'] if s['id'] == f'note:{identity}')
    assert source['text'] == original
    assert source['excerpt_spans'] == [{'start': 0, 'end': len(original)}]
    assert evidence.collect_evidence(engine, 1)['diagnostics']['cache_hits'] == 1
    with Session(engine) as db:
        db.get(Note, identity).content = original.replace('source worksheet identity', 'source workbook and worksheet identity')
        db.commit()
    changed = evidence.collect_evidence(engine, 1)
    assert changed['fingerprint'] != first['fingerprint']
    assert 'FOREIGN NOTE' not in str(changed)
    assert sum(len(s['text']) for s in changed['sources']) <= evidence.scheme_inspection_limits()[0]
    engine.dispose()


def test_read_boundary_does_not_present_a_cut_diagram_as_a_complete_passage(tmp_path):
    from backend.app.integrations.codex.scheme_passages import READ_CHARS

    engine = setup(tmp_path)
    prefix = 'Earlier discussion.\n\n' * ((READ_CHARS - 300) // 21)
    diagram = 'Unique incomplete workflow\nInput\n  ↓\n' + 'A processing operation\n  ↓\n' * 100 + 'Output\n'
    original = prefix + diagram
    assert len(prefix) < READ_CHARS < len(original)
    with Session(engine) as db:
        note = db.query(Note).filter_by(project_id=1).first()
        note.content = original
        db.commit()
        identifier = note.id
    result = evidence.collect_evidence(engine, 1)
    source = next(s for s in result['sources'] if s['id'] == f'note:{identifier}')
    assert source['inspected_chars'] == READ_CHARS
    assert 'Unique incomplete workflow' not in source['text']
    assert any('inspection bound' in note for note in result['coverage']['limitations'])
    engine.dispose()


def test_fenced_diagram_with_blank_lines_is_selected_atomically_or_omitted():
    fence = '```mermaid\nflowchart TD\n A[Input] --> B[Preparation]\n\n B --> C[Validation and reporting]\n C --> D[Output]\n```\n'
    original = 'Earlier context. ' * 500 + '\n\n' + fence
    small = select_passages(original, limit=65)
    assert 'B --> C' not in small['text'] and '```' not in small['text']
    complete = select_passages(original, limit=180)
    assert fence in complete['text']
    assert complete['text'].count('```') == 2
    incomplete = select_passages('Some context.\n\n' + fence.removesuffix('```\n'), limit=6000)
    assert incomplete['text'] == 'Some context.' and incomplete['omitted']


def test_shared_turns_are_deduplicated_before_budget_can_displace_unique_turn(tmp_path):
    engine = setup(tmp_path)
    shared = ['Architecture first context ' + 'a' * 2970, 'Workflow second context ' + 'b' * 2970]
    with Session(engine) as db:
        db.execute(text('DELETE FROM codex_history_turns WHERE project_id=1'))
        original = db.get(CodexSession, 1)
        original.history_indexed_activity = 'current'
        original.history_indexed_at = 'now'
        for sid in (3, 4):
            db.add(CodexSession(id=sid, project_id=1, thread_id=f'fork-{sid}',
                               history_indexed_activity='current', history_indexed_at='now'))
        db.flush()
        for sid in (1, 3, 4):
            values = [*shared, 'Unique output handoff decision.'] if sid == 1 else shared
            for index, value in enumerate(values):
                db.execute(text('INSERT INTO codex_history_turns '
                    '(project_id,session_id,session_title,turn_id,status,messages,content) '
                    'VALUES (1,:sid,\'Source\',:tid,\'completed\',:messages,:content)'),
                    {'sid': sid, 'tid': f't-{sid}-{index}',
                     'messages': json.dumps([{'role': 'user', 'text': value}]), 'content': value})
        db.commit()
    result = evidence.collect_evidence(engine, 1)
    turns = [source for source in result['sources'] if source['provenance'] == 'Codex conversation']
    assert len(turns) == result['coverage']['conversation_excerpts'] == 3
    assert any(source['text'] == 'Unique output handoff decision.' for source in turns)
    assert any(source.get('shared_sessions') for source in turns)
    engine.dispose()


def test_conversation_coverage_counts_retained_excerpts_not_omitted_query_rows(tmp_path):
    engine = setup(tmp_path)
    with Session(engine) as db:
        db.execute(text('UPDATE codex_history_turns SET content=:value WHERE project_id=1'),
                   {'value': 'Architecture ' + 'x' * 7000})
        db.commit()
    result = evidence.collect_evidence(engine, 1)
    assert result['coverage']['conversation_excerpts'] == 0
    assert not any(source['provenance'] == 'Codex conversation' for source in result['sources'])
    engine.dispose()
