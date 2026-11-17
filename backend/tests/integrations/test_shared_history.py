"""Exact-prefix detection uses no AI, filesystem history mutation, or database."""
import pytest

from backend.app.integrations.codex.shared_history import SharedPrefixIndex


def turn(identifier, text=None, role='user', status='completed'):
    return {'id': str(identifier), 'status': status,
            'messages': [{'role': role, 'text': text if text is not None else f'Original turn {identifier}: ' + 'context ' * 15}]}


def test_exact_prefix_with_remapped_ids_keeps_diverged_and_reconverged_suffix():
    index = SharedPrefixIndex()
    original = [turn(i) for i in range(5)]
    fork = [{**item, 'id': f'fork-{item["id"]}'} for item in original]
    fork[3] = turn('fork-3', 'A different decision.')
    index.add_session(1, original)
    index.add_session(2, iter(fork))
    assert index.prefixes == {1: 0, 2: 3}
    assert index.canonical == {(2, f'fork-{i}'): (1, str(i)) for i in range(3)}
    assert (2, 'fork-4') not in index.canonical
    assert index.aliases[(1, '0')] == [{'session_id': 2, 'turn_id': 'fork-0'}]


def test_nested_forks_point_to_retained_turns_and_preserve_every_alias():
    index = SharedPrefixIndex()
    first = [turn(i) for i in range(3)]
    second = [{**item, 'id': f'b-{item["id"]}'} for item in first] + [turn('new-b')]
    third = [{**item, 'id': f'c-{item["id"]}'} for item in second] + [turn('new-c')]
    index.add_session(1, first)
    index.add_session(2, second)
    index.add_session(3, third)
    assert index.prefixes[3] == 4
    assert index.canonical[(3, 'c-b-0')] == (1, '0')
    assert index.canonical[(3, 'c-new-b')] == (2, 'new-b')
    assert index.aliases[(1, '0')] == [
        {'session_id': 2, 'turn_id': 'b-0'}, {'session_id': 3, 'turn_id': 'c-b-0'}]
    assert all(value not in index.canonical for value in index.canonical.values())


@pytest.mark.parametrize('copy', [
    [turn('x', 'do'), turn('y', 'continue')],
    [turn('x', 'do' + ' ' * 120), turn('y', 'continue' + ' ' * 120)],
    [turn('x', 'long text ' * 100)],
])
def test_short_or_single_turn_sequences_are_not_collapsed(copy):
    index = SharedPrefixIndex()
    index.add_session(1, copy)
    index.add_session(2, [{**item, 'id': f'b-{item["id"]}'} for item in copy])
    assert index.canonical == {} and index.prefixes[2] == 0


@pytest.mark.parametrize('changed', [
    turn('1', 'Original turn 1: ' + 'context ' * 15, role='assistant'),
    turn('1', status='failed'),
    turn('1', 'Original turn 1: ' + 'context ' * 15 + ' '),
])
def test_exact_roles_status_and_whitespace_are_preserved(changed):
    index = SharedPrefixIndex()
    index.add_session(1, [turn(0), turn(1), turn(2)])
    index.add_session(2, [turn(0), changed, turn(2)])
    assert index.canonical == {}


@pytest.mark.parametrize('barrier', [
    turn('barrier', status='inProgress'), turn('barrier', status='unknown'),
    turn('barrier', ''), turn('barrier', '  \n '), None,
    {'id': 'barrier', 'status': 'completed', 'messages': [{'role': 'tool', 'text': 'result'}]},
    {'id': 'barrier', 'status': 'completed', 'messages': [{'role': 'user', 'text': None}]},
])
def test_uncertain_turn_stops_prefix_matching_but_consumes_iterator(barrier):
    index = SharedPrefixIndex()
    index.add_session(1, [turn(0), turn(1), turn(2), turn(3)])
    consumed = []

    def fork():
        for item in [turn(0), turn(1), barrier, turn(2), turn(3)]:
            consumed.append(item)
            yield item

    index.add_session(2, fork())
    assert len(consumed) == 5
    assert index.prefixes[2] == 2
    assert set(index.canonical) == {(2, '0'), (2, '1')}


def test_same_session_repetition_and_separate_project_instances_remain_separate():
    index = SharedPrefixIndex()
    content = [turn('a', 'context ' * 30), turn('b', 'context ' * 30)]
    index.add_session(1, content)
    assert index.canonical == {}
    other_project = SharedPrefixIndex()
    other_project.add_session(2, content)
    assert other_project.canonical == {}


def test_broken_iterator_and_duplicate_session_do_not_corrupt_existing_metadata():
    index = SharedPrefixIndex()
    index.add_session(1, [turn(0), turn(1)])

    def broken():
        yield turn(0)
        raise RuntimeError('Interrupted read')

    with pytest.raises(RuntimeError, match='Interrupted'):
        index.add_session(2, broken())
    assert index.prefixes == {1: 0} and index.canonical == {}
    index.add_session(2, [turn(0), turn(1)])
    before = dict(index.canonical)
    with pytest.raises(ValueError, match='only be added once'):
        index.add_session(2, [turn(0)])
    assert index.canonical == before


def test_repeated_turn_id_is_a_barrier_and_input_is_not_mutated():
    index = SharedPrefixIndex()
    turns = [turn(0), turn(1), turn(1), turn(3)]
    before = repr(turns)
    index.add_session(1, [turn(0), turn(1), turn(2), turn(3)])
    index.add_session(2, turns)
    assert index.prefixes[2] == 2
    assert (2, '3') not in index.canonical
    assert repr(turns) == before


@pytest.mark.parametrize('second_length,shared', [(99, 0), (100, 2)])
def test_substantial_prefix_threshold_is_inclusive(second_length, shared):
    index = SharedPrefixIndex()
    original = [turn('a', 'x' * 100), turn('b', 'y' * second_length)]
    index.add_session(1, original)
    index.add_session(2, [{**item, 'id': f'copy-{item["id"]}'} for item in original])
    assert index.prefixes[2] == shared
    assert all(isinstance(digest, bytes) and len(digest) == 32 for digest in index._prefixes)
    assert all(isinstance(reference, tuple) for reference in index._prefixes.values())


def test_fork_origin_is_the_complete_matched_prefix_not_an_isolated_common_reply():
    index = SharedPrefixIndex()
    first = [turn('first-start', 'do'), turn('first-long', 'A decision. ' * 30)]
    second = [turn('second-start', 'do'), turn('second-long', 'B decision. ' * 30)]
    third = [{**item, 'id': 'fork-' + item['id']} for item in second]
    index.add_session(1, first)
    index.add_session(2, second)
    assert index.canonical == {}
    index.add_session(3, third)
    assert index.canonical[(3, 'fork-second-start')] == (2, 'second-start')
    assert index.canonical[(3, 'fork-second-long')] == (2, 'second-long')
    assert (1, 'first-start') not in index.aliases
    assert index.aliases[(2, 'second-start')] == [
        {'session_id': 3, 'turn_id': 'fork-second-start'}]
