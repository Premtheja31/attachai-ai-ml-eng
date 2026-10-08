"""Pydantic models defining the structured-output contract with the LLM.

Passed to the OpenAI parse API, which converts them to a strict JSON schema
enforced server-side — the model cannot return a kind outside the Literal,
a missing field, or a non-numeric confidence.
"""

from typing import Literal

from pydantic import BaseModel, field_validator


class ExtractedAttributeModel(BaseModel):
    kind: Literal["need", "offer", "context", "interest"]
    text: str
    confidence: float
    restricted: bool

    @field_validator("confidence")
    @classmethod
    def _clamp_confidence(cls, v: float) -> float:
        return max(0.0, min(1.0, v))


class ExtractionResult(BaseModel):
    attributes: list[ExtractedAttributeModel]
