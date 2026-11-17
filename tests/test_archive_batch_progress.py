"""Batch totals describe the current phase and preserve cache inputs."""
from backend.tests.integrations.test_archive import env, turn
from backend.app.integrations.codex import archive


def test_exact_history_total_precedes_first_request_and_cache_replay(env, monkeypatch):
    engine, manager = env
    manager.turns['a'] = [turn(i, 'x' * 15000) for i in range(8)]
    events = []
    monkeypatch.setattr(archive.progress, 'update', lambda project_id, **fields: events.append(fields))
    original = manager.generate_brief
    observed = []

    def generate(prompt, schema):
        totals = [event for event in events if 'batch_total' in event]
        assert totals and totals[-1]['batch_total'] > 1
        assert events[-1]['stage'] == 'Summarizing history batch'
        assert any(event.get('batch_started_at') for event in events)
        observed.append(totals[-1]['batch_total'])
        return original(prompt, schema)

    manager.generate_brief = generate
    first, _ = archive.archive_history(engine, 1, manager)
    count = len(manager.inputs)
    assert observed == [count] * count
    assert events[-1]['batch_completed'] == count
    assert events[-1]['cache_hits'] == 0
    timing = [event for event in events if 'last_batch_seconds' in event]
    assert len(timing) == count
    assert all(event['last_batch_seconds'] >= 0 and event['batch_started_at'] is None for event in timing)
    events.clear()
    second, _ = archive.archive_history(engine, 1, manager)
    assert second == first and len(manager.inputs) == count
    assert events[-1]['cache_hits'] == count
    assert events[-1]['batch_completed'] == count
