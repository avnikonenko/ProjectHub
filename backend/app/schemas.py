from typing import Annotated, Literal
from copy import deepcopy
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints, create_model

Priority = Literal['low', 'medium', 'high', 'critical']
QuestionStatus = Literal['open', 'under_discussion', 'awaiting_validation', 'resolved', 'reopened']
DecisionStatus = Literal['proposed', 'accepted', 'superseded', 'rejected']
TaskStatus = Literal['backlog', 'ready', 'in_progress', 'blocked', 'completed', 'not_planned']
NOTE_CONTENT_MAX_BYTES = 16 * 1024 * 1024


def _note_content_bytes(value):
    # Count UTF-8 in chunks so rejecting a multi-byte value does not first create
    # another complete, potentially much larger byte string in memory.
    size = 0
    for start in range(0, len(value), 65536):
        size += len(value[start:start + 65536].encode('utf-8'))
        if size > NOTE_CONTENT_MAX_BYTES:
            raise ValueError('Note content exceeds 16 MiB of UTF-8 text')
    return value


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid', str_max_length=200000, str_strip_whitespace=True)


class ProjectCreate(Input):
    name: str = Field(min_length=1, max_length=200)
    description: str = ''
    goals: str = ''
    status: str = Field(default='planning', min_length=1, max_length=80)
    repository_path: str | None = None
    git_remote: str = ''
    tags: list[str] = Field(default_factory=list, max_length=50)
    architecture_mermaid: str = ''


class QuestionCreate(Input):
    title: str = Field(min_length=1, max_length=500)
    description: str = ''
    category: str = Field(default='technical', min_length=1, max_length=80)
    priority: Priority = 'medium'
    status: QuestionStatus = 'open'


class AnswerCreate(Input):
    solution: str = Field(min_length=1)
    explanation: str = ''
    advantages: str = ''
    limitations: str = ''
    evidence: str = ''
    source: str = Field(default='user', min_length=1)


class AcceptAnswer(Input):
    answer_id: int
    create_task: bool = False
    next_step: str = Field(default='', max_length=500)


class DecisionCreate(Input):
    title: str = Field(min_length=1, max_length=500)
    description: str = ''
    next_step: str = Field(default='', max_length=500)
    rationale: str = ''
    alternatives: str = ''
    evidence: str = ''
    limitations: str = ''
    status: DecisionStatus = 'proposed'
    question_id: int | None = None
    answer_id: int | None = None


class TaskCreate(Input):
    pinned: bool = False
    title: str = Field(min_length=1, max_length=500)
    description: str = ''
    priority: Priority = 'medium'
    status: TaskStatus = 'backlog'
    question_id: int | None = None
    decision_id: int | None = None
    session_id: int | None = None


class DecisionTask(Input):
    title: str | None = Field(default=None, min_length=1, max_length=500)
    description: str | None = None
    priority: Priority = 'medium'


class TaskBulkCreate(Input):
    items: list[TaskCreate] = Field(min_length=1, max_length=100)


class NoteCreate(Input):
    title: str = Field(min_length=1, max_length=500)
    # Imported documents and shared conversations can exceed the generic field
    # limit. Preserve their exact content when opened and saved in the editor.
    content: Annotated[str, StringConstraints(max_length=NOTE_CONTENT_MAX_BYTES, strip_whitespace=False),
                       AfterValidator(_note_content_bytes)] = ''
    kind: Literal['note', 'ai_note', 'assumption', 'technical_assumption', 'scientific_assumption', 'evidence', 'limitation', 'document', 'proposal'] = 'note'
    reference: str = ''


class ChatGPTShareImport(Input):
    url: str = Field(min_length=1, max_length=2048)


def patch_model(name, model):
    # Keep field validators while allowing omission; explicit null is checked at route level.
    fields = {}
    for key, field in model.model_fields.items():
        copied = deepcopy(field)
        copied.default = None
        copied.default_factory = None
        fields[key] = (field.annotation, copied)
    return create_model(name, __base__=Input, **fields)

ProjectPatch = patch_model('ProjectPatch', ProjectCreate)
QuestionPatch = patch_model('QuestionPatch', QuestionCreate)
DecisionPatch = patch_model('DecisionPatch', DecisionCreate)
TaskPatch = patch_model('TaskPatch', TaskCreate)
NotePatch = patch_model('NotePatch', NoteCreate)
