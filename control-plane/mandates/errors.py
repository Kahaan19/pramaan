from enum import Enum


class MandateErrorCode(str, Enum):
    """Every way a mandate chain can fail verification. Each is raised by exactly
    one function in this package — see mandates/*.py docstrings for which.
    """

    MALFORMED = "malformed"
    UNKNOWN_SIGNER = "unknown_signer"
    BAD_SIGNATURE_INTENT = "bad_signature_intent"
    BAD_SIGNATURE_CART = "bad_signature_cart"
    EXPIRED = "expired"
    INTENT_MISMATCH = "intent_mismatch"
    MERCHANT_NOT_ALLOWLISTED = "merchant_not_allowlisted"
    CART_TOTAL_MISMATCH = "cart_total_mismatch"
    CART_EXCEEDS_INTENT = "cart_exceeds_intent"
    REPLAYED_NONCE = "replayed_nonce"


class MandateError(Exception):
    def __init__(self, code: MandateErrorCode, message: str):
        super().__init__(message)
        self.code = code
        self.message = message

    def __repr__(self) -> str:
        return f"MandateError({self.code.value}, {self.message!r})"
