"""Loads the DEMO user/merchant Ed25519 signing keys from secrets/ (written
by scripts/generate_keys.py) and signs Intent/Cart mandates for the buyer
agent to send to the REAL, running control plane. These are the same keys
the control plane's real Keyring verifies against (control-plane/mandates/
keys.py::get_keyring loads their PUBLIC halves from MANDATE_KEYRING_DIR) --
unlike eval/, which deliberately signs with its own throwaway keys against
its own throwaway database (see eval/buyers.py).
"""

import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTROL_PLANE_DIR = REPO_ROOT / "control-plane"
if str(CONTROL_PLANE_DIR) not in sys.path:
    sys.path.insert(0, str(CONTROL_PLANE_DIR))

from nacl.encoding import Base64Encoder  # noqa: E402
from nacl.signing import SigningKey  # noqa: E402

from mandates.canonical import signing_bytes  # noqa: E402
from mandates.schemas import UnsignedCartMandate, UnsignedIntentMandate  # noqa: E402

USER_ID = "user_kahaan"
MERCHANT_ID = "merchant_demo_01"


def _load_signing_key(role: str) -> SigningKey:
    seed_b64 = (REPO_ROOT / "secrets" / f"{role}_ed25519.key").read_text().strip()
    return SigningKey(seed_b64.encode("ascii"), encoder=Base64Encoder)


def _sign(mandate, key: SigningKey) -> str:
    return Base64Encoder.encode(key.sign(signing_bytes(mandate)).signature).decode("ascii")


def sign_intent(
    *,
    max_amount_paise: int,
    merchant_allowlist: list[str] | None = None,
    category: str = "groceries",
    human_present: bool = True,
    expires_in_hours: float = 24.0,
) -> dict:
    """24h default expiry -- long enough that a queued STEP_UP survives until
    a human actually clicks Approve in the dashboard (see README's note on
    the demo's step-up scenario).
    """
    key = _load_signing_key("user")
    unsigned = UnsignedIntentMandate(
        intent_id=f"intent_ba_{uuid.uuid4().hex[:12]}",
        user_id=USER_ID,
        max_amount_paise=max_amount_paise,
        merchant_allowlist=merchant_allowlist or [MERCHANT_ID],
        category=category,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=expires_in_hours),
        human_present=human_present,
        nonce=uuid.uuid4().hex,
    )
    return {**unsigned.model_dump(mode="json"), "signature": _sign(unsigned, key)}


def sign_cart(
    *,
    intent_id: str,
    total_paise: int,
    items: list[dict] | None = None,
    merchant_id: str = MERCHANT_ID,
    cart_id: str | None = None,
) -> dict:
    key = _load_signing_key("merchant")
    unsigned = UnsignedCartMandate(
        cart_id=cart_id or f"cart_ba_{uuid.uuid4().hex[:12]}",
        intent_id=intent_id,
        merchant_id=merchant_id,
        items=items or [{"sku": "sku_demo_widget", "qty": 1, "unit_price_paise": total_paise}],
        total_paise=total_paise,
        nonce=uuid.uuid4().hex,
    )
    return {**unsigned.model_dump(mode="json"), "signature": _sign(unsigned, key)}
