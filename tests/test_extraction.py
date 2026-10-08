import httpx
import pytest
from fastapi.testclient import TestClient

from app.custom_model import OpenAILLMClient
from app.llm_client import ExtractedAttribute, FakeLLMClient
from app.main import app
from app.models import Club, ConversationMessage, Member, MemberAttribute
from app.routers.extraction import get_llm_client
from app.services.matching_service import build_member_profile_text

client = TestClient(app)

NEED_ATTR = ExtractedAttribute(
    kind="need", text="needs a fractional CFO", confidence=0.9, restricted=False
)
RESTRICTED_ATTR = ExtractedAttribute(
    kind="context", text="manages a chronic condition", confidence=0.9, restricted=True
)


class FlakyLLMClient:
    """Class-based double: fails permanently for messages containing the
    trigger, succeeds for everything else — simulates one message in a
    batch exhausting its retries."""

    def __init__(self, trigger: str = "FAIL_ME") -> None:
        self.trigger = trigger
        self.calls: list[str] = []

    def extract_attributes(self, message_text: str) -> list[ExtractedAttribute]:
        self.calls.append(message_text)
        if self.trigger in message_text:
            raise RuntimeError("provider rejected the request permanently")
        return [NEED_ATTR]


class ExtractionTestBase:
    """Shared setup/helpers for the extraction endpoint tests."""

    @pytest.fixture(autouse=True)
    def _clear_llm_override(self):
        yield
        app.dependency_overrides.pop(get_llm_client, None)

    @staticmethod
    def install_llm(fake) -> None:
        app.dependency_overrides[get_llm_client] = lambda: fake

    @staticmethod
    def seed_club(db, club_id="riverside", with_admin=True):
        db.add(Club(id=club_id, name=club_id.title()))
        admin = None
        if with_admin:
            admin = Member(
                club_id=club_id, name=f"{club_id} admin", email=f"admin@{club_id}.test",
                token=f"adm-{club_id}", role="admin",
            )
            db.add(admin)
        member = Member(
            club_id=club_id, name=f"{club_id} member", email=f"member@{club_id}.test",
            token=f"tok-{club_id}", role="member",
        )
        db.add(member)
        db.commit()
        return admin, member

    @staticmethod
    def add_message(db, member, body):
        msg = ConversationMessage(member_id=member.id, club_id=member.club_id, body=body)
        db.add(msg)
        db.commit()
        return msg

    @staticmethod
    def extract(club_id, message_ids, token):
        return client.post(
            f"/clubs/{club_id}/extract-attributes",
            json={"message_ids": message_ids},
            headers={"X-Member-Token": token},
        )


class TestExtractionPipeline(ExtractionTestBase):
    def test_happy_path_writes_attributes(self, db):
        _, member = self.seed_club(db)
        msg = self.add_message(db, member, "I need a CFO and I manage a chronic condition.")
        fake = FakeLLMClient(canned_response=[NEED_ATTR, RESTRICTED_ATTR])
        self.install_llm(fake)

        resp = self.extract("riverside", [msg.id], "adm-riverside")

        assert resp.status_code == 200
        body = resp.json()
        assert body["results"] == [
            {"message_id": msg.id, "status": "processed", "attributes_created": 2}
        ]
        assert body["summary"]["processed"] == 1
        assert fake.calls == [msg.body]

        rows = db.query(MemberAttribute).filter(MemberAttribute.source_message_id == msg.id).all()
        assert len(rows) == 2
        for row in rows:
            assert row.member_id == member.id
            assert row.club_id == "riverside"
        assert {r.restricted for r in rows} == {True, False}

    def test_rerun_is_idempotent_and_skips_llm(self, db):
        _, member = self.seed_club(db)
        msg = self.add_message(db, member, "I need a CFO.")
        fake = FakeLLMClient(canned_response=[NEED_ATTR])
        self.install_llm(fake)

        first = self.extract("riverside", [msg.id], "adm-riverside")
        assert first.json()["results"][0]["status"] == "processed"

        second = self.extract("riverside", [msg.id], "adm-riverside")
        assert second.json()["results"][0]["status"] == "already_processed"
        assert second.json()["summary"]["already_processed"] == 1

        # no duplicate rows AND no second LLM call
        rows = db.query(MemberAttribute).filter(MemberAttribute.source_message_id == msg.id).all()
        assert len(rows) == 1
        assert len(fake.calls) == 1

    def test_zero_attribute_message_reprocessed_without_duplicates(self, db):
        _, member = self.seed_club(db)
        msg = self.add_message(db, member, "See you Saturday!")
        fake = FakeLLMClient(canned_response=[])
        self.install_llm(fake)

        first = self.extract("riverside", [msg.id], "adm-riverside")
        assert first.json()["results"][0] == {
            "message_id": msg.id, "status": "processed", "attributes_created": 0
        }

        # no attribute row marks it done, so a re-run calls the LLM again —
        # idempotent in effect: still zero rows, no duplicates
        second = self.extract("riverside", [msg.id], "adm-riverside")
        assert second.json()["results"][0]["status"] == "processed"
        assert len(fake.calls) == 2
        assert db.query(MemberAttribute).count() == 0


