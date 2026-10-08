"""Real LLM client for the Part 3 extraction pipeline.

Implements the LLMClient protocol from app.llm_client against the OpenAI
API with structured (schema-enforced) output.
"""

import random
import time

from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI, RateLimitError

from app.config import settings
from app.custom_model.prompts import EXTRACTION_SYSTEM_PROMPT
from app.custom_model.schemas import ExtractionResult
from app.llm_client import ExtractedAttribute


class OpenAILLMClient:
    """Retry strategy: the SDK's own silent retries are disabled
    (max_retries=0) so this class is the single place retries happen — up to
    settings.llm_max_attempts attempts with exponential backoff plus jitter,
    retrying only transient failures (429 rate limits, 5xx, timeouts,
    connection errors). Anything else (bad key, malformed request) raises
    immediately.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        max_attempts: int | None = None,
    ) -> None:
        self._client = OpenAI(api_key=api_key or settings.openai_api_key, max_retries=0)
        self.model = model or settings.openai_model
        self.max_attempts = max_attempts or settings.llm_max_attempts

    def extract_attributes(self, message_text: str) -> list[ExtractedAttribute]:
        for attempt in range(1, self.max_attempts + 1):
            try:
                return self._call(message_text)
            except Exception as exc:
                if attempt == self.max_attempts or not self._is_transient(exc):
                    raise
                backoff = 2 ** (attempt - 1) + random.uniform(0, 0.5)
                time.sleep(backoff)
        raise RuntimeError("unreachable")  # loop always returns or raises

    @staticmethod
    def _is_transient(exc: Exception) -> bool:
        if isinstance(exc, (RateLimitError, APITimeoutError, APIConnectionError)):
            return True
        return isinstance(exc, APIStatusError) and exc.status_code >= 500

    def _call(self, message_text: str) -> list[ExtractedAttribute]:
        completion = self._client.chat.completions.parse(
            model=self.model,
            temperature=0,
            messages=[
                {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                {"role": "user", "content": message_text},
            ],
            response_format=ExtractionResult,
        )
        parsed = completion.choices[0].message.parsed
        if parsed is None:  # model refusal — treat as "nothing extractable"
            return []
        return [
            ExtractedAttribute(
                kind=a.kind, text=a.text, confidence=a.confidence, restricted=a.restricted
            )
            for a in parsed.attributes
        ]
