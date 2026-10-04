from decimal import Decimal, ROUND_HALF_UP

from app.core.config import get_settings


# Ledger amounts retain four decimal places. Presentation layers may round to
# two decimals, but settlement must preserve the actual per-second charge.
MONEY_QUANTUM = Decimal("0.0001")
QUANTITY_QUANTUM = Decimal("0.0001")


def call_price_inr() -> Decimal:
    return get_settings().call_price_inr.quantize(MONEY_QUANTUM)


def calculate_call_charge(duration_seconds: int, unit_price: Decimal | None = None) -> tuple[Decimal, Decimal]:
    if duration_seconds < 0:
        raise ValueError("Duration cannot be negative")
    price = (unit_price or call_price_inr()).quantize(MONEY_QUANTUM)
    minutes = (Decimal(duration_seconds) / Decimal("60")).quantize(QUANTITY_QUANTUM, rounding=ROUND_HALF_UP)
    amount = (Decimal(duration_seconds) / Decimal("60") * price).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
    return minutes, amount