class TestExtractionAuthorization(ExtractionTestBase):
    def test_member_cannot_trigger_extraction(self, db):
        _, member = self.seed_club(db)
        msg = self.add_message(db, member, "hello")
        self.install_llm(FakeLLMClient())

        resp = self.extract("riverside", [msg.id], "tok-riverside")
        assert resp.status_code == 403

    def test_admin_of_other_club_rejected(self, db):
        _, member = self.seed_club(db, "riverside")
        self.seed_club(db, "oakhurst")
        msg = self.add_message(db, member, "hello")
        self.install_llm(FakeLLMClient())

        resp = self.extract("riverside", [msg.id], "adm-oakhurst")
        assert resp.status_code == 403

    def test_cross_club_and_unknown_message_ids_are_not_found(self, db):
        self.seed_club(db, "riverside")
        _, oakhurst_member = self.seed_club(db, "oakhurst")
        foreign_msg = self.add_message(db, oakhurst_member, "oakhurst secret")
        self.install_llm(FakeLLMClient(canned_response=[NEED_ATTR]))

        resp = self.extract("riverside", [foreign_msg.id, 999999], "adm-riverside")

        assert resp.status_code == 200
        statuses = {r["message_id"]: r["status"] for r in resp.json()["results"]}
        assert statuses == {foreign_msg.id: "not_found", 999999: "not_found"}
        assert db.query(MemberAttribute).count() == 0

    def test_empty_message_ids_rejected(self, db):
        self.seed_club(db)
        self.install_llm(FakeLLMClient())

        resp = self.extract("riverside", [], "adm-riverside")
        assert resp.status_code == 422


class TestExtractionFailureIsolation(ExtractionTestBase):
    def test_one_permanent_failure_does_not_fail_batch(self, db):
        _, member = self.seed_club(db)
        ok_1 = self.add_message(db, member, "I need a CFO.")
        bad = self.add_message(db, member, "FAIL_ME please")
        ok_2 = self.add_message(db, member, "I can offer CFO services.")
        self.install_llm(FlakyLLMClient())

        resp = self.extract("riverside", [ok_1.id, bad.id, ok_2.id], "adm-riverside")

        assert resp.status_code == 200
        results = resp.json()["results"]
        assert [r["status"] for r in results] == ["processed", "failed", "processed"]
        assert "RuntimeError" in results[1]["error"]
        assert resp.json()["summary"] == {
            "processed": 2, "already_processed": 0, "failed": 1, "not_found": 0
        }
        # the two successes were committed despite the failure between them
        by_source = lambda mid: db.query(MemberAttribute).filter(
            MemberAttribute.source_message_id == mid
        ).count()
        assert by_source(ok_1.id) == 1
        assert by_source(ok_2.id) == 1
        assert by_source(bad.id) == 0


class TestRestrictedEnforcement(ExtractionTestBase):
    def test_restricted_attribute_never_reaches_matching_profile(self, db):
        _, member = self.seed_club(db)
        msg = self.add_message(db, member, "I need a CFO and I manage a chronic condition.")
        self.install_llm(FakeLLMClient(canned_response=[NEED_ATTR, RESTRICTED_ATTR]))

        assert member.profile_embedding is None
        resp = self.extract("riverside", [msg.id], "adm-riverside")
        assert resp.status_code == 200

        db.expire_all()
        profile_text = build_member_profile_text(member, db)
        assert "needs a fractional CFO" in profile_text
        assert "chronic condition" not in profile_text
        # the embedding was refreshed as part of extraction
        assert member.profile_embedding is not None


class TestOpenAIClientRetryClassification:
    @staticmethod
    def _status_error(code):
        from openai import APIStatusError

        req = httpx.Request("POST", "http://api.test")
        return APIStatusError("err", response=httpx.Response(code, request=req), body=None)

    def test_transient_errors_are_retryable(self):
        from openai import APIConnectionError, APITimeoutError, RateLimitError

        req = httpx.Request("POST", "http://api.test")
        rate_limited = RateLimitError("rl", response=httpx.Response(429, request=req), body=None)
        assert OpenAILLMClient._is_transient(rate_limited) is True
        assert OpenAILLMClient._is_transient(self._status_error(500)) is True
        assert OpenAILLMClient._is_transient(self._status_error(503)) is True
        assert OpenAILLMClient._is_transient(APITimeoutError(request=req)) is True
        assert OpenAILLMClient._is_transient(APIConnectionError(request=req)) is True

    def test_non_transient_errors_fail_fast(self):
        assert OpenAILLMClient._is_transient(self._status_error(400)) is False
        assert OpenAILLMClient._is_transient(self._status_error(401)) is False
        assert OpenAILLMClient._is_transient(ValueError("parse failure")) is False
