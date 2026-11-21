"""Append-only snapshots of successful project generations."""
from sqlalchemy import delete, func, select

from ...models import ActiveGenerationRevision, GenerationRevision, ProjectBrief, ProjectKnowledge, ProjectQA, ProjectScheme

REVISION_KINDS = ('brief', 'qa', 'scheme', 'knowledge', 'parameters')
_CURRENT_MODELS = {'brief': ProjectBrief, 'qa': ProjectQA, 'scheme': ProjectScheme, 'knowledge': ProjectKnowledge}


def save_revision(db, project_id, kind, snapshot):
    if kind in REVISION_KINDS:
        db.execute(delete(ActiveGenerationRevision).where(
            ActiveGenerationRevision.project_id == project_id, ActiveGenerationRevision.kind == kind))
    _append(db, project_id, kind, snapshot)


def _append(db, project_id, kind, snapshot):
    db.add(GenerationRevision(project_id=project_id, kind=kind,
                              generated_at=snapshot['generated_at'], snapshot=snapshot))


def _revisions(project_id, kind, *columns):
    return select(*columns).where(GenerationRevision.project_id == project_id, GenerationRevision.kind == kind)


def _selected(project_id, kind):
    return _revisions(project_id, kind, GenerationRevision.id).join(
        ActiveGenerationRevision, (ActiveGenerationRevision.revision_id == GenerationRevision.id)
        & (ActiveGenerationRevision.project_id == project_id) & (ActiveGenerationRevision.kind == kind))


def active_parameter_revision(project_id, column):
    latest = _revisions(project_id, 'parameters', GenerationRevision.id).order_by(GenerationRevision.id.desc()).limit(1)
    return _revisions(project_id, 'parameters', column).where(
        GenerationRevision.id == func.coalesce(_selected(project_id, 'parameters').correlate(None).scalar_subquery(),
                                                latest.correlate(None).scalar_subquery()))


def active_revision_id(db, project_id, kind):
    if kind == 'parameters':
        return db.scalar(active_parameter_revision(project_id, GenerationRevision.id))
    selected = db.scalar(_selected(project_id, kind))
    if selected is not None:
        return selected
    model = _CURRENT_MODELS[kind]
    timestamp = model.generated_at if kind in ('brief', 'qa') else model.snapshot['generated_at'].as_string()
    revisions = _revisions(project_id, kind, GenerationRevision.id).where(
        GenerationRevision.generated_at == select(timestamp).where(model.project_id == project_id).scalar_subquery())
    if kind == 'knowledge':
        publication = select(model.snapshot['publication_id'].as_string()).where(model.project_id == project_id)
        revisions = revisions.where(GenerationRevision.snapshot['publication_id'].as_string() == publication.scalar_subquery())
    return db.scalar(revisions.order_by(GenerationRevision.id.desc()).limit(1))


def qa_revision_matches(db, project_id, generated_at, active_id, requested_id):
    if requested_id is not None:
        return requested_id == active_id
    # Older clients sent only a timestamp. Accept it when it identifies at
    # most one saved version, including old results without revision rows.
    matches = _revisions(project_id, 'qa', GenerationRevision.id).where(
        GenerationRevision.generated_at == generated_at).limit(2)
    return len(db.scalars(matches).all()) < 2


def list_revisions(db, project_id, kind, offset):
    # Extract only the listing metadata in SQLite. Loading whole ORM entities
    # deserializes eleven potentially large snapshots just to return ten labels.
    rows = db.execute(select(
        GenerationRevision.id, GenerationRevision.generated_at,
        GenerationRevision.snapshot['generation'].label('generation'),
    ).where(
        GenerationRevision.project_id == project_id, GenerationRevision.kind == kind,
    ).order_by(GenerationRevision.id.desc()).offset(offset).limit(11)).mappings().all()
    return {
        'results': [{'id': row.id, 'generated_at': row.generated_at,
                     'generation': row.generation} for row in rows[:10]],
        'next_offset': offset + 10 if len(rows) > 10 else None,
        'active_id': active_revision_id(db, project_id, kind),
    }


class RevisionUnavailable(ValueError):
    pass


def _valid_references(refs):
    return isinstance(refs, list) and all(
        isinstance(ref, dict) and isinstance(ref.get('source_id'), str) for ref in refs)


