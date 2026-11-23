import ast
import re
from textwrap import dedent

from backend.app.integrations.codex import scheme_evidence
from backend.app.integrations.codex.scheme_evidence import _EvidenceScope, _predicate_shape
from backend.app.integrations.codex.scheme_local_handoffs import local_handoff_rows


def _rows(source, scope):
    return local_handoff_rows(
        ast.parse(dedent(source).lstrip('\n')), _EvidenceScope(scope).score, shape=_predicate_shape,
    )


def _bundle(rows, *terms):
    matches = [
        (line, report) for line, report in rows
        if all(term.casefold() in report.casefold() for term in terms)
    ]
    assert matches, rows
    return matches[0]


def _anchors(line, report):
    return {line, *map(int, re.findall(r'\b(?:line\s+|L)(\d+)\b', report, re.I))}


def test_use_of_produced_value_survives_an_intervening_unrelated_call():
    rows = _rows("""
        def export(source):
            result = transform(source)
            refresh()
            publish(result)
    """, 'Trace transform result into publish')

    line, report = _bundle(rows, 'Direct value transfer', 'transform', 'publish')
    assert {2, 4} <= _anchors(line, report)
    assert 'refresh' not in report
    assert all(
        not ('direct value transfer' in candidate.casefold() and 'refresh' in candidate)
        for _, candidate in rows
    )


def test_disjoint_branch_assignments_remain_visible_at_the_same_consumer():
    rows = _rows("""
        def execute(enabled):
            if enabled:
                choice = load_profile()
            else:
                choice = load_default()
            dispatch(choice)
    """, 'Explain dispatch choice enabled profile default')

    line, report = _bundle(rows, 'load_profile', 'load_default', 'dispatch')
    assert {2, 3, 5, 6} <= _anchors(line, report)
    assert 'enabled' in report
    assert re.search(r'\b(?:else|not|false)\b', report, re.I)
    assert 'unresolved' not in report.casefold()


def test_mutually_exclusive_guards_do_not_link_producer_and_consumer():
    source = """
        def execute(enabled):
            if enabled:
                value = prepare()
            if not enabled:
                store(value)
    """
    rows = _rows(source, 'Trace prepare into store')
    _bundle(rows, 'store', 'Unresolved input')
    assert all('Direct value transfer' not in report for _, report in rows)


def test_condition_mutated_through_an_alias_does_not_establish_a_handoff():
    rows = _rows("""
        def execute():
            values = [1]
            alias = values
            if values:
                alias.clear()
            if values:
                value = prepare()
                store(value)
    """, 'Trace prepare into store')
    assert all('Direct value transfer' not in report for _, report in rows)


def test_unknown_effect_receiving_a_guard_alias_does_not_establish_a_handoff():
    rows = _rows("""
        def execute():
            values = [1]
            alias = values
            if values:
                unknown_effect(alias)
            if values:
                value = prepare()
                store(value)
    """, 'Trace prepare into store')
    assert all('Direct value transfer' not in report for _, report in rows)


def test_fixed_and_configured_inputs_are_preserved_without_literal_text():
    rows = _rows("""
        def execute(options):
            estimator = "PRIVATE_CONFIGURATION_CONTENT"
            program = options.program
            dispatch(estimator, program)
    """, 'Explain dispatch estimator program options')

    line, report = _bundle(rows, 'estimator', 'options.program', 'dispatch')
    assert {2, 3, 4} <= _anchors(line, report)
    assert 'PRIVATE_CONFIGURATION_CONTENT' not in report


def test_configured_value_bundle_keeps_the_consumers_guard():
    rows = _rows("""
        def execute(options, enabled):
            choice = options.program
            if enabled:
                dispatch(choice)
    """, 'Explain dispatch choice program enabled')

    line, report = _bundle(rows, 'options.program', 'dispatch')
    assert {2, 3, 4} <= _anchors(line, report)
    assert 'enabled' in report


def test_default_reaching_the_join_retains_the_branch_where_it_was_not_overridden():
    rows = _rows("""
        def execute(enabled):
            choice = load_default()
            if enabled:
                choice = load_profile()
            dispatch(choice)
    """, 'Explain dispatch choice enabled profile default')

    line, report = _bundle(rows, 'load_profile', 'load_default', 'dispatch')
    assert {2, 3, 4, 5} <= _anchors(line, report)
    assert re.search(r'\bload_default\b[^|]*\bnot\s+enabled\b', report, re.I)


def test_reassignment_prevents_a_stale_producer_from_becoming_an_edge():
    rows = _rows("""
        def export(source, replacement):
            result = transform(source)
            result = replacement
            publish(result)
    """, 'Trace transform result into publish')

    assert all(
        not ('direct value transfer' in report.casefold() and 'transform' in report and 'publish' in report)
        for _, report in rows
    )
    assert _bundle(rows, 'replacement', 'publish')


