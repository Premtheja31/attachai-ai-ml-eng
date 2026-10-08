from dataclasses import dataclass

from app.config import settings


class PaymentTimeoutError(Exception):
    """Raised when the (mock) payment provider times out with an ambiguous
    response — the caller does not know whether the charge went through."""


@dataclass
class ChargeResult:
    status: str  # "succeeded" | "failed"


class PaymentMockClient:
    def __init__(self) -> None:
        # Every attempted charge, in order — a stand-in for the provider's own
        # ledger, useful for proving a bug caused two real-world charges.
        self.charge_log: list[int] = []
        self._results_by_idempotency_key: dict[str, ChargeResult] = {}

    def charge(self, amount_cents: int, idempotency_key: str | None = None) -> ChargeResult:
        if idempotency_key is not None and idempotency_key in self._results_by_idempotency_key:
            # Replayed request: the provider recognises the key and returns the
            # original outcome without creating a second real-world charge.
            return self._results_by_idempotency_key[idempotency_key]
        self.charge_log.append(amount_cents)
        if amount_cents == settings.payment_timeout_trigger_cents:
            raise PaymentTimeoutError("provider timed out")
        result = ChargeResult(status="failed" if amount_cents <= 0 else "succeeded")
        if idempotency_key is not None:
            self._results_by_idempotency_key[idempotency_key] = result
        return result


payment_mock_client = PaymentMockClient()
