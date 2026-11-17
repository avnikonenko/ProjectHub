"""Execute the real initial migration SQL against SQLite.

The Alembic operation adapter is mocked; the DDL, foreign keys, triggers and
FTS5 queries are real. This does not replace the full Alembic/API tests.
"""
import importlib.util
from pathlib import Path
import sqlite3
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch


class MigrationSQLTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.db.execute('PRAGMA foreign_keys=ON')
        adapter = ModuleType('alembic')
        adapter.op = SimpleNamespace(execute=self.db.execute)
        path = Path(__file__).resolve().parents[1] / 'migrations/versions/0001_initial.py'
        spec = importlib.util.spec_from_file_location('initial_migration_under_test', path)
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'alembic': adapter}):
            spec.loader.exec_module(module)
            module.upgrade()
        for pid in (1, 2):
            self.db.execute(
                'INSERT INTO projects VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?)',
                (pid, f'Project {pid}', 'description', 'goals', 'planning', '', '[]', '', 'now', 'now'))

    def note(self, nid, pid, title, content):
        self.db.execute('INSERT INTO notes VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                        (nid, pid, title, content, 'note', '', 'now', 'now'))

    def search(self, term, pid):
        return self.db.execute(
            'SELECT entity_id FROM search_index WHERE search_index MATCH ? AND project_id=?',
            (term, pid)).fetchall()

    def test_fts_isolation_and_updates(self):
        self.note(1, 1, 'Evidence', 'ligand benchmark')
        self.note(2, 2, 'Other evidence', 'ligand control')
        self.assertEqual(self.search('ligand', 1), [(1,)])
        self.assertEqual(self.search('ligand', 2), [(2,)])
        self.db.execute('UPDATE notes SET content=? WHERE id=1', ('protonation validation',))
        self.assertEqual(self.search('ligand', 1), [])
        self.assertEqual(self.search('protonation', 1), [(1,)])
        self.db.execute('DELETE FROM notes WHERE id=1')
        self.assertEqual(self.search('protonation', 1), [])

    def test_foreign_key_rejects_missing_project(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.note(1, 999, 'Missing owner', 'content')

    def test_project_search_trigger_tracks_goals(self):
        self.db.execute('UPDATE projects SET goals=? WHERE id=1', ('Reproducible spectroscopy',))
        self.assertEqual(self.search('spectroscopy', 1), [(1,)])
        self.assertEqual(self.search('spectroscopy', 2), [])

    def test_all_core_and_codex_tables_created(self):
        names = {row[0] for row in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({'projects', 'questions', 'answers', 'decisions', 'tasks', 'notes',
                         'audit_events', 'codex_sessions', 'context_packages', 'deliveries',
                         'codex_events', 'search_index'}.issubset(names))
        self.assertEqual(self.db.execute('PRAGMA foreign_key_check').fetchall(), [])


if __name__ == '__main__':
    unittest.main()
