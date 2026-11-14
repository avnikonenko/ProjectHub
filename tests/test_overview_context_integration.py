"""Later original context survives bounded synthesis and citation restoration."""
from copy import deepcopy
import json

from backend.app.integrations.codex import knowledge_brief as module
from tests.test_synthesis_capacity import Capture, source_views


def test_bounded_synthesis_includes_later_completion_and_restores_original_citation(monkeypatch):
    sources = source_views(monkeypatch, count=600, statement_chars=450)
    report = {
        'id': 'original:completion', 'conversation_id': 'original:discussion',
        'turn_id': 'turn:one', 'message_order': 2, 'order': 2, 'role': 'assistant',
        'text': 'This joined text is not an authoritative original quotation.',
        'excerpts': ['Implemented on the development branch.',
                     'Validation: 790 passed, 22 skipped.',
                     'Next remains a small autonomous recovery pilot.'],
    }
    completion = deepcopy(sources[0])
    completion['id'] = 'knowledge:completion'
    completion['item']['evidence'] = [{
        'source_id': report['id'], 'role': report['role'],
        'conversation_id': report['conversation_id'], 'order': report['order'],
        'excerpt': report['excerpts'][0]}]
    sources.append(completion)
    before = deepcopy(sources)
    candidate = {'coverage': {}, 'sources': [
        {'id': 'original:source', 'conversation_id': 'original:discussion',
         'turn_id': 'turn:one', 'message_order': 1, 'order': 1, 'role': 'assistant',
         'text': 'An exact original proposal.', 'excerpts': ['An exact original proposal.']}, report]}
    candidate_before = deepcopy(candidate)

    class CiteCompletion(Capture):
        def generate_brief(self, prompt, schema):
            response = json.loads(super().generate_brief(prompt, schema))
            context = [s for s in self.payload['sources'] if s.get('kind') == 'original_conversation_context']
            assert len(context) == 1
            assert context[0]['excerpts'] == report['excerpts']
            assert report['text'] not in json.dumps(context)
            response['sections']['next_steps'] = [{
                'text': 'The assistant reports implementation and regression validation complete; a live pilot remains.',
                'classification': 'reported', 'source_ids': [context[0]['id']]}]
            return json.dumps(response)

    manager, diagnostics = CiteCompletion(), {}
    sections, _, cited = module._synthesize(manager, candidate, {'project': {}}, {},
                                          selection_diagnostics=diagnostics)
    assert diagnostics['selected_context_sources'] == 1
    assert manager.encoded_size <= module.MAX_SYNTHESIS_CHARS
    assert sections['next_steps'][0]['source_ids'] == ['original:completion']
    assert any(source['id'] == 'original:completion' for source in cited)
    assert sources == before and candidate == candidate_before
