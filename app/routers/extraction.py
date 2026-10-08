from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import require_admin
from app.custom_model import OpenAILLMClient
from app.db import get_db
from app.llm_client import LLMClient
from app.models import Member
from app.schemas import ExtractAttributesIn
from app.services.extraction_service import ExtractionService

router = APIRouter(prefix="/clubs", tags=["extraction"])


def get_llm_client() -> LLMClient:
    """Dependency so tests can override with FakeLLMClient; the real
    pipeline path always uses the real OpenAI client."""
    return OpenAILLMClient()


@router.post("/{club_id}/extract-attributes")
def extract_attributes(
    club_id: str,
    payload: ExtractAttributesIn,
    admin: Member = Depends(require_admin),
    db: Session = Depends(get_db),
    llm: LLMClient = Depends(get_llm_client),
):
    if admin.club_id != club_id:
        raise HTTPException(status_code=403, detail="not an admin of this club")
    service = ExtractionService(llm=llm, db=db)
    return service.extract_for_messages(club_id, payload.message_ids)
