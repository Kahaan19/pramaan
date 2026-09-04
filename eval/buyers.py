"""Synthetic buyer/merchant identities for the eval batch, and the
in-memory Keyring built over them.

Deliberately NOT the real secrets/ keypairs: the eval batch runs against its
own throwaway database (eval/run_batch.py) and its own throwaway signers, so
it never depends on -- or risks colliding with -- the demo's real Ed25519
keys. mandates.keys.Keyring accepts an in-memory mapping for exactly this
reason (see control-plane/tests/conftest.py's own `keyring` fixture, which
does the same thing).

merchant_rogue_99 is deliberately left OUT of both dicts below -- M3 needs an
unregistered signer so verify_mandate_chain raises UNKNOWN_SIGNER before any
signature is even checked.
"""

from nacl.signing import SigningKey

from mandates.keys import Keyring

MERCHANT_DEMO = "merchant_demo_01"
MERCHANT_PARTNER = "merchant_partner_02"  # registered, but absent from rules.yaml's platform allowlist (F2)
MERCHANT_ROGUE = "merchant_rogue_99"  # never registered -- UNKNOWN_SIGNER (M3)

# Every synthetic buyer used anywhere in eval/batch.py. Centralized here (not
# generated ad hoc in batch.py) so the Keyring and the batch can never
# reference a user_id the other doesn't know about.
BUYER_IDS = [
    # L1 -- routine small purchases, one buyer each
    "eval_honest_small_01",
    "eval_honest_small_02",
    "eval_honest_small_03",
    "eval_honest_small_04",
    # L2 -- high-value legit purchases (STEP_UP by amount), one buyer each
    "eval_honest_stepup_01",
    "eval_honest_stepup_02",
    "eval_honest_stepup_03",
    # L3 -- delegated legit purchase (STEP_UP by human_present=false)
    "eval_honest_delegated_01",
    # L4 reuses eval_honest_small_01's already-executed cart -- no new buyer.
    # L5 -- one busy honest buyer, 6 purchases in the window
    "eval_busy_honest",
    # L6 -- one buyer, 4 high-value carts (fills the approval queue)
    "eval_highvalue_honest",
    # M1 -- over-mandate spend (mandate layer), two buyers
    "eval_rogue_overmandate_01",
    "eval_rogue_overmandate_02",
    # M2 -- over platform cap (policy layer), two buyers
    "eval_rogue_overcap_01",
    "eval_rogue_overcap_02",
    # M3 -- unknown payee
    "eval_rogue_unknown_payee",
    # M4 -- off the intent's OWN allowlist, two buyers
    "eval_rogue_offallowlist_01",
    "eval_rogue_offallowlist_02",
    # M5 -- off the PLATFORM allowlist (merchant_partner_02)
    "eval_rogue_platform_allowlist",
    # M6 -- tampered cart price
    "eval_rogue_tampered_price",
    # M7 -- tampered intent cap
    "eval_rogue_tampered_cap",
    # M8 -- internally inconsistent cart (items != total)
    "eval_rogue_total_mismatch",
    # M9 reuses eval_rogue_overcap_01's burned nonce -- no new buyer.
    # M10 -- expired intent
    "eval_rogue_expired",
    # M11 -- disallowed category
    "eval_rogue_category",
    # M12 + the 5 indistinguishable prefix attempts -- one runaway buyer
    "eval_rogue_runaway",
]


def build_keyring() -> tuple[Keyring, dict[str, SigningKey], dict[str, SigningKey]]:
    """Returns (keyring, user_signing_keys, merchant_signing_keys) -- the
    signing keys are handed back too because eval/batch.py needs them to
    actually sign each attempt's mandates; the Keyring only ever holds
    VerifyKeys (the control plane's real Keyring never sees a private key
    either -- see mandates/keys.py).
    """
    user_signing_keys = {user_id: SigningKey.generate() for user_id in BUYER_IDS}
    merchant_signing_keys = {
        MERCHANT_DEMO: SigningKey.generate(),
        MERCHANT_PARTNER: SigningKey.generate(),
    }

    keyring = Keyring(
        user_keys={uid: sk.verify_key for uid, sk in user_signing_keys.items()},
        merchant_keys={mid: sk.verify_key for mid, sk in merchant_signing_keys.items()},
    )
    return keyring, user_signing_keys, merchant_signing_keys
