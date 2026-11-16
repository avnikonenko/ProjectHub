"""Explicit navigation metadata generation for one exact saved result.

Preview tags are derived metadata updated on the existing preview revision; its
publication identity and paired brief remain untouched.
"""
from contextlib import ExitStack
from copy import deepcopy
import os
import json

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ...config import ConfigError, ai_worker_settings
from ...generation_profiles import get_generation_profile
from ...models import GenerationRevision, Project, ProjectKnowledge, ProjectQA
from . import progress
from .brief import BriefBusy, generation_lock
from .knowledge_previews import PREVIEW_KIND, read_knowledge_preview
from .knowledge_profiles import configure_profile_manager, use_knowledge_profile
from .protocol import CodexError
from .qa import read_qa
from .topic_groups import group_records, FIELDS, MAX_BATCH_CHARS, MAX_BATCH_ITEMS
from .manager import tag_generation_context


class TagGenerationConflict(CodexError):
    """The selected saved result changed before tags could be committed."""


def _selected(db, project_id, target, publication_id, generated_at):
    if db.get(Project, project_id) is None:
        raise CodexError('Project not found.')
    if target == 'qa':
        row = db.get(ProjectQA, project_id)
        value = read_qa(db, project_id)
        if value is None or value['generated_at'] != generated_at:
            raise TagGenerationConflict('The selected Q&A changed. Refresh before generating tags.')
        return row, value
    if target == 'knowledge_preview':
        value = read_knowledge_preview(db, project_id)
        row = db.scalar(select(GenerationRevision).where(
            GenerationRevision.project_id == project_id, GenerationRevision.kind == PREVIEW_KIND,
        ).order_by(GenerationRevision.id.desc()).limit(1))
        value = value['knowledge'] if value else None
    else:
        row = db.get(ProjectKnowledge, project_id)
        value = deepcopy(row.snapshot) if row else None
    if value is None or value.get('publication_id') != publication_id or value.get('project_id') != project_id:
        raise TagGenerationConflict('The selected Knowledge result changed. Refresh before generating tags.')
    return row, value


def generate_tags(engine, project_id, manager, *, target, publication_id=None, generated_at=None):
    if target not in ('knowledge', 'knowledge_preview', 'qa'):
        raise CodexError('Unknown tag generation target.')
    if not generation_lock.acquire(blocking=False):
        raise BriefBusy('Another AI generation is running.')
    try:
        progress.begin(project_id, 'tags')
        with ExitStack() as scope:
            try:
                settings = ai_worker_settings()
                profile = get_generation_profile(settings['generation_profile'])
            except (ConfigError, ValueError) as exc:
                raise CodexError('Could not read the tag generation profile and worker settings.') from exc
            scope.enter_context(use_knowledge_profile(profile, settings['extraction_workers']))
            manager, _ = configure_profile_manager(manager, profile)
            session = getattr(manager, 'generation_session', None)
            if callable(session):
                scope.enter_context(session())
            with Session(engine) as db:
                row, previous = _selected(db, project_id, target, publication_id, generated_at)
                # Include the complete paired preview in the concurrency token.
                token = deepcopy(row.snapshot if target != 'qa' else previous)
                row_id = row.id if target == 'knowledge_preview' else None
            records = previous.get('items', [])
            if not isinstance(records, list) or any(not isinstance(item, dict) for item in records):
                raise CodexError('The saved result has invalid tag records. Previous tags are preserved.')
            if target == 'qa':
                records = [{
                    'id': item['id'], 'category': 'Q&A', 'design_status': 'UNKNOWN', 'scope': 'Project',
                    'statement': '\n'.join([item['question'], item['answer'],
                                            *[variant['answer'] for variant in item.get('variants', [])]]),
                } for item in records]
            progress.update(project_id, stage='Generating tags for saved result')
            optimized = os.getenv('PROJECTHUB_COMPACT_TAGS', '1') == '1'
            compact_chars = sum(len(json.dumps({key: item[key] for key in FIELDS if key in item},
                                               ensure_ascii=False, separators=(',', ':'))) + 1
                                for item in records)
            # A small result can build its catalog and assignments in one call.
            use_catalog = optimized and (len(records) > MAX_BATCH_ITEMS or compact_chars > MAX_BATCH_CHARS)
            grouping_options = {'compact_assignment': True} if use_catalog else {}
            if optimized:
                scope.enter_context(tag_generation_context())
            grouping = group_records(engine, project_id, manager, records, previous=previous, **grouping_options)
            progress.check_cancelled()

            def save():
                with Session(engine) as db:
                    db.execute(text('BEGIN IMMEDIATE'))
                    row, current = _selected(db, project_id, target, publication_id, generated_at)
                    current_token = row.snapshot if target != 'qa' else current
                    if current_token != token or (row_id is not None and row.id != row_id):
                        raise TagGenerationConflict('The selected saved result changed during tag generation. Previous tags are preserved; retry.')
                    if target == 'qa':
                        row.generation = {**(row.generation or {}), **deepcopy(grouping)}
                    elif target == 'knowledge_preview':
                        snapshot = deepcopy(row.snapshot)
                        snapshot['result']['knowledge'].update(deepcopy(grouping))
                        row.snapshot = snapshot
                    else:
                        row.snapshot = {**deepcopy(row.snapshot), **deepcopy(grouping)}
                    db.commit()
                return {'target': target, **grouping, **(
                    {'generated_at': generated_at} if target == 'qa' else {'publication_id': publication_id})}

            return progress.complete(project_id, save)
    except Exception as exc:
        progress.finish(project_id, error=str(exc) if isinstance(exc, CodexError)
                        else 'Tag generation failed. Previous saved tags are preserved.')
        raise
    finally:
        generation_lock.release()
