"""Canonical JSON + the exact bytes that get signed / verified.

Canonicalizing via the pydantic model (never a raw wire dict) is deliberate:
model_dump() renders an omitted Optional field the same as an explicit null,
so a signer who omits `category` and a verifier who received it as `null`
produce byte-identical signing input. Canonicalizing the raw wire dict
instead would NOT have this property.
"""

import json
from typing import Any

from mandates.schemas import UnsignedCartMandate, UnsignedIntentMandate

# Domain-separation tags. Without these, a valid intent signature and a valid
# cart signature over the same bytes would be interchangeable -- a signed
# intent could be replayed as if it were a signed cart. The trailing version
# number is free forward-compat if the wire shape ever changes.
_INTENT_TAG = b"pramaan.mandate.intent.v1\n"
_CART_TAG = b"pramaan.mandate.cart.v1\n"


def canonical_json(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def signing_bytes(mandate: UnsignedIntentMandate | UnsignedCartMandate) -> bytes:
    """Bytes to sign or verify. Accepts either the Unsigned* shape or the
    signed subclass (the `signature` field is excluded either way, so passing
    an already-signed instance to re-derive its own signing bytes is safe).
    """
    if isinstance(mandate, UnsignedCartMandate):
        tag = _CART_TAG
    elif isinstance(mandate, UnsignedIntentMandate):
        tag = _INTENT_TAG
    else:
        raise TypeError(f"unsupported mandate type: {type(mandate)!r}")

    payload = mandate.model_dump(mode="json", exclude={"signature"})
    return tag + canonical_json(payload)
