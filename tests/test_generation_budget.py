"""Overall deadlines apply across calls and before publication."""
import threading
import pytest
from backend.app.integrations.codex import progress
from backend.app.integrations.codex.manager import wait_for_generation
from backend.app.integrations.codex.protocol import CodexError


def test_budget_blocks_model_wait_and_publication(monkeypatch):
    monkeypatch.setenv('PROJECTHUB_GENERATION_BUDGET', '30')
    clock = [100.0]
    monkeypatch.setattr(progress.time, 'monotonic', lambda: clock[0])
    progress.begin(98123, 'brief')
    saved = []
    try:
        progress.update(98123, batch_completed=2, batch_total=4)
        clock[0] += 31
        with pytest.raises(CodexError, match='overall time budget'):
            wait_for_generation(threading.Event(), 600)
        with pytest.raises(CodexError, match='overall time budget'):
            progress.complete(98123, lambda: saved.append(True))
        assert not saved
    finally:
        progress.finish(98123, error='Budget exhausted')
    assert progress.read(98123, 'brief')['state'] == 'failed'


def test_budget_restarts_for_new_generation(monkeypatch):
    monkeypatch.setenv('PROJECTHUB_GENERATION_BUDGET', '30')
    progress.begin(98124, 'brief')
    progress.finish(98124)
    progress.begin(98124, 'brief')
    try:
        progress.check_cancelled()
        assert progress.read(98124, 'brief')['budget_seconds'] == 30
    finally:
        progress.finish(98124)
