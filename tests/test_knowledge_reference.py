"""Reference annotation integrity; this does not measure model accuracy."""
import json
from pathlib import Path


def test_synthetic_reference_evidence_is_revision_pinned_and_separates_uncertainty():
    reference = json.loads((Path(__file__).parent / 'fixtures/knowledge/synthetic_design_reference.json').read_text())
    assert reference['annotation_status'] == 'synthetic_fixture'
    sources = {source['id']: source for source in reference['sources']}
    cases = reference['cases']
    assert len({case['id'] for case in cases}) == len(cases)
    for case in cases:
        for evidence in case['evidence']:
            assert evidence['source_id'] in sources
            assert evidence['source_revision'] == sources[evidence['source_id']]['content_sha256']
            assert evidence['role'] in ('user', 'assistant')
            assert evidence['transcript_end'] - evidence['transcript_start'] == len(evidence['excerpt'])
            assert evidence['message_end'] - evidence['message_start'] == len(evidence['excerpt'])
    agreed = [case for case in cases if case['expected']['design_status'] == 'AGREED']
    assert agreed and all(any(e['role'] == 'user' for e in case['evidence']) for case in agreed)
    skeptical = next(case for case in cases if case['id'] == 'toolkit_skepticism_not_rejection')
    assert skeptical['expected']['design_status'] != 'REJECTED'
    reader = next(case for case in cases if case['id'] == 'reader_rationale_acceptance_unverified')
    assert reader['expected']['design_status'] != 'AGREED'


def test_accepted_scope_survives_new_ambiguous_discussion(tmp_path):
    """Pipeline regression using reference excerpts, with mocked extraction—not an accuracy score."""
    from sqlalchemy.orm import Session
    from backend.app.db import initialize
    from backend.app.models import Project, Note
    from backend.app.integrations.codex.knowledge_sources import load_sources
    from backend.app.integrations.codex.knowledge import build_candidate

    reference = json.loads((Path(__file__).parent / 'fixtures/knowledge/synthetic_design_reference.json').read_text())
    case = next(c for c in reference['cases'] if c['id'] == 'scope_docking_md_first_export')
    proposal = next(e['excerpt'] for e in case['evidence'] if e['role'] == 'assistant')
    acceptance = next(e['excerpt'] for e in case['evidence'] if e['role'] == 'user')
    engine, _ = initialize(tmp_path)
    with Session(engine) as db:
        db.add(Project(name='Reference regression'))
        db.flush()
        db.add(Note(project_id=1, kind='document', title='Original excerpts', reference=reference['sources'][0]['url'],
                    content='User:\nWhich scope?\n\nChatGPT:\n'+proposal+'\n\nUser:\n'+acceptance))
        db.commit()

    class Extraction:
        calls = 0
        def generate_brief(self, prompt, schema):
            self.calls += 1
            payload = json.loads(prompt.split('\n', 1)[1])
            sources = payload['sources']
            if not any(proposal in s['text'] for s in sources):
                return json.dumps({'items': []})
            proposer = next(s for s in sources if proposal in s['text'])
            accepter = next(s for s in sources if acceptance == s['text'])
            return json.dumps({'items': [{
                'statement': case['statement'], 'category': 'DECISION', 'design_status': 'AGREED',
                'scope': 'project:framework', 'rationale': 'Acorn Runner is the first export target.',
                'alternatives': '', 'limitations': '', 'agreement_basis': 'proposal_acceptance',
                'revises_statement': '', 'conflicting_statements': [],
                'evidence': [{'source_id': proposer['id'], 'role': 'assistant', 'excerpt': proposal},
                             {'source_id': accepter['id'], 'role': 'user', 'excerpt': acceptance}],
            }]})
    manager = Extraction()
    first = build_candidate(engine, 1, manager, load_sources(engine, 1))
    assert manager.calls == 1 and len(first['items']) == 1
    with Session(engine) as db:
        db.add(Note(project_id=1, kind='document', title='New ambiguous discussion',
                    reference=reference['sources'][1]['url'],
                    content='User:\nMaybe use another scope?\n\nChatGPT:\nWe already agreed to change it.'))
        db.commit()
    second = build_candidate(engine, 1, manager, load_sources(engine, 1), previous=first)
    assert second['diagnostics']['cache_hits'] == 1
    assert manager.calls == 2
    retained = next(i for i in second['items'] if i['id'] == first['items'][0]['id'])
    assert retained['design_status'] == 'AGREED' and retained['source_state'] == 'valid'
    assert retained['rationale'] == first['items'][0]['rationale']
    assert retained['evidence'] == first['items'][0]['evidence']
    engine.dispose()
