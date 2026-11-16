"""Pair saved assistant responses with recorded review requests conservatively."""
from sqlalchemy import select

from ...models import CodexEvent, Delivery

_REQUEST_SEPARATOR = '\nEND PROJECT REFERENCE DATA\nUSER REQUEST:\n'


def _turn_id(event):
    payload = event.payload if isinstance(event.payload, dict) else {}
    turn = payload.get('turn')
    value = turn.get('id') if isinstance(turn, dict) else None
    return value if isinstance(value, str) and value.strip() else None


def response_question(db, project_id, session_id, event):
    """Return question metadata only for a unique, same-session review delivery.

    New records retain the protocol turn ID. Legacy records require matching
    adjacent start/completion boundaries; timestamps alone never pair requests.
    """
    if event.project_id != project_id or event.session_id != session_id:
        return {}
    payload = event.payload if isinstance(event.payload, dict) else {}
    item = payload.get('item')
    if (event.method != 'item/completed' or not isinstance(item, dict)
            or item.get('type') != 'agentMessage' or not isinstance(item.get('text'), str)):
        return {}
    turn_id = payload.get('turnId')
    if 'turnId' in payload:
        if not isinstance(turn_id, str) or not turn_id.strip():
            return {}
    else:
        boundaries = select(CodexEvent).where(
            CodexEvent.project_id == project_id, CodexEvent.session_id == session_id,
            CodexEvent.method.in_(('turn/started', 'turn/completed')),
        )
        before = db.scalar(boundaries.where(CodexEvent.id < event.id)
                           .order_by(CodexEvent.id.desc()).limit(1))
        after = db.scalar(boundaries.where(CodexEvent.id > event.id)
                          .order_by(CodexEvent.id).limit(1))
        if (before is None or after is None or before.method != 'turn/started'
                or after.method != 'turn/completed' or not _turn_id(before)
                or _turn_id(before) != _turn_id(after)):
            return {}
        turn_id = _turn_id(before)
    deliveries = list(db.scalars(select(Delivery).where(
        Delivery.project_id == project_id, Delivery.session_id == session_id,
        Delivery.decision_id.is_(None), Delivery.turn_id == turn_id,
    ).order_by(Delivery.id).limit(2)))
    if len(deliveries) != 1:
        return {}
    delivery = deliveries[0]
    if not isinstance(delivery.content, str):
        return {}
    _, separator, question = delivery.content.partition(_REQUEST_SEPARATOR)
    if not separator:
        if ('PROJECT REFERENCE DATA' in delivery.content
                or delivery.content.startswith('Discuss this request in read-only mode.')):
            return {}
        question = delivery.content
    if not question.strip():
        return {}
    result = {'question': question}
    if isinstance(delivery.created_at, str) and delivery.created_at:
        result['question_created_at'] = delivery.created_at
    return result
