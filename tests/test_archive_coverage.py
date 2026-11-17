"""Saved history input accounting with temporary SQLite and mocked Codex."""
from copy import deepcopy

import pytest
from sqlalchemy.orm import Session

from backend.app.models import CodexSession, Project
from backend.app.integrations.codex import archive
from backend.app.integrations.codex.protocol import CodexError
from backend.tests.integrations.test_archive import env, turn


def test_archive_counts_linked_sessions_active_turns_and_exact_prefix_dedup(env):
    engine, manager = env
    prefix = ['Shared initial result: ' + 'A' * 120, 'Shared follow-up result: ' + 'B' * 120]
    manager.turns['a'] = [turn('a-1', prefix[0]), turn('a-2', prefix[1]),
                          turn('a-3', 'Original outcome'), turn('a-4', 'Still running', 'inProgress')]
    manager.turns['fork'] = [turn('f-1', prefix[0]), turn('f-2', prefix[1]),
                             turn('f-3', 'Fork outcome')]
    with Session(engine) as db:
        db.add(CodexSession(id=3, project_id=1, thread_id='fork'))
        db.commit()
    with archive.archive_worker_scope(1):
        archive.archive_history(engine, 1, manager)
        counts = archive.archive_diagnostics()['archive_conversations']
        assert {key: counts[key] for key in (
            'linked_sessions', 'read_sessions', 'finished_turns', 'active_turns_skipped',
            'duplicate_turns_omitted', 'unique_turns', 'fragments')} == {
                'linked_sessions': 2, 'read_sessions': 2, 'finished_turns': 6,
                'active_turns_skipped': 1, 'duplicate_turns_omitted': 2,
                'unique_turns': 4, 'fragments': 4,
            }
        assert counts['scope'] == 'linked_codex_sessions'
        assert all(thread_id != 'b' for thread_id, _ in manager.calls)
        counts['notes'].clear()
        assert archive.archive_diagnostics()['archive_conversations']['notes']
    assert archive.archive_diagnostics() == {}


def test_archive_empty_project_records_zero_without_borrowing_other_sessions(env):
    engine, manager = env
    with Session(engine) as db:
        db.add(Project(id=3, name='No linked sessions'))
        db.commit()
    with archive.archive_worker_scope(1):
        evidence, _ = archive.archive_history(engine, 3, manager)
        counts = archive.archive_diagnostics()['archive_conversations']
    assert evidence == [] and manager.calls == []
    assert counts['linked_sessions'] == counts['read_sessions'] == 0
    assert counts['finished_turns'] == counts['unique_turns'] == counts['fragments'] == 0


def test_archive_failed_replacement_does_not_reuse_successful_coverage(env):
    engine, manager = env
    with archive.archive_worker_scope(1):
        archive.archive_history(engine, 1, manager)
        original = deepcopy(archive.archive_diagnostics()['archive_conversations'])
        assert original['read_sessions'] == 1
        manager.turns['a'] = [turn('new', 'Uncached replacement')]
        manager.fail = True
        with pytest.raises(CodexError, match='Generation failed'):
            archive.archive_history(engine, 1, manager)
        assert 'archive_conversations' not in archive.archive_diagnostics()
    assert archive.archive_diagnostics() == {}
