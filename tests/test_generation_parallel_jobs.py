"""Concurrent project views use real SQLite/public services; model calls are mocked."""
from concurrent.futures import ThreadPoolExecutor
import json
import threading

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db import initialize
from backend.app.integrations.codex import brief, generation_attempts, progress, scheme
from backend.app.integrations.codex.protocol import CodexError
from backend.app.models import GenerationRevision, Project, ProjectBrief, ProjectScheme
from backend.tests.integrations.test_brief import FakeCodex
from backend.tests.integrations.test_scheme import evidence as scheme_evidence, output as scheme_output


def run_views(tmp_path, monkeypatch, *, cancel_scheme=False):
    engine, _ = initialize(tmp_path)
    with Session(engine) as db:
        db.add(Project(id=1, name='Concurrent project', architecture_mermaid='flowchart LR\nReader --> Store'))
        db.commit()
    monkeypatch.setenv('PROJECTHUB_GENERATION_PROFILE', 'balanced')
    monkeypatch.setenv('PROJECTHUB_EXTRACTION_WORKERS', '3')
    monkeypatch.setenv('PROJECTHUB_GENERATION_BUDGET', '420')
    scheme_limits = (60_000, 2 * 1024 * 1024, 160_000)
    monkeypatch.setattr(scheme, 'scheme_inspection_limits', lambda: scheme_limits)
    monkeypatch.setattr(brief, 'archive_history', lambda *args: ([], ['No linked sessions in fixture.']))

    def collect_scheme(_engine, _project_id, limits):
        assert limits == scheme_limits
        return scheme_evidence()

    monkeypatch.setattr(scheme, 'collect_evidence', collect_scheme)
    ready = {kind: threading.Event() for kind in ('brief', 'scheme')}
    release = threading.Event()

    class OverviewManager(FakeCodex):
        def __init__(self):
            super().__init__()
            self.closed = True

        def generate_brief(self, prompt, schema):
            ready['brief'].set()
            assert release.wait(10), 'Fixture did not release overview model call'
            return super().generate_brief(prompt, schema)

    class SchemeManager:
        def generate_brief(self, prompt, schema):
            ready['scheme'].set()
            assert release.wait(10), 'Fixture did not release scheme model call'
            return json.dumps(scheme_output())

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            overview = pool.submit(brief.refresh_brief, engine, 1, OverviewManager())
            diagram = pool.submit(scheme.refresh_scheme, engine, 1, SchemeManager())
            try:
                assert ready['brief'].wait(5), 'Overview could not start concurrently'
                assert ready['scheme'].wait(5), 'Scheme could not start concurrently'
                assert progress.read(1, 'brief')['state'] == 'running'
                assert progress.read(1, 'scheme')['state'] == 'running'
                assert not brief.generation_lock.acquire(blocking=False), 'Maintenance must exclude active jobs'
                with pytest.raises(brief.BriefBusy):
                    scheme.refresh_scheme(engine, 1, SchemeManager())
                if cancel_scheme:
                    status = progress.read(1, 'scheme')
                    progress.cancel(1, 'scheme', status['started_at'])
                assert progress.read(1, 'brief').get('cancel_requested') is not True
            finally:
                release.set()
            overview.result(timeout=10)
            if cancel_scheme:
                with pytest.raises(CodexError, match='cancelled'):
                    diagram.result(timeout=10)
            else:
                diagram.result(timeout=10)
        assert progress.read(1, 'brief')['state'] == 'completed'
        assert progress.read(1, 'scheme')['state'] == ('cancelled' if cancel_scheme else 'completed')
        with Session(engine) as db:
            assert db.get(ProjectBrief, 1) is not None
            assert (db.get(ProjectScheme, 1) is not None) is (not cancel_scheme)
            kinds = list(db.scalars(select(GenerationRevision.kind).where(GenerationRevision.project_id == 1)))
            assert sorted(kinds) == (['brief'] if cancel_scheme else ['brief', 'scheme'])
            overview_settings = generation_attempts.read_latest(db, 1, 'brief')['settings']
            scheme_settings = generation_attempts.read_latest(db, 1, 'scheme')['settings']
            assert overview_settings['extraction_workers'] == 3
            assert 'extraction_workers' not in scheme_settings
            assert 'scheme_read_bytes' not in overview_settings
            assert (scheme_settings['scheme_source_chars'], scheme_settings['scheme_read_bytes'],
                    scheme_settings['scheme_package_chars']) == scheme_limits
            assert all(settings['generation_budget'] == 420 and settings['generation_profile'] == 'balanced'
                       for settings in (overview_settings, scheme_settings))
        assert brief.generation_lock.acquire(blocking=False)
        brief.generation_lock.release()
    finally:
        engine.dispose()


def test_overview_and_scheme_overlap_and_save_independently(tmp_path, monkeypatch):
    run_views(tmp_path, monkeypatch)


def test_cancel_one_view_does_not_cancel_or_overwrite_another(tmp_path, monkeypatch):
    run_views(tmp_path, monkeypatch, cancel_scheme=True)
