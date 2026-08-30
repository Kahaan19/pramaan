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
    amount_paise: int
    order_id: str
    payment_link_id: str
    short_url: str
    payment_link_status: str
    payment_id: str | None
    payment_status: str | None
    replayed: bool


@router.post("/checkout", response_model=CheckoutResponse)
async def checkout(body: CheckoutRequest, db: Session = Depends(get_db)) -> CheckoutResponse:
    idempotency_key = body.idempotency_key or uuid.uuid4().hex
    try:
        result = await run_demo_checkout(
            db=db,
            amount_paise=body.amount_paise,
            idempotency_key=idempotency_key,
            description=body.description,
        )
    except RazorpayToolError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return CheckoutResponse(**result)
