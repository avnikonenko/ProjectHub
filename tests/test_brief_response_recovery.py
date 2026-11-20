"""Retry only invalid completed brief responses, in every Overview path."""
from copy import deepcopy
import json

import pytest

from backend.app.integrations.codex import brief
from backend.app.integrations.codex.protocol import CodexError


def reply():
    sections = {name: [] for name in brief.SECTION_NAMES}
    for section in ('overview', 'architecture'):
        sections[section].append({'text': 'The project stores data.', 'source_ids': ['s1'], 'classification': 'reported'})
    return {'sections': sections, 'project_map': {'nodes': [], 'edges': []}}


@pytest.mark.parametrize('invalid_response', ['json', 'shape', 'missing', 'length', 'sentence', 'citation', 'map'])
def test_corrective_retry_revalidates_completed_responses_only(invalid_response):
    response = reply()
    if invalid_response == 'shape':
        response['unsupported'] = []
    elif invalid_response == 'missing':
        response['sections']['architecture'] = []
    elif invalid_response == 'length':
        response['sections']['architecture'][0]['text'] = 'x' * 601
    elif invalid_response == 'sentence':
        response['sections']['overview'][0]['text'] = 'The project stores data and:'
    elif invalid_response == 'citation':
        response['sections']['overview'][0]['source_ids'] = ['outside']
    elif invalid_response == 'map':
        response['project_map']['edges'] = [{'from': 'a', 'to': 'b', 'label': 'Flow',
                                             'inferred': True, 'source_ids': ['s1']}]
    replies = ['not JSON' if invalid_response == 'json' else json.dumps(response), json.dumps(reply())]
    sources = [{'id': 's1'}]
    requests = []

    def request(prompt, schema):
        requests.append((prompt, deepcopy(schema)))
        return replies.pop(0)

    prompt = 'Return only JSON.\n{"sources":[{"id":"s1"}]}'
    sections, graph = brief.generate_validated_brief(request, prompt, brief.OUTPUT_SCHEMA, sources)
    assert (sections, graph) == (reply()['sections'], reply()['project_map'])
    assert len(requests) == 2
    assert requests[1][0].startswith('Correct the previous Overview attempt.')
    assert requests[1][0].endswith(prompt)
    assert requests[0][1] == requests[1][1]


def test_failed_correction_never_accepts_foreign_citations_or_retries_again():
    response = reply()
    response['sections']['architecture'][0]['source_ids'] = ['outside']
    replies = ['not JSON', json.dumps(response)]

    with pytest.raises(brief.BriefResponseError, match='foreign sources'):
        brief.generate_validated_brief(lambda *_: replies.pop(0), 'prompt', brief.OUTPUT_SCHEMA, [{'id': 's1'}])
    assert not replies


def test_transport_failure_is_not_a_completed_invalid_response():
    requests = []

    def request(*args):
        requests.append(args)
        raise CodexError('Connection unavailable')

    with pytest.raises(CodexError, match='Connection unavailable'):
        brief.generate_validated_brief(request, 'prompt', {}, [])
    assert len(requests) == 1
