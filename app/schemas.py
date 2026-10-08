from pydantic import BaseModel, Field


class ConfirmPaymentIn(BaseModel):
    amount_cents: int


class ExtractAttributesIn(BaseModel):
    message_ids: list[int] = Field(min_length=1)
