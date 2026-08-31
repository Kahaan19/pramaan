"""Pydantic schemas for the two mandate types.

Each mandate has an Unsigned* base holding exactly the signed fields, and a
signed subclass that adds `signature`. Signing/canonicalization always
operates on the Unsigned* shape (see canonical.py) so the signature field can
never accidentally end up inside its own signed payload.

Structural/type validation lives here (positive amounts, integer paise, non-
empty collections, UTC-normalized expiry). Business-rule checks that need
both mandates together (cart total vs intent cap, merchant allowlist, cart
binding) live in scope.py instead.
"""

from datetime import datetime, timezone

from pydantic import BaseModel, StrictBool, StrictInt, field_validator

ID_MAX_LEN = 128
# Sane upper bound so a paise amount can never approach BigInteger overflow
# territory; also catches an obviously wrong unit (e.g. rupees passed as paise).
MAX_PAISE = 100_000_000_00  # ₹1,00,00,000


def _nonempty_id(value: str, field_name: str) -> str:
    if not value or not value.strip():
        raise ValueError(f"{field_name} must not be empty")
    if len(value) > ID_MAX_LEN:
        raise ValueError(f"{field_name} must be at most {ID_MAX_LEN} chars")
    return value


def _positive_paise(value: int, field_name: str) -> int:
    if value <= 0:
        raise ValueError(f"{field_name} must be a positive integer (paise)")
    if value > MAX_PAISE:
        raise ValueError(f"{field_name} exceeds the sane maximum of {MAX_PAISE} paise")
    return value


class CartItem(BaseModel):
    sku: str
    qty: StrictInt
    unit_price_paise: StrictInt

    @field_validator("sku")
    @classmethod
    def _sku_valid(cls, v: str) -> str:
        return _nonempty_id(v, "sku")

    @field_validator("qty")
    @classmethod
    def _qty_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("qty must be a positive integer")
        return v

    @field_validator("unit_price_paise")
    @classmethod
    def _price_positive(cls, v: int) -> int:
        return _positive_paise(v, "unit_price_paise")


class UnsignedIntentMandate(BaseModel):
    intent_id: str
    user_id: str
    max_amount_paise: StrictInt
    merchant_allowlist: list[str]
    category: str | None = None
    expires_at: datetime
    human_present: StrictBool
    nonce: str

    @field_validator("intent_id", "user_id", "nonce")
    @classmethod
    def _ids_valid(cls, v: str, info) -> str:
        return _nonempty_id(v, info.field_name)

    @field_validator("max_amount_paise")
    @classmethod
    def _amount_valid(cls, v: int) -> int:
        return _positive_paise(v, "max_amount_paise")

    @field_validator("merchant_allowlist")
    @classmethod
    def _allowlist_nonempty(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("merchant_allowlist must not be empty")
        return v

    @field_validator("expires_at")
    @classmethod
    def _expires_at_utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("expires_at must be timezone-aware")
        # Force UTC + drop sub-second precision so the same instant always
        # canonicalizes to the same bytes regardless of the signer's offset
        # (pydantic preserves e.g. "+05:30" verbatim but normalizes "+00:00"
        # to "Z" -- without this, +05:30 and Z would sign differently for the
        # same instant).
        return v.astimezone(timezone.utc).replace(microsecond=0)


class IntentMandate(UnsignedIntentMandate):
    signature: str


class UnsignedCartMandate(BaseModel):
    cart_id: str
    intent_id: str
    merchant_id: str
    items: list[CartItem]
    total_paise: StrictInt
    nonce: str

    @field_validator("cart_id", "intent_id", "merchant_id", "nonce")
    @classmethod
    def _ids_valid(cls, v: str, info) -> str:
        return _nonempty_id(v, info.field_name)

    @field_validator("items")
    @classmethod
    def _items_nonempty(cls, v: list[CartItem]) -> list[CartItem]:
        if not v:
            raise ValueError("items must not be empty")
        return v

    @field_validator("total_paise")
    @classmethod
    def _total_valid(cls, v: int) -> int:
        return _positive_paise(v, "total_paise")


class CartMandate(UnsignedCartMandate):
    signature: str
