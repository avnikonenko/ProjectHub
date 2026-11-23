from pathlib import Path
from textwrap import dedent

from backend.app.integrations.codex.scheme_workflow_sql import workflow_sql_rows


def statements(source):
    return [summary for _, summary in workflow_sql_rows(dedent(source).strip())]


def test_match_across_implicit_and_explicit_python_sql_concatenation():
    assert statements('''
        query = (
            "SELECT body "
            "FROM doc_index" + join +
            " WHERE doc_index MATCH :request AND tag='PRIVATE_VALUE'"
        )
        other = f"SELECT * FROM [more_index] WHERE more_index MATCH {parameter}"
        db.execute(text(query))
        db.execute(text(other))
        db.execute("INSERT OR REPLACE INTO saved_rows VALUES ('PRIVATE_VALUE')")
    ''') == [
        'SELECT FROM doc_index WHERE doc_index MATCH',
        'SELECT FROM more_index WHERE more_index MATCH',
        'INSERT INTO saved_rows',
    ]


def test_comments_quoted_values_and_dynamic_identifiers_do_not_supply_matches():
    assert not statements('''
        "SELECT * FROM private_docstring WHERE private_docstring MATCH :test"
        a = "SELECT * FROM docs WHERE msg='docs MATCH PRIVATE_FTS'"
        b = "SELECT * FROM docs -- docs MATCH PRIVATE_COMMENT"
        c = "SELECT * FROM docs WHERE unrelated MATCH :request"
        d = f"INSERT INTO dynamic_{table} VALUES ('PRIVATE_VALUE')"
        e = "SELECT * FROM docs WHERE :docs MATCH :request"
        db.execute(a)
        db.execute(b)
        db.execute(c)
        db.execute(d)
        db.execute(e)
    ''')


def test_unused_sql_literal_is_not_runtime_evidence():
    assert not statements('''
        HELP = "SELECT * FROM docs WHERE docs MATCH :request"
        def search(db, HELP):
            query = "SELECT * FROM stale WHERE stale MATCH :request"
            query = "SELECT * FROM other"
            db.execute(query)
            db.execute(HELP)
            query = "SELECT * FROM later WHERE later MATCH :request"
            return HELP
    ''')


def test_sql_alias_uses_the_binding_present_when_the_alias_was_assigned():
    assert statements('''
        def search(db):
            query = "SELECT * FROM original WHERE original MATCH :request"
            alias = query
            query = "SELECT * FROM replacement WHERE replacement MATCH :request"
            db.execute(alias)
    ''') == ['SELECT FROM original WHERE original MATCH']


def test_imported_and_linked_conversation_queries_match_separate_indices():
    backend = Path(__file__).resolve().parents[1] / 'backend/app'
    project_search = workflow_sql_rows((backend / 'project_search.py').read_text())
    history_search = workflow_sql_rows((backend / 'integrations/codex/history_search.py').read_text())
    assert any(row == 'SELECT FROM search_index WHERE search_index MATCH' for _, row in project_search)
    assert {'SELECT FROM search_index WHERE search_index MATCH',
            'SELECT FROM codex_history_fts WHERE codex_history_fts MATCH',
            'INSERT INTO codex_history_turns'} <= {row for _, row in history_search}
