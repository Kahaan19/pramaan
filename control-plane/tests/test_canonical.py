import json

from mandates.canonical import canonical_json, signing_bytes
from mandates.schemas import UnsignedIntentMandate

from .conftest import unsigned_cart, unsigned_intent


def test_canonical_json_ignores_key_order():
    a = {"b": 1, "a": "x", "n": [3, 1]}
    b = {"a": "x", "n": [3, 1], "b": 1}
    assert canonical_json(a) == canonical_json(b)


def test_canonical_json_sorts_nested_dict_keys_inside_lists():
    payload = {"items": [{"unit_price_paise": 1, "qty": 2, "sku": "a"}]}
    assert canonical_json(payload) == b'{"items":[{"qty":2,"sku":"a","unit_price_paise":1}]}'


def test_ensure_ascii_is_pinned_false():
    payload = {"sku": "chai ☕"}
    encoded = canonical_json(payload)
    assert "☕".encode("utf-8") in encoded
    assert b"\\u2615" not in encoded


def test_omitted_category_equals_explicit_null():
    base = dict(
        intent_id="intent_1",
        user_id="user_kahaan",
        max_amount_paise=200000,
        merchant_allowlist=["merchant_demo_01"],
        expires_at="2026-09-05T23:59:59Z",
        human_present=True,
        nonce="n1",
    )
    omitted = UnsignedIntentMandate(**base)
    explicit_null = UnsignedIntentMandate(**base, category=None)
    assert signing_bytes(omitted) == signing_bytes(explicit_null)


def test_utc_offset_equals_z_for_same_instant():
    base = dict(
        intent_id="intent_1",
        user_id="user_kahaan",
        max_amount_paise=200000,
        merchant_allowlist=["merchant_demo_01"],
        category="retail",
        human_present=True,
        nonce="n1",
    )
    as_z = UnsignedIntentMandate(**base, expires_at="2026-09-05T23:59:59Z")
    as_ist = UnsignedIntentMandate(**base, expires_at="2026-09-06T05:29:59+05:30")
    assert signing_bytes(as_z) == signing_bytes(as_ist)


def test_intent_and_cart_tags_differ_for_equivalent_bytes():
    intent = unsigned_intent()
    cart = unsigned_cart(intent.intent_id)
    # sanity: signing_bytes always begins with a domain tag distinguishing type
    assert signing_bytes(intent).startswith(b"pramaan.mandate.intent.v1\n")
    assert signing_bytes(cart).startswith(b"pramaan.mandate.cart.v1\n")


def test_signing_bytes_excludes_signature_field_on_signed_instance():
    from nacl.signing import SigningKey

    from .conftest import sign_intent

    # Build via the real signing helper to get a signed instance, then
    # confirm re-deriving its signing bytes doesn't fold in `signature`.
    sk = SigningKey.generate()
    intent = sign_intent(unsigned_intent(), sk)
    payload = json.loads(signing_bytes(intent).split(b"\n", 1)[1])
    assert "signature" not in payload
