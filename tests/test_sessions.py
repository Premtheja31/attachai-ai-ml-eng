from fastapi.testclient import TestClient

from app.main import app
from app.models import Club, Member
from app.services.payment_mock import payment_mock_client

client = TestClient(app)


def test_session_books_end_to_end(db):
    db.add(Club(id="riverside", name="Riverside"))
    m = Member(club_id="riverside", name="A", email="a@example.com", token="tok-a", role="member")
    db.add(m)
    db.commit()

    resp = client.post("/sessions", headers={"X-Member-Token": "tok-a"})
    assert resp.status_code == 200
    session_id = resp.json()["session_id"]

    resp = client.post(
        f"/sessions/{session_id}/turn",
        json={"intent": "book", "party_size": 2},
        headers={"X-Member-Token": "tok-a"},
    )
    assert resp.json()["status"] == "awaiting_confirmation"

    resp = client.post(
        f"/sessions/{session_id}/turn",
        json={"intent": "affirm", "amount_cents": 4000},
        headers={"X-Member-Token": "tok-a"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "confirmed"


def _booked_session(db):
    db.add(Club(id="riverside", name="Riverside"))
    m = Member(club_id="riverside", name="A", email="a@example.com", token="tok-a", role="member")
    db.add(m)
    db.commit()
    session_id = client.post("/sessions", headers={"X-Member-Token": "tok-a"}).json()["session_id"]
    client.post(
        f"/sessions/{session_id}/turn",
        json={"intent": "book", "party_size": 2},
        headers={"X-Member-Token": "tok-a"},
    )
    return session_id


def test_crash_then_retry_charges_exactly_once(db):
    session_id = _booked_session(db)
    charges_before = len(payment_mock_client.charge_log)

    resp = client.post(
        f"/sessions/{session_id}/turn",
        json={"intent": "affirm", "amount_cents": 4000, "simulate_crash": True},
        headers={"X-Member-Token": "tok-a"},
    )
    assert resp.status_code == 500

    resp = client.post(
        f"/sessions/{session_id}/turn",
        json={"intent": "affirm", "amount_cents": 4000},
        headers={"X-Member-Token": "tok-a"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "confirmed"

    # the crash + retry produced exactly ONE real-world charge
    assert len(payment_mock_client.charge_log) == charges_before + 1


def test_affirm_after_confirmation_does_not_recharge(db):
    session_id = _booked_session(db)

    resp = client.post(
        f"/sessions/{session_id}/turn",
        json={"intent": "affirm", "amount_cents": 4000},
        headers={"X-Member-Token": "tok-a"},
    )
    assert resp.json()["status"] == "confirmed"
    charges_after_first = len(payment_mock_client.charge_log)

    resp = client.post(
        f"/sessions/{session_id}/turn",
        json={"intent": "affirm", "amount_cents": 4000},
        headers={"X-Member-Token": "tok-a"},
    )
    assert resp.json()["status"] == "confirmed"
    assert len(payment_mock_client.charge_log) == charges_after_first


def test_failed_charge_does_not_confirm_session(db):
    session_id = _booked_session(db)

    # amount_cents <= 0 always fails in the mock provider
    resp = client.post(
        f"/sessions/{session_id}/turn",
        json={"intent": "affirm", "amount_cents": 0},
        headers={"X-Member-Token": "tok-a"},
    )
    assert resp.json()["status"] == "payment_failed"

    state = client.get(f"/sessions/{session_id}", headers={"X-Member-Token": "tok-a"}).json()
    assert state["status"] == "active"
    assert state["awaiting_confirmation"] is True

    # a fresh affirm with a valid amount succeeds as a NEW attempt
    resp = client.post(
        f"/sessions/{session_id}/turn",
        json={"intent": "affirm", "amount_cents": 4000},
        headers={"X-Member-Token": "tok-a"},
    )
    assert resp.json()["status"] == "confirmed"


def test_refund_dispute_escalates(db):
    db.add(Club(id="riverside", name="Riverside"))
    m = Member(club_id="riverside", name="A", email="a@example.com", token="tok-a", role="member")
    db.add(m)
    db.commit()

    session_id = client.post("/sessions", headers={"X-Member-Token": "tok-a"}).json()["session_id"]

    resp = client.post(
        f"/sessions/{session_id}/turn",
        json={"intent": "refund_dispute"},
        headers={"X-Member-Token": "tok-a"},
    )
    assert resp.json()["status"] == "escalated"
