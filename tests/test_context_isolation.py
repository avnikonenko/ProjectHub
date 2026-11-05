"""Independent database-level checks; no model calls or HTTP stubs."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from backend.app.models import Base, Project, Decision, Question, Task, CodexSession, Delivery
from backend.app.integrations.codex.manager import CodexManager, context_preview


class ContextIsolationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='projecthub-qa-')
        self.addCleanup(self.directory.cleanup)
        self.engine = create_engine('sqlite:///' + str(Path(self.directory.name) / 'test.sqlite3'))
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        with Session(self.engine) as db:
            a, b = Project(name='A'), Project(name='B')
            db.add_all([a, b]); db.flush()
            self.a, self.b = a.id, b.id
            db.add_all([
                Decision(project_id=a.id, title='Accepted A', description='Recorded choice', status='accepted'),
                Decision(project_id=a.id, title='Unaccepted idea', status='proposed'),
                Decision(project_id=b.id, title='PRIVATE OTHER PROJECT', status='accepted'),
                Question(project_id=a.id, title='Open question', status='open'),
                Question(project_id=b.id, title='PRIVATE QUESTION', status='open'),
                Task(project_id=a.id, title='Incomplete task'),
                Task(project_id=a.id, title='Completed task', status='completed'),
            ])
            db.commit()

    def test_context_selects_only_owned_relevant_records(self):
        with Session(self.engine) as db:
            preview = context_preview(db, self.a)
            self.assertNotIn('PRIVATE', preview['content'])
            self.assertNotIn('Unaccepted idea', preview['content'])
            self.assertNotIn('Completed task', preview['content'])
            data = json.loads(preview['content'])
            self.assertEqual([d['title'] for d in data['accepted_decisions']], ['Accepted A'])
            self.assertEqual([q['title'] for q in data['unresolved_questions']], ['Open question'])
            self.assertEqual([t['title'] for t in data['incomplete_tasks']], ['Incomplete task'])

    def test_context_version_is_stable_and_content_addressed(self):
        with Session(self.engine) as db:
            first, second = context_preview(db, self.a), context_preview(db, self.a)
            self.assertEqual(first, second)
            self.assertEqual(first['version'], hashlib.sha256(first['content'].encode()).hexdigest())

    def test_restart_marks_uncertain_runs_without_resubmission(self):
        with Session(self.engine) as db:
            session = CodexSession(project_id=self.a, thread_id='qa-thread', status='running')
            db.add(session); db.flush()
            delivery = Delivery(project_id=self.a, session_id=session.id, content='Review only', status='sending')
            db.add(delivery); db.commit()
            sid, did = session.id, delivery.id
        manager = CodexManager(self.engine, self.directory.name)
        self.addCleanup(manager.close)
        self.assertEqual(manager.clients, {})
        with Session(self.engine) as db:
            self.assertEqual(db.get(CodexSession, sid).status, 'interrupted')
            self.assertEqual(db.get(Delivery, did).status, 'unverified')


if __name__ == '__main__':
    unittest.main()
