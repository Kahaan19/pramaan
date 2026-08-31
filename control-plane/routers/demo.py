from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from db import get_db
from executor.gate import AllowResult, DenyResult, StepUpResult, mandate_error_status_code, run_gate
from executor.razorpay_mcp import RazorpayToolError
from mandates.errors import MandateError
from mandates.schemas import CartMandate, IntentMandate

router = APIRouter(prefix="/demo", tags=["demo"])


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
    try:
        result = await run_gate(db, body.intent, body.cart)
    except MandateError as exc:
        return JSONResponse(
            status_code=mandate_error_status_code(exc.code),
            content={"error_code": exc.code.value, "detail": exc.message},
        )
    except RazorpayToolError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

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

    assert isinstance(result, AllowResult)
    return JSONResponse(
        status_code=200,
        content={"verdict": _verdict_dict(result.verdict), "checkout": result.checkout},
    )
