from fastapi.testclient import TestClient

from app.main import app
from app.models import Club, Member, MemberAttribute

client = TestClient(app)


def test_generate_reason_includes_attributes(db):
    db.add(Club(id="riverside", name="Riverside"))
    m1 = Member(club_id="riverside", name="A", email="a@example.com", token="tok-a", role="member")
    m2 = Member(club_id="riverside", name="B", email="b@example.com", token="tok-b", role="member")
    db.add_all([m1, m2])
    db.commit()

    db.add(MemberAttribute(member_id=m1.id, club_id="riverside", kind="need", text="needs a CFO", confidence=0.9))
    db.add(MemberAttribute(member_id=m2.id, club_id="riverside", kind="offer", text="offers CFO services", confidence=0.9))
    db.commit()

    resp = client.get(
        f"/introductions/{m1.id}/{m2.id}",
        params={"reason": "business"},
        headers={"X-Member-Token": "tok-a"},
    )
    assert resp.status_code == 200
    assert "CFO" in resp.json()["reason_text"]


def _two_members_with_attrs(db):
    db.add(Club(id="riverside", name="Riverside"))
    m1 = Member(club_id="riverside", name="A", email="a@example.com", token="tok-a", role="member")
    m2 = Member(club_id="riverside", name="B", email="b@example.com", token="tok-b", role="member")
    db.add_all([m1, m2])
    db.commit()
    db.add(MemberAttribute(member_id=m1.id, club_id="riverside", kind="need", text="needs a CFO", confidence=0.9))
    db.add(MemberAttribute(member_id=m2.id, club_id="riverside", kind="offer", text="offers CFO services", confidence=0.9))
    db.commit()
    return m1, m2


def test_cross_club_caller_is_rejected(db):
    m1, m2 = _two_members_with_attrs(db)
    db.add(Club(id="oakhurst", name="Oakhurst"))
    outsider = Member(club_id="oakhurst", name="C", email="c@example.com", token="tok-c", role="member")
    db.add(outsider)
    db.commit()

    resp = client.get(
        f"/introductions/{m1.id}/{m2.id}",
        params={"reason": "business"},
        headers={"X-Member-Token": "tok-c"},
    )
    assert resp.status_code == 403


def test_unknown_member_returns_404(db):
    m1, _ = _two_members_with_attrs(db)
    resp = client.get(
        f"/introductions/{m1.id}/999999",
        params={"reason": "business"},
        headers={"X-Member-Token": "tok-a"},
    )
    assert resp.status_code == 404


def test_restricted_and_low_confidence_attributes_never_appear(db):
    m1, m2 = _two_members_with_attrs(db)
    db.add(
        MemberAttribute(
            member_id=m2.id, club_id="riverside", kind="context",
            text="manages anxiety", confidence=0.9, restricted=True,
        )
    )
    db.add(
        MemberAttribute(
            member_id=m2.id, club_id="riverside", kind="need",
            text="might be raising next year", confidence=0.3,
        )
    )
    db.commit()

    resp = client.get(
        f"/introductions/{m1.id}/{m2.id}",
        params={"reason": "business"},
        headers={"X-Member-Token": "tok-a"},
    )
    assert resp.status_code == 200
    reason = resp.json()["reason_text"]
    assert "anxiety" not in reason
    assert "might be raising" not in reason
    assert "CFO" in reason


def test_insufficient_basis_when_only_restricted_attributes(db):
    db.add(Club(id="riverside", name="Riverside"))
    m1 = Member(club_id="riverside", name="A", email="a@example.com", token="tok-a", role="member")
    m2 = Member(club_id="riverside", name="B", email="b@example.com", token="tok-b", role="member")
    db.add_all([m1, m2])
    db.commit()
    db.add(MemberAttribute(member_id=m1.id, club_id="riverside", kind="need", text="needs a CFO", confidence=0.9))
    db.add(
        MemberAttribute(
            member_id=m2.id, club_id="riverside", kind="context",
            text="recently diagnosed with a chronic condition", confidence=0.9, restricted=True,
        )
    )
    db.commit()

    resp = client.get(
        f"/introductions/{m1.id}/{m2.id}",
        params={"reason": "business"},
        headers={"X-Member-Token": "tok-a"},
    )
    assert resp.status_code == 200
    reason = resp.json()["reason_text"]
    assert reason == "insufficient basis for an introduction"
    assert "chronic" not in reason
