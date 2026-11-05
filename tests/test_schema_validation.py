"""Validate create/PATCH input invariants independently of HTTP dependencies."""
import unittest
from pydantic import ValidationError
from backend.app.schemas import ProjectCreate, ProjectPatch, QuestionPatch, TaskPatch


class SchemaValidationTests(unittest.TestCase):
    def test_nonblank_name_and_unknown_fields_rejected(self):
        for payload in ({'name': '   '}, {'name': 'Valid', 'surprise': True}):
            with self.assertRaises(ValidationError):
                ProjectCreate(**payload)

    def test_patch_preserves_size_constraints(self):
        for payload in ({'name': 'x' * 201}, {'tags': ['x'] * 51}):
            with self.assertRaises(ValidationError):
                ProjectPatch(**payload)
        self.assertEqual(ProjectPatch(description='Edited').model_dump(exclude_unset=True),
                         {'description': 'Edited'})

    def test_patch_preserves_enum_and_title_constraints(self):
        for cls, payload in ((QuestionPatch, {'priority': 'invalid'}),
                             (QuestionPatch, {'title': ' ' * 3}),
                             (TaskPatch, {'status': 'silently_done'})):
            with self.assertRaises(ValidationError):
                cls(**payload)

    def test_optional_repository_can_be_cleared(self):
        self.assertEqual(ProjectPatch(repository_path=None).model_dump(exclude_unset=True),
                         {'repository_path': None})


if __name__ == '__main__':
    unittest.main()
