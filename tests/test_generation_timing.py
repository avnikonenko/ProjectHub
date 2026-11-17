"""Stage timing uses monotonic clocks and remains isolated from callers."""
from backend.app.integrations.codex import progress


def test_stage_timing_and_terminal_duration(monkeypatch):
    clock = [10.0]
    monkeypatch.setattr(progress.time, 'monotonic', lambda: clock[0])
    progress.begin(98421, 'knowledge')
    clock[0] = 12
    progress.update(98421, stage='Extracting source-linked knowledge')
    clock[0] = 17
    progress.update(98421, stage='Generating knowledge-based brief')
    status = progress.read(98421, 'knowledge')
    assert status['elapsed_seconds'] == 7
    assert status['stage_seconds']['Extracting source-linked knowledge'] == 5
    status['stage_seconds'].clear()
    assert progress.read(98421, 'knowledge')['stage_seconds']
    clock[0] = 20
    progress.finish(98421)
    terminal = progress.read(98421, 'knowledge')
    assert terminal['elapsed_seconds'] == 10
    assert terminal['stage_seconds']['Generating knowledge-based brief'] == 3
    clock[0] = 100
    assert progress.read(98421, 'knowledge')['elapsed_seconds'] == 10


def test_cancel_response_cannot_mutate_stored_timing():
    progress.begin(98422, 'knowledge')
    try:
        progress.update(98422, stage='Extracting source-linked knowledge')
        started = progress.read(98422, 'knowledge')['started_at']
        response = progress.cancel(98422, 'knowledge', started)
        response['stage_seconds'].clear()
        response['stage_seconds']['injected'] = 999
        current = progress.read(98422, 'knowledge')
        assert current['stage_seconds']
        assert 'injected' not in current['stage_seconds']
    finally:
        progress.finish(98422)
