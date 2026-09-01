from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from db import get_db
from executor.gate import (
    AllowResult,
    DenyResult,
    ReplayResult,
    StepUpResult,
    mandate_error_status_code,
    run_gate,
)
from executor.razorpay_mcp import RazorpayToolError
from mandates.errors import MandateError
from mandates.schemas import CartMandate, IntentMandate

router = APIRouter(prefix="/demo", tags=["demo"])

# A cached (previously-executed) checkout's status maps to the HTTP status a
# caller would have gotten had they somehow reached this state fresh --
# COMMITTED looks like success, FAILED looks like the executor error it is,
# IN_FLIGHT means a concurrent duplicate is still being processed.
_REPLAY_STATUS_CODES = {"COMMITTED": 200, "FAILED": 502, "IN_FLIGHT": 409}


class GatedCheckoutRequest(BaseModel):
    """No amount_paise, no client idempotency_key. extra="forbid" so a body
    that tries to smuggle either back in is rejected loudly (422) rather
    than silently ignored -- the charged amount comes ONLY from the signed
    cart.total_paise, and the idempotency key is ALWAYS derived from
    cart.cart_id.
    """

    model_config = ConfigDict(extra="forbid")

    intent: IntentMandate
    cart: CartMandate


def _verdict_dict(verdict) -> dict:
    return {
        "decision": verdict.decision.value,
        "rule_fired": verdict.rule_fired,
        "reason": verdict.reason,
        "evaluated_at": verdict.evaluated_at.isoformat(),
        "rules_version": verdict.rules_version,
        "rules_sha256": verdict.rules_sha256,
    }


@router.post("/checkout")
async def checkout(body: GatedCheckoutRequest, db: Session = Depends(get_db)) -> JSONResponse:
    # `except*` cannot contain `return`/`break`/`continue` (a language
    # restriction on except* blocks), so the MandateError branch stashes its
    # response and returns after the try/except* statement instead.
    mandate_error_response: JSONResponse | None = None

    try:
        result = await run_gate(db, body.intent, body.cart)
    except* MandateError as eg:
        # except* (not a plain except) because executor/gate.py and
        # executor/checkout.py both use except* around the Razorpay call, and
        # except* always binds an ExceptionGroup -- even for a single plain
        # exception -- so a plain `except MandateError` here would stop
        # matching once those changes landed. len==1 is the expected case
        # (verify_mandate_chain raises at most one MandateError); a genuine
        # multi-exception group is unexpected and reported via its first error.
        exc = eg.exceptions[0]
        mandate_error_response = JSONResponse(
            status_code=mandate_error_status_code(exc.code),
            content={"error_code": exc.code.value, "detail": exc.message},
        )
    except* RazorpayToolError as eg:
        exc = eg.exceptions[0]
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    if mandate_error_response is not None:
        return mandate_error_response

    if isinstance(result, DenyResult):
        return JSONResponse(status_code=403, content={"verdict": _verdict_dict(result.verdict)})

    if isinstance(result, StepUpResult):
        return JSONResponse(
            status_code=202,
            content={
                "verdict": _verdict_dict(result.verdict),
                "step_up_request_id": result.step_up_request_id,
                "cart_id": body.cart.cart_id,
            },
        )

    if isinstance(result, ReplayResult):
        status_code = _REPLAY_STATUS_CODES.get(result.checkout["status"], 409)
        return JSONResponse(status_code=status_code, content={"checkout": result.checkout, "replayed": True})

    assert isinstance(result, AllowResult)
    return JSONResponse(
        status_code=200,
        content={"verdict": _verdict_dict(result.verdict), "checkout": result.checkout},
    )
