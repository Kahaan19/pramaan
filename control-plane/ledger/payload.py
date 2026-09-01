"""One flat payload shape for every event type. Every field is ALWAYS
present (default None where an event type doesn't use it) so that "None" and
"missing" can never be different byte sequences -- the same reasoning
mandates/schemas.py already applies to Optional fields, generalized across
event types instead of across one schema's own fields.

extra="forbid" + StrictInt/StrictBool: a typo'd field or a float creeping
into amount_paise must fail loudly, not silently enter a hash.

Never store: short_url (a capability URL -- possession opens the payment
page), raw upstream error text (the MCP server's own message strings, which
we don't control and which can echo request parameters), or any traceback/
exception repr (the mcp client carries the Razorpay token in a header).
error_detail is a short, bounded, human-authored string only.
"""

from pydantic import BaseModel, ConfigDict, StrictBool, StrictInt

SCHEMA_VERSION = 1


class LedgerPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: StrictInt = SCHEMA_VERSION
    event_type: str
    ts: str  # pre-rendered by ledger/hashing.py::render_ts -- never a datetime
    transaction_id: str
    explanation: str
    cart_id: str | None = None
    intent_id: str | None = None
    user_id: str | None = None
    merchant_id: str | None = None
    actor: str | None = None
    amount_paise: StrictInt | None = None
    human_present: StrictBool | None = None

    # Mandate verification -- digests + signatures, never full bodies, and
    # the digest is over signing_bytes() so it covers exactly what the
    # Ed25519 signature covers (including the domain tag), not model_dump().
    intent_digest: str | None = None
    intent_signature: str | None = None
    cart_digest: str | None = None
    cart_signature: str | None = None
    mandate_error_code: str | None = None
    mandate_error_message: str | None = None

    # Policy verdict
    decision: str | None = None
    rule_fired: str | None = None
    reason: str | None = None
    all_violations: tuple[str, ...] | None = None
    rules_version: StrictInt | None = None
    rules_sha256: str | None = None

    # Executor / Razorpay -- reference ids only, never short_url or raw
    # upstream text.
    razorpay_tool: str | None = None
    razorpay_outcome: str | None = None  # "ok" | "error"
    order_id: str | None = None
    payment_link_id: str | None = None
    payment_id: str | None = None
    checkout_status: str | None = None

    # Short, bounded, human-authored only -- never str(exc) or a traceback.
    error_detail: str | None = None
