from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import get_current_member
from app.config import settings
from app.db import get_db
from app.models import Member, MemberAttribute

router = APIRouter(prefix="/introductions", tags=["introductions"])


@router.get("/{member_a_id}/{member_b_id}")
def generate_reason(
    member_a_id: int,
    member_b_id: int,
    reason: str,
    db: Session = Depends(get_db),
    member: Member = Depends(get_current_member),
):
    member_a = db.get(Member, member_a_id)
    member_b = db.get(Member, member_b_id)
    if member_a is None or member_b is None:
        raise HTTPException(status_code=404, detail="member not found")
    if member_a.club_id != member.club_id or member_b.club_id != member.club_id:
        raise HTTPException(status_code=403, detail="not a member of this club")

    def usable_attributes(target_id: int) -> list[MemberAttribute]:
        # Restricted attributes (health/clinical/psychometric) and
        # low-confidence speculation must never surface in an introduction.
        return (
            db.query(MemberAttribute)
            .filter(
                MemberAttribute.member_id == target_id,
                MemberAttribute.restricted.is_(False),
                MemberAttribute.confidence >= settings.intro_confidence_threshold,
            )
            .all()
        )

    attrs_a = usable_attributes(member_a_id)
    attrs_b = usable_attributes(member_b_id)
    if not attrs_a or not attrs_b:
        return {"reason_text": "insufficient basis for an introduction"}

    a_text = "; ".join(a.text for a in attrs_a)
    b_text = "; ".join(b.text for b in attrs_b)
    return {"reason_text": f"Because {a_text} and {b_text} — a good {reason} match."}
