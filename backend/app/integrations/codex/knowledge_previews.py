"""Recovery of successful unpublished previews through existing revisions."""
from copy import deepcopy

from sqlalchemy import select

from ...models import GenerationRevision, Project
from .protocol import CodexError
from .revisions import save_revision

PREVIEW_KIND = 'knowledge_preview'


def _validate_result(result, project_id):
    if not isinstance(result, dict):
        raise CodexError('Saved knowledge preview has invalid data.')
    required = {'published', 'publication_id', 'knowledge', 'brief', 'baseline_brief', 'diagnostics'}
    if not required.issubset(result) or result['published'] is not False:
        raise CodexError('Saved knowledge preview has invalid data.')
    candidate, brief = result['knowledge'], result['brief']
    if not isinstance(candidate, dict) or not isinstance(brief, dict):
        raise CodexError('Saved knowledge preview has invalid data.')
    state = brief.get('project_state')
    identity, timestamp = result['publication_id'], brief.get('generated_at')
    if (candidate.get('project_id') != project_id or not isinstance(state, dict)
            or not isinstance(identity, str) or not identity.strip()
            or candidate.get('publication_id') != identity or state.get('publication_id') != identity
            or not isinstance(timestamp, str) or not timestamp.strip() or candidate.get('generated_at') != timestamp):
        raise CodexError('Saved knowledge preview identities do not match this project.')
    return timestamp


def stage_knowledge_preview(db, project_id, result):
    """Stage one complete result; the caller guards and commits its transaction."""
    if db.get(Project, project_id) is None:
        raise CodexError('Project not found.')
    timestamp = _validate_result(result, project_id)
    save_revision(db, project_id, PREVIEW_KIND, {'generated_at': timestamp, 'result': deepcopy(result)})


def read_knowledge_preview(db, project_id):
    if db.get(Project, project_id) is None:
        raise CodexError('Project not found.')
    row = db.scalar(select(GenerationRevision).where(
        GenerationRevision.project_id == project_id, GenerationRevision.kind == PREVIEW_KIND,
    ).order_by(GenerationRevision.id.desc()).limit(1))
    if row is None:
        return None
    snapshot = row.snapshot
    if not isinstance(snapshot, dict):
        raise CodexError('Saved knowledge preview has invalid data.')
    result = snapshot.get('result')
    timestamp = _validate_result(result, project_id)
    if snapshot.get('generated_at') != timestamp or row.generated_at != timestamp:
        raise CodexError('Saved knowledge preview timestamps do not match.')
    return deepcopy(result)
