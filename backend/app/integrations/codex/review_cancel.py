"""Cancel a specific review request without interrupting another session turn."""
from sqlalchemy import select

from ...models import CodexEvent, CodexSession, Delivery
from ...services import audit, scoped, serialize
from .protocol import CodexError

_TERMINAL = frozenset(('cancelled', 'completed', 'failed', 'interrupted'))


def cancel_review(db, project_id, delivery_id, manager):
    # send() holds this same lock while deciding whether to queue or submit.
    # Refresh after acquiring it so a completed send cannot be cancelled as a
    # stale queued request, and a queued cancellation cannot race an auto-retry.
    with manager.lock:
        db.expire_all()
        delivery = scoped(db, Delivery, project_id, delivery_id)
        session = scoped(db, CodexSession, project_id, delivery.session_id)
        if delivery.decision_id is not None:
            raise CodexError('Only review requests can be cancelled here.')
        if delivery.status in _TERMINAL:
            return {**serialize(delivery), 'cancel_requested': False}
        if delivery.status in ('draft', 'queued'):
            before = serialize(delivery)
            delivery.status, delivery.error = 'cancelled', ''
            audit(db, project_id, delivery, 'review_cancelled', before)
            db.commit()
            return {**serialize(delivery), 'cancel_requested': False}
        if delivery.status != 'sent' or not delivery.turn_id:
            raise CodexError('The review delivery is not confirmed. Inspect its request history before cancelling.')
        if not session.is_linked or session.status != 'running':
            raise CodexError('This review is no longer the active Codex turn.')
        # interrupt() resolves the latest known turn. Check that it is exactly
        # the requested delivery; never interrupt a newer review or decision.
        latest = db.scalar(select(Delivery).where(
            Delivery.project_id == project_id, Delivery.session_id == session.id,
            Delivery.turn_id.is_not(None),
        ).order_by(Delivery.id.desc()))
        if latest is None or latest.id != delivery.id:
            raise CodexError('A different Codex turn is active; this review was not interrupted.')
        latest_event = db.scalar(select(CodexEvent).where(
            CodexEvent.project_id == project_id, CodexEvent.session_id == session.id,
            CodexEvent.method.in_(('turn/started', 'turn/completed')),
        ).order_by(CodexEvent.id.desc()))
        if latest_event is not None:
            payload = latest_event.payload if isinstance(latest_event.payload, dict) else {}
            observed = payload.get('turn', {})
            if (not isinstance(observed, dict) or observed.get('id') != delivery.turn_id
                    or latest_event.method == 'turn/completed'):
                raise CodexError('A different or completed Codex turn was observed; no interrupt was sent.')
        result = manager.interrupt(session)
        if not isinstance(result, dict) or result.get('status') != 'interrupt_requested':
            raise CodexError('Codex did not confirm the interrupt request. Check the review status before retrying.')
        # Completion can arrive while Codex acknowledges the interrupt. Keep its
        # actual terminal status and saved response; acknowledgement is not proof
        # that generation stopped or that a completed answer should be removed.
        db.refresh(delivery)
        audit(db, project_id, delivery, 'review_cancel_requested', serialize(delivery))
        db.commit()
        return {**serialize(delivery), 'cancel_requested': delivery.status == 'sent'}
