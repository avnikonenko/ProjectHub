"""Persistent knowledge records. Provenance is retained in append-only audit events."""
from datetime import datetime, timezone
from sqlalchemy import ForeignKey, String, Text, JSON, Integer
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow():
    return datetime.now(timezone.utc).isoformat()


class Base(DeclarativeBase):
    pass


class Stamp:
    created_at: Mapped[str] = mapped_column(String, default=utcnow)
    updated_at: Mapped[str] = mapped_column(String, default=utcnow, onupdate=utcnow)


class Project(Stamp, Base):
    __tablename__ = 'projects'
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    codex_account: Mapped[str | None] = mapped_column(String(200), nullable=True)
    description: Mapped[str] = mapped_column(Text, default='')
    goals: Mapped[str] = mapped_column(Text, default='')
    status: Mapped[str] = mapped_column(String, default='planning')
    repository_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    git_remote: Mapped[str] = mapped_column(Text, default='')
    tags: Mapped[list] = mapped_column(JSON, default=list)
    architecture_mermaid: Mapped[str] = mapped_column(Text, default='')


class Scoped(Stamp):
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey('projects.id'), index=True)


class Question(Scoped, Base):
    __tablename__ = 'questions'
    title: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, default='')
    category: Mapped[str] = mapped_column(String, default='technical')
    priority: Mapped[str] = mapped_column(String, default='medium')
    status: Mapped[str] = mapped_column(String, default='open')
    accepted_answer_id: Mapped[int | None] = mapped_column(ForeignKey('answers.id'), nullable=True)


class Answer(Scoped, Base):
    __tablename__ = 'answers'
    question_id: Mapped[int] = mapped_column(ForeignKey('questions.id'), index=True)
    solution: Mapped[str] = mapped_column(Text)
    explanation: Mapped[str] = mapped_column(Text, default='')
    advantages: Mapped[str] = mapped_column(Text, default='')
    limitations: Mapped[str] = mapped_column(Text, default='')
    evidence: Mapped[str] = mapped_column(Text, default='')
    source: Mapped[str] = mapped_column(Text, default='user')
    status: Mapped[str] = mapped_column(String, default='proposed')


class Decision(Scoped, Base):
    __tablename__ = 'decisions'
    title: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, default='')
    next_step: Mapped[str] = mapped_column(Text, default='')
    rationale: Mapped[str] = mapped_column(Text, default='')
    alternatives: Mapped[str] = mapped_column(Text, default='')
    evidence: Mapped[str] = mapped_column(Text, default='')
    limitations: Mapped[str] = mapped_column(Text, default='')
    status: Mapped[str] = mapped_column(String, default='proposed')
    question_id: Mapped[int | None] = mapped_column(ForeignKey('questions.id'), nullable=True)
    answer_id: Mapped[int | None] = mapped_column(ForeignKey('answers.id'), nullable=True)


class Task(Scoped, Base):
    __tablename__ = 'tasks'
    title: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, default='')
    priority: Mapped[str] = mapped_column(String, default='medium')
    status: Mapped[str] = mapped_column(String, default='backlog')
    question_id: Mapped[int | None] = mapped_column(ForeignKey('questions.id'), nullable=True)
    decision_id: Mapped[int | None] = mapped_column(ForeignKey('decisions.id'), nullable=True)
    session_id: Mapped[int | None] = mapped_column(ForeignKey('codex_sessions.id'), nullable=True)


class Note(Scoped, Base):
    __tablename__ = 'notes'
    title: Mapped[str] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text, default='')
    kind: Mapped[str] = mapped_column(String, default='note')
    reference: Mapped[str] = mapped_column(Text, default='')


class AuditEvent(Base):
    __tablename__ = 'audit_events'
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey('projects.id'), index=True)
    entity_type: Mapped[str] = mapped_column(String)
    entity_id: Mapped[int] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String)
    before: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    after: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[str] = mapped_column(String, default=utcnow)


class CodexSession(Scoped, Base):
    __tablename__ = 'codex_sessions'
    codex_account: Mapped[str | None] = mapped_column(String(200), nullable=True)
    thread_id: Mapped[str] = mapped_column(String, unique=True)
    title: Mapped[str] = mapped_column(Text, default='Codex discussion')
    status: Mapped[str] = mapped_column(String, default='idle')
    last_error: Mapped[str] = mapped_column(Text, default='')


class ContextPackage(Scoped, Base):
    __tablename__ = 'context_packages'
    session_id: Mapped[int | None] = mapped_column(ForeignKey('codex_sessions.id'), nullable=True)
    content: Mapped[str] = mapped_column(Text)
    version: Mapped[str] = mapped_column(String)


class Delivery(Scoped, Base):
    __tablename__ = 'deliveries'
    session_id: Mapped[int] = mapped_column(ForeignKey('codex_sessions.id'))
    decision_id: Mapped[int | None] = mapped_column(ForeignKey('decisions.id'), nullable=True)
    context_id: Mapped[int | None] = mapped_column(ForeignKey('context_packages.id'), nullable=True)
    content: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String, default='draft')
    error: Mapped[str] = mapped_column(Text, default='')
    turn_id: Mapped[str | None] = mapped_column(String, nullable=True)


class CodexEvent(Base):
    __tablename__ = 'codex_events'
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey('projects.id'), index=True)
    session_id: Mapped[int] = mapped_column(ForeignKey('codex_sessions.id'), index=True)
    method: Mapped[str] = mapped_column(String)
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[str] = mapped_column(String, default=utcnow)


class ProjectBrief(Base):
    __tablename__ = 'project_briefs'
    project_id: Mapped[int] = mapped_column(ForeignKey('projects.id'), primary_key=True)
    generated_at: Mapped[str] = mapped_column(String)
    sections: Mapped[dict] = mapped_column(JSON)
    sources: Mapped[list] = mapped_column(JSON)
    coverage: Mapped[list] = mapped_column(JSON)
    project_map: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class ProjectHistoryBatch(Base):
    __tablename__ = 'project_history_batches'
    project_id: Mapped[int] = mapped_column(ForeignKey('projects.id'), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64), primary_key=True)
    evidence: Mapped[list] = mapped_column(JSON)


class ProjectQA(Base):
    __tablename__ = 'project_qa'
    project_id: Mapped[int] = mapped_column(ForeignKey('projects.id'), primary_key=True)
    generated_at: Mapped[str] = mapped_column(String)
    items: Mapped[list] = mapped_column(JSON)
    sources: Mapped[list] = mapped_column(JSON)
