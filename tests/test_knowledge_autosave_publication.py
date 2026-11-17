"""Normal auto-save uses the atomic publication path; real SQLite, mocked AI."""
import time

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.integrations.codex import knowledge_brief as pilot, progress
from backend.app.integrations.codex.knowledge_previews import read_knowledge_preview
from backend.app.integrations.codex.protocol import CodexError
from backend.app.models import GenerationRevision, ProjectHistoryBatch
from backend.tests.integrations.test_knowledge_brief import prepared, FakeSynthesis, _stored


def test_autosave_publishes_current_records_and_summary_without_preview_or_activation(prepared, monkeypatch):
    before = _stored(prepared.engine)
    foreign = _stored(prepared.engine, 2)
    monkeypatch.setattr(pilot, 'stage_knowledge_preview',
                        lambda *args: pytest.fail('Normal auto-save staged an unpublished preview'))
    monkeypatch.setattr(pilot, 'publish_saved_knowledge_preview',
                        lambda *args: pytest.fail('Normal auto-save needed a separate activation'))

    response = pilot.generate_knowledge_brief(prepared.engine, 1, FakeSynthesis(), publish=True)
    saved, overview, revisions = _stored(prepared.engine)
    assert response['published'] is True
    assert saved == response['knowledge']
    assert saved['summary'] == response['brief']
    assert saved['publication_id'] == saved['summary']['project_state']['publication_id'] == response['publication_id']
    assert overview == before[1]
    assert revisions[:-1] == before[2]
    assert revisions[-1][0] == 'knowledge' and revisions[-1][2] == saved
    assert _stored(prepared.engine, 2) == foreign
    assert progress.read(1, 'knowledge')['state'] == 'completed'
    with Session(prepared.engine) as db:
        assert read_knowledge_preview(db, 1) is None


@pytest.mark.parametrize('failure', ['model_timeout', 'publication_budget'])
def test_autosave_timeout_retains_previous_publication_and_cache_then_retry_can_save(prepared, monkeypatch, failure):
    before = _stored(prepared.engine)
    foreign = _stored(prepared.engine, 2)
    original_synthesis = pilot._synthesize

    class TimeoutSynthesis(FakeSynthesis):
        def generate_brief(self, prompt, schema):
            raise CodexError('Codex did not finish generation within 600 seconds.')

    if failure == 'publication_budget':
        def expired_after_valid_synthesis(*args, **kwargs):
            result = original_synthesis(*args, **kwargs)
            # The model returned valid output, but the final guarded commit must
            # still reject a run whose shared per-job budget has now elapsed.
            with progress._lock:
                progress._clocks[1, 'knowledge'] = (time.monotonic() - 31, 30)
            return result
        monkeypatch.setattr(pilot, '_synthesize', expired_after_valid_synthesis)
    manager = TimeoutSynthesis() if failure == 'model_timeout' else FakeSynthesis()
    with pytest.raises(CodexError, match='within 600 seconds|overall time budget'):
        pilot.generate_knowledge_brief(prepared.engine, 1, manager, publish=True)
    assert _stored(prepared.engine) == before
    assert _stored(prepared.engine, 2) == foreign
    assert progress.read(1, 'knowledge')['state'] == 'failed'
    with Session(prepared.engine) as db:
        assert db.get(ProjectHistoryBatch, (1, 'successful-extraction')) is not None
        assert read_knowledge_preview(db, 1) is None
        assert len(list(db.scalars(select(GenerationRevision).where(
            GenerationRevision.project_id == 1, GenerationRevision.kind == 'knowledge')))) == 1

    monkeypatch.setattr(pilot, '_synthesize', original_synthesis)
    response = pilot.generate_knowledge_brief(prepared.engine, 1, FakeSynthesis(), publish=True)
    saved, overview, revisions = _stored(prepared.engine)
    assert response['published'] is True and saved == response['knowledge']
    assert saved['summary'] == response['brief']
    assert saved['publication_id'] != before[0]['publication_id']
    assert overview == before[1]
    assert revisions[:-1] == before[2]
    with Session(prepared.engine) as db:
        assert db.get(ProjectHistoryBatch, (1, 'successful-extraction')) is not None
        assert read_knowledge_preview(db, 1) is None
