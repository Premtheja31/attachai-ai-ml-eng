"""Real LLM integration for the extraction pipeline (Part 3).

Layout:
- schemas.py        — Pydantic models for the structured-output contract
- prompts.py        — the extraction system prompt (few-shot from seed data)
- openai_client.py  — OpenAI client setup + retry strategy
"""

from app.custom_model.openai_client import OpenAILLMClient
from app.custom_model.schemas import ExtractedAttributeModel, ExtractionResult

__all__ = ["OpenAILLMClient", "ExtractedAttributeModel", "ExtractionResult"]
