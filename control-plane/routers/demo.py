import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from db import get_db
from executor.checkout import run_demo_checkout
from executor.razorpay_mcp import RazorpayToolError

router = APIRouter(prefix="/demo", tags=["demo"])


class CheckoutRequest(BaseModel):
    amount_paise: int = Field(gt=0, description="Amount in integer paise, e.g. 1299 for Rs 12.99")
    idempotency_key: str | None = Field(
        default=None, description="Reuse to safely retry without double-charging"
    )
    description: str | None = None


class CheckoutResponse(BaseModel):
    idempotency_key: str
    status: str
    amount_paise: int
    order_id: str | None
    payment_link_id: str | None
    short_url: str | None
    payment_link_status: str | None
    payment_id: str | None
    payment_status: str | None
    replayed: bool


@router.post("/checkout", response_model=CheckoutResponse)
async def checkout(body: CheckoutRequest, db: Session = Depends(get_db)) -> CheckoutResponse:
    # NOTE: this endpoint is still the ungated Phase 0 spine -- no mandate or
    # policy gate in front of it yet. cart_id is a throwaway stand-in for the
    # duration of the fix(executor) commit; Phase 2's gate (executor/gate.py)
    # replaces this whole request/response shape with one driven by a signed
    # cart, at which point cart_id becomes the real mandate cart_id.
    cart_id = body.idempotency_key or uuid.uuid4().hex
    try:
        result = await run_demo_checkout(
            db=db,
            cart_id=cart_id,
            amount_paise=body.amount_paise,
            description=body.description,
        )
    except RazorpayToolError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return CheckoutResponse(**result)