def activate_revision(db, row):
    """Select an existing result in the caller's locked write transaction."""
    from .brief import read_brief
    from .knowledge import knowledge_with_legacy_summary, preserve_legacy_knowledge_summary, read_knowledge
    from .qa import read_qa
    from .scheme import read_scheme, scheme_snapshot_for_display

    project_id, kind, snapshot = row.project_id, row.kind, row.snapshot
    readers = {'brief': read_brief, 'qa': read_qa, 'knowledge': read_knowledge, 'scheme': read_scheme}
    if (not isinstance(snapshot, dict) or snapshot.get('project_id', project_id) != project_id
            or snapshot.get('generated_at') != row.generated_at):
        raise RevisionUnavailable('This saved version is incomplete or belongs to a different project.')
    previous_id = active_revision_id(db, project_id, kind)
    if previous_id == row.id:
        return readers[kind](db, project_id) if kind in readers else snapshot

    if kind == 'knowledge':
        snapshot = knowledge_with_legacy_summary(db, project_id, snapshot)
        summary = snapshot.get('summary')
        state = summary.get('project_state') if isinstance(summary, dict) else None
        if (not isinstance(snapshot.get('publication_id'), str) or not snapshot['publication_id'].strip()
                or not isinstance(state, dict) or summary.get('generated_at') != row.generated_at
                or state.get('publication_id') != snapshot['publication_id']
                or summary.get('project_id', project_id) != project_id or state.get('project_id', project_id) != project_id):
            raise RevisionUnavailable('This saved Knowledge version has no matching Knowledge Summary.')
        items, sources = snapshot.get('items'), snapshot.get('sources')
        if (not isinstance(snapshot.get('extraction_version'), str) or not isinstance(sources, list)
                or not isinstance(items, list) or any(not isinstance(item, dict)
                or item.get('project_id', project_id) != project_id or not isinstance(item.get('id'), str)
                or not _valid_references(item.get('evidence'))
                or not _valid_references(item.get('context_dependencies', []))
                for item in items)
                or any(not isinstance(source, dict) or source.get('project_id', project_id) != project_id for source in sources)):
            raise RevisionUnavailable('This saved Knowledge version is missing valid records or sources.')
    if kind in ('brief', 'qa'):
        fields = {'sources': list} | ({'sections': dict, 'coverage': list} if kind == 'brief' else {'items': list})
        if any(not isinstance(snapshot.get(key), expected) for key, expected in fields.items()):
            raise RevisionUnavailable('This saved version is missing required result data.')
    if kind == 'brief':
        preserve_legacy_knowledge_summary(db, project_id)
    if kind != 'parameters':
        model = _CURRENT_MODELS[kind]
        active = db.get(model, project_id)
        current = active.snapshot if kind == 'scheme' and active is not None else readers[kind](db, project_id)
        previous = db.get(GenerationRevision, previous_id) if previous_id is not None else None
        # Preserve current results predating revisions and any later tag edits.
        if current and current.get('generated_at') and (previous is None or current != previous.snapshot):
            _append(db, project_id, kind, current)
        if active is None:
            active = model(project_id=project_id)
            db.add(active)
        if kind in ('scheme', 'knowledge'):
            active.snapshot = snapshot
        else:
            active.generated_at = row.generated_at
            active.sources = snapshot['sources']
            generation = {key: snapshot[key] for key in ('diagnostics', 'coverage') if key in snapshot}
            if kind == 'brief':
                active.sections, active.coverage = snapshot['sections'], snapshot['coverage']
                active.project_map, active.project_state = snapshot.get('project_map'), snapshot.get('project_state')
            else:
                active.items = snapshot['items']
                generation.update({key: snapshot[key] for key in ('topic_groups', 'topic_grouping') if snapshot.get(key)})
            active.generation = ({**(snapshot.get('generation') or {}), **generation}
                                 if snapshot.get('generation') is not None or generation else None)
    selection = db.get(ActiveGenerationRevision, (project_id, kind))
    if selection is None:
        db.add(ActiveGenerationRevision(project_id=project_id, kind=kind, revision_id=row.id))
    else:
        selection.revision_id = row.id
    return scheme_snapshot_for_display(snapshot) if kind == 'scheme' else snapshot
