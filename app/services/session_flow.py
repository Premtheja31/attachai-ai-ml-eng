from sqlalchemy.orm import Session as DbSession

from app.models import Booking, ConversationSession, PaymentAttempt
from app.services.payment_mock import payment_mock_client


class SimulatedCrash(Exception):
    """Lets a caller reproduce a process crash between the charge succeeding
    and the session/booking being persisted — the same ambiguous-outcome
    scenario as a real payment-provider timeout, but deterministic to trigger."""


def advance_turn(session: ConversationSession, intent: str, payload: dict, db: DbSession) -> dict:
    if intent == "refund_dispute":
        session.status = "escalated"
        db.commit()
        return {"status": "escalated"}

    if intent == "book":
        party_size = payload.get("party_size")
        if party_size is not None:
            session.party_size = party_size
        if session.party_size is not None:
            session.awaiting_confirmation = True
        db.commit()
        return {
            "status": "awaiting_confirmation" if session.awaiting_confirmation else "need_party_size",
            "party_size": session.party_size,
        }

    if intent == "affirm" and session.awaiting_confirmation:
        booking = None
        prior_attempts: list[PaymentAttempt] = []
        if session.booking_id is not None:
            booking = db.get(Booking, session.booking_id)
            prior_attempts = (
                db.query(PaymentAttempt)
                .filter(PaymentAttempt.booking_id == session.booking_id)
                .order_by(PaymentAttempt.id)
                .all()
            )

        if any(a.status == "succeeded" for a in prior_attempts):
            # A charge already went through on an earlier turn — never re-charge.
            booking.status = "confirmed"
            session.status = "confirmed"
            session.awaiting_confirmation = False
            db.commit()
            return {"status": "confirmed", "booking_id": booking.id}

        attempt = next((a for a in prior_attempts if a.status == "pending"), None)
        if attempt is None:
            amount_cents = payload.get("amount_cents", 0)
            if booking is None:
                booking = Booking(
                    member_id=session.member_id,
                    club_id=session.club_id,
                    description="Session-confirmed booking",
                    amount_cents=amount_cents,
                    status="pending",
                )
                db.add(booking)
                db.flush()
                session.booking_id = booking.id
            attempt = PaymentAttempt(
                booking_id=booking.id,
                idempotency_key=f"session-{session.id}-attempt-{len(prior_attempts) + 1}",
                amount_cents=amount_cents,
                status="pending",
            )
            db.add(attempt)
            # Commit the in-flight attempt BEFORE calling the provider: if the
            # process dies after the charge, the retry finds this pending
            # attempt and replays the same idempotency key instead of
            # charging the member a second time.
            db.commit()

        result = payment_mock_client.charge(attempt.amount_cents, idempotency_key=attempt.idempotency_key)

        if payload.get("simulate_crash"):
            raise SimulatedCrash("process died after the charge, before the session/booking were saved")

        attempt.status = result.status
        if result.status == "succeeded":
            booking.status = "confirmed"
            session.status = "confirmed"
            session.awaiting_confirmation = False
            db.commit()
            return {"status": "confirmed", "booking_id": booking.id}
        db.commit()
        return {"status": "payment_failed", "booking_id": booking.id}

    return {"status": session.status}
