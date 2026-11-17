"""Companions preserve literal later context, not inferred progress or answers."""
from copy import deepcopy

import pytest

from backend.app.integrations.codex.overview_context import build_overview_companions


def original(identifier, message, role='assistant', *, turn='turn-a', conversation='session-a',
             order=None, excerpts=None):
    return {'id': identifier, 'role': role, 'turn_id': turn, 'conversation_id': conversation,
            'order': message if order is None else order, 'message_order': message,
            'text': 'Joined display text must never become a new quotation.',
            'excerpts': ['Exact saved excerpt.'] if excerpts is None else excerpts}


def knowledge(identifier, *refs):
    return {'id': identifier, 'item': {'statement': 'An interpreted claim.',
            'source_state': 'valid', 'evidence': [{'source_id': ref, 'order': 9999} for ref in refs]}}


def build_with_active_support(candidate, sources):
    # An additional active item establishes that saved completion excerpts are
    # current evidence; merely retaining them in candidate.sources is not enough.
    support = knowledge('knowledge:accessible-source-support',
                        *(source['id'] for source in candidate['sources']))
    return build_overview_companions(candidate, [*sources, support])


def test_later_reply_uses_saved_excerpts_and_keeps_original_citation_identity():
    candidate = {'sources': [original('opaque-question', 1, 'user'),
                             original('opaque-interim', 2),
                             original('opaque-final', 4, excerpts=['Implemented both.', '790 passed.'])]}
    sources = [knowledge('knowledge:question', 'opaque-question')]
    before = deepcopy((candidate, sources))
    companions, groups = build_with_active_support(candidate, sources)
    context, = companions['knowledge:question']
    assert context == {'id': 'overview-context:opaque-final', 'kind': 'original_conversation_context',
                       'source_id': 'opaque-final', 'turn_id': 'turn-a',
                       'role': 'assistant', 'conversation_id': 'session-a', 'order': 4,
                       'message_order': 4, 'excerpts': ['Implemented both.', '790 passed.']}
    assert 'text' not in context and 'item' not in context
    assert groups[context['id']] == [{**candidate['sources'][-1], 'classification': 'reported'}]
    assert (candidate, sources) == before


def test_every_latest_message_fragment_is_preserved_except_exact_already_cited_source():
    candidate = {'sources': [original('question', 1, 'user'),
                             original('last-fragment-b', 4, order=5, excerpts=['Second excerpt.']),
                             original('last-fragment-a', 4, order=4, excerpts=['First excerpt.'])]}
    sources = [knowledge('knowledge:one', 'question', 'last-fragment-a'),
               knowledge('knowledge:two', 'question'), knowledge('knowledge:latest-only', 'last-fragment-a')]
    companions, groups = build_with_active_support(candidate, sources)
    assert [record['id'] for record in companions['knowledge:one']] == ['overview-context:last-fragment-b']
    assert [record['id'] for record in companions['knowledge:two']] == [
        'overview-context:last-fragment-a', 'overview-context:last-fragment-b']
    assert 'knowledge:latest-only' not in companions
    assert len(groups) == 2


def test_each_evidenced_turn_has_its_own_companion_without_cross_session_ordering():
    candidate = {'sources': [original('a-question', 1, 'user'), original('a-answer', 4),
                             original('b-question', 10, 'user', turn='turn-b'),
                             original('b-answer', 11, turn='turn-b'),
                             original('foreign-answer', 99, conversation='other-session')]}
    sources = [knowledge('knowledge:both', 'a-question', 'b-question')]
    companions, _ = build_with_active_support(candidate, sources)
    assert {record['id'] for record in companions['knowledge:both']} == {
        'overview-context:a-answer', 'overview-context:b-answer'}


@pytest.mark.parametrize('field,value', [('message_order', True), ('message_order', '4'),
                                       ('message_order', None), ('order', False),
                                       ('turn_id', None), ('conversation_id', None)])
