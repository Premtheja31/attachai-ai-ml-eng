"""Extraction pipeline (Part 3): raw conversation messages -> MemberAttribute rows.

Per-message guarantees:
- Idempotency: a message is already processed when any MemberAttribute row
  exists with source_message_id == message.id — it is skipped without an
  LLM call or writes.
- Failure isolation: one message's extraction failure (after the client's
  retries are exhausted) is reported in that message's result and never
  fails the rest of the batch.
- Durability: each message's attributes are committed individually, so a
  crash mid-batch loses at most the in-flight message; completed ones are
  skipped as already-processed on the re-run.
"""

from sqlalchemy.orm import Session

from app.llm_client import LLMClient
from app.models import ConversationMessage, Member, MemberAttribute
from app.services.embedding_pipeline import refresh_member_embedding


class ExtractionService:
    """Runs the extraction pipeline for one club's batch of messages."""

    def __init__(self, llm: LLMClient, db: Session) -> None:
        self.llm = llm
        self.db = db

    def extract_for_messages(self, club_id: str, message_ids: list[int]) -> dict:
        messages_by_id = self._club_messages(club_id, message_ids)

        results: list[dict] = []
        members_with_new_attributes: set[int] = set()

        for message_id in message_ids:
            result = self._process_one(messages_by_id.get(message_id), message_id)
            results.append(result)
            if result["status"] == "processed" and result["attributes_created"] > 0:
                members_with_new_attributes.add(messages_by_id[message_id].member_id)

        self._refresh_embeddings(members_with_new_attributes)
        return {"results": results, "summary": self._summarize(results)}

    def _club_messages(self, club_id: str, message_ids: list[int]) -> dict[int, ConversationMessage]:
        messages = (
            self.db.query(ConversationMessage)
            .filter(
                ConversationMessage.id.in_(message_ids),
                # Messages outside this club are reported as not_found,
                # exactly like nonexistent ids — the endpoint must not act
                # as a cross-club existence oracle.
                ConversationMessage.club_id == club_id,
            )
            .all()
        )
        return {m.id: m for m in messages}

    def _process_one(self, message: ConversationMessage | None, message_id: int) -> dict:
        if message is None:
            return {"message_id": message_id, "status": "not_found", "attributes_created": 0}

        if self._already_processed(message):
            return {"message_id": message_id, "status": "already_processed", "attributes_created": 0}

        try:
            attributes = self.llm.extract_attributes(message.body)
        except Exception as exc:
            return {
                "message_id": message_id,
                "status": "failed",
                "attributes_created": 0,
                "error": f"{type(exc).__name__}: {exc}",
            }

        for attribute in attributes:
            self.db.add(
                MemberAttribute(
                    member_id=message.member_id,
                    club_id=message.club_id,
                    kind=attribute["kind"],
                    text=attribute["text"],
                    confidence=attribute["confidence"],
                    restricted=attribute["restricted"],
                    source_message_id=message.id,
                )
            )
        self.db.commit()
        return {"message_id": message_id, "status": "processed", "attributes_created": len(attributes)}

    def _already_processed(self, message: ConversationMessage) -> bool:
        return (
            self.db.query(MemberAttribute)
            .filter(MemberAttribute.source_message_id == message.id)
            .first()
            is not None
        )

    def _refresh_embeddings(self, member_ids: set[int]) -> None:
        # New attributes change the member's profile text, so their matching
        # embedding must be rebuilt (build_member_profile_text excludes
        # restricted and below-threshold attributes — see matching_service).
        for member_id in sorted(member_ids):
            member = self.db.get(Member, member_id)
            if member is not None:
                refresh_member_embedding(member, self.db)

    @staticmethod
    def _summarize(results: list[dict]) -> dict:
        summary = {"processed": 0, "already_processed": 0, "failed": 0, "not_found": 0}
        for result in results:
            summary[result["status"]] += 1
        return summary
