import math

from sqlalchemy.orm import Session

from app.config import settings
from app.embeddings import embedding_client
from app.models import Member, MemberAttribute


def build_member_profile_text(member: Member, db: Session) -> str:
    # Single choke point for what feeds matching: restricted attributes
    # (health/clinical/psychometric) are excluded unconditionally — policy,
    # not configurable — and a configurable confidence floor (default 0.0)
    # is available for noise filtering. Every consumer (rank_candidates,
    # refresh_member_embedding, the seed) inherits this filter.
    attrs = (
        db.query(MemberAttribute)
        .filter(
            MemberAttribute.member_id == member.id,
            MemberAttribute.restricted.is_(False),
            MemberAttribute.confidence >= settings.matching_confidence_threshold,
        )
        .all()
    )
    return " | ".join(a.text for a in attrs)


def cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def rank_candidates(member: Member, reason: str, db: Session) -> list[dict]:
    candidates = (
        db.query(Member)
        .filter(Member.club_id == member.club_id, Member.id != member.id)
        .all()
    )

    results = []
    for candidate in candidates:
        # Recomputes the requesting member's own embedding on every iteration
        # via embedding_client.embed(), instead of once before the loop.
        query_vec = embedding_client.embed(build_member_profile_text(member, db))
        candidate_vec = candidate.profile_embedding
        if candidate_vec is None:
            continue
        score = cosine_similarity(query_vec, [float(v) for v in candidate_vec])
        results.append({"member_id": candidate.id, "name": candidate.name, "score": round(float(score), 4)})

    results.sort(key=lambda r: -r["score"])
    return results
