import ast
from pathlib import Path
from textwrap import dedent

from backend.app.integrations.codex.scheme_migration_sql import compact_migration_sql, migration_sql_declarations


def declarations(source):
    return [summary for _, summary in migration_sql_declarations(ast.parse(dedent(source)))]


def test_projecthub_migration_supplies_fts5_table_and_note_triggers():
    path = Path(__file__).resolve().parents[1] / 'migrations/versions/0001_initial.py'
    source = path.read_text()
    found = migration_sql_declarations(ast.parse(source))
    assert (32, 'CREATE VIRTUAL TABLE search_index USING fts5') in found
    assert (45, 'CREATE TRIGGER notes_search_insert AFTER INSERT ON notes: INSERT INTO search_index') in found
    assert (46, 'CREATE TRIGGER notes_search_update AFTER UPDATE ON notes: '
                'DELETE FROM search_index, INSERT INTO search_index') in found
    assert (47, 'CREATE TRIGGER notes_search_delete AFTER DELETE ON notes: DELETE FROM search_index') in found
    compact = compact_migration_sql(found)
    for action, line in (('INSERT', 45), ('UPDATE', 46), ('DELETE', 47)):
        [summary] = [summary for number, summary in compact if number == line]
        assert summary.startswith(f'CREATE TRIGGERS AFTER {action}: ')
        assert f'notes_search_{action.lower()} ON notes' in summary
        assert len(summary) <= 320


def test_only_upgrade_sql_structure_is_exposed():
    observed = declarations("""
        from alembic import op as migration

        def upgrade():
            migration.execute("CREATE TABLE records (token TEXT DEFAULT 'PRIVATE_TOKEN')")
            migration.execute(
                "CREATE TRIGGER update_records AFTER UPDATE ON records "
                "WHEN old.token = 'PRIVATE_PREDICATE' BEGIN "
                "INSERT INTO changes VALUES('PRIVATE_DATA; DELETE FROM private_table'); "
                "UPDATE change_counts SET total = 1; "
                "END"
            )
            migration.execute("INSERT INTO records VALUES('PRIVATE_SEED')")

        def downgrade():
            migration.execute("CREATE TABLE down_only (id INTEGER)")
    """)
    assert observed == [
        'CREATE TABLE records',
        'CREATE TRIGGER update_records AFTER UPDATE ON records: INSERT INTO changes, UPDATE change_counts',
    ]


def test_dynamic_sql_and_rebound_operators_are_ignored():
    assert declarations("""
        from alembic import op

        def upgrade():
            for table in ["notes"]:
                table = dynamic_table()
                op.execute(f"CREATE TRIGGER {table}_search_insert AFTER INSERT ON {table} "
                           "BEGIN INSERT INTO index_rows VALUES (42); END")
            op.execute(f"CREATE TABLE known_{dynamic_name()} (id INTEGER)")
            op.execute(external_sql())
    """) == []
    assert declarations("""
        from alembic import op
        from other import op

        def upgrade():
            op.execute("CREATE TABLE misleading (id INTEGER)")
    """) == []
