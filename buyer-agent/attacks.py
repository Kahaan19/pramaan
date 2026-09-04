"""The one genuine POST-signing mandate mutation used by the scripted demo.

Two of the three attack categories (over-mandate spend, goal-hijack/off-
allowlist) are expressed honestly as sign-time PARAMETERS in
scenarios.py -- an agent asking its own user/merchant key to sign a
request that is malicious by its very shape (too big, wrong payee) is a
more realistic threat than a man-in-the-middle editing bytes in flight, and
it is what a compromised or prompt-injected agent can actually do (it can
ask for a signature; it cannot forge one). The tampered-cart attack is
different in kind -- it requires an already-validly-signed cart to be
altered by something OTHER than the signer -- so it is the one real
mutation function here.
"""


def tamper_cart_price(cart: dict, new_total_paise: int) -> dict:
    """Mutates a cart's total_paise AFTER it was signed. The signature was
    computed over the ORIGINAL bytes (mandates/canonical.py::signing_bytes),
    so this no longer verifies -- mandates/verify.py must reject it with
    BAD_SIGNATURE_CART. Demonstrates that a relay sitting between the buyer
    agent and the merchant cannot alter a price after the merchant signed
    it, even though the JSON is plain text.
    """
    tampered = dict(cart)
    tampered["total_paise"] = new_total_paise
    return tampered
