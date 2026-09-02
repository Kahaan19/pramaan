import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from db import get_db
from executor.gate import (
    AllowResult,
    DenyResult,
    ReplayResult,
    StepUpNotFoundOrResolvedResult,
    StepUpResult,
    mandate_error_status_code,
    run_gate,
    run_step_up_approval,
    run_step_up_denial,
)
from executor.razorpay_mcp import RazorpayToolError
from executor.step_up import StepUpRequest, get_by_cart_id, list_pending
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


class StepUpDecisionRequest(BaseModel):
    """`actor` is a plain, unauthenticated string naming who clicked the
    button -- there is no operator identity/auth system in this demo. Stated
    honestly rather than implying a real approval-authority check exists.
    """

    model_config = ConfigDict(extra="forbid")

    actor: str | None = None


def _verdict_dict(verdict) -> dict:
    return {
        "decision": verdict.decision.value,
        "rule_fired": verdict.rule_fired,
        "reason": verdict.reason,
        "evaluated_at": verdict.evaluated_at.isoformat(),
        "rules_version": verdict.rules_version,
        "rules_sha256": verdict.rules_sha256,
    }


def _step_up_summary(row: StepUpRequest) -> dict:
    """Parses the stored mandate snapshots for display -- the dashboard's
    approval queue must show the reviewer the real mandate, not a rubber
    stamp (OWASP's warning, ARCHITECTURE.md 5.7). Never re-derives the
    charge from anything other than this stored snapshot.
    """
    intent = json.loads(row.intent_json)
    cart = json.loads(row.cart_json)
    return {
        "cart_id": row.cart_id,
        "user_id": row.user_id,
        "intent_id": row.intent_id,
        "merchant_id": cart.get("merchant_id"),
        "amount_paise": row.amount_paise,
        "items": cart.get("items", []),
        "category": intent.get("category"),
        "max_amount_paise": intent.get("max_amount_paise"),
        "human_present": intent.get("human_present"),
        "rule_fired": row.rule_fired,
        "reason": row.reason,
        "status": row.status,
        "created_at": row.created_at.isoformat(),
        "expires_at": row.expires_at.isoformat(),
        "intent_expires_at": intent.get("expires_at"),
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


@router.get("/step-up")
def list_step_up_queue(limit: int = 50, db: Session = Depends(get_db)) -> dict:
    return {"pending": [_step_up_summary(row) for row in list_pending(db, limit=limit)]}


@router.get("/step-up/{cart_id}")
def get_step_up(cart_id: str, db: Session = Depends(get_db)) -> dict:
    row = get_by_cart_id(db, cart_id)
    if row is None:
        raise HTTPException(status_code=404, detail="no step-up request for this cart_id")
    return _step_up_summary(row)


@router.post("/step-up/{cart_id}/approve")
async def approve_step_up(
    cart_id: str, body: StepUpDecisionRequest, db: Session = Depends(get_db)
) -> JSONResponse:
    """Approval re-verifies the mandate and re-evaluates policy from the
    STORED signed snapshot with a fresh clock -- see
    executor/gate.py::run_step_up_approval -- and executes through the exact
    same _execute_allowed() path an automatic ALLOW uses. No shortcut.
    """
    mandate_error_response: JSONResponse | None = None

    try:
        result = await run_step_up_approval(db, cart_id, actor=body.actor)
    except* MandateError as eg:
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

    if isinstance(result, StepUpNotFoundOrResolvedResult):
        raise HTTPException(status_code=409, detail="no pending step-up request for this cart_id")

    if isinstance(result, DenyResult):
        # The human approved, but re-evaluated policy vetoed it (e.g. the
        # intent expired while queued, or a velocity/pending-approval limit
        # was hit by other transactions since queuing).
        return JSONResponse(status_code=403, content={"verdict": _verdict_dict(result.verdict)})

    assert isinstance(result, AllowResult)
    return JSONResponse(
        status_code=200,
        content={"verdict": _verdict_dict(result.verdict), "checkout": result.checkout},
    )


@router.post("/step-up/{cart_id}/deny")
async def deny_step_up(cart_id: str, body: StepUpDecisionRequest, db: Session = Depends(get_db)) -> dict:
    result = await run_step_up_denial(db, cart_id, actor=body.actor)
    if isinstance(result, StepUpNotFoundOrResolvedResult):
        raise HTTPException(status_code=409, detail="no pending step-up request for this cart_id")
    return {"cart_id": result.cart_id, "status": result.status}
