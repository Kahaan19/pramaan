"""The chain-hash primitive, plus the ONE datetime rendering used anywhere a
timestamp enters a hashed payload.

Why a dedicated renderer: `canonical_json()` cannot serialize a raw datetime
at all (verified: raises TypeError). The obvious alternatives disagree with
each other -- `dt.isoformat()` yields "...+00:00" while Pydantic's
`model_dump(mode="json")` yields "...Z" for the same instant, and this
codebase already uses BOTH styles in different places. Postgres also returns
TIMESTAMPTZ in the *session's* timezone, not necessarily UTC. Any of these
could silently change the bytes a hash was computed over. So `render_ts()` is
called exactly once, at write time, before a timestamp ever enters a
LedgerPayload -- payload.ts is a plain string field, never a datetime -- and
verify_chain() never needs to re-render anything: it re-hashes the STORED
canonical bytes verbatim (see writer.py / verify.py).
"""

import hashlib
from datetime import datetime, timezone

from pydantic import BaseModel

CHAIN_TAG = b"pramaan.ledger.v1\n"
GENESIS_PREV_HASH = "0" * 64


class _TsWrapper(BaseModel):
    """Reuses Pydantic's own mode="json" datetime rendering (the same
    mechanism mandates/schemas.py relies on) instead of hand-rolling ISO
    formatting, which is exactly the kind of thing that drifts subtly.
    """

    ts: datetime


def render_ts(dt: datetime) -> str:
    if dt.tzinfo is None:
        raise ValueError("render_ts requires a timezone-aware datetime")
    # Pydantic's mode="json" normalizes "+00:00" to "Z" but PRESERVES a
    # non-UTC offset verbatim (confirmed: a +05:30 datetime stays +05:30) --
    # the exact trap documented in mandates/schemas.py. Force UTC first so
    # the same instant always renders identically regardless of input tz.
    return _TsWrapper(ts=dt.astimezone(timezone.utc)).model_dump(mode="json")["ts"]


def chain_hash(prev_hash: str, payload_canonical: bytes) -> str:
    """prev_hash and the returned row_hash are both 64-char lowercase hex
    strings throughout the ledger -- stored, compared, and hashed as their
    ASCII text form, never as raw binary. Keeps every value inspectable via
    plain SQL and avoids an extra hex<->bytes conversion boundary.
    """
    digest = hashlib.sha256(CHAIN_TAG + prev_hash.encode("ascii") + payload_canonical)
    return digest.hexdigest()
