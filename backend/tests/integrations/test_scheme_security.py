"""Independent adversarial checks; static fixtures never execute repository code."""
import json
import subprocess

from sqlalchemy.orm import Session

from backend.app.db import initialize
from backend.app.models import Note, Project
from backend.app.integrations.codex.scheme_evidence import collect_evidence


def _project(tmp_path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    subprocess.run(['git', 'init', '-q', str(repo)], check=True)
    engine, _ = initialize(tmp_path / 'data')
    with Session(engine) as db:
        db.add(Project(name='Example', repository_path=str(repo)))
        db.commit()
    return engine, repo


def test_code_declarations_do_not_copy_private_initializer_literals(tmp_path):
    engine, repo = _project(tmp_path)
    (repo / 'app.ts').write_text(
        'import source from "https://user:EXAMPLE_PRIVATE_IMPORT@example.test/module.js?token=EXAMPLE_QUERY_TOKEN";\n'
        'export const API_KEY = "EXAMPLE_PRIVATE_INITIALIZER";\n'
        'export function serve() { return API_KEY; }\n'
    )
    result = collect_evidence(engine, 1)
    assert 'EXAMPLE_PRIVATE_INITIALIZER' not in json.dumps(result)
    assert 'EXAMPLE_PRIVATE_IMPORT' not in json.dumps(result)
    assert 'EXAMPLE_QUERY_TOKEN' not in json.dumps(result)
    assert any(source.get('path') == 'app.ts' for source in result['sources'])
    engine.dispose()


def test_omitted_project_notes_are_reported_as_partial_coverage(tmp_path):
    engine, repo = _project(tmp_path)
    (repo / 'app.py').write_text('class App: pass\n')
    with Session(engine) as db:
        db.add_all([Note(project_id=1, title=f'Note {i}', content='Architecture discussion') for i in range(7)])
        db.commit()
    result = collect_evidence(engine, 1)
    assert len([source for source in result['sources'] if source['id'].startswith('note:')]) == 6
    assert result['coverage']['partial']
    assert any('note' in message.lower() or 'record' in message.lower()
               for message in result['coverage']['limitations'])
    engine.dispose()