def test_alias_is_not_reported_as_direct_value_transfer():
    rows = _rows("""
        def export(source):
            result = transform(source)
            forwarded = result
            publish(forwarded)
    """, 'Trace transform and publish forwarded value')

    assert all(
        not ('direct value transfer' in report.casefold() and 'transform' in report and 'publish' in report)
        for _, report in rows
    )
    assert _bundle(rows, 'Unresolved', 'publish')


def test_guarded_calls_on_same_receiver_establish_sequence_only():
    rows = _rows("""
        def execute(pipeline, allowed):
            pipeline.prepare()
            if allowed:
                pipeline.publish()
    """, 'Trace pipeline prepare then publish when allowed')

    line, report = _bundle(rows, 'Conditional sequence', 'pipeline.prepare', 'pipeline.publish')
    assert {2, 3, 4} <= _anchors(line, report)
    assert 'allowed' in report
    assert 'direct value transfer' not in report.casefold()


def test_mutually_exclusive_calls_on_one_receiver_do_not_form_a_sequence():
    rows = _rows("""
        def execute(pipeline, allowed):
            if allowed:
                pipeline.prepare()
            else:
                pipeline.publish()
    """, 'Trace pipeline prepare then publish when allowed')

    assert all(
        not ('conditional sequence' in report.casefold() and 'pipeline.prepare' in report and 'pipeline.publish' in report)
        for _, report in rows
    )


def test_structural_excerpt_does_not_truncate_a_handoff_into_a_partial_claim(monkeypatch):
    monkeypatch.setattr(
        scheme_evidence, 'local_handoff_rows',
        lambda *args, **kwargs: [(17, 'Local handoff into transfer: ' + 'input_part ' * 300)],
    )

    excerpt = scheme_evidence._structural_excerpt(
        'def example():\n    pass\n', 'example.py', focus=_EvidenceScope('Explain transfer').score,
    )

    assert 'L17: Local handoff into transfer. Handoff details omitted by excerpt budget.' in excerpt
    assert 'input_part' not in excerpt


def test_unreachable_calls_do_not_create_handoffs():
    source = """def stopped():
    with scope():
        return
    value = produce()
    consume(value)

def disabled():
    if 0:
        value = produce()
        consume(value)
"""
    assert local_handoff_rows(ast.parse(source), lambda _: 1, shape=_predicate_shape) == []


def test_surviving_path_retains_early_return_condition():
    line, report = _bundle(_rows("""
        def export(blocked):
            result = produce()
            if blocked:
                return
            consume(result)
    """, 'consume produced result'), 'Direct value transfer', 'not blocked')
    assert {2, 3, 5} <= _anchors(line, report)


def test_guarded_first_call_precedes_the_consumer_after_the_join():
    line, report = _bundle(_rows("""
        def execute(pipeline, enabled):
            if enabled:
                pipeline.prepare()
            pipeline.publish()
    """, 'prepare and publish'), 'Conditional sequence', 'enabled')
    assert {2, 3, 4} <= _anchors(line, report)
    assert 'direct value transfer' not in report.casefold()


def test_known_local_guard_prevents_unreachable_sequence():
    source = """def operate(pipeline):
    allowed = True
    if allowed:
        pipeline.prepare()
    if not allowed:
        pipeline.publish()
"""
    assert local_handoff_rows(ast.parse(source), lambda _: 1, shape=_predicate_shape) == []


def test_budget_omission_does_not_admit_other_occurrence_on_same_line(monkeypatch):
    short = 'Local handoff into consume: short'
    long = 'Local handoff into consume: ' + 'large origin ' * 40
    kept = {'from_line': 2, 'to_line': 4, 'to_column': 2}

    def generate(*args, structured, **kwargs):
        structured[(4, short)] = [kept]
        structured[(4, long)] = [{'from_line': 3, 'to_line': 4, 'to_column': 40}]
        return [(4, short), (4, long)]

    monkeypatch.setattr(scheme_evidence, 'local_handoff_rows', generate)
    admitted = []
    excerpt = scheme_evidence._structural_excerpt('def f(): pass', 'entry.py', focus=lambda _: 1,
                                                  admitted_handoffs=admitted)
    assert 'Handoff details omitted by excerpt budget' in excerpt
    assert admitted == [kept]


def test_unreachable_or_unsupported_guards_do_not_produce_verified_contracts():
    from backend.app.integrations.codex.scheme_handoff_contract import make_contracts

    source = """def run(enabled):
    if False and enabled:
        x = produce()
        consume(x)
    if 1 == 2:
        x = produce()
        consume(x)
    if 8 - 8:
        x = produce()
        consume(x)

def assigned_condition():
    x = produce()
    if (x := None) or True:
        consume(x)
"""
    structured = {}
    local_handoff_rows(ast.parse(source), lambda _: 1, shape=_predicate_shape, structured=structured)
    assert make_contracts([{'id': 'code', 'local_handoffs': [
        item for group in structured.values() for item in group
    ]}]) == []
