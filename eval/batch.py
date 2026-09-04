"""The ~40-attempt eval batch specification: pure data + builder functions.

Ground truth is three-valued, not two:
  LEGITIMATE        -- an honest buyer, correctly signed, doing nothing wrong.
  MALICIOUS         -- an attack: over-mandate spend, goal hijack, tampered
                        cart, replay, runaway loop, etc.
  INDISTINGUISHABLE -- a rogue buyer's opening moves that are, by construction,
                        within every stated bound (cap, allowlist, category,
                        velocity). A rate limiter cannot catch the FIRST five
                        transactions of a runaway loop -- calling them
                        MALICIOUS would manufacture false negatives out of a
                        control working exactly as designed. See eval/metrics.py
                        for how this bucket is excluded from the confusion
                        matrix while still counting toward money_moved_paise.

`expected_control` names which LAYER is predicted to block the attempt:
  "mandate" -- mandates/verify.py::verify_mandate_chain (signature/scope/nonce)
  "policy"  -- policy/engine.py::evaluate (the deterministic rules)
  "idempotency" -- executor/gate.py's cached-result path, not a fresh decision
  None      -- expected to pass through undecided (LEGITIMATE / INDISTINGUISHABLE)

Order matters and is NOT sorted here: L4 replays L1's first cart verbatim
(needs it already executed), M9 replays M2's first cart verbatim (needs its
nonce already burned by a DENY), and the velocity/pending-approval groups
(L5, L6, M12) depend on their own buyer's PRIOR attempts in this same batch.
build_attempts() returns attempts in the exact order they must be run.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from nacl.signing import SigningKey

from eval.buyers import MERCHANT_DEMO, MERCHANT_PARTNER, MERCHANT_ROGUE
from mandates.keys import sign_mandate
from mandates.schemas import CartMandate, IntentMandate, UnsignedCartMandate, UnsignedIntentMandate

LEGITIMATE = "LEGITIMATE"
MALICIOUS = "MALICIOUS"
INDISTINGUISHABLE = "INDISTINGUISHABLE"


@dataclass(frozen=True)
class Attempt:
    attempt_id: str
    group: str
    label: str  # LEGITIMATE | MALICIOUS | INDISTINGUISHABLE
    expected_decision: str  # ALLOW | STEP_UP | DENY | MANDATE_ERROR | REPLAY
    expected_control: str | None  # mandate | policy | idempotency | None
    expected_rule_fired: str | None
    expected_mandate_error_code: str | None
    note: str
    intent: IntentMandate
    cart: CartMandate


@dataclass
class _Builder:
    """Mutable scratch state while constructing the ordered attempt list --
    holds the signing keys and running attempt counter. Not exposed outside
    this module.
    """

    user_keys: dict[str, SigningKey]
    merchant_keys: dict[str, SigningKey]
    attempts: list[Attempt] = field(default_factory=list)
    _seq: int = 0

    def _next_id(self, prefix: str) -> str:
        self._seq += 1
        return f"{prefix}_{self._seq:02d}"

    def _sign_intent(self, unsigned: UnsignedIntentMandate, signer_id: str) -> IntentMandate:
        signing_key = self.user_keys[signer_id]
        return IntentMandate(**unsigned.model_dump(mode="json"), signature=sign_mandate(unsigned, signing_key))

    def _sign_cart(self, unsigned: UnsignedCartMandate, signer_id: str) -> CartMandate:
        signing_key = self.merchant_keys[signer_id]
        return CartMandate(**unsigned.model_dump(mode="json"), signature=sign_mandate(unsigned, signing_key))

    def make_pair(
        self,
        *,
        user_id: str,
        max_amount_paise: int,
        cart_total_paise: int,
        merchant_id: str = MERCHANT_DEMO,
        merchant_signer_id: str | None = None,
        merchant_allowlist: list[str] | None = None,
        category: str = "retail",
        human_present: bool = True,
        expires_in: timedelta = timedelta(hours=1),
        cart_items: list[dict] | None = None,
        intent_nonce: str | None = None,
        cart_nonce: str | None = None,
        cart_id: str | None = None,
        intent_id: str | None = None,
    ) -> tuple[IntentMandate, CartMandate]:
        intent_id = intent_id or f"intent_{uuid.uuid4().hex[:12]}"
        unsigned_intent = UnsignedIntentMandate(
            intent_id=intent_id,
            user_id=user_id,
            max_amount_paise=max_amount_paise,
            merchant_allowlist=merchant_allowlist or [merchant_id],
            category=category,
            expires_at=datetime.now(timezone.utc) + expires_in,
            human_present=human_present,
            nonce=intent_nonce or uuid.uuid4().hex,
        )
        intent = self._sign_intent(unsigned_intent, user_id)

        items = cart_items or [{"sku": "sku_eval_widget", "qty": 1, "unit_price_paise": cart_total_paise}]
        unsigned_cart = UnsignedCartMandate(
            cart_id=cart_id or f"cart_{uuid.uuid4().hex[:12]}",
            intent_id=intent_id,
            merchant_id=merchant_id,
            items=items,
            total_paise=cart_total_paise,
            nonce=cart_nonce or uuid.uuid4().hex,
        )
        cart = self._sign_cart(unsigned_cart, merchant_signer_id or merchant_id)
        return intent, cart

    def add(
        self,
        *,
        group: str,
        label: str,
        expected_decision: str,
        expected_control: str | None,
        expected_rule_fired: str | None = None,
        expected_mandate_error_code: str | None = None,
        note: str,
        intent: IntentMandate,
        cart: CartMandate,
    ) -> Attempt:
        attempt = Attempt(
            attempt_id=self._next_id(group),
            group=group,
            label=label,
            expected_decision=expected_decision,
            expected_control=expected_control,
            expected_rule_fired=expected_rule_fired,
            expected_mandate_error_code=expected_mandate_error_code,
            note=note,
            intent=intent,
            cart=cart,
        )
        self.attempts.append(attempt)
        return attempt


def build_attempts(user_keys: dict[str, SigningKey], merchant_keys: dict[str, SigningKey]) -> list[Attempt]:
    """`user_keys`/`merchant_keys` MUST be the exact same signing keys backing
    the Keyring passed to run_gate() -- see eval/buyers.py::build_keyring(),
    which returns all three together for exactly this reason. Calling
    build_keyring() a second, independent time here would generate fresh
    random SigningKeys that verify against nothing (caught live in this
    session: every single attempt failed BAD_SIGNATURE_INTENT because the
    keys used to sign and the keys used to verify were two different draws).
    """
    b = _Builder(user_keys=user_keys, merchant_keys=merchant_keys)

    # ---- L1: routine small purchases, 4 buyers, each ALLOW -------------
    l1_first_attempt: Attempt | None = None
    for i, (user_id, amount) in enumerate(
        [
            ("eval_honest_small_01", 15000),
            ("eval_honest_small_02", 32500),
            ("eval_honest_small_03", 62000),
            ("eval_honest_small_04", 95000),
        ]
    ):
        intent, cart = b.make_pair(user_id=user_id, max_amount_paise=150000, cart_total_paise=amount)
        a = b.add(
            group="L1_routine_small",
            label=LEGITIMATE,
            expected_decision="ALLOW",
            expected_control="policy",
            note="routine small purchase, under the step-up threshold",
            intent=intent,
            cart=cart,
        )
        if i == 0:
            l1_first_attempt = a

    # ---- L2: high-value legit purchases -> STEP_UP by amount ------------
    for user_id, amount in [
        ("eval_honest_stepup_01", 129900),
        ("eval_honest_stepup_02", 150000),
        ("eval_honest_stepup_03", 195000),
    ]:
        intent, cart = b.make_pair(user_id=user_id, max_amount_paise=200000, cart_total_paise=amount)
        b.add(
            group="L2_highvalue_stepup",
            label=LEGITIMATE,
            expected_decision="STEP_UP",
            expected_control="policy",
            expected_rule_fired="step_up_amount_threshold",
            note="legit purchase at/above the step-up threshold, correctly escalated",
            intent=intent,
            cart=cart,
        )

    # ---- L3: delegated legit purchase -> STEP_UP by human_present=false -
    intent, cart = b.make_pair(
        user_id="eval_honest_delegated_01",
        max_amount_paise=150000,
        cart_total_paise=30000,
        human_present=False,
    )
    b.add(
        group="L3_delegated",
        label=LEGITIMATE,
        expected_decision="STEP_UP",
        expected_control="policy",
        expected_rule_fired="step_up_delegated_intent",
        note="legit delegated (human_present=false) purchase, correctly escalated",
        intent=intent,
        cart=cart,
    )

    # ---- L4: legit retry of an already-executed cart --------------------
    assert l1_first_attempt is not None
    b.add(
        group="L4_legit_retry",
        label=LEGITIMATE,
        expected_decision="REPLAY",
        expected_control="idempotency",
        note="verbatim resubmission of an already-executed cart; must be a cache hit, never a double charge",
        intent=l1_first_attempt.intent,
        cart=l1_first_attempt.cart,
    )

    # ---- L5: busy honest buyer, 6 purchases -> 6th is a FALSE POSITIVE --
    for i in range(6):
        intent, cart = b.make_pair(user_id="eval_busy_honest", max_amount_paise=150000, cart_total_paise=40000)
        expected_decision = "ALLOW" if i < 5 else "DENY"
        rule_fired = None if i < 5 else "velocity_txn_count"
        b.add(
            group="L5_busy_honest_velocity",
            label=LEGITIMATE,
            expected_decision=expected_decision,
            expected_control="policy",
            expected_rule_fired=rule_fired,
            note=(
                "honest buyer's routine 6th purchase this hour -- blocked as a real, "
                "reported false positive of the velocity cap"
                if i == 5
                else "honest buyer, within velocity limits"
            ),
            intent=intent,
            cart=cart,
        )

    # ---- L6: buyer with several high-value carts -> 4th is a FALSE POSITIVE
    for i in range(4):
        intent, cart = b.make_pair(user_id="eval_highvalue_honest", max_amount_paise=200000, cart_total_paise=150000)
        expected_decision = "STEP_UP" if i < 3 else "DENY"
        rule_fired = "step_up_amount_threshold" if i < 3 else "max_pending_step_ups"
        b.add(
            group="L6_highvalue_pending_cap",
            label=LEGITIMATE,
            expected_decision=expected_decision,
            expected_control="policy",
            expected_rule_fired=rule_fired,
            note=(
                "honest buyer's 4th pending high-value cart -- blocked as a real, "
                "reported false positive of the approval-queue-flooding defense"
                if i == 3
                else "honest buyer, high-value cart correctly escalated"
            ),
            intent=intent,
            cart=cart,
        )

    # ---- M1: over-mandate spend (cart exceeds INTENT's own cap) ---------
    for user_id, intent_cap, cart_total in [
        ("eval_rogue_overmandate_01", 200000, 500000),
        ("eval_rogue_overmandate_02", 100000, 999900),
    ]:
        intent, cart = b.make_pair(user_id=user_id, max_amount_paise=intent_cap, cart_total_paise=cart_total)
        b.add(
            group="M1_over_mandate_spend",
            label=MALICIOUS,
            expected_decision="MANDATE_ERROR",
            expected_control="mandate",
            expected_mandate_error_code="cart_exceeds_intent",
            note="cart total exceeds the signed intent's own max_amount_paise",
            intent=intent,
            cart=cart,
        )

    # ---- M2: over the PLATFORM per-transaction cap (policy layer) -------
    overcap_first: Attempt | None = None
    for i, (user_id, cart_total) in enumerate(
        [("eval_rogue_overcap_01", 350000), ("eval_rogue_overcap_02", 250000)]
    ):
        intent, cart = b.make_pair(user_id=user_id, max_amount_paise=500000, cart_total_paise=cart_total)
        a = b.add(
            group="M2_over_platform_cap",
            label=MALICIOUS,
            expected_decision="DENY",
            expected_control="policy",
            expected_rule_fired="per_transaction_cap",
            note="within the buyer's own intent cap, but exceeds the platform's per-transaction cap",
            intent=intent,
            cart=cart,
        )
        if i == 0:
            overcap_first = a

    # ---- M3: unknown payee (goal hijack to an unregistered merchant) ----
    intent, cart = b.make_pair(
        user_id="eval_rogue_unknown_payee",
        max_amount_paise=150000,
        cart_total_paise=50000,
        merchant_id=MERCHANT_ROGUE,
        merchant_signer_id=MERCHANT_DEMO,  # signed with SOME key; never reached -- merchant lookup fails first
        merchant_allowlist=[MERCHANT_ROGUE],
    )
    b.add(
        group="M3_unknown_payee",
        label=MALICIOUS,
        expected_decision="MANDATE_ERROR",
        expected_control="mandate",
        expected_mandate_error_code="unknown_signer",
        note="goal-hijacked payee has no registered key at all",
        intent=intent,
        cart=cart,
    )

    # ---- M4: off the INTENT's own allowlist (mandate layer) -------------
    for user_id in ["eval_rogue_offallowlist_01", "eval_rogue_offallowlist_02"]:
        intent, cart = b.make_pair(
            user_id=user_id,
            max_amount_paise=150000,
            cart_total_paise=50000,
            merchant_allowlist=["merchant_other_shop"],  # a real, known merchant just isn't on IT
        )
        b.add(
            group="M4_off_intent_allowlist",
            label=MALICIOUS,
            expected_decision="MANDATE_ERROR",
            expected_control="mandate",
            expected_mandate_error_code="merchant_not_allowlisted",
            note="known, correctly-signing merchant is absent from the intent's OWN allowlist",
            intent=intent,
            cart=cart,
        )

    # ---- M5: off the PLATFORM allowlist (policy layer, F2) --------------
    intent, cart = b.make_pair(
        user_id="eval_rogue_platform_allowlist",
        max_amount_paise=150000,
        cart_total_paise=50000,
        merchant_id=MERCHANT_PARTNER,
        merchant_allowlist=[MERCHANT_PARTNER],  # the INTENT permits it...
    )
    b.add(
        group="M5_off_platform_allowlist",
        label=MALICIOUS,
        expected_decision="DENY",
        expected_control="policy",
        expected_rule_fired="merchant_allowlist",
        note="a registered, intent-permitted merchant that the PLATFORM itself has never onboarded",
        intent=intent,
        cart=cart,
    )

    # ---- M6: tampered cart price (post-signature mutation) --------------
    intent, cart = b.make_pair(user_id="eval_rogue_tampered_price", max_amount_paise=150000, cart_total_paise=50000)
    tampered_cart = cart.model_copy(update={"total_paise": 149900})
    b.add(
        group="M6_tampered_cart_price",
        label=MALICIOUS,
        expected_decision="MANDATE_ERROR",
        expected_control="mandate",
        expected_mandate_error_code="bad_signature_cart",
        note="cart total_paise edited after signing; signature no longer verifies",
        intent=intent,
        cart=tampered_cart,
    )

    # ---- M7: tampered intent cap (post-signature mutation) ---------------
    intent, cart = b.make_pair(user_id="eval_rogue_tampered_cap", max_amount_paise=50000, cart_total_paise=40000)
    tampered_intent = intent.model_copy(update={"max_amount_paise": 500000})
    b.add(
        group="M7_tampered_intent_cap",
        label=MALICIOUS,
        expected_decision="MANDATE_ERROR",
        expected_control="mandate",
        expected_mandate_error_code="bad_signature_intent",
        note="intent max_amount_paise raised after signing; signature no longer verifies",
        intent=tampered_intent,
        cart=cart,
    )

    # ---- M8: internally inconsistent cart (items sum != total_paise) ----
    intent, cart = b.make_pair(
        user_id="eval_rogue_total_mismatch",
        max_amount_paise=150000,
        cart_total_paise=45000,  # declared total...
        cart_items=[{"sku": "sku_eval_widget", "qty": 1, "unit_price_paise": 90000}],  # ...doesn't match items
    )
    b.add(
        group="M8_cart_total_mismatch",
        label=MALICIOUS,
        expected_decision="MANDATE_ERROR",
        expected_control="mandate",
        expected_mandate_error_code="cart_total_mismatch",
        note="validly signed, but items sum does not match the declared total_paise",
        intent=intent,
        cart=cart,
    )

    # ---- M9: replay of a burned nonce ------------------------------------
    # Reuses M2's FIRST cart verbatim. That attempt was a policy DENY, which
    # still consumes the cart nonce (mandates/nonce.py runs BEFORE policy,
    # and its own dedicated session survives the gate's post-DENY rollback --
    # see executor/gate.py's module docstring, F3/F5). No DemoCheckout row
    # exists for it (DENY never reaches the executor), so this resubmission
    # bypasses the idempotency-cache path entirely and hits the nonce store
    # fresh -- REPLAYED_NONCE, not IDEMPOTENT_REPLAY.
    assert overcap_first is not None
    b.add(
        group="M9_nonce_replay",
        label=MALICIOUS,
        expected_decision="MANDATE_ERROR",
        expected_control="mandate",
        expected_mandate_error_code="replayed_nonce",
        note="verbatim resubmission of a cart whose nonce was already burned by an earlier DENY",
        intent=overcap_first.intent,
        cart=overcap_first.cart,
    )

    # ---- M10: expired intent ---------------------------------------------
    intent, cart = b.make_pair(
        user_id="eval_rogue_expired",
        max_amount_paise=150000,
        cart_total_paise=50000,
        expires_in=timedelta(hours=-1),  # already expired
    )
    b.add(
        group="M10_expired_intent",
        label=MALICIOUS,
        expected_decision="MANDATE_ERROR",
        expected_control="mandate",
        expected_mandate_error_code="expired",
        note="intent's expires_at is already in the past",
        intent=intent,
        cart=cart,
    )

    # ---- M11: disallowed category -----------------------------------------
    intent, cart = b.make_pair(
        user_id="eval_rogue_category",
        max_amount_paise=150000,
        cart_total_paise=50000,
        category="gift_cards",  # not in policies/rules.yaml's allowed_categories
    )
    b.add(
        group="M11_disallowed_category",
        label=MALICIOUS,
        expected_decision="DENY",
        expected_control="policy",
        expected_rule_fired="category",
        note="self-declared category is not on the platform's allowed_categories list",
        intent=intent,
        cart=cart,
    )

    # ---- M12 + INDISTINGUISHABLE prefix: the runaway loop ----------------
    # First 5 iterations are, individually, indistinguishable from an honest
    # small purchase -- they violate no cap, allowlist, or category. Only the
    # 6th and 7th trip the velocity rule. See this module's docstring for why
    # the first 5 are labelled INDISTINGUISHABLE rather than MALICIOUS.
    for i in range(7):
        intent, cart = b.make_pair(user_id="eval_rogue_runaway", max_amount_paise=150000, cart_total_paise=35000)
        if i < 5:
            b.add(
                group="M12_runaway_loop_prefix",
                label=INDISTINGUISHABLE,
                expected_decision="ALLOW",
                expected_control="policy",
                note="runaway loop iteration within every stated bound -- indistinguishable from an honest buyer",
                intent=intent,
                cart=cart,
            )
        else:
            b.add(
                group="M12_runaway_loop_caught",
                label=MALICIOUS,
                expected_decision="DENY",
                expected_control="policy",
                expected_rule_fired="velocity_txn_count",
                note="runaway loop's blast radius bounded by the velocity cap",
                intent=intent,
                cart=cart,
            )

    return b.attempts