def test_unreliable_original_order_does_not_create_companions(field, value):
    question, answer = original('question', 1, 'user'), original('answer', 4)
    answer[field] = value
    assert build_with_active_support({'sources': [question, answer]},
                                    [knowledge('knowledge:one', 'question')]) == ({}, {})


@pytest.mark.parametrize('excerpts', [[], [''], ['   '], [None], 'not a list'])
def test_unusable_latest_excerpts_do_not_fall_back_to_older_reply(excerpts):
    candidate = {'sources': [original('question', 1, 'user'), original('interim', 2),
                             original('latest', 4, excerpts=excerpts)]}
    assert build_with_active_support(candidate, [knowledge('knowledge:one', 'question')]) == ({}, {})


def test_joined_text_is_not_used_when_saved_excerpts_are_missing():
    answer = original('latest', 4)
    del answer['excerpts']
    assert build_with_active_support({'sources': [original('question', 1, 'user'), answer]},
                                    [knowledge('knowledge:one', 'question')]) == ({}, {})


def test_source_id_shape_and_item_chronology_cannot_invent_companions():
    candidate = {'sources': [original('codex:99:turn:fake:m:1:p:1', 5, 'user'),
                             original('codex:99:turn:fake:m:99:p:1', 4)]}
    sources = [knowledge('knowledge:one', 'codex:99:turn:fake:m:1:p:1'),
               {'id': 'project:metadata', 'text': 'Metadata has no evidence.'}]
    assert build_with_active_support(candidate, sources) == ({}, {})


def test_later_unknown_message_order_prevents_falsely_treating_interim_as_latest():
    unknown = original('unknown-position', 4)
    unknown['message_order'] = None
    candidate = {'sources': [original('question', 1, 'user'), original('interim', 2), unknown]}
    assert build_with_active_support(candidate, [knowledge('knowledge:one', 'question')]) == ({}, {})


@pytest.mark.parametrize('state', ['unavailable', 'excluded', 'stale', 'unknown'])
def test_historical_latest_reply_is_not_used_or_replaced_with_interim(state):
    candidate = {'sources': [original('question', 1, 'user'), original('interim', 2), original('latest', 4)]}
    report = knowledge('knowledge:report', 'latest')
    report['item']['source_state'] = state
    sources = [knowledge('knowledge:question', 'question'), knowledge('knowledge:interim', 'interim'), report]
    assert build_overview_companions(candidate, sources) == ({}, {})


@pytest.mark.parametrize('field,value', [('superseded_by', ['knowledge:replacement']),
                                      ('reconciliation_state', 'unresolved')])
def test_superseded_or_unreconciled_latest_reply_does_not_supply_context(field, value):
    candidate = {'sources': [original('question', 1, 'user'), original('latest', 4)]}
    report = knowledge('knowledge:report', 'latest')
    report['item'][field] = value
    assert build_overview_companions(candidate, [knowledge('knowledge:question', 'question'), report]) == ({}, {})


def test_saved_item_revision_must_match_original_when_available():
    answer = original('latest', 4)
    answer['revision'] = 'current-revision'
    report = knowledge('knowledge:report', 'latest')
    candidate = {'sources': [original('question', 1, 'user'), answer], 'items': [
        {'id': report['id'], **report['item'], 'evidence': [
            {'source_id': 'latest', 'revision': 'old-revision'}]}]}
    sources = [knowledge('knowledge:question', 'question'), report]
    assert build_overview_companions(candidate, sources) == ({}, {})
    candidate['items'][0]['evidence'][0]['revision'] = 'current-revision'
    companions, _ = build_overview_companions(candidate, sources)
    assert companions['knowledge:question'][0]['id'] == 'overview-context:latest'


def test_one_unavailable_fragment_prevents_incomplete_latest_message_context():
    candidate = {'sources': [original('question', 1, 'user'), original('latest-a', 4, order=4),
                             original('latest-b', 4, order=5)]}
    sources = [knowledge('knowledge:question', 'question'), knowledge('knowledge:report', 'latest-a')]
    assert build_overview_companions(candidate, sources) == ({}, {})
